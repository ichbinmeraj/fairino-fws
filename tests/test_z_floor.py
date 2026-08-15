"""limits.z_floor_mm: a configured floor that actually protects something.

The floor was configurable, documented ("Refuse any commanded pose below this
TCP height"), and enforced NOWHERE. `pathcheck.validate` grew a `z_floor`
parameter and a unit test for it, but no call site ever passed one, and the
Cartesian jog never checked height at all -- so an operator who set
`z_floor_mm` to protect a table got a promise and no protection.

That is the precise failure this project condemns everywhere else, so these
tests pin the floor at both routes that can command a pose.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod


def _client(fake, **over):
    app_mod.create_app(config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
        **over,
    }))
    return TestClient(app_mod.app)


def _lease(c):
    app_mod.control._leases.clear()      # leases persist across create_app
    token = c.post("/api/v1/control", json={"client_id": "t"}).json()["token"]
    h = {"X-FWS-Control-Token": token}
    c.post("/api/v1/robot/enable", json={"enable": True, "confirm": True},
           headers=h)
    return h


class TestTheJogPreflightHonoursTheFloor:
    def test_a_jog_that_would_break_the_floor_is_refused(self, fake):
        """The floor is set ABOVE the arm's current height, so any solved
        target is below it and the move must be refused."""
        with _client(fake, **{"limits.z_floor_mm": 100000.0}) as c:
            h = _lease(c)
            r = c.post("/api/v1/motion/jog/linear", headers=h,
                       json={"axis": 3, "direction": 0, "step": 5, "vel": 10})
            assert r.status_code == 409
            assert "floor" in r.json()["detail"]

    def test_the_same_jog_passes_with_no_floor_configured(self, fake):
        """Unset means no floor -- the default, and the behaviour every
        existing deployment already has."""
        with _client(fake) as c:
            h = _lease(c)
            r = c.post("/api/v1/motion/jog/linear", headers=h,
                       json={"axis": 3, "direction": 0, "step": 5, "vel": 10})
            assert r.status_code != 409, r.text

    def test_a_floor_far_below_does_not_block(self, fake):
        with _client(fake, **{"limits.z_floor_mm": -100000.0}) as c:
            h = _lease(c)
            r = c.post("/api/v1/motion/jog/linear", headers=h,
                       json={"axis": 3, "direction": 0, "step": 5, "vel": 10})
            assert r.status_code != 409, r.text

    def test_the_check_is_solved_forward_not_added_to_current_z(self, fake):
        """A tool-frame Z step is not a base-frame Z step. The floor is
        checked against the forward-solved target so it means the same thing
        in both frames -- a floor that only works in one is worse than none."""
        import inspect
        src = inspect.getsource(app_mod.jog_linear)
        assert "forward_kin(target_joints)" in src

    def test_an_uncheckable_floor_refuses_rather_than_passes(self, fake):
        """A guard that could not run says so. If the predicted height cannot
        be computed, a configured floor must refuse -- jogging past an
        unverifiable floor is exactly what the floor exists to prevent."""
        from fws.driver import RobotError
        with _client(fake, **{"limits.z_floor_mm": 10.0}) as c:
            h = _lease(c)

            def boom(_joints):
                raise RobotError("controller said no")

            app_mod.driver.forward_kin = boom
            r = c.post("/api/v1/motion/jog/linear", headers=h,
                       json={"axis": 3, "direction": 0, "step": 5, "vel": 10})
            assert r.status_code == 409
            assert "could not be checked" in r.json()["detail"]


class TestTheProgramValidatorHonoursTheFloor:
    def test_the_configured_floor_reaches_pathcheck(self, fake, monkeypatch):
        """The route built its own pathcheck call and never passed z_floor,
        so the parameter -- and its unit test -- protected nothing in
        production. Pin that the wiring exists."""
        import fws.programs_api as pa
        seen = {}
        real = pa.validate_path

        def spy(src, **kw):
            seen.update(kw)
            return real(src, **kw)

        monkeypatch.setattr(pa, "validate_path", spy)
        with _client(fake, **{"limits.z_floor_mm": 42.5}) as c:
            fake.files["floor.lua"] = b"MoveJ(...)\n"
            c.post("/api/v1/programs/floor.lua/validate")
        assert seen.get("z_floor") == 42.5, (
            "the configured floor must reach pathcheck.validate")


@pytest.mark.parametrize("route,payload", [
    ("/api/v1/motion/jog/linear",
     {"axis": 3, "direction": 0, "step": 5, "vel": 10}),
])
def test_full_access_lifts_the_floor_like_every_other_guard(
        fake, route, payload):
    """The floor is a software guard, so --full-access takes it off with the
    rest of them. Consistency matters more than the individual guard here."""
    from fws import access
    try:
        with _client(fake, **{"limits.z_floor_mm": 100000.0,
                              "features.full_access": True}) as c:
            r = c.post(route, json=payload)
            assert r.status_code != 409, r.text
    finally:
        access.set_full_access(False)


class TestTheAuditTrailCanBeDurable:
    """`AuditLog` accepted a `path` and nothing ever passed one, so every
    trail died with the process -- and the sink's own comment claimed its
    failures were "visible in health" while nothing counted them. Both halves
    are pinned here."""

    def test_events_are_written_to_the_configured_file(self, fake, tmp_path):
        import json
        trail = tmp_path / "audit.jsonl"
        with _client(fake, **{"audit.file": str(trail)}) as c:
            h = _lease(c)
            c.post("/api/v1/motion/stop", headers=h)
        assert trail.exists(), "the configured sink must be written"
        lines = [json.loads(ln) for ln in
                 trail.read_text().splitlines() if ln.strip()]
        assert lines, "at least one event"
        assert all("action" in e and "ts" in e for e in lines)

    def test_a_relative_path_lands_in_the_data_dir(self, fake, tmp_path):
        with _client(fake, **{"audit.file": "trail.jsonl",
                              "server.data_dir": str(tmp_path)}) as c:
            _lease(c)
            c.post("/api/v1/motion/stop")
        assert (tmp_path / "trail.jsonl").exists()

    def test_health_admits_an_in_memory_only_trail(self, fake):
        with _client(fake) as c:
            au = c.get("/api/v1/system/health").json()["audit"]
        assert au["durable"] is False
        assert au["file"] is None

    def test_health_reports_a_durable_trail(self, fake, tmp_path):
        with _client(fake, **{"audit.file": str(tmp_path / "a.jsonl")}) as c:
            c.post("/api/v1/motion/stop")
            au = c.get("/api/v1/system/health").json()["audit"]
        assert au["durable"] is True

    def test_a_failing_sink_warns_instead_of_going_quiet(self, fake, tmp_path):
        """A sink that starts failing keeps the API answering and the deque
        filling, so health is the only place it can surface."""
        with _client(fake, **{"audit.file": str(tmp_path / "a.jsonl")}) as c:
            app_mod.audit.path = tmp_path / "no-such-dir" / "a.jsonl"
            c.post("/api/v1/motion/stop")
            d = c.get("/api/v1/system/health").json()
        assert d["audit"]["sink_errors"] >= 1
        assert d["audit"]["durable"] is False
        assert any("audit file sink has failed" in w for w in d["warnings"])

    def test_a_failing_sink_never_breaks_the_command(self, fake, tmp_path):
        """Stop must work whether or not its record can be written."""
        with _client(fake, **{"audit.file": str(tmp_path / "a.jsonl")}) as c:
            app_mod.audit.path = tmp_path / "nope" / "a.jsonl"
            assert c.post("/api/v1/motion/stop").status_code == 200


class TestTheTypedMotionRoutesAreAudited:
    """The audit trail promises "who commanded what, when" -- and recorded
    nothing for jog, linear jog, enable, error-reset or stop, because only the
    router-based surfaces were given the recorder. Every command that can move
    the arm from the console went unrecorded."""

    @pytest.mark.parametrize("action,route,payload", [
        ("motion.jog", "/api/v1/motion/jog",
         {"joint": 1, "direction": 1, "step": 1, "vel": 5}),
        ("motion.jog_linear", "/api/v1/motion/jog/linear",
         {"axis": 1, "direction": 1, "step": 1, "vel": 5}),
        ("robot.enable", "/api/v1/robot/enable",
         {"enable": True, "confirm": True}),
        ("errors.reset", "/api/v1/errors/reset", None),
        ("motion.stop", "/api/v1/motion/stop", None),
    ])
    def test_the_command_leaves_a_line(self, fake, action, route, payload):
        with _client(fake) as c:
            h = _lease(c)
            c.post(route, headers=h, **({"json": payload} if payload else {}))
            actions = [e["action"] for e in
                       c.get("/api/v1/events").json()["events"]]
        assert action in actions, f"{route} must be audited"

    def test_stop_is_recorded_after_the_stop_not_before(self, fake):
        """Every other command audits BEFORE transmission so a wedge still
        leaves a record. Stop is the deliberate exception: nothing may sit
        between the request and the stop."""
        import inspect
        src = inspect.getsource(app_mod.stop)
        assert src.index("_stop_all()") < src.index('audit.record("motion.stop"')


class TestTheWatchdogStopIsRecorded:
    """The gateway stopping the arm by itself -- because a lease holder went
    away mid-move -- is the single most important thing the audit trail can
    hold. It existed only as a print(), so an incident review found the arm
    stopped and nothing saying who or why."""

    def test_a_lapsed_motion_lease_leaves_an_audit_line(self, fake):
        with _client(fake) as c:
            app_mod.control._leases.clear()
            r = c.post("/api/v1/control",
                       json={"client_id": "vanisher", "domains": ["motion"],
                             "ttl_s": 5})
            lease = app_mod.control.held_by("motion")
            assert lease is not None and r.status_code == 201
            # Fire the watchdog path directly: waiting out a real TTL would
            # add five seconds to the suite for the same assertion.
            app_mod._on_lease_lapse("expired", lease)
            events = c.get("/api/v1/events").json()["events"]
            hit = [e for e in events if e["action"] == "watchdog.stop"]
            assert hit, "the watchdog stop must be audited"
            assert hit[0]["actor"] == "vanisher"
            assert hit[0]["reason"] == "expired"
            assert "results" in hit[0], "what the stop actually did"

    def test_a_lapsed_lease_without_motion_stops_nothing(self, fake):
        """Only a motion holder going away is a reason to stop the arm."""
        with _client(fake) as c:
            app_mod.control._leases.clear()
            # The audit log is a process-wide singleton, so an earlier test's
            # watchdog line is still in it. Compare against events recorded
            # from HERE, not the whole history.
            before = c.get("/api/v1/events").json()["events"]
            mark = max((e["seq"] for e in before), default=0)
            c.post("/api/v1/control",
                   json={"client_id": "cfg", "domains": ["config"], "ttl_s": 5})
            app_mod._on_lease_lapse("expired",
                                    app_mod.control.held_by("config"))
            new = [e for e in c.get("/api/v1/events").json()["events"]
                   if e["seq"] > mark]
            assert "watchdog.stop" not in [e["action"] for e in new]
