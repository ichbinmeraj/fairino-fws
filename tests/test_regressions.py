"""Regression tests for specific fixed bugs."""
from __future__ import annotations

import struct
import time

import pytest

from fws.driver import RobotError
from fws.protocol.commands import COMMANDS
from fws.telemetry import JOINTS_OFF, TCP_OFF


class TestStartJogWireOrder:
    """StartJOG uses wire argument order, not the SDK signature order."""

    def test_driver_sends_wire_order_not_signature_order(self, driver, fake):
        driver.enable(True)
        driver.jog(joint=6, positive=True, max_dis=10.0, vel=5.0)

        _, args = next(c for c in reversed(fake.calls) if c[0] == "StartJOG")
        ref, nb, direction, vel, _acc, max_dis = args
        assert (ref, nb, direction) == (0, 6, 1)
        assert vel == 5.0, "velocity landed in the wrong slot"
        assert max_dis == 10.0, "distance landed in the wrong slot"

    def test_registry_records_the_wire_order(self):
        assert COMMANDS["StartJOG"].wire_args == (
            "ref", "nb", "dir", "vel", "acc", "max_dis")


class TestMoveLIsNotDirectlyCallable:
    """MoveL is a composite, not directly callable."""

    def test_registry_classifies_movel_as_composite(self):
        c = COMMANDS["MoveL"]
        assert c.kind == "composite"
        assert not c.callable_directly
        assert "GetInverseKin" in c.wire_sequence

    def test_fake_rejects_a_wrong_length_array(self, driver):
        with pytest.raises(RobotError, match="33-element"):
            driver._call("MoveL", [0.0] * 30)

    def test_driver_builds_exactly_33_elements(self, driver, fake):
        driver.move_l(pose=[1.0] * 6, joints=[2.0] * 6)
        _, args = next(c for c in reversed(fake.calls) if c[0] == "MoveL")
        assert len(args[0]) == 33


class TestBlindWhileFaulted:
    """XML-RPC getters fail with error 14 when faulted; the telemetry stream
    survives."""

    def test_getter_fails_but_stream_survives(self, driver, telemetry, fake):
        assert driver.joints()
        fake.latch_fault(1, 22)

        with pytest.raises(RobotError, match="error 14"):
            driver.joints()

        time.sleep(0.3)
        snap = telemetry.snapshot()
        assert snap["connected"] is True
        assert snap["joints"] is not None

    def test_reset_restores_the_getters(self, driver, fake):
        fake.latch_fault(1, 22)
        driver.reset_errors()
        assert driver.error_code() == (0, 0)
        assert driver.joints()


class TestSoftLimitLatchesFault:
    """Jogging into a soft limit latches main=1 sub=22 and refuses motion."""

    def test_jog_into_limit_faults_and_then_refuses(self, driver, fake):
        driver.enable(True)
        fake.state.joints[4] = 174.0          # J5 limit is 175
        driver.jog(joint=5, positive=True, max_dis=10.0, vel=30.0)

        deadline = time.time() + 5
        while time.time() < deadline and not fake.state.faulted:
            time.sleep(0.05)

        assert fake.state.faulted, "expected a soft-limit fault"
        assert (fake.state.error_main, fake.state.error_sub) == (1, 22)
        assert fake.state.joints[4] <= 175.0, "arm passed its own soft limit"


class TestJogStartLatency:
    """StartJOG returns before the arm begins moving."""

    def test_motion_has_not_begun_when_the_call_returns(self, driver, fake):
        driver.enable(True)
        before = list(fake.state.joints)
        driver.jog(joint=1, positive=True, max_dis=5.0, vel=10.0)
        assert fake.state.joints == before, "fake moved instantly; unrealistic"

    def test_motion_does_eventually_begin(self, driver, fake):
        driver.enable(True)
        before = list(fake.state.joints)
        driver.jog(joint=1, positive=True, max_dis=5.0, vel=30.0)
        deadline = time.time() + 5
        while time.time() < deadline and fake.state.joints == before:
            time.sleep(0.02)
        assert fake.state.joints != before


