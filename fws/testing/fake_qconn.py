"""A minimal QNX qconn agent for tests: sends the QCONN banner, answers
`service <name>` with OK, and echoes other lines with a canned reply."""
from __future__ import annotations

import contextlib
import socket
import threading


class FakeQconnAgent:
    def __init__(self, *, send_banner: bool = True,
                 sinfo: str = "QNX localhost 8.0.0"):
        self.send_banner = send_banner
        self.sinfo = sinfo
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.host, self.port = self._sock.getsockname()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> FakeQconnAgent:
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

    def _session(self, conn: socket.socket) -> None:
        f = conn.makefile("rwb")
        try:
            if self.send_banner:
                f.write(b"QCONN\r\n")
                f.flush()
            while not self._stop.is_set():
                line = f.readline()
                if not line:
                    return
                text = line.decode(errors="replace").strip()
                if text.startswith("service"):
                    f.write(b"OK\r\n")
                elif text == "info":
                    f.write(self.sinfo.encode() + b"\r\n")
                else:
                    f.write(b"OK\r\n")
                f.flush()
        except OSError:
            pass
        finally:
            try:
                f.close()
                conn.close()
            except OSError:
                pass
