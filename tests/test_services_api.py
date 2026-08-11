"""The controller-services API: FTP, shell, restart, qconn, 8060."""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod
from fws.testing.fake_ftp import FakeFtpServer
from fws.testing.fake_qconn import FakeQconnAgent
from fws.testing.fake_shell import FakeTelnetServer, qnx_like_handler

FTP_FILES = {
    "/fruser/force_test.lua": b"-- force\n",
    "/fruser/paint.lua": b"-- paint\n",
}


@pytest.fixture
def services():
    """All four base-service fakes, running together."""
    ftp = FakeFtpServer(files=dict(FTP_FILES))
    tel = FakeTelnetServer(password="s3cret", handler=qnx_like_handler())
    qc = FakeQconnAgent()
    with ftp, tel, qc:
        yield {"ftp": ftp, "shell": tel, "qconn": qc}


TEST_KEY = "svc-test-key"


def _make_client(fake, tmp_path, services, **service_overrides):
    # Privileged services refuse to start without auth, so every client is
    # authenticated and sends the key by default.
    keyfile = tmp_path / "keys"
    keyfile.write_text(f"{TEST_KEY}  test\n")
    base = {
        "robot.ip": "127.0.0.1",
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
        "server.data_dir": str(tmp_path),
        "auth.api_keys_file": str(keyfile),
        "services.ftp_port": services["ftp"].port,
        "services.shell_port": services["shell"].port,
        "services.shell_user": "root",
        "services.shell_password": "s3cret",
        "services.shell_prompt": "#",
        "services.qconn_port": services["qconn"].port,
        "services.connect_timeout_s": 5.0,
        "services.command_timeout_s": 5.0,
    }
    base.update(service_overrides)
    app_mod.create_app(config_mod.load(**base))
    c = TestClient(app_mod.app, headers={"X-API-Key": TEST_KEY})
    c.__enter__()
    app_mod.control._leases.clear()
    return c


@pytest.fixture
def client(fake, tmp_path, services):
    c = _make_client(fake, tmp_path, services,
                     **{"services.ftp_enabled": True,
                        "services.shell_enabled": True,
                        "services.qconn_enabled": True,
                        "services.lua_validate_enabled": True})
    yield c
    app_mod.control._leases.clear()
    c.__exit__(None, None, None)


class TestFlagsGateEverything:
    def test_disabled_services_are_403_not_404(self, fake, tmp_path, services):
        """A disabled service returns 403, not 404."""
        c = _make_client(fake, tmp_path, services)  # nothing enabled
        try:
            for method, path in [("get", "/api/v1/controller/files"),
                                 ("get", "/api/v1/controller/processes"),
                                 ("get", "/api/v1/controller/qconn")]:
                r = getattr(c, method)(path)
                assert r.status_code == 403, path
                assert "disabled" in r.json()["detail"]
        finally:
            c.__exit__(None, None, None)

    def test_services_status_reports_what_is_on(self, client):
        d = client.get("/api/v1/controller/services").json()
        assert d["enabled"]["shell"] is True
        assert "cannot authenticate the daemons" in d["note"]


class TestFtp:
    def test_true_listing_over_ftp(self, client):
        d = client.get("/api/v1/controller/files").json()
        names = {e["name"] for e in d["entries"]}
        assert "force_test.lua" in names
        assert d["source"] == "ftp"

    def test_download_round_trips(self, client):
        d = client.get("/api/v1/controller/files/download",
                       params={"path": "force_test.lua"}).json()
        assert base64.b64decode(d["content_base64"]) == b"-- force\n"

    def test_upload_needs_confirmation_and_warns(self, client, services):
        r = client.put("/api/v1/controller/files",
                       json={"path": "raw.lua",
                             "content_base64": base64.b64encode(b"x").decode()})
        assert r.status_code == 400
        assert "compile-and-register" in r.json()["detail"]

    def test_confirmed_upload_lands_and_is_flagged_raw(self, client, services):
        payload = base64.b64encode(b"-- raw\n").decode()
        r = client.put("/api/v1/controller/files",
                       json={"path": "raw.lua", "content_base64": payload,
                             "confirm": True})
        assert r.status_code == 200
        assert "no compiler verdict" in r.json()["warning"]
        assert services["ftp"].uploaded["/fruser/raw.lua"] == b"-- raw\n"