class TestMaxDisIsABound:
    """The controller self-terminates a jog at max_dis."""

    def test_jog_self_terminates_at_max_dis(self, driver, fake):
        driver.enable(True)
        start = fake.state.joints[0]
        driver.jog(joint=1, positive=True, max_dis=3.0, vel=30.0)

        deadline = time.time() + 10
        while time.time() < deadline and fake.state.moving is False:
            time.sleep(0.02)
        while time.time() < deadline and fake.state.moving:
            time.sleep(0.02)

        travelled = fake.state.joints[0] - start
        assert travelled == pytest.approx(3.0, abs=0.2), (
            f"travelled {travelled}, expected the max_dis bound of 3.0")


class TestTelemetryFrame:
    """The 8083 telemetry frame layout."""

    def test_frame_is_433_bytes_with_a_valid_checksum(self, fake):
        frame = fake.build_frame()
        assert len(frame) == 433
        assert frame[:2] == b"\x5a\x5a"
        declared = struct.unpack_from("<H", frame, 3)[0]
        assert declared == 426
        stored = struct.unpack_from("<H", frame, 5 + declared)[0]
        assert stored == sum(frame[:5 + declared]) & 0xFFFF

    def test_offsets_match_what_the_controller_reports(self, fake, driver):
        frame = fake.build_frame()
        assert list(struct.unpack_from("<6d", frame, JOINTS_OFF)) == \
            pytest.approx(driver.joints())
        assert list(struct.unpack_from("<6d", frame, TCP_OFF)) == \
            pytest.approx(driver.tcp_pose())

    def test_corrupt_frame_is_dropped_not_zero_filled(self, fake, telemetry):
        """A corrupt frame is dropped, not zero-filled."""
        good = fake.build_frame()
        bad = bytearray(good)
        bad[JOINTS_OFF] ^= 0xFF                     # break the payload
        before = telemetry.snapshot()
        telemetry._parse(bytes(bad))
        after = telemetry.snapshot()
        assert after["bad_checksum"] == before.get("bad_checksum", 0) + 1
        assert after.get("joints") == before.get("joints"), (
            "a corrupt frame must not replace good data")


class TestIntrospectionIsBlocked:
    """Introspection methods are blocked."""

    @pytest.mark.parametrize("method", [
        "system.listMethods", "system.methodHelp", "system.methodSignature",
    ])
    def test_driver_refuses(self, driver, method):
        with pytest.raises(RobotError, match="introspection is blocked"):
            driver._call(method)

    def test_refusal_happens_before_any_network_call(self, driver, fake):
        with pytest.raises(RobotError):
            driver._call("system.listMethods")
        assert not any(c[0].startswith("system.") for c in fake.calls)


class TestDangerousCommandsAreRefused:
    def test_firmware_and_shutdown_paths_are_refused(self):
        for name in ("JointAllParamUpgrade", "KernelUpgrade", "SoftwareUpgrade",
                     "ShutDownRobotOS", "GetLuaList"):
            assert COMMANDS[name].danger == "refused", name
            assert not COMMANDS[name].callable_directly, name

    def test_every_upgrade_WRITE_is_refused(self):
        """Anything that writes firmware is refused; reading status is not."""
        for name, c in COMMANDS.items():
            if "Upgrade" not in name:
                continue
            if name.startswith(("Get", "Is", "Query")):
                assert c.danger != "refused", f"{name} only reads status"
            else:
                assert c.danger == "refused", f"{name} writes firmware"

    def test_stop_commands_are_identified(self):
        """Stops must never be gated behind a control lock."""
        for name in ("StopMotion", "ImmStopJOG", "ProgramStop"):
            assert COMMANDS[name].danger == "stop"


class TestUnreachableTargets:
    def test_inverse_kinematics_reports_112_not_a_wrong_answer(self, driver):
        with pytest.raises(RobotError, match="112"):
            driver.inverse_kin([5000.0, 0.0, 500.0, 0.0, 0.0, 0.0])

    def test_forward_and_inverse_round_trip(self, driver):
        joints = driver.joints()
        pose = driver.forward_kin(joints)
        assert driver.inverse_kin(pose) == pytest.approx(joints, abs=0.01)


