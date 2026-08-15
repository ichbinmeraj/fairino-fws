"""The supported test harness, exercised the way a customer would use it.

These tests deliberately go through the PUBLIC surface -- `from fws.testing
import gateway`, real HTTP over a real socket -- rather than the TestClient
the rest of the suite uses. The point of the harness is that someone outside
this package can depend on it, so it has to be tested from outside too: a
harness that only works when imported from FWS's own conftest is the problem
it exists to solve.
"""
from __future__ import annotations

import time

import pytest

from fws.testing import FakeController, gateway


class TestItRunsTheWholeStack:
    def test_the_api_answers_over_real_http(self):
        with gateway() as g:
            r = g.get("/api/v1/state")
            assert r.status_code == 200
            assert "joints" in r.json()

    def test_telemetry_reaches_the_client(self):
        with gateway() as g:
            assert g.wait_for_telemetry(), "the 8083 stream never delivered"
            assert len(g.get("/api/v1/state").json()["joints"]) == 6

    def test_it_serves_its_own_openapi(self):
        """A client generator points at this; if it 404s the harness is
        useless for the thing most people want it for."""
        with gateway() as g:
            spec = g.get("/openapi.json").json()
            assert "/api/v1/state" in spec["paths"]

    def test_two_gateways_do_not_collide(self):
        """Ephemeral ports throughout, so a parallel test suite works."""
        with gateway() as a, gateway() as b:
            assert a.url != b.url
            assert a.get("/api/v1/state").status_code == 200
            assert b.get("/api/v1/state").status_code == 200

    def test_settings_can_be_overridden_by_dotted_key(self):
        with gateway(**{"limits.jog_max_deg": 3.0}) as g:
            h = g.take_control()
            r = g.post("/api/v1/motion/jog", headers=h,
                       json={"joint": 1, "direction": 1, "step": 5, "vel": 5})
            assert r.status_code == 422, "the override must reach the app"

    def test_a_refusal_is_returned_not_raised(self):
        """FWS says a great deal through its refusals; a harness that raised
        on 4xx would make the interesting assertions awkward."""
        with gateway() as g:
            r = g.post("/api/v1/robot/enable", json={"enable": True})
            assert r.status_code == 400
            assert "confirm" in r.text

    def test_the_robot_address_cannot_be_pointed_at_a_real_arm(self):
        """Overriding robot.ip is exactly what this harness exists to
        prevent; the fake's address always wins."""
        with gateway(**{"robot.ip": "192.168.57.2"}) as g:
            assert g.settings.robot.ip == "127.0.0.1"


class TestTheScenarioApi:
    def test_trip_fault_shows_up_through_the_api(self):
        with gateway() as g:
            assert g.get("/api/v1/errors").json()["faulted"] is False
            g.controller.trip_fault()
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if g.get("/api/v1/errors").json()["faulted"]:
                    break
                time.sleep(0.05)
            assert g.get("/api/v1/errors").json()["faulted"] is True

    def test_clear_fault_undoes_it(self):
        with gateway() as g:
            g.controller.trip_fault()
            g.controller.clear_fault()
            assert g.controller.state.error_main == 0

    def test_set_joints_moves_what_telemetry_reports(self):
        with gateway() as g:
            g.controller.set_joints([10.0, -80.0, 80.0, -90.0, -90.0, 5.0])
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                j = g.get("/api/v1/state").json()["joints"]
                if abs(j[0] - 10.0) < 0.01:
                    break
                time.sleep(0.05)
            assert abs(g.get("/api/v1/state").json()["joints"][0] - 10.0) < 0.01

    def test_set_force_reaches_the_force_reading(self):
        with gateway() as g:
            g.controller.set_force([1.0, 2.0, 3.0, 0.1, 0.2, 0.3])
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                f = g.get("/api/v1/state").json().get("force")
                if f and abs(f[0] - 1.0) < 0.01:
                    break
                time.sleep(0.05)
            assert abs(g.get("/api/v1/state").json()["force"][0] - 1.0) < 0.01

    def test_corrupt_next_frame_is_dropped_and_counted(self):
        """A corrupt frame carries plausible-looking joint angles, so a
        client that ignores the checksum reads a believable lie. FWS must
        drop it and count it."""
        with gateway() as g:
            before = g.get("/api/v1/system/health").json()["bad_checksum"]
            g.controller.corrupt_next_frame(3)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                after = g.get("/api/v1/system/health").json()["bad_checksum"]
                if after > before:
                    break
                time.sleep(0.05)
            assert g.get(
                "/api/v1/system/health").json()["bad_checksum"] > before

    def test_the_scenario_api_validates_its_input(self):
        c = FakeController()
        with pytest.raises(ValueError, match="six joint angles"):
            c.set_joints([0.0, 0.0])
        with pytest.raises(ValueError, match="six force"):
            c.set_force([0.0])


class TestBringYourOwnController:
    def test_a_caller_supplied_controller_is_used_and_left_alone(self):
        """The caller owns what the caller made -- the harness must not stop
        a controller it did not start, or a suite sharing one across tests
        breaks on the second use."""
        c = FakeController(jog_start_latency_s=0.05,
                           transfer_port_delay_s=0.05,
                           software_version="v9.9.9")
        c.start()
        try:
            with gateway(controller=c) as g:
                assert g.controller is c
                v = g.get("/api/v1/system/version").json()
                assert "9.9.9" in str(v)
            # Still alive after the gateway went away.
            with gateway(controller=c) as g2:
                assert g2.get("/api/v1/state").status_code == 200
        finally:
            c.stop()


class TestThePytestPlugin:
    def test_the_fixture_is_importable_and_shaped_right(self):
        """The plugin is a documented entry point; a typo in it would only
        surface in someone else's suite."""
        from fws.testing import pytest_plugin
        assert hasattr(pytest_plugin, "fws_gateway")
        assert hasattr(pytest_plugin, "fws_gateway_session")