class TestShell:
    def test_processes_lists_via_pidin(self, client, services):
        d = client.get("/api/v1/controller/processes").json()
        assert "procnto" in d["output"]
        assert "pidin" in services["shell"].commands_seen

    def test_shell_needs_confirmation(self, client):
        r = client.post("/api/v1/controller/shell",
                        json={"command": "echo hi"})
        assert r.status_code == 400

    def test_confirmed_shell_runs_and_frames_output(self, client):
        r = client.post("/api/v1/controller/shell",
                        json={"command": "echo hello", "confirm": True})
        assert r.status_code == 200
        assert r.json()["output"] == "hello"

    def test_allowlist_blocks_a_command_outside_it(self, fake, tmp_path,
                                                   services):
        c = _make_client(fake, tmp_path, services,
                         **{"services.shell_enabled": True,
                            "services.shell_allowlist": ["pidin", "echo"]})
        try:
            r = c.post("/api/v1/controller/shell",
                       json={"command": "rm -rf /", "confirm": True})
            assert r.status_code == 403
            assert "allowlist" in r.json()["detail"]
            # An allowed one still runs.
            assert c.post("/api/v1/controller/shell",
                          json={"command": "echo ok",
                                "confirm": True}).status_code == 200
        finally:
            c.__exit__(None, None, None)


class TestRestart:
    def test_restart_refuses_without_a_configured_command(self, client):
        """FWS must not guess the process to signal."""
        r = client.post("/api/v1/controller/restart",
                        json={"confirm": True,
                              "i_understand_the_arm_may_move_or_stop": True})
        assert r.status_code == 400
        assert "will not guess" in r.json()["detail"]

    def test_restart_needs_both_acknowledgements(self, fake, tmp_path,
                                                 services):
        c = _make_client(fake, tmp_path, services,
                         **{"services.shell_enabled": True,
                            "services.shell_restart_command": "echo restarted"})
        try:
            r = c.post("/api/v1/controller/restart", json={"confirm": True})
            assert r.status_code == 422
        finally:
            c.__exit__(None, None, None)

    def test_a_configured_restart_runs_the_command(self, fake, tmp_path,
                                                   services):
        c = _make_client(fake, tmp_path, services,
                         **{"services.shell_enabled": True,
                            "services.shell_restart_command": "echo restarted"})
        try:
            r = c.post("/api/v1/controller/restart",
                       json={"confirm": True,
                             "i_understand_the_arm_may_move_or_stop": True})
            assert r.status_code == 200
            assert r.json()["restarted"] is True
            assert "restarted" in r.json()["output"]
            assert "echo restarted" in services["shell"].commands_seen
        finally:
            c.__exit__(None, None, None)


class TestQconnAndValidator:
    def test_qconn_liveness(self, client):
        d = client.get("/api/v1/controller/qconn").json()
        assert d["reachable"] is True
        assert d["agent"] == "qconn"

    def test_lua_validate_refuses_with_the_real_reason(self, client):
        r = client.post("/api/v1/controller/lua-validate",
                        json={"command": "print(1)", "confirm": True})
        assert r.status_code == 501
        assert "no verified request framing" in r.json()["detail"]


class TestStartupRefusal:
    """The config-level startup guard: privileged services need auth."""

    def test_shell_without_auth_refuses_to_start(self):
        s = config_mod.load(**{"services.shell_enabled": True})
        problems = s.check_safe_to_start()
        assert any("root-equivalent" in p for p in problems)

    def test_ftp_read_without_auth_is_allowed_on_loopback(self):
        s = config_mod.load(**{"services.ftp_enabled": True})
        assert s.check_safe_to_start() == []

    def test_shell_with_auth_is_allowed(self, tmp_path):
        kf = tmp_path / "keys"
        kf.write_text("secret-key  ci\n")
        s = config_mod.load(**{"services.shell_enabled": True,
                               "auth.api_keys_file": str(kf)})
        assert s.check_safe_to_start() == []


