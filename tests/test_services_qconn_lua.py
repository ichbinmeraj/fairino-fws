"""qconn liveness client and the 8060 Lua-validator reachability probe."""
from __future__ import annotations

import socket

import pytest

from fws.services import ServiceError, ServiceUnavailable
from fws.services.lua_validate import LuaValidateClient
from fws.services.qconn import QconnClient, liveness
from fws.testing.fake_qconn import FakeQconnAgent


class TestQconnLiveness:
    def test_banner_proves_the_agent_is_alive(self):
        with FakeQconnAgent() as agent:
            info = liveness(agent.host, agent.port, timeout_s=4)
        assert info["reachable"] is True
        assert info["agent"] == "qconn"
        assert info["handshake_s"] >= 0

    def test_service_selection_returns_ok(self):
        with FakeQconnAgent() as agent, \
                QconnClient(agent.host, agent.port, timeout_s=4) as c:
            assert c.select_service("sinfo") == "OK"

    def test_a_non_qconn_banner_is_rejected(self):
        """A non-qconn banner must fail loudly, not read as alive."""
        with FakeQconnAgent(send_banner=False) as agent:
            client = QconnClient(agent.host, agent.port, timeout_s=2)
            with pytest.raises(ServiceError):
                client.connect()

    def test_an_unreachable_port_is_service_unavailable(self):
        with pytest.raises(ServiceUnavailable):
            liveness("127.0.0.1", 1, timeout_s=2)


class TestLuaValidatorHealthIsReadBackwards:
    """8060 health reads backwards: a refused connection means the validator is
    attached and healthy; a successful one means nobody is attached."""

    def test_a_socket_that_ACCEPTS_is_reported_UNHEALTHY(self):
        """A listening socket means nobody attached: reported unhealthy."""
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        host, port = srv.getsockname()
        try:
            info = LuaValidateClient(host, port, timeout_s=3).probe_health()
            assert info["validator_attached"] is False
            assert info["healthy"] is False
            assert "NO client is attached" in info["warning"]
        finally:
            srv.close()

    def test_a_REFUSED_port_is_reported_HEALTHY(self):
        """A refused connection means the validator is attached: reported healthy."""
        info = LuaValidateClient("127.0.0.1", 1, timeout_s=2).probe_health()
        assert info["validator_attached"] is True
        assert info["healthy"] is True
        assert "attached and healthy" in info["note"]

    def test_validate_refuses_with_the_real_reason(self):
        """validate() must refuse without sending bytes, and point at the proven
        path."""
        c = LuaValidateClient("127.0.0.1", 8060)
        with pytest.raises(ServiceError) as e:
            c.validate("print(1)")
        msg = str(e.value)
        assert "no verified request framing" in msg
        assert "8060" in msg
        assert "files/lua" in msg          # points at the proven path


class TestValidatorTimeoutIsNotHealthy:
    """A timeout to 8060 must read as unknown, not be folded into the 'healthy'
    (refused) bucket."""

    def test_a_timeout_reports_unknown_not_healthy(self, monkeypatch):
        import socket as _socket

        from fws.services import lua_validate as lv

        def timeout(*a, **k):
            raise TimeoutError("timed out")

        monkeypatch.setattr(_socket, "create_connection", timeout)
        info = lv.LuaValidateClient("192.0.2.1", 8060, timeout_s=1).probe_health()
        assert info["healthy"] is None, "timeout must NOT read as healthy"
        assert info["validator_attached"] is None
        assert "could not reach" in info["warning"]

    def test_a_refusal_still_reports_healthy(self):
        # Port 1 refuses fast (ConnectionRefusedError) -> attached/healthy.
        info = LuaValidateClient("127.0.0.1", 1, timeout_s=2).probe_health()
        assert info["healthy"] is True
        assert info["validator_attached"] is True
