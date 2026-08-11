"""The stop path."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod


@pytest.fixture
def client(fake):
    app_mod.create_app(config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
    }))
    with TestClient(app_mod.app) as c:
        yield c


class TestStopIssuesBothCommands:
    def test_both_wire_commands_are_sent(self, client, fake):
        fake.calls.clear()
        client.post("/api/v1/motion/stop")
        sent = [c[0] for c in fake.calls]
        assert "ImmStopJOG" in sent, "jogs must be stopped"
        assert "StopMotion" in sent, (
            "program-space moves must be stopped; ImmStopJOG does not")

    def test_jog_stop_comes_first(self, client, fake):
        fake.calls.clear()
        client.post("/api/v1/motion/stop")
        sent = [c[0] for c in fake.calls]
        assert sent.index("ImmStopJOG") < sent.index("StopMotion")

    def test_every_stop_route_stops_program_space_moves(self, client, fake):
        """No route calling itself a stop may be blind to program-space motion."""
        # Enumerate from the OpenAPI schema, not app.routes: an included
        # router appears as one opaque entry with no .path, so a route sweep
        # misses router-mounted routes such as /api/v1/execution/stop.
        spec = client.get("/openapi.json").json()
        paths = sorted(p for p, ops in spec["paths"].items()
                       if p.endswith("/stop") and "post" in ops)
        assert len(paths) >= 2, f"expected several stop routes, found {paths}"
        for p in paths:
            fake.calls.clear()
            assert client.post(p).status_code == 200, p
            assert "StopMotion" in [c[0] for c in fake.calls], (
                f"{p} does not stop program-space moves")


class TestStopNeverFails:
    """Stop must return 200 even when the controller errors."""

    def test_returns_200_even_when_the_controller_errors(self, client, fake):
        fake.latch_fault(1, 22)
        r = client.post("/api/v1/motion/stop")
        assert r.status_code == 200

    def test_one_failing_command_does_not_suppress_the_other(
            self, client, fake, monkeypatch):
        from fws.driver import RobotError

        def boom():
            raise RobotError("simulated ImmStopJOG failure")

        monkeypatch.setattr(app_mod.driver, "stop", boom)
        fake.calls.clear()
        r = client.post("/api/v1/motion/stop")
        assert r.status_code == 200
        assert "error" in r.json()["results"]["ImmStopJOG"]
        assert "StopMotion" in [c[0] for c in fake.calls], (
            "a failing ImmStopJOG must not prevent StopMotion")


class TestStopDoesNotLieAboutOutcome:
    def test_no_hardcoded_stopped_true(self, client):
        body = client.post("/api/v1/motion/stop").json()
        assert "stopped" not in body, (
            "{'stopped': true} asserts the arm halted when only a call returned")
        assert body["stop_requested"] is True
        assert "results" in body

    def test_confirmation_comes_from_telemetry(self, client, telemetry):
        body = client.post("/api/v1/motion/stop").json()
        assert body["confirmation_source"] == "telemetry-8083"
        assert body["confirmed"] in (True, False, None)

    def test_confirmed_is_none_when_telemetry_is_unavailable(
            self, client, monkeypatch):
        """Unknown must be reported as unknown, not as success."""
        monkeypatch.setattr(app_mod.telemetry, "snapshot", lambda: {})
        assert client.post("/api/v1/motion/stop").json()["confirmed"] is None

    def test_confirms_standstill_when_the_arm_is_idle(self, client, telemetry):
        body = client.post("/api/v1/motion/stop").json()
        assert body["confirmed"] is True


class TestStopReachesRegisteredRunners:
    """Stop reaches registered runners via AbortRegistry and adds no extra wire
    traffic."""

    def test_stop_raises_registered_abort_flags_and_sends_nothing_extra(
            self, client, fake):
        class Runner:
            """Stands in for an application's path executor."""

            def __init__(self):
                self.aborted = False

            def request_abort(self):
                self.aborted = True      # no transmission, per the protocol

        runner = Runner()
        app_mod.abortables.register(runner)
        try:
            fake.calls.clear()
            assert client.post("/api/v1/motion/stop").status_code == 200
            assert runner.aborted, "stop did not reach the registered runner"
            # Stop-class commands only: the fault poller writes unrelated
            # getters into fake.calls.
            STOPS = {"ImmStopJOG", "StopMotion", "StopJOG", "ProgramStop"}
            sent = [c[0] for c in fake.calls if c[0] in STOPS]
            assert sent == ["ImmStopJOG", "StopMotion"], (
                f"the abort registry must add no wire traffic; sent {sent}")
        finally:
            app_mod.abortables.clear()