class TestRefusalIsEnforcedInTheDriver:
    """Refusal of dangerous commands is enforced in the driver itself."""

    def test_the_command_that_caused_the_outage_is_refused(self, driver):
        from fws.driver import RobotError
        with pytest.raises(RobotError, match="refused by FWS"):
            driver._call("ShutDownRobotOS")

    def test_every_firmware_write_is_refused(self, driver):
        from fws.driver import RobotError
        for method in ("SetCtrlFirmwareUpgrade", "SetEndFirmwareUpgrade",
                       "SetJointFirmwareUpgrade", "SoftwareUpgrade",
                       "SlaveFileWrite", "SetSysServoBootMode"):
            with pytest.raises(RobotError, match="refused by FWS"):
                driver._call(method)

    def test_the_rpc_wedger_is_refused(self, driver):
        from fws.driver import RobotError
        with pytest.raises(RobotError, match="refused by FWS"):
            driver._call("GetLuaList")

    def test_refusal_happens_before_anything_reaches_the_wire(self, driver,
                                                              fake):
        """Not merely an error afterwards -- the command must never be sent."""
        from fws.driver import RobotError
        with pytest.raises(RobotError):
            driver._call("ShutDownRobotOS")
        assert "ShutDownRobotOS" not in [c[0] for c in fake.calls]

    def test_the_escape_hatch_is_keyword_only(self, driver):
        """So it cannot be passed by accident as a positional argument."""
        import inspect
        sig = inspect.signature(driver._call)
        p = sig.parameters["allow_refused"]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY
        assert p.default is False

    def test_the_escape_hatch_works_when_taken_deliberately(self, driver):
        """allow_refused lets a refused command through to the controller."""
        from fws.driver import RobotError
        with pytest.raises(RobotError) as e:
            driver._call("GetLuaList", allow_refused=True)
        assert "refused by FWS" not in str(e.value), \
            "the escape hatch did not let the call through"
        assert "-506" in str(e.value) or "not defined" in str(e.value)

    def test_introspection_is_still_blocked(self, driver):
        """Introspection is still blocked in the driver."""
        from fws.driver import RobotError
        for method in ("system.listMethods", "system.methodHelp"):
            with pytest.raises(RobotError, match="introspection is blocked"):
                driver._call(method)

    def test_fws_itself_sends_no_refused_command_without_the_hatch(self):
        """No FWS module sends a refused command without allow_refused."""
        import pathlib
        import re

        from fws.driver import REFUSED

        hits = []
        for f in pathlib.Path("fws").rglob("*.py"):
            if f.name == "driver.py":
                continue
            for i, line in enumerate(f.read_text().splitlines(), 1):
                m = re.search(r'_call\(\s*"([A-Za-z_]+)"', line)
                if m and m.group(1) in REFUSED and "allow_refused" not in line:
                    hits.append(f"{f}:{i} {m.group(1)}")
        assert not hits, f"refused command sent without the escape hatch: {hits}"


class TestInternalClientPortsReadBackwards:
    """Ports 8060-8062 refuse a connection when healthy; the sense is inverted."""

    def test_recovery_readiness_ignores_the_internal_client_ports(self):
        from fws.system_api import BOOT_LAYERS, CLAIMED_WHEN_HEALTHY
        boot_ports = {p for _, ports, _ in BOOT_LAYERS for p in ports}
        inverted = {p for p, _ in CLAIMED_WHEN_HEALTHY}
        assert not boot_ports & inverted, (
            "an inverted-sense port is being used to decide readiness; that "
            "reports a working controller as still booting")

    def test_the_lua_verdict_channel_is_tracked(self):
        """8060 is tracked as claimed-when-healthy."""
        from fws.system_api import CLAIMED_WHEN_HEALTHY
        assert 8060 in {p for p, _ in CLAIMED_WHEN_HEALTHY}

    def test_the_telemetry_port_is_excluded(self):
        """8083 is single-client but does not refuse a second connection."""
        from fws.system_api import CLAIMED_WHEN_HEALTHY
        assert 8083 not in {p for p, _ in CLAIMED_WHEN_HEALTHY}


class TestRefusalFloorIsDerivedNotRemembered:
    """driver.REFUSED must cover everything the registry refuses; derived, not
    hand-maintained."""

    def test_driver_refuses_everything_the_registry_refuses(self):
        from fws.driver import REFUSED
        from fws.protocol.commands import COMMANDS
        registry = {n for n, c in COMMANDS.items() if c.danger == "refused"}
        missing = sorted(registry - REFUSED)
        assert not missing, (
            f"refused by the registry but reachable through the driver: "
            f"{missing}. Add them to fws/driver.py REFUSED.")

    def test_the_driver_may_refuse_more_than_the_registry(self):
        """The driver floor may be stricter than the registry."""
        from fws.driver import REFUSED
        assert {"GetLuaListPrepare", "GetLuaNameWithID"} <= REFUSED