class TestAllowlistCannotBeBypassed:
    """A first-token allowlist is bypassable via shell metacharacters; they must
    be refused."""

    def _client_with_allowlist(self, fake, tmp_path, services):
        return _make_client(fake, tmp_path, services,
                            **{"services.shell_enabled": True,
                               "services.shell_allowlist": ["pidin", "echo"]})

    @pytest.mark.parametrize("payload", [
        "echo x; reboot", "echo x && rm -rf /", "pidin | halt",
        "echo $(reboot)", "echo `reboot`", "echo x > /etc/passwd",
    ])
    def test_metacharacter_commands_are_refused(self, fake, tmp_path, services,
                                                payload):
        c = self._client_with_allowlist(fake, tmp_path, services)
        try:
            r = c.post("/api/v1/controller/shell",
                       json={"command": payload, "confirm": True})
            assert r.status_code == 403, payload
            assert "metacharacter" in r.json()["detail"]
            # And it never reached the shell.
            assert payload not in services["shell"].commands_seen
        finally:
            c.__exit__(None, None, None)

    def test_a_plain_allowlisted_command_still_runs(self, fake, tmp_path,
                                                    services):
        c = self._client_with_allowlist(fake, tmp_path, services)
        try:
            assert c.post("/api/v1/controller/shell",
                          json={"command": "echo ok",
                                "confirm": True}).status_code == 200
        finally:
            c.__exit__(None, None, None)


class TestRebootConnectionDropIsSuccess:
    """A reboot drops the socket; that is success, not a 503."""

    def test_reboot_reports_success_when_the_connection_closes(self, fake,
                                                              tmp_path,
                                                              services):
        # Point reboot at 'exit' so the session drop surfaces as
        # ServiceUnavailable.
        c = _make_client(fake, tmp_path, services,
                         **{"services.shell_enabled": True,
                            "services.shell_reboot_command": "exit"})
        try:
            r = c.post("/api/v1/controller/reboot",
                       json={"confirm": True,
                             "i_have_physical_or_switched_power": True})
            # The drop must be treated as the expected reboot outcome (200),
            # not 503.
            assert r.status_code == 200
            assert r.json()["reboot_requested"] is True
        finally:
            c.__exit__(None, None, None)


class TestWebSocketStateRequiresAuth:
    """/ws/state must enforce auth, not bypass the HTTP middleware."""

    def test_ws_state_rejects_without_a_key_when_auth_configured(self, fake,
                                                                tmp_path,
                                                                services):
        c = _make_client(fake, tmp_path, services)
        try:
            import starlette.websockets
            # No ?key -> must be rejected (closed), not stream state.
            with pytest.raises(starlette.websockets.WebSocketDisconnect), \
                    c.websocket_connect("/ws/state") as ws:
                ws.receive_text()
        finally:
            c.__exit__(None, None, None)

    def test_ws_state_accepts_with_a_valid_key(self, fake, tmp_path, services):
        c = _make_client(fake, tmp_path, services)
        try:
            with c.websocket_connect(f"/ws/state?key={TEST_KEY}") as ws:
                # Any state frame proves the key was accepted; assert on a
                # field the snapshot always carries.
                msg = ws.receive_text()
                assert "connected" in msg and "frames" in msg
        finally:
            c.__exit__(None, None, None)


class TestStartupGuardEnforcedEverywhere:
    """The startup refusal runs in the lifespan, so any start enforces it."""

    def test_shell_without_auth_refuses_to_start_via_the_app(self, fake,
                                                            tmp_path, services):
        # Enable shell but provide NO api_keys_file -> lifespan must refuse.
        app_mod.create_app(config_mod.load(**{
            "robot.ip": "127.0.0.1", "robot.rpc_port": fake.rpc_port,
            "robot.telemetry_port": fake.stream_port,
            "robot.upload_port": fake.upload_port,
            "robot.download_port": fake.download_port,
            "server.data_dir": str(tmp_path),
            "services.shell_enabled": True,
            "services.shell_port": services["shell"].port}))
        with pytest.raises(RuntimeError) as e, TestClient(app_mod.app):
            pass
        assert "refuses to start" in str(e.value)
        assert "root-equivalent" in str(e.value)
