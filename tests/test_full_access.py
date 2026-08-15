"""features.full_access: the developer switch that removes every software guard.

The rest of the suite proves the guards work. This file proves the switch
actually turns them OFF -- all of them, at once, from one flag -- because a
half-removed guard is the worst of both worlds: the developer still hits a
refusal, and the refusal no longer means anything.

The switch is process-global (fws/access.py latches it in create_app), so
every test here restores it in a fixture teardown. If that ever leaks, the
guarded tests elsewhere start failing, which is the alarm we want.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fws import access
from fws import app as app_mod
from fws import config as config_mod
from fws import invoke as policy
from fws.driver import RobotDriver


def _settings(fake, **over):
    return config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
        **over,
    })


@pytest.fixture(autouse=True)
def _restore_switch():
    """Never leak the switch into another test."""
    yield
    access.set_full_access(False)


@pytest.fixture
def open_client(fake):
    """A gateway started with every guard off."""
    app_mod.create_app(_settings(fake, **{"features.full_access": True}))
    with TestClient(app_mod.app) as c:
        yield c


@pytest.fixture
def guarded_client(fake):
    """The default gateway, for the side-by-side assertions."""
    app_mod.create_app(_settings(fake))
    with TestClient(app_mod.app) as c:
        yield c


class TestOneFlagTurnsEverythingOn:
    def test_it_forces_the_dependent_feature_flags(self, fake):
        s = _settings(fake, **{"features.full_access": True})
        assert s.features.enable_movel
        assert s.features.enable_command_passthrough
        assert s.features.enable_unverified_commands
        assert s.features.enable_shutdown

    def test_it_forces_the_controller_services_on(self, fake):
        s = _settings(fake, **{"features.full_access": True})
        assert s.services.ftp_enabled
        assert s.services.shell_enabled
        assert s.services.qconn_enabled
        assert s.services.lua_validate_enabled

    def test_the_default_is_still_off(self, fake):
        """The package ships safe to anyone who does not opt in."""
        s = _settings(fake)
        assert s.features.full_access is False
        assert s.features.enable_movel is False
        assert s.services.shell_enabled is False

    def test_the_mode_is_visible_to_clients(self, open_client):
        """A developer -- or the console -- can tell which mode this is."""
        assert open_client.get("/").json()["full_access"] is True


class TestTheStartupGateStandsAside:
    def test_a_public_bind_without_auth_no_longer_refuses(self, fake):
        """Normally a hard refusal (exit 3). Under full access it starts."""
        s = _settings(fake, **{"server.bind_host": "0.0.0.0",
                               "features.full_access": True})
        assert s.check_safe_to_start() == []

    def test_but_it_still_says_what_it_would_have_refused(self, fake):
        """Silence would be dishonest: the reasons are still reported, as
        warnings, so the operator sees the trade they made."""
        s = _settings(fake, **{"server.bind_host": "0.0.0.0",
                               "features.full_access": True})
        warnings = s.startup_warnings()
        assert any("no authentication" in w for w in warnings)

    def test_the_guarded_gateway_still_refuses(self, fake):
        s = _settings(fake, **{"server.bind_host": "0.0.0.0"})
        assert s.check_safe_to_start() != []
        assert s.startup_warnings() == []


class TestTheInvokeGateIsGone:
    def test_a_motion_command_needs_no_lease_and_no_confirm(self, open_client):
        """Normally 428: hold the motion lease AND resend with confirm=true."""
        r = open_client.post("/api/v1/invoke/ResetAllError", json={"args": []})
        assert r.status_code != 428, r.text

    def test_the_same_call_is_gated_by_default(self, guarded_client):
        r = guarded_client.post("/api/v1/invoke/StartJOG",
                                json={"args": [1, 1, 0, 10.0, 10.0, 10.0]})
        assert r.status_code in (409, 428)

    def test_a_refused_command_becomes_callable(self, open_client, fake):
        """The 13 names that write firmware, halt the controller, or wedge the
        RPC channel. This is the sharpest edge of the switch: it is exactly
        the call that can brick a controller, and it now goes through."""
        r = open_client.post("/api/v1/invoke/ShutDownRobotOS",
                             json={"args": []})       # registry arity is 0
        assert r.status_code != 403, r.text
        assert fake.shut_down, "the call reached the wire"

    def test_a_typed_route_owned_command_is_reachable_raw(self, open_client):
        """StartJOG carries no max_dis bound when called raw -- normally a 409
        pointing at the typed route."""
        r = open_client.post("/api/v1/invoke/StartJOG",
                             json={"args": [1, 1, 0, 10.0, 10.0, 10.0]})
        assert r.status_code != 409, r.text

    def test_the_gate_helper_returns_an_actor_without_a_lease(self):
        access.set_full_access(True)
        cmd = policy.lookup("GetSoftwareVersion")

        class _NoLocks:
            def held_by(self, domain):
                return None

        assert policy.gate(cmd, confirm=False, token=None,
                           control=_NoLocks()) == "full-access"


class TestTheDriverFloorIsGone:
    def test_the_driver_sends_a_refused_command(self, fake):
        """The refusal was enforced twice -- HTTP and driver. Both lift, so
        this is not merely moved down a layer."""
        access.set_full_access(True)
        d = RobotDriver(fake.host, timeout=3.0, port=fake.rpc_port)
        d._call("ShutDownRobotOS")
        assert fake.shut_down, "the call reached the wire"

    def test_the_driver_refuses_it_by_default(self, fake):
        from fws.driver import RobotError
        d = RobotDriver(fake.host, timeout=3.0, port=fake.rpc_port)
        with pytest.raises(RobotError, match="refused"):
            d._call("ShutDownRobotOS")


class TestMotionBoundsAreLifted:
    def test_a_jog_beyond_the_configured_step_is_accepted(self, open_client):
        """jog_max_deg is 15 by default; 90 would normally be a 422."""
        r = open_client.post("/api/v1/motion/jog",
                             json={"joint": 1, "direction": 1,
                                   "step": 90.0, "vel": 100.0})
        assert r.status_code != 422, r.text

    def test_the_same_jog_is_bounded_by_default(self, guarded_client):
        r = guarded_client.post("/api/v1/motion/jog",
                                json={"joint": 1, "direction": 1,
                                      "step": 90.0, "vel": 100.0})
        assert r.status_code == 422

    def test_enabling_servos_needs_no_confirmation(self, open_client):
        r = open_client.post("/api/v1/robot/enable", json={"enable": True})
        assert r.status_code != 400, r.text

    def test_enabling_servos_asks_by_default(self, guarded_client):
        r = guarded_client.post("/api/v1/robot/enable", json={"enable": True})
        assert r.status_code == 400


class TestWhatFullAccessDoesNotChange:
    """The switch removes SOFTWARE guards. It does not invent capability, and
    it must not quietly change what the wire does."""

    def test_stop_is_still_open_and_unauthenticated(self, open_client):
        assert open_client.post("/api/v1/motion/stop").status_code == 200

    def test_arguments_are_still_type_checked(self, open_client):
        """Coercion is not a safety gate, it is correctness: the controller
        does not ignore a wrong arity safely, so this stays."""
        r = open_client.post("/api/v1/invoke/SetSpeed",
                             json={"args": ["not-a-number"]})
        assert r.status_code == 422

    def test_the_audit_line_is_still_written(self, open_client):
        open_client.post("/api/v1/invoke/ResetAllError", json={"args": []})
        events = open_client.get("/api/v1/events").json()["events"]
        assert any(e["action"].startswith("invoke.") for e in events)