class TestGenericInvokerCannotUndercutABoundedRoute:
    """The generic invoker must not offer an unbounded route a typed route bounds."""

    def test_startjog_is_refused_by_the_generic_invoker(self):
        from fws.invoke import Refusal, check_callable, lookup
        with pytest.raises(Refusal) as e:
            check_callable(lookup("StartJOG"))
        assert e.value.status == 409
        assert "/api/v1/motion/jog" in e.value.detail

    def test_every_owned_command_names_a_route_that_exists(self):
        """A refusal that points nowhere is worse than no refusal."""
        from fws.app import app
        from fws.invoke import TYPED_ROUTE_OWNED
        served = set(app.openapi()["paths"])
        for cmd, pointer in TYPED_ROUTE_OWNED.items():
            targets = [w.strip() for w in pointer.split() if w.startswith("/api/")]
            assert targets, f"{cmd} names no route"
            for t in targets:
                assert t in served, f"{cmd} points at {t}, which is not served"

    def test_the_scaled_and_framed_commands_are_owned(self):
        """Scaled and framed commands are owned by typed routes."""
        from fws.invoke import TYPED_ROUTE_OWNED
        for n in ("SetAO", "SetLoadWeight", "FileUpload", "FileDownload"):
            assert n in TYPED_ROUTE_OWNED

    def test_ordinary_commands_are_still_reachable(self):
        """Ordinary commands remain directly reachable."""
        from fws.invoke import TYPED_ROUTE_OWNED
        from fws.protocol.commands import COMMANDS
        callable_now = [n for n, c in COMMANDS.items()
                        if c.callable_directly and c.danger != "refused"
                        and n not in TYPED_ROUTE_OWNED]
        assert len(callable_now) > 400, len(callable_now)


class TestBothNewRoutersAreMounted:
    """The invoke and files routers are mounted."""

    def test_invoke_and_files_are_served(self):
        from fws.app import app
        paths = set(app.openapi()["paths"])
        assert "/api/v1/invoke/{name}" in paths
        assert any(p.startswith("/api/v1/files") for p in paths)

    def test_no_route_points_at_a_route_that_does_not_exist(self):
        """No route points at a path that is not served."""
        import pathlib
        import re

        from fws.app import app
        served = set(app.openapi()["paths"])
        ref = re.compile(r"/api/v1/[a-z_]+(?:/\{[a-z_]+\})?")
        for f in pathlib.Path("fws").rglob("*_api.py"):
            for m in ref.finditer(f.read_text()):
                path = m.group(0)
                if path.count("/") <= 3:
                    continue                      # a prefix, not a route
                assert path in served or any(
                    p.startswith(path) for p in served), \
                    f"{f.name} points at {path}, which is not served"


class TestLoadedProgramFieldTruncates:
    """The frame's 20-byte program-path field silently truncates."""

    def test_the_key_is_named_so_nobody_mistakes_it(self, telemetry):
        snap = telemetry.snapshot()
        assert "loaded_program_truncated" in snap
        assert "loaded_program" not in snap, (
            "a key called loaded_program would be read as authoritative")

    def test_the_field_is_bounded_to_its_width(self):
        from fws.telemetry import PROGRAM_LEN
        assert PROGRAM_LEN == 20

    def test_a_long_path_is_cut_not_corrupted(self):
        """Truncation must produce a short string, never a decode error."""
        import struct

        from fws.telemetry import PROGRAM_LEN, PROGRAM_OFF, Telemetry
        frame = bytearray(433)
        struct.pack_into("<H", frame, 3, 426)
        long_path = b"/fruser/a_very_long_program_name.lua"
        frame[PROGRAM_OFF:PROGRAM_OFF + PROGRAM_LEN] = long_path[:PROGRAM_LEN]
        struct.pack_into("<H", frame, 431, sum(frame[:431]) & 0xFFFF)

        t = Telemetry()
        t._parse(bytes(frame))
        got = t.snapshot()["loaded_program_truncated"]
        assert got == long_path[:PROGRAM_LEN].decode()
        assert len(got) == PROGRAM_LEN


