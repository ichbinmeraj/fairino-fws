"""Minimal telnet client for the controller's root shell (port 23).

Uses raw sockets (telnetlib was removed in Python 3.13). Implements only IAC
option negotiation, refusing all options; output is framed by prompt. Login,
password and shell prompts are matched by substring and configurable
(defaults follow QNX convention: `login:`, `Password:`, `#`).
"""
from __future__ import annotations

import socket
import time

from . import ServiceAuthError, ServiceTimeout, ServiceUnavailable

# Telnet control bytes (RFC 854). Only what negotiation needs.
IAC = 255   # interpret as command
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250    # subnegotiation begin
SE = 240    # subnegotiation end


class ShellResult:
    """The outcome of one command."""

    def __init__(self, command: str, output: str, duration_s: float):
        self.command = command
        self.output = output
        self.duration_s = duration_s

    def as_dict(self) -> dict:
        return {"command": self.command, "output": self.output,
                "duration_s": round(self.duration_s, 3)}


class ShellClient:
    """One telnet login, commands, then close; not persistent."""

    def __init__(self, host: str, port: int = 23, *, user: str = "root",
                 password: str = "", prompt: str = "#",
                 login_prompt: str = "login:", password_prompt: str = "assword",
                 connect_timeout_s: float = 8.0,
                 command_timeout_s: float = 20.0,
                 newline: bytes = b"\n", initial_quiet_s: float = 2.0):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.prompt = prompt.encode()
        self.login_prompt = login_prompt.lower().encode()
        self.password_prompt = password_prompt.lower().encode()
        self.connect_timeout_s = connect_timeout_s
        self.command_timeout_s = command_timeout_s
        self.newline = newline
        self.initial_quiet_s = initial_quiet_s
        self._sock: socket.socket | None = None
        self._buf = b""
        # Incomplete IAC/SB sequence carried from one recv() to the next, so a
        # telnet command split across a TCP segment boundary is not dropped or
        # emitted as literal output.
        self._pending = b""

    # -- connection lifecycle -------------------------------------------
    def __enter__(self) -> ShellClient:
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        try:
            self._sock = socket.create_connection(
                (self.host, self.port), timeout=self.connect_timeout_s)
        except OSError as e:
            raise ServiceUnavailable(
                f"telnet {self.host}:{self.port} unreachable: {e}") from e
        self._sock.settimeout(self.connect_timeout_s)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    # -- the telnet plumbing --------------------------------------------
    def _recv_filtered(self) -> bytes:
        """One recv with IAC negotiation answered and stripped.

        Non-command bytes pass through; `IAC DO x` -> `IAC WONT x`,
        `IAC WILL x` -> `IAC DONT x` (all options refused).
        """
        assert self._sock is not None
        try:
            chunk = self._sock.recv(4096)
        except TimeoutError:
            raise  # _read_until turns this into ServiceTimeout
        except OSError as e:
            # Reset / broken pipe mid-read: wrap in the package error family.
            raise ServiceUnavailable(
                f"telnet read failed: {type(e).__name__}: {e}") from e
        if not chunk:
            raise ServiceUnavailable("telnet connection closed by controller")
        # Prepend any partial IAC/SB sequence carried from the last recv.
        raw = self._pending + chunk
        self._pending = b""
        out = bytearray()
        i = 0
        replies = bytearray()
        while i < len(raw):
            b = raw[i]
            if b != IAC:
                out.append(b)
                i += 1
                continue
            if i + 1 >= len(raw):
                self._pending = raw[i:]  # dangling IAC -> carry, do not drop
                break
            cmd = raw[i + 1]
            if cmd in (DO, DONT, WILL, WONT):
                if i + 2 >= len(raw):
                    self._pending = raw[i:]  # option byte not here yet -> carry
                    break
                opt = raw[i + 2]
                # Refuse everything: DO->WONT, WILL->DONT.
                if cmd == DO:
                    replies += bytes([IAC, WONT, opt])
                elif cmd == WILL:
                    replies += bytes([IAC, DONT, opt])
                # DONT/WONT need no reply.
                i += 3
            elif cmd == SB:
                # Skip a subnegotiation block up to IAC SE.
                j = raw.find(bytes([IAC, SE]), i + 2)
                if j < 0:
                    self._pending = raw[i:]  # unterminated SB -> carry
                    break
                i = j + 2
            elif cmd == IAC:
                out.append(IAC)  # escaped 0xFF -> literal
                i += 2
            else:
                i += 2  # two-byte command, ignored
        if replies:
            try:
                self._sock.sendall(bytes(replies))
            except OSError as e:
                raise ServiceUnavailable(
                    f"telnet negotiation failed: {e}") from e
        return bytes(out)

    def _read_until(self, needles: tuple[bytes, ...], timeout_s: float) -> bytes:
        """Accumulate filtered bytes until one of `needles` appears, or time
        out. Case-insensitive on the accumulated tail."""
        assert self._sock is not None
        deadline = time.monotonic() + timeout_s
        while True:
            low = self._buf.lower()
            for n in needles:
                idx = low.find(n)
                if idx >= 0:
                    end = idx + len(n)
                    consumed, self._buf = self._buf[:end], self._buf[end:]
                    return consumed
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ServiceTimeout(
                    f"timed out after {timeout_s:.0f}s waiting for "
                    f"{[n.decode(errors='replace') for n in needles]}; "
                    f"got {self._buf[-120:]!r}")
            self._sock.settimeout(remaining)
            try:
                self._buf += self._recv_filtered()
            except TimeoutError as e:
                # socket.timeout is TimeoutError in 3.10+; convert to
                # ServiceTimeout, reporting what was awaited.
                raise ServiceTimeout(
                    f"timed out after {timeout_s:.0f}s waiting for "
                    f"{[n.decode(errors='replace') for n in needles]}; "
                    f"got {self._buf[-120:]!r}") from e

    def _send_line(self, text: str) -> None:
        assert self._sock is not None
        try:
            self._sock.sendall(text.encode() + self.newline)
        except OSError as e:
            raise ServiceUnavailable(f"telnet send failed: {e}") from e

    # -- the useful surface ---------------------------------------------
    def login(self) -> None:
        """Reach a shell prompt. Raises ServiceAuthError if credentials are
        rejected.

        Some QNX telnetd stay silent until the client sends a byte, so the
        first read uses a short window and, on silence, one CRLF nudge is sent
        before waiting the full timeout. A server that greets immediately is
        never nudged.
        """
        try:
            got = self._read_until(
                (self.login_prompt, self.prompt),
                min(self.initial_quiet_s, self.connect_timeout_s))
        except ServiceTimeout:
            # Silent server: nudge with CRLF and wait the full window. Wrap a
            # send failure on the nudge as ServiceError, not a bare OSError.
            assert self._sock is not None
            try:
                self._sock.sendall(b"\r\n")
            except OSError as e:
                raise ServiceUnavailable(
                    f"telnet nudge failed: {e}") from e
            got = self._read_until((self.login_prompt, self.prompt),
                                   self.connect_timeout_s)
        if self.login_prompt not in got.lower():
            # A shell prompt appeared with no login prompt: no login required.
            return
        self._send_line(self.user)
        got = self._read_until((self.password_prompt, self.prompt),
                               self.connect_timeout_s)
        if self.password_prompt not in got.lower():
            # Username alone reached a prompt (no password set). Done.
            return
        self._send_line(self.password)
        # Either a shell prompt (success) or the login prompt again (rejected).
        got = self._read_until((self.prompt, self.login_prompt),
                               self.connect_timeout_s)
        if self.login_prompt in got.lower():
            raise ServiceAuthError("controller rejected the shell credentials")

    #: A token improbable in real output, used to frame command output.
    _SENTINEL = "__fws_end_r7q3z__"

    def run(self, command: str) -> ShellResult:
        """Run one command and return its output.

        Output is framed by a sentinel echoed after the command, not by the
        prompt char, so a prompt char (`#`) inside the output does not
        truncate it.
        """
        started = time.monotonic()
        self._buf = b""  # discard the banner/prompt already consumed
        self._send_line(command)
        self._send_line(f"echo {self._SENTINEL}")
        raw = self._read_until((self._SENTINEL.encode(),),
                               self.command_timeout_s)
        text = raw.decode(errors="replace")
        lines = text.splitlines()
        # First line is the echo of the command; drop it.
        if lines and command.strip() in lines[0]:
            lines = lines[1:]
        # The read stopped at the marker's echo line (which contains the
        # sentinel and usually the prompt); drop it and anything after.
        for k, ln in enumerate(lines):
            if self._SENTINEL in ln:
                lines = lines[:k]
                break
        # Drop a trailing bare-prompt line if one remains.
        while lines and (self.prompt.decode() in lines[-1]
                         and len(lines[-1].strip()) <= len(self.prompt) + 2):
            lines.pop()
        output = "\n".join(lines).strip("\r\n")
        return ShellResult(command, output, time.monotonic() - started)


def run_command(host: str, command: str, *, port: int = 23, user: str = "root",
                password: str = "", prompt: str = "#",
                connect_timeout_s: float = 8.0,
                command_timeout_s: float = 20.0) -> ShellResult:
    """Connect, log in, run one command, close."""
    client = ShellClient(host, port, user=user, password=password,
                         prompt=prompt, connect_timeout_s=connect_timeout_s,
                         command_timeout_s=command_timeout_s)
    with client:
        client.login()
        return client.run(command)
