"""Conservative client for the QNX qconn target agent (port 8000).

Unauthenticated, root-capable agent open by default on the controller. This
is a liveness/information channel: connect, read the `QCONN` banner, select a
service, exchange text lines. It does not implement process control.
Everything raises ServiceError.
"""
from __future__ import annotations

import socket
import time

from . import ServiceError, ServiceTimeout, ServiceUnavailable

BANNER = b"QCONN"


class QconnClient:
    def __init__(self, host: str, port: int = 8000, *, timeout_s: float = 8.0):
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self._sock: socket.socket | None = None

    def __enter__(self) -> QconnClient:
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        try:
            self._sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout_s)
        except OSError as e:
            raise ServiceUnavailable(
                f"qconn {self.host}:{self.port} unreachable: {e}") from e
        self._sock.settimeout(self.timeout_s)
        banner = self._read_line()
        if BANNER not in banner:
            raise ServiceError(
                f"qconn did not present its banner; got {banner!r}. This may "
                f"not be a qconn agent.")

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _read_line(self) -> bytes:
        assert self._sock is not None
        buf = b""
        deadline = time.monotonic() + self.timeout_s
        while b"\n" not in buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ServiceTimeout(
                    f"qconn read timed out; got {buf[-80:]!r}")
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(1024)
            except TimeoutError as e:
                raise ServiceTimeout(
                    f"qconn read timed out; got {buf[-80:]!r}") from e
            except OSError as e:
                # Reset mid-read: wrap in the package error family.
                raise ServiceUnavailable(
                    f"qconn read failed: {type(e).__name__}: {e}") from e
            if not chunk:
                if buf:
                    break
                raise ServiceUnavailable("qconn closed the connection")
            buf += chunk
        return buf

    def _send_line(self, text: str) -> None:
        assert self._sock is not None
        try:
            self._sock.sendall(text.encode() + b"\r\n")
        except OSError as e:
            raise ServiceUnavailable(f"qconn send failed: {e}") from e

    def select_service(self, name: str) -> str:
        """Select a qconn service (e.g. 'sinfo'). Returns the agent's reply
        line. An 'OK' means the service loaded."""
        self._send_line(f"service {name}")
        return self._read_line().decode(errors="replace").strip()

    def command(self, text: str) -> str:
        """Send one raw line to the selected service and return the reply line."""
        self._send_line(text)
        return self._read_line().decode(errors="replace").strip()


def liveness(host: str, port: int = 8000, *, timeout_s: float = 5.0) -> dict:
    """Prove the qconn agent is alive and reachable; returns handshake info."""
    client = QconnClient(host, port, timeout_s=timeout_s)
    started = time.monotonic()
    with client:
        # connect() already validated the banner; reaching here means alive.
        return {
            "reachable": True,
            "agent": "qconn",
            "host": host,
            "port": port,
            "handshake_s": round(time.monotonic() - started, 3),
        }
