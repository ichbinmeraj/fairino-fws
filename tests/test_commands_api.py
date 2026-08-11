"""The command catalogue and its gates."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod


@pytest.fixture
def client(fake):
    settings = config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
    })
    app_mod.create_app(settings)
    with TestClient(app_mod.app) as c:
        yield c


@pytest.fixture
def full_client(fake):
    """Both opt-ins set: the only way to reach a non-read command."""
    settings = config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
        "features.enable_command_passthrough": True,
        "features.enable_unverified_commands": True,
    })
    app_mod.create_app(settings)
    with TestClient(app_mod.app) as c:
        yield c


@pytest.fixture
def open_client(fake):
    settings = config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
        "features.enable_command_passthrough": True,
    })
    app_mod.create_app(settings)
    with TestClient(app_mod.app) as c:
        yield c


class TestCatalogue:
    def test_lists_every_command(self, client):
        d = client.get("/api/v1/commands?limit=1000").json()
        assert d["summary"]["total"] == 594
        assert d["matched"] == 594

    def test_filters(self, client):
        refused = client.get("/api/v1/commands?danger=refused").json()
        # 11: SlaveFileWrite and SetSysServoBootMode are refused in
        # fws/driver.py, though the catalogue lists them as callable.
        assert refused["matched"] == 11
        assert all(not c["callable"] for c in refused["commands"])

        simple = client.get("/api/v1/commands?kind=simple&limit=1000").json()
        assert simple["matched"] == 486

    def test_search(self, client):
        d = client.get("/api/v1/commands?q=jog").json()
        assert any(c["name"] == "StartJOG" for c in d["commands"])

    def test_detail_shows_the_wire_order(self, client):
        d = client.get("/api/v1/commands/StartJOG").json()
        assert d["wire_args"] == ["ref", "nb", "dir", "vel", "acc", "max_dis"]
        assert d["verified"] is True

    def test_composite_shows_its_sequence(self, client):
        d = client.get("/api/v1/commands/MoveL").json()
        assert d["kind"] == "composite"
        assert d["callable"] is False
        assert "GetInverseKin" in d["wire_sequence"]

    def test_sdk_defects_are_surfaced(self, client):
        d = client.get("/api/v1/commands/SetTrajectoryJForceFz").json()
        assert "sdk_defect" in d
        assert "wrong axis" in d["sdk_defect"]

    def test_unknown_command_is_404(self, client):
        assert client.get("/api/v1/commands/NoSuchThing").status_code == 404


class TestGates:
    """Order matters: refused wins over every other setting."""

    @pytest.mark.parametrize("name", [
        "ShutDownRobotOS", "KernelUpgrade", "JointAllParamUpgrade",
        "SetJointFirmwareUpgrade", "GetLuaList",
    ])
    def test_refused_stays_refused_even_with_passthrough_on(
            self, open_client, name):
        r = open_client.post(f"/api/v1/commands/{name}",
                             json={"args": [], "confirm": True})
        assert r.status_code == 403
        assert "refused" in r.json()["detail"].lower()

    def test_composite_is_rejected_as_unrepresentable(self, open_client):
        r = open_client.post("/api/v1/commands/MoveL",
                             json={"args": [[0.0] * 33], "confirm": True})
        assert r.status_code == 422
        assert "composite" in r.json()["detail"]

    def test_passthrough_off_by_default(self, client):
        r = client.post("/api/v1/commands/GetSoftwareVersion", json={"args": []})
        assert r.status_code == 403
        assert "passthrough is disabled" in r.json()["detail"]

    def test_unverified_needs_its_own_opt_in(self, open_client):
        """Unverified commands need their own opt-in on this route."""
        r = open_client.post("/api/v1/commands/GetSlaveHardVersion",
                             json={"args": []})
        assert r.status_code == 403
        assert "never been exercised" in r.json()["detail"]

    def test_motion_is_referred_to_the_route_that_can_check_a_lease(
            self, full_client):
        """A motion command is refused here and referred to the route that can
        verify a lease."""
        r = full_client.post("/api/v1/commands/ActGripper",
                             json={"args": [1, 1]})
        assert r.status_code == 428
        detail = r.json()["detail"]
        assert "confirm=true" in detail
        assert "'motion' control lock" in detail
        assert "/api/v1/invoke/ActGripper" in detail

    def test_a_bounded_command_is_referred_to_its_typed_route(self, full_client):
        """A bounded command like StartJOG is refused by generic routes and
        referred to /motion/jog."""
        r = full_client.post("/api/v1/commands/StartJOG",
                             json={"args": [0, 1, 1, 5.0, 100.0, 360.0],
                                   "confirm": True})
        assert r.status_code == 409
        assert "/api/v1/motion/jog" in r.json()["detail"]

    def test_wrong_arity_is_rejected_before_transmission(self, full_client, fake):
        """A wrong argument count is rejected before it reaches the robot."""
        r = full_client.post("/api/v1/commands/ActGripper",
                             json={"args": [1], "confirm": True})
        assert r.status_code == 422
        assert "wire order" in r.json()["detail"]
        assert "ActGripper" not in [c[0] for c in fake.calls], (
            "a wrong argument count must never reach the robot")

    def test_a_verified_read_works(self, open_client):
        r = open_client.post("/api/v1/commands/GetSoftwareVersion",
                             json={"args": []})
        assert r.status_code == 200
        assert r.json()["result"][1] == "FR5-V1-002(V6.0)"


class TestTheAllowlistIsNoLongerNeeded:
    """The route gates on evidence-based command class instead of an allowlist."""

    @pytest.mark.parametrize("name,arity", [
        ("ARCStart", 3), ("ActGripper", 2), ("TractorMoveL", 2)])
    def test_the_exhibits_are_now_classified_motion(self, full_client,
                                                    name, arity):
        d = full_client.get(f"/api/v1/commands/{name}").json()
        assert d["danger"] == "motion"
        assert d["requires_lock"] == "motion"
        assert d["requires_confirm"] is True
        assert d["basis"], "a classification must say what it rests on"

        r = full_client.post(f"/api/v1/commands/{name}",
                             json={"args": [0] * arity, "confirm": True})
        assert r.status_code == 428
        assert "'motion' control lock" in r.json()["detail"]

    def test_reads_still_work(self, open_client):
        r = open_client.post("/api/v1/commands/GetSoftwareVersion",
                             json={"args": []})
        assert r.status_code == 200

    def test_the_hole_is_closed(self):
        """'other' no longer exists as a class; unknown is gated as strictly
        as motion."""
        from fws.protocol.commands import COMMANDS
        assert not [n for n, c in COMMANDS.items() if c.danger == "other"]
        for name in ("ARCStart", "ActGripper", "TractorMoveL"):
            assert COMMANDS[name].danger == "motion"
