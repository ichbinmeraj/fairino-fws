"""`fws --simulator`: a working gateway with no hardware."""
from __future__ import annotations

import pytest

from fws import cli


class TestSimulatorFlag:
    def test_the_flag_exists_under_both_names(self):
        p = cli.build_parser()
        assert p.parse_args(["--simulator"]).simulator is True
        assert p.parse_args(["--sim"]).simulator is True
        assert p.parse_args([]).simulator is False

    def test_help_leads_with_it(self):
        """--help mentions the simulator flag."""
        assert "--simulator" in cli.build_parser().format_help()

    def test_the_banner_says_no_robot_is_connected(self):
        """The banner must make clear no robot is connected."""
        assert "SIMULATOR" in cli.SIM_BANNER
        assert "No robot is connected" in cli.SIM_BANNER
        assert "nothing will move" in cli.SIM_BANNER


class TestSimulatorWiring:
    """Verify the simulator's config wiring."""

    def test_check_mode_starts_a_simulator_and_points_the_gateway_at_it(
            self, capsys):
        rc = cli.main(["--simulator", "--check"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "SIMULATOR" in out
        assert "configuration OK" in out
        # The gateway must be pointed at loopback, not the default robot IP.
        assert "192.168.57.2" not in out, (
            "simulator mode left the gateway aimed at a real controller")
        assert "127.0.0.1" in out

    def test_every_port_override_is_a_real_settings_key(self):
        """A typo here would silently leave one channel aimed at the robot."""
        from fws import config as config_mod
        robot = config_mod.load().robot
        for key in ("ip", "rpc_port", "telemetry_port",
                    "upload_port", "download_port"):
            assert hasattr(robot, key), f"robot.{key} is not a settings field"

    def test_the_simulator_serves_a_real_api(self):
        """End to end: the fake answers the gateway's own routes."""
        from fastapi.testclient import TestClient

        from fws import app as app_mod
        from fws import config as config_mod
        from fws.testing.fake_controller import FakeController

        sim = FakeController()
        sim.start()
        try:
            settings = config_mod.load(**{
                "robot.ip": sim.host,
                "robot.rpc_port": sim.rpc_port,
                "robot.telemetry_port": sim.stream_port,
                "robot.upload_port": sim.upload_port,
                "robot.download_port": sim.download_port,
            })
            app_mod.create_app(settings)
            with TestClient(app_mod.app) as c:
                assert c.get("/api/v1/system/version").status_code == 200
                st = c.get("/api/v1/state")
                assert st.status_code == 200
                assert len(st.json()["joints"]) == 6
                up = c.put("/api/v1/programs/sim.lua",
                           json={"content": "WaitMs(1)\n", "overwrite": True})
                assert up.status_code == 200
        finally:
            sim.stop()


class TestBothUploadRoutesExplainThemselves:
    """Both Lua upload routes must explain a rejection, not return a bare -1."""

    def test_a_rejected_program_upload_is_422_not_502(self, fake):
        from fastapi.testclient import TestClient

        from fws import app as app_mod
        from fws import config as config_mod
        app_mod.create_app(config_mod.load(**{
            "robot.ip": fake.host, "robot.rpc_port": fake.rpc_port,
            "robot.telemetry_port": fake.stream_port,
            "robot.upload_port": fake.upload_port,
            "robot.download_port": fake.download_port,
        }))
        with TestClient(app_mod.app) as c:
            r = c.put("/api/v1/programs/bad.lua",
                      json={"content": "NoSuchThing(1)\n", "overwrite": True})
            assert r.status_code == 422, "a compiler rejection is not a 502"
            d = r.json()["detail"]
            assert "compiler rejected" in d["message"]
            assert "overwritten" in d["file_state"], (
                "the caller must be told the old program is gone")

    @pytest.mark.parametrize("route", ["/api/v1/programs/x.lua",
                                       "/api/v1/files/lua/x.lua"])
    def test_a_valid_program_succeeds_on_both(self, fake, route):
        from fastapi.testclient import TestClient

        from fws import app as app_mod
        from fws import config as config_mod
        app_mod.create_app(config_mod.load(**{
            "robot.ip": fake.host, "robot.rpc_port": fake.rpc_port,
            "robot.telemetry_port": fake.stream_port,
            "robot.upload_port": fake.upload_port,
            "robot.download_port": fake.download_port,
        }))
        with TestClient(app_mod.app) as c:
            r = c.put(route, json={"content": "WaitMs(1)\n", "overwrite": True})
            assert r.status_code == 200