class TestLiveStreamDoesNotDriftBehindREST:
    """The websocket stream carries every field parsed from the frame."""

    def test_the_stream_carries_every_parsed_field(self, telemetry):
        import inspect

        from fws import app as app_mod

        snap = telemetry.snapshot()
        parsed = {k for k in snap if k != "ts"}
        src = inspect.getsource(app_mod.ws_state)
        assert "**{k: v for k, v in t.items()" in src, (
            "the websocket payload must be derived from the snapshot, not a "
            "hand-written key list -- a list is what drifted")
        # And the parser must actually be producing the expected fields.
        assert {"joints", "tcp", "ft", "joint_torque"} <= parsed

    def test_the_timestamp_is_the_only_field_held_back(self):
        """`ts` is the only field withheld from the stream payload."""
        import inspect

        from fws import app as app_mod
        src = inspect.getsource(app_mod.ws_state)
        assert 'k != "ts"' in src


class TestTheUnreachablePaintPath:
    """pathcheck rejects a pose outside the arm's reach envelope."""

    # x=750, z=550: sqrt(750^2 + 550^2) = 929.6 mm, outside the reach envelope.
    THE_POSE = (750.0, 0.0, 550.0, 180.0, 0.0, 0.0)

    def test_the_pose_that_shipped_is_rejected(self):
        from fws import pathcheck
        from fws.testing.kinematics import inverse

        src = "MoveL(0,0,0,0,0,0,{},{},{},{},{},{},0,0,0)".format(*self.THE_POSE)
        r = pathcheck.validate(
            src, inverse_kin=inverse,
            joint_limits=lambda: [(-175.0, 175.0)] * 6)
        assert r["failed"] == 1
        assert r["safe_to_run"] is False

    def test_the_simulator_refuses_it_too(self):
        """The simulator's inverse kinematics also rejects the pose."""
        from fws.testing.kinematics import Unreachable, inverse

        with pytest.raises(Unreachable):
            inverse(list(self.THE_POSE))

    def test_what_cannot_be_checked_is_never_silently_passed(self):
        """An unresolvable target is never reported as a clean run."""
        from fws import pathcheck

        src = "Lin(P1, 0, 100, 100, 0)\nPTP(P2, 100, 0)"
        r = pathcheck.validate(
            src, inverse_kin=lambda p: [0.0] * 6,
            joint_limits=lambda: [(-175.0, 175.0)] * 6)
        assert r["failed"] == 0        # nothing failed...
        assert r["safe_to_run"] is False   # ...and it is still not safe
        assert r["unchecked"] == 2
        assert "NOT a clean bill of health" in r["verdict"]

    # Blocking the run (ProgramRun never reaching the wire) is asserted in
    # test_programs.py::TestRunIsGatedOnValidation, not duplicated here.


