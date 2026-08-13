"""server.read_only: observation without the ability to command.

The mode exists so a gateway can be pointed at a production robot with a
guarantee stronger than "nobody will press the buttons". The rule is by verb,
not by route, so a mutating route added later is refused by construction
rather than remembered in a denylist.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import cli
from fws import config as config_mod


@pytest.fixture
def readonly(fake):
    app_mod.create_app(config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
        "server.read_only": True,
    }))
    with TestClient(app_mod.app) as c:
        yield c


class TestReads:
    def test_state_is_served(self, readonly):
        assert readonly.get("/api/v1/state").status_code == 200

    def test_identity_limits_capabilities_are_served(self, readonly):
        for path in ("/api/v1/robot", "/api/v1/robot/limits",
                     "/api/v1/capabilities", "/api/v1/errors"):
            assert readonly.get(path).status_code == 200, path

    def test_the_websocket_stream_is_served(self, readonly):
        with readonly.websocket_connect("/ws/state") as ws:
            frame = ws.receive_json()
        assert "joints" in frame

    def test_the_descriptor_says_so(self, readonly):
        assert readonly.get("/").json()["read_only"] is True


class TestRefusals:
    def test_every_non_get_operation_is_refused(self, readonly):
        """Walk the app's own OpenAPI schema: every non-GET operation must
        403 before its handler runs. Derived from the schema, not a list kept
        by hand, so a new mutating route cannot dodge this test."""
        import re
        checked = 0
        for path, ops in app_mod.app.openapi()["paths"].items():
            for method in ops:
                if method in ("get", "head", "options"):
                    continue
                # Any syntactically valid value: the 403 must arrive before
                # validation ever sees the parameters.
                concrete = re.sub(r"\{[^}]+\}", "0", path)
                r = readonly.request(method.upper(), concrete)
                assert r.status_code == 403, f"{method} {path} -> {r.status_code}"
                assert "read-only" in r.json()["detail"]
                checked += 1
        assert checked >= 30, "schema walk found suspiciously few operations"

    def test_stop_is_refused_too(self, readonly):
        """Deliberate: a gateway that can stop a program someone else started
        is not read-only. The refusal must say what to use instead."""
        r = readonly.post("/api/v1/motion/stop")
        assert r.status_code == 403
        assert "E-stop" in r.json()["detail"]

    def test_the_lock_cannot_be_acquired(self, readonly):
        r = readonly.post("/api/v1/control",
                          json={"client_id": "x"})
        assert r.status_code == 403

    def test_refusal_comes_before_auth(self, fake, tmp_path):
        """403 read-only, not 401: holding a key changes nothing about what
        a read-only gateway will do, so it must not be asked for first."""
        keys = tmp_path / "keys"
        keys.write_text("some-key\n")
        app_mod.create_app(config_mod.load(**{
            "robot.ip": fake.host,
            "robot.rpc_port": fake.rpc_port,
            "robot.telemetry_port": fake.stream_port,
            "robot.upload_port": fake.upload_port,
            "robot.download_port": fake.download_port,
            "server.read_only": True,
            "auth.api_keys_file": str(keys),
        }))
        with TestClient(app_mod.app) as c:
            r = c.post("/api/v1/motion/jog", json={})
            assert r.status_code == 403
            assert "read-only" in r.json()["detail"]


class TestWiring:
    def test_cli_flag_maps_to_the_setting(self):
        args = cli.build_parser().parse_args(["--read-only"])
        assert vars(args)["server.read_only"] is True

    def test_absent_flag_does_not_override_the_config_file(self):
        """argparse must yield None when the flag is absent, because load()
        drops None overrides -- a False default would silently clobber
        read_only=true from fws.toml."""
        args = cli.build_parser().parse_args([])
        assert vars(args)["server.read_only"] is None

    def test_default_is_off(self):
        assert config_mod.load().server.read_only is False

    def test_summary_reports_it(self):
        s = config_mod.load(**{"server.read_only": True})
        assert s.summary()["read_only"] is True


class TestConfigPrecedence:
    """Precedence is CLI > env > file > defaults, as documented. The stock
    pydantic-settings order ranked the file (passed as init kwargs) above
    env, silently discarding an env var whenever the file set the same key —
    a real trap for the shipped Docker/compose deployment."""

    def test_env_overrides_the_config_file(self, tmp_path, monkeypatch):
        f = tmp_path / "fws.toml"
        f.write_text("[server]\nread_only = false\n")
        monkeypatch.setenv("FWS_SERVER__READ_ONLY", "true")
        assert config_mod.load(f).server.read_only is True

    def test_cli_overrides_env(self, tmp_path, monkeypatch):
        f = tmp_path / "fws.toml"
        f.write_text("[server]\nread_only = false\n")
        monkeypatch.setenv("FWS_SERVER__READ_ONLY", "true")
        s = config_mod.load(f, **{"server.read_only": False})
        assert s.server.read_only is False

    def test_file_still_applies_without_env(self, tmp_path):
        f = tmp_path / "fws.toml"
        f.write_text("[server]\nread_only = true\n")
        assert config_mod.load(f).server.read_only is True
