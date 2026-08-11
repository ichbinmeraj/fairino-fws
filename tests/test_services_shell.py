"""The telnet shell client, against an in-process fake QNX telnetd."""
from __future__ import annotations

import pytest

from fws.services import ServiceAuthError, ServiceTimeout
from fws.services.shell import ShellClient, run_command
from fws.testing.fake_shell import FakeTelnetServer, qnx_like_handler


@pytest.fixture
def server():
    with FakeTelnetServer(password="s3cret",
                          handler=qnx_like_handler()) as srv:
        yield srv


def _run(server, cmd, **kw):
    kw.setdefault("user", "root")
    kw.setdefault("password", "s3cret")
    kw.setdefault("prompt", "#")
    kw.setdefault("connect_timeout_s", 30)
    kw.setdefault("command_timeout_s", 30)
    return run_command(server.host, cmd, port=server.port, **kw)


class TestTheHappyPath:
    def test_a_command_runs_and_output_is_framed(self, server):
        """Echo and trailing prompt are peeled; only the output remains."""
        r = _run(server, "echo hello world")
        assert r.output == "hello world"
        assert r.command == "echo hello world"
        assert r.duration_s >= 0

    def test_multiline_output_survives(self, server):
        r = _run(server, "pidin")
        assert "procnto" in r.output
        assert len(r.output.splitlines()) >= 2

    def test_an_unknown_command_returns_the_shell_error(self, server):
        """A restart flow must cope with 'command not found', not hang."""
        r = _run(server, "frobnicate")
        assert "not found" in r.output

    def test_the_server_actually_saw_the_command(self, server):
        _run(server, "uname -a")
        assert "uname -a" in server.commands_seen


class TestNegotiationAndAuth:
    def test_iac_negotiation_is_refused_not_echoed(self, server):
        """IAC negotiation must be stripped and refused, not echoed into output."""
        r = _run(server, "echo clean")
        assert r.output == "clean"
        assert "\xff" not in r.output

    def test_a_bad_password_raises_auth_error(self, server):
        with pytest.raises(ServiceAuthError):
            _run(server, "echo x", password="WRONG", connect_timeout_s=30,
                 command_timeout_s=30)

    def test_a_server_needing_no_login_still_works(self):
        with FakeTelnetServer(require_login=False,
                              handler=qnx_like_handler()) as srv:
            r = run_command(srv.host, "echo direct", port=srv.port,
                            prompt="#", connect_timeout_s=30,
                            command_timeout_s=30)
            assert r.output == "direct"

    def test_a_server_that_does_not_negotiate_still_works(self):
        with FakeTelnetServer(negotiate=False,
                              handler=qnx_like_handler()) as srv:
            r = run_command(srv.host, "echo nonneg", port=srv.port,
                            user="root", password="s3cret", prompt="#",
                            connect_timeout_s=30, command_timeout_s=30)
            assert r.output == "nonneg"


class TestFailureModes:
    def test_an_unreachable_port_is_service_unavailable(self):
        from fws.services import ServiceUnavailable
        # Port 1 is not listening; connect refuses fast.
        with pytest.raises(ServiceUnavailable):
            run_command("127.0.0.1", "echo x", port=1, connect_timeout_s=2,
                        command_timeout_s=2)

    def test_a_wrong_prompt_times_out_rather_than_lying(self):
        """A prompt that never appears must time out, not return a truncated success."""
        with FakeTelnetServer(password="s3cret",
                              handler=qnx_like_handler()) as srv, \
                pytest.raises(ServiceTimeout):
            run_command(srv.host, "echo x", port=srv.port, user="root",
                        password="s3cret", prompt="NEVER_APPEARS$$",
                        connect_timeout_s=3, command_timeout_s=2)


class TestReuseAcrossCommands:
    def test_one_login_many_commands(self, server):
        c = ShellClient(server.host, server.port, user="root",
                        password="s3cret", prompt="#", connect_timeout_s=30,
                        command_timeout_s=30)
        with c:
            c.login()
            assert c.run("echo one").output == "one"
            assert c.run("echo two").output == "two"
            assert "procnto" in c.run("pidin").output


class TestOutputFramingIsRobust:
    """Output containing the prompt char must not truncate; framing uses a sentinel."""

    def test_output_containing_the_prompt_char_is_not_truncated(self):
        # Fake echo returns its argument verbatim, so this output contains '#'.
        with FakeTelnetServer(password="s3cret",
                              handler=qnx_like_handler()) as srv:
            r = run_command(srv.host, 'echo a # b # c', port=srv.port,
                            user="root", password="s3cret", prompt="#",
                            connect_timeout_s=30, command_timeout_s=30)
            assert r.output == "a # b # c", (
                "a '#' in output must not truncate the result")

    def test_multiline_output_with_hash_lines_survives(self):
        table = {"cat cfg": "# comment one\nreal line\n# comment two"}
        with FakeTelnetServer(password="s3cret",
                              handler=qnx_like_handler(table)) as srv:
            r = run_command(srv.host, "cat cfg", port=srv.port, user="root",
                            password="s3cret", prompt="#", connect_timeout_s=30,
                            command_timeout_s=30)
            assert "# comment one" in r.output
            assert "# comment two" in r.output
            assert "real line" in r.output


class TestIacSplitAcrossRecv:
    """A telnet command split across recv() boundaries must not leak as output;
    the client carries an incomplete IAC prefix forward."""

    def test_a_split_iac_sequence_is_not_emitted_as_output(self):
        from fws.services.shell import DO, IAC, ShellClient

        c = ShellClient("x", 1, prompt="#")

        class FakeSock:
            def __init__(self):
                # Deliver `IAC DO 3` split as [IAC], [DO, 3, 'h','i'].
                self.chunks = [bytes([IAC]), bytes([DO, 3]) + b"hi"]
                self.sent = b""

            def recv(self, n):
                return self.chunks.pop(0) if self.chunks else b""

            def settimeout(self, t):
                pass

            def sendall(self, data):
                self.sent += bytes(data)

        c._sock = FakeSock()
        first = c._recv_filtered()
        assert first == b"", "the lone IAC must be carried, not emitted"
        second = c._recv_filtered()
        assert second == b"hi", "continuation is clean text, no stray bytes"
        assert bytes([IAC]) not in second
