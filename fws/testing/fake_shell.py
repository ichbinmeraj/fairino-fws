"""An in-process telnet server that behaves enough like QNX telnetd, on
a real loopback socket.

Sends an IAC negotiation on connect, presents login/password prompts, checks
one credential pair, then serves a prompt and answers commands from a supplied
table. Unknown commands echo a sh-style 'not found'.
"""
from __future__ import annotations

import contextlib
import socket
import threading
from collections.abc import Callable


class FakeTelnetServer:
    def __init__(self, *, user: str = "root", password: str = "s3cret",
                 prompt: str = "# ",
                 handler: Callable[[str], str] | None = None,
                 negotiate: bool = True, require_login: bool = True):
        self.user = user
        self.password = password
        self.prompt = prompt.encode()
        self.handler = handler or (lambda cmd: "")
        self.negotiate = negotiate
        self.require_login = require_login
        self.commands_seen: list[str] = []
        self._leftover: dict[int, bytes] = {}   # per-connection read remainder
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.host, self.port = self._sock.getsockname()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> FakeTelnetServer:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError):
            self._sock.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,),
                             daemon=True).start()

    def _readline(self, conn: socket.socket) -> str:
        # Return EXACTLY ONE line, keeping any bytes after the first newline
        # for the next call. The client sends the command and its sentinel
        # echo back-to-back; when the two coalesce into one TCP segment (which
        # they do a few % of the time), reading "up to the last newline" fed
        # both to the handler as a single merged command. A real telnetd's
        # line discipline splits on each newline, so the fake must too.
        buf = self._leftover.pop(id(conn), b"")
        while b"\n" not in buf:
            chunk = conn.recv(256)
            if not chunk:
                break
            # Strip any IAC replies the client sends (WONT/DONT); they are
            # three-byte sequences starting 0xFF.
            filtered = bytearray()
            i = 0
            while i < len(chunk):
                if chunk[i] == 255 and i + 2 < len(chunk) + 1:
                    i += 3
                else:
                    filtered.append(chunk[i])
                    i += 1
            buf += bytes(filtered)
        line, sep, rest = buf.partition(b"\n")
        if sep and rest:
            self._leftover[id(conn)] = rest
        return line.decode(errors="replace").strip("\r\n")

    def _session(self, conn: socket.socket) -> None:
        try:
            if self.negotiate:
                # IAC DO SUPPRESS-GO-AHEAD, IAC WILL ECHO -- the two a real
                # telnetd usually opens with. The client must refuse both.
                conn.sendall(bytes([255, 253, 3, 255, 251, 1]))
            if self.require_login:
                conn.sendall(b"\r\nQNX Neutrino\r\nlogin: ")
                user = self._readline(conn)
                conn.sendall(b"Password: ")
                pw = self._readline(conn)
                if user != self.user or pw != self.password:
                    conn.sendall(b"\r\nLogin incorrect\r\nlogin: ")
                    conn.close()
                    return
            conn.sendall(b"\r\n" + self.prompt)
            while not self._stop.is_set():
                cmd = self._readline(conn)
                if not cmd:
                    if self._peer_gone(conn):
                        return
                    continue
                self.commands_seen.append(cmd)
                if cmd in ("exit", "logout"):
                    conn.close()
                    return
                out = self.handler(cmd)
                # Echo the command as a terminal would, then output, then a
                # fresh prompt -- exactly what the client's framing must peel.
                reply = cmd.encode() + b"\r\n"
                if out:
                    reply += out.encode() + b"\r\n"
                reply += self.prompt
                conn.sendall(reply)
        except OSError:
            pass
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    @staticmethod
    def _peer_gone(conn: socket.socket) -> bool:
        conn.setblocking(False)
        try:
            return conn.recv(1, socket.MSG_PEEK) == b""
        except (BlockingIOError, OSError):
            return False
        finally:
            conn.setblocking(True)


def qnx_like_handler(table: dict[str, str] | None = None) -> Callable[[str], str]:
    """A command handler with a few QNX-ish canned answers, plus a table."""
    table = table or {}

    def handle(cmd: str) -> str:
        if cmd in table:
            return table[cmd]
        head = cmd.split()[0] if cmd.split() else ""
        if head == "echo":
            return cmd[len("echo"):].strip()
        if head == "pidin":
            return ("     pid tid name               prio STATE\n"
                    "       1   1 proc/boot/procnto    0f READY\n"
                    "  245801   1 bin/rcheck           10r RECEIVE")
        if head in ("slay", "kill", "on"):
            return ""   # succeeds silently
        if head == "uname":
            return "QNX localhost 8.0.0 ..."
        return f"sh: {head}: command not found"

    return handle
