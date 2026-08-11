"""The callable command surface and its classification gate."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fws import invoke as policy
from fws.control import ControlLock
from fws.driver import REFUSED as DRIVER_REFUSED
from fws.driver import RobotDriver
from fws.events import AuditLog
from fws.invoke_api import build
from fws.protocol.commands import COMMANDS

BASE = "/api/v1/invoke"


class Harness:
    def __init__(self, client, control, audit, fake):
        self.client = client
        self.control = control
        self.audit = audit
        self.fake = fake

    def lease(self, *domains: str, client_id: str = "test-client") -> str:
        return self.control.acquire(client_id, list(domains), ttl_s=60).token

    def post(self, name: str, args=None, confirm=False, token=None):
        headers = {"X-FWS-Control-Token": token} if token else {}
        return self.client.post(f"{BASE}/{name}",
                                json={"args": args or [], "confirm": confirm},
                                headers=headers)

    def sent(self, name: str) -> list:
        return [c for c in self.fake.calls if c[0] == name]


@pytest.fixture
def h(fake):
    driver = RobotDriver(fake.host, timeout=3.0, port=fake.rpc_port,
                         upload_port=fake.upload_port,
                         download_port=fake.download_port)
    control = ControlLock()
    audit = AuditLog()
    app = FastAPI()
    app.include_router(build(lambda: driver, lambda: control,
                             lambda action, **kw: audit.record(action, **kw)))
    with TestClient(app) as client:
        yield Harness(client, control, audit, fake)


class TestClassificationIsSound:
    """The danger classification is evidence-based."""

    @pytest.mark.parametrize("name,why", [
        ("ARCStart", "strikes a welding arc"),
        ("ActGripper", "activates a gripper, which closes on things"),
        ("TractorMoveL", "drives a mobile base in a straight line"),
    ])
    def test_the_named_leaks_are_classified_motion(self, name, why):
        """These commands are classified motion."""
        c = COMMANDS[name]
        assert c.danger == "motion", f"{name} {why}"
        assert c.basis, "a classification with no stated basis is not auditable"
        domain, confirm = policy.requirements(c)
        assert domain == "motion" and confirm is True

    def test_nothing_is_classified_other_any_more(self):
        """No command is classified 'other'; the default 'unknown' is gated like
        motion."""
        assert [n for n, c in COMMANDS.items() if c.danger == "other"] == []

    def test_the_whole_previously_leaking_set_is_now_gated(self):
        """Motion-like commands are gated or positively established as reads."""
        import re
        leaky = [n for n in COMMANDS
                 if re.search(r"move|jog|start|servo|grip|arc", n, re.I)
                 and COMMANDS[n].callable_directly]
        assert len(leaky) > 40, "the sample shrank; re-derive it"
        for name in leaky:
            c = COMMANDS[name]
            domain, _ = policy.requirements(c)
            if domain is not None:
                continue
            assert c.danger in ("read", "stop"), name
            if c.danger == "read":
                assert any("payload" in b for b in c.basis), (
                    f"{name} is open and its read status rests on one signal")

    def test_resuming_motion_is_not_in_the_never_locked_class(self):
        """Resume commands are motion-class, not in the never-locked stop class."""
        assert COMMANDS["ResumeMotion"].danger == "motion"
        assert COMMANDS["ResumeMotion"].kind == "local"
        assert COMMANDS["ProgramResume"].danger == "motion"
        for name, c in COMMANDS.items():
            if c.danger == "stop":
                assert "Resume" not in name, name

    def test_the_registry_refuses_everything_the_driver_refuses(self):
        """The registry refuses everything the driver refuses."""
        for wire in DRIVER_REFUSED:
            entries = [c for c in COMMANDS.values() if c.wire_name == wire]
            for c in entries:
                assert c.danger == "refused", c.python_name
                assert not c.callable_directly, c.python_name

    def test_every_class_has_an_explicit_gate(self):
        """Every danger class has an explicit gate in the matrix."""
        for c in COMMANDS.values():
            assert c.danger in {*policy.REQUIREMENTS, "refused"}, c.danger

    def test_the_kinematics_calculators_stay_open(self):
        """The kinematics calculators stay open (read-class)."""
        for name in ("GetForwardKin", "GetInverseKin"):
            assert COMMANDS[name].danger == "read", name
            assert policy.requirements(COMMANDS[name]) == (None, False)

    def test_a_getter_with_a_measured_side_effect_is_not_read(self):
        """A getter with a measured side effect is not classified read."""
        for name in ("GetSSHKeygen", "GetTPDStartPose",
                     "GetTrajectoryStartPose"):
            assert COMMANDS[name].danger != "read", name
            assert COMMANDS[name].confidence == "measured", name

    def test_every_command_carries_its_derivation(self):
        for name, c in COMMANDS.items():
            assert c.basis, name
            assert c.confidence in ("measured", "documented", "inferred",
                                    "none"), name


class TestGate:
    def test_read_is_open(self, h):
        r = h.post("GetSoftwareVersion")
        assert r.status_code == 200
        assert r.json()["result"][1] == "FR5-V1-002(V6.0)"

    def test_stop_is_open(self, h):
        """A lock that can block the stop path is a hazard, not a control."""
        assert h.post("StopMotion").status_code == 200

    def test_config_needs_a_held_lease(self, h):
        r = h.post("SetSpeed", [50])
        assert r.status_code == 428
        assert "'config' control lock" in r.json()["detail"]
        assert h.post("SetSpeed", [50],
                      token=h.lease("config")).status_code == 200

    def test_motion_needs_both_a_lease_and_confirmation(self, h):
        # ActGripper, not StartJOG: fws/invoke.py refuses StartJOG so the
        # bounded /motion/jog route stays the only way to reach it. ActGripper
        # is motion-class and unowned.
        args = [1, 1]
        r = h.post("ActGripper", args)
        assert r.status_code == 428
        detail = r.json()["detail"]
        # Both prerequisites in one answer: a caller should not have to
        # acquire a lease to discover it also needed confirm=true.
        assert "'motion' control lock" in detail
        assert "confirm=true" in detail

        token = h.lease("motion")
        r = h.post("ActGripper", args, token=token)
        assert r.status_code == 428
        assert "confirm=true" in r.json()["detail"]
        assert not h.sent("ActGripper"), "nothing may be sent while refused"

        r = h.post("ActGripper", args, confirm=True, token=token)
        assert r.status_code == 200
        assert h.sent("ActGripper")[-1][1] == (1, 1)

    def test_unknown_is_gated_exactly_like_motion(self, h):
        """An unknown-class command is gated exactly like motion."""
        assert COMMANDS["Mode"].danger == "unknown"
        r = h.post("Mode", [1])
        assert r.status_code == 428
        assert "'motion' control lock" in r.json()["detail"]
        assert h.post("Mode", [1], confirm=True,
                      token=h.lease("motion")).status_code == 200

    def test_a_lease_held_by_someone_else_is_423_not_428(self, h):
        """428 means "go and acquire one"; 423 means "you cannot"."""
        h.lease("motion", client_id="the-other-client")
        r = h.post("ActGripper", [1, 1], confirm=True,
                   token="not-the-right-token")
        assert r.status_code == 423
        assert "the-other-client" in r.json()["detail"]

    @pytest.mark.parametrize("name", [
        "ShutDownRobotOS", "KernelUpgrade", "JointAllParamUpgrade",
        "SetJointFirmwareUpgrade", "GetLuaList", "SlaveFileWrite",
        "SetSysServoBootMode",
    ])
    def test_refused_stays_refused_with_a_lease_and_a_confirmation(self, h, name):
        r = h.post(name, confirm=True, token=h.lease("motion", "config"))
        assert r.status_code == 403
        assert "refused" in r.json()["detail"].lower()
        assert not h.sent(COMMANDS[name].wire_name or name)

    def test_composites_stay_uncallable(self, h):
        r = h.post("MoveL", [[0.0] * 33], confirm=True,
                   token=h.lease("motion"))
        assert r.status_code == 422
        assert "composite" in r.json()["detail"]
        assert "GetInverseKin" in r.json()["detail"]

    def test_locals_stay_uncallable(self, h):
        r = h.post("ResumeMotion", confirm=True, token=h.lease("motion"))
        assert r.status_code == 422
        assert "local" in r.json()["detail"]


class TestTypedInvocation:
    def test_too_few_arguments_are_rejected_before_transmission(self, h):
        r = h.post("ActGripper", [1], confirm=True, token=h.lease("motion"))
        assert r.status_code == 422
        assert "wire order" in r.json()["detail"]
        assert not h.sent("ActGripper")

    def test_too_many_arguments_are_rejected_before_transmission(self, h):
        """Arity is exact: too many arguments are rejected before transmission."""
        r = h.post("ActGripper", [1, 1, 99, 99],
                   confirm=True, token=h.lease("motion"))
        assert r.status_code == 422
        assert not h.sent("ActGripper")

        r = h.post("GetSoftwareVersion", ["junk"] * 9)
        assert r.status_code == 422
        assert not h.sent("GetSoftwareVersion")

    def test_a_bad_type_names_the_argument(self, h):
        r = h.post("SetSpeed", ["fast"], token=h.lease("config"))
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert "vel" in detail and "int" in detail
        assert not h.sent("SetSpeed")

    def test_arguments_are_coerced_to_the_types_the_sdk_coerces_them_to(self, h):
        """Arguments are coerced to the types the SDK coerces them to."""
        r = h.post("ActGripper", ["1", "2"], confirm=True,
                   token=h.lease("motion"))
        assert r.status_code == 200
        sent = h.sent("ActGripper")[-1][1]
        assert sent == (1, 2)
        assert [type(v) for v in sent] == [int, int]

    def test_null_is_never_sent(self, h):
        r = h.post("SetSpeed", [None], token=h.lease("config"))
        assert r.status_code == 422
        assert "null" in r.json()["detail"]

    def test_a_constant_position_must_be_sent_as_the_constant(self):
        """A constant-argument position must be sent as the constant."""
        consts = [c for c in COMMANDS.values()
                  if c.callable_directly and "const" in c.wire_types]
        assert consts, "no constant-argument command in the registry"
        cmd = consts[0]
        i = list(cmd.wire_types).index("const")
        args = [0] * cmd.arity
        args[i] = "definitely-not-the-constant"
        with pytest.raises(policy.Refusal) as e:
            policy.coerce_args(cmd, args)
        assert "constant" in e.value.detail


class TestAudit:
    def test_every_invocation_is_recorded(self, h):
        h.post("GetSoftwareVersion")
        h.post("ActGripper", [1, 1], confirm=True, token=h.lease("motion"))
        actions = [e["action"] for e in h.audit.recent(10)]
        assert "invoke.read" in actions
        assert "invoke.motion" in actions
        motion = next(e for e in h.audit.recent(10)
                      if e["action"] == "invoke.motion")
        assert motion["command"] == "ActGripper"
        assert motion["args"] == [1, 1]
        # The lease holder, not "anonymous": an audit line without an owner
        # answers "what" and not "who".
        assert motion["actor"] == "test-client"

    def test_a_refused_call_is_not_recorded_as_an_invocation(self, h):
        h.post("ShutDownRobotOS", confirm=True, token=h.lease("motion"))
        assert not [e for e in h.audit.recent(10)
                    if e["action"].startswith("invoke.")]


class TestCatalogue:
    def test_a_command_reports_how_its_class_was_derived(self, h):
        d = h.client.get(f"{BASE}/ActGripper").json()
        assert d["danger"] == "motion"
        assert d["confidence"] == "documented"
        assert any("夹爪" in b for b in d["basis"])
        assert d["requires_lock"] == "motion"
        assert d["requires_confirm"] is True

    def test_the_wire_order_and_types_are_published(self, h):
        d = h.client.get(f"{BASE}/StartJOG").json()
        assert d["wire_args"] == ["ref", "nb", "dir", "vel", "acc", "max_dis"]
        assert d["wire_types"] == ["int", "int", "int", "float", "float", "float"]

    def test_unverified_is_reported_not_refused(self, h):
        d = h.client.get(f"{BASE}/GetSlaveHardVersion").json()
        assert d["verified"] is False
        assert any("never exercised" in w for w in d["warnings"])

    def test_filtering_by_class_and_confidence(self, h):
        d = h.client.get(f"{BASE}?danger=motion&limit=1000").json()
        assert d["matched"] > 200
        assert all(c["danger"] == "motion" for c in d["commands"])
        d = h.client.get(f"{BASE}?confidence=none&limit=1000").json()
        assert all(c["danger"] == "unknown" for c in d["commands"])

    def test_the_policy_is_published(self, h):
        d = h.client.get(f"{BASE}/policy").json()
        classes = {row["class"] for row in d["matrix"]}
        assert classes == {"refused", "motion", "unknown", "config", "stop",
                           "read"}
        assert "exact, never minimum" in d["arity"]

    def test_an_unknown_command_is_404(self, h):
        assert h.client.get(f"{BASE}/NoSuchThing").status_code == 404
        assert h.post("NoSuchThing").status_code == 404


class TestCommandsThatWeakenAGuard:
    """Config that weakens a protection requires confirmation, unlike ordinary
    config."""

    NAMES = ("SetRobotStopOnComDisc", "SetLimitPositive", "SetLimitNegative",
             "SetAnticollision", "SetCollisionStrategy",
             "SetCollisionDetectionMethod")

    @pytest.mark.parametrize("name", NAMES)
    def test_the_hazard_is_recorded(self, name):
        assert "weakens-a-safety-guard" in COMMANDS[name].hazards

    @pytest.mark.parametrize("name", NAMES)
    def test_confirmation_is_required_despite_being_config_class(self, name):
        from fws.invoke import requirements
        cmd = COMMANDS[name]
        assert cmd.danger == "config", "premise: these classify as config"
        domain, confirm = requirements(cmd)
        assert domain == "config"
        assert confirm is True, (
            f"{name} switches a protection off and needs no confirmation")

    def test_ordinary_config_still_needs_no_confirmation(self):
        """Ordinary config still needs no confirmation."""
        from fws.invoke import requirements
        assert requirements(COMMANDS["SetSpeed"]) == ("config", False)

    def test_the_watchdog_switch_is_covered(self):
        """The comms-loss watchdog switch requires confirmation."""
        from fws.invoke import requirements
        assert requirements(COMMANDS["SetRobotStopOnComDisc"])[1] is True


class TestTheStopClassOnlyContainsStops:
    """The ungated stop class must contain only commands that halt or suspend."""

    def test_reset_is_not_reachable_ungated(self):
        from fws.invoke import Refusal, check_callable, lookup
        with pytest.raises(Refusal) as e:
            check_callable(lookup("ResetAllError"))
        assert e.value.status == 409
        assert "/api/v1/errors/reset" in e.value.detail

    def test_every_remaining_ungated_command_actually_stops_something(self):
        """Every ungated command actually halts or suspends something."""
        from fws.invoke import TYPED_ROUTE_OWNED, requirements
        ungated = sorted(
            n for n, c in COMMANDS.items()
            if c.danger == "stop" and c.callable_directly
            and n not in TYPED_ROUTE_OWNED and requirements(c) == (None, False))
        assert set(ungated) == {
            # halts
            "ImmStopJOG", "StopJOG", "StopMotion", "ProgramStop",
            "TractorStop",
            # suspends -- resuming needs its own call, which is gated
            "ProgramPause",
            # ends a force mode; the arm stops complying, it does not move
            "FT_ComplianceStop",
        }, (f"the ungated set changed to {ungated}; every member must halt or "
            f"suspend something, never permit motion. PauseMotion is absent "
            f"because it is `local` kind and so not directly callable.")

    def test_the_real_stops_stay_ungated(self):
        """The real stops stay ungated."""
        from fws.invoke import requirements
        for n in ("ImmStopJOG", "StopMotion", "ProgramStop"):
            assert requirements(COMMANDS[n]) == (None, False), n