class TestSilentSuccessIsNeverReported:
    """A check never reports success on a question it never asked."""

    def test_a_failed_fault_query_does_not_read_as_no_fault(self, fake):
        """A failed fault query reports faulted: null, not false."""
        from fastapi.testclient import TestClient

        from fws import app as app_mod
        from fws import config as config_mod
        from fws.driver import RobotError
        app_mod.create_app(config_mod.load(**{
            "robot.ip": fake.host, "robot.rpc_port": fake.rpc_port,
            "robot.telemetry_port": fake.stream_port,
            "robot.upload_port": fake.upload_port,
            "robot.download_port": fake.download_port}))
        with TestClient(app_mod.app) as c:
            drv = app_mod.driver
            orig = drv.error_code
            drv.error_code = lambda: (_ for _ in ()).throw(RobotError("down"))
            try:
                f = c.get("/api/v1/robot/state").json()["fault"]
            finally:
                drv.error_code = orig
        assert f["rpc_responding"] is False
        assert f["faulted"] is None, (
            "null means UNKNOWN; False would be a lie")
        assert f["main"] is None and f["sub"] is None
        assert "does NOT know" in f["note"]

    def test_a_runner_that_fails_to_abort_is_reported_by_stop(self):
        """A runner that fails to abort is counted and reported by stop."""
        from fws.runners import AbortRegistry

        class Broken:
            def request_abort(self):
                raise RuntimeError("wedged")

        class Fine:
            def __init__(self):
                self.hit = False

            def request_abort(self):
                self.hit = True

        reg = AbortRegistry()
        good, bad = Fine(), Broken()
        reg.register(good)
        reg.register(bad)
        reached = reg.request_abort_all()
        assert good.hit, "one bad runner must not stop the others being told"
        assert reached == 1
        assert reg.failed_last_call == 1, "the failure must be COUNTED"

    def test_stop_names_the_runners_it_could_not_abort(self, fake):
        from fastapi.testclient import TestClient

        from fws import app as app_mod
        from fws import config as config_mod
        app_mod.create_app(config_mod.load(**{
            "robot.ip": fake.host, "robot.rpc_port": fake.rpc_port,
            "robot.telemetry_port": fake.stream_port,
            "robot.upload_port": fake.upload_port,
            "robot.download_port": fake.download_port}))

        class Broken:
            def request_abort(self):
                raise RuntimeError("wedged")

        # Held in a local: the registry stores WEAK references, so an
        # unreferenced runner is collected before stop ever sees it.
        broken = Broken()
        with TestClient(app_mod.app) as c:
            app_mod.abortables.register(broken)
            try:
                d = c.post("/api/v1/motion/stop").json()
            finally:
                app_mod.abortables.clear()
        assert "runners" in d["results"], "the runner outcome must be reported"
        assert "FAILED to abort" in d["results"]["runners"]
        assert "physical stop" in d["results"]["runners"]

    def test_stop_still_returns_200_when_a_runner_fails(self, fake):
        """Stop returns 200 even when a runner fails to abort."""
        from fastapi.testclient import TestClient

        from fws import app as app_mod
        from fws import config as config_mod
        app_mod.create_app(config_mod.load(**{
            "robot.ip": fake.host, "robot.rpc_port": fake.rpc_port,
            "robot.telemetry_port": fake.stream_port,
            "robot.upload_port": fake.upload_port,
            "robot.download_port": fake.download_port}))

        class Broken:
            def request_abort(self):
                raise RuntimeError("wedged")

        broken = Broken()
        with TestClient(app_mod.app) as c:
            app_mod.abortables.register(broken)
            try:
                assert c.post("/api/v1/motion/stop").status_code == 200
            finally:
                app_mod.abortables.clear()


class TestHealthSaysWhichChecksDidNotRun:
    """Health names which checks did not run rather than swallowing failures."""

    def _client(self, fake):
        from fastapi.testclient import TestClient

        from fws import app as app_mod
        from fws import config as config_mod
        app_mod.create_app(config_mod.load(**{
            "robot.ip": fake.host, "robot.rpc_port": fake.rpc_port,
            "robot.telemetry_port": fake.stream_port,
            "robot.upload_port": fake.upload_port,
            "robot.download_port": fake.download_port}))
        return app_mod, TestClient(app_mod.app)

    def test_a_healthy_controller_reports_all_checks_ran(self, fake):
        _, c = self._client(fake)
        with c:
            d = c.get("/api/v1/system/health").json()
        assert d["all_checks_ran"] is True
        assert d["checks_not_run"] == []

    def test_a_failed_check_is_named_rather_than_swallowed(self, fake,
                                                           monkeypatch):
        app_mod, c = self._client(fake)
        from fws.driver import TransportError
        real = app_mod.driver._call

        def flaky(method, *a, **kw):
            if method == "GetForceSensorPayload":
                raise TransportError("GetForceSensorPayload: timed out")
            return real(method, *a, **kw)

        monkeypatch.setattr(app_mod.driver, "_call", flaky)
        with c:
            d = c.get("/api/v1/system/health").json()
        assert d["all_checks_ran"] is False
        assert d["checks_not_run"], "the skipped check must be reported"
        entry = d["checks_not_run"][0]
        assert "force payload" in entry["check"]
        assert "TransportError" in entry["why"]
        assert "not a clean result" in entry["means"]

    def test_health_reports_the_disconnect_watchdog(self, fake):
        """`Nothing has lapsed` and `nothing is watching` must differ."""
        _, c = self._client(fake)
        with c:
            d = c.get("/api/v1/system/health").json()
        assert "control_watchdog" in d
        assert set(d["control_watchdog"]) >= {"running", "healthy",
                                              "reap_errors"}

    def test_an_unhealthy_watchdog_becomes_a_warning(self, fake):
        app_mod, c = self._client(fake)
        with c:
            app_mod.control._lapse_errors = 1
            try:
                d = c.get("/api/v1/system/health").json()
            finally:
                app_mod.control._lapse_errors = 0
        assert any("watchdog is not healthy" in w for w in d["warnings"])
        assert any("may NOT trigger a stop" in w for w in d["warnings"])
