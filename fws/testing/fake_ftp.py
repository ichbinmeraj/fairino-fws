"""A minimal FTP server for testing the FtpClient, on a real loopback socket.

Implements enough of RFC 959 for ftplib (USER/PASS auth, PASV data channels,
TYPE, LIST, RETR, STOR, DELE, QUIT). Files live in an in-memory dict keyed by
absolute path; directories are implied by the LIST target.
"""
from __future__ import annotations

import contextlib
import socket
import threading


class FakeFtpServer:
    def __init__(self, *, user: str = "", password: str = "",
                 files: dict[str, bytes] | None = None):
        # "" user means accept anonymous with any/no password.
        self.user = user
        self.password = password
        self.files: dict[str, bytes] = dict(files or {})
        self.uploaded: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.host, self.port = self._sock.getsockname()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> FakeFtpServer:
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
        authed_user = None
        data_listener: socket.socket | None = None

        def reply(code: int, text: str) -> None:
            f.write(f"{code} {text}\r\n".encode())
            f.flush()

        def open_pasv() -> socket.socket:
            ds = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            ds.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ds.bind(("127.0.0.1", 0))
            ds.listen(1)
            _, dport = ds.getsockname()
            p1, p2 = dport >> 8, dport & 0xFF
            reply(227, f"Entering Passive Mode (127,0,0,1,{p1},{p2})")
            return ds

        try:
            reply(220, "fake-ftp ready")
            while not self._stop.is_set():
                line = f.readline()
                if not line:
                    return
                text = line.decode(errors="replace").strip("\r\n")
                cmd, _, arg = text.partition(" ")
                cmd = cmd.upper()

                if cmd == "USER":
                    authed_user = arg
                    reply(331, "need password")
                elif cmd == "PASS":
                    ok = ((self.user == "" ) or
                          (authed_user == self.user and arg == self.password))
                    reply(230 if ok else 530,
                          "logged in" if ok else "login incorrect")
                    if not ok:
                        return
                elif cmd == "TYPE":
                    reply(200, "type set")
                elif cmd == "PASV":
                    data_listener = open_pasv()
                elif cmd in ("LIST", "NLST"):
                    if data_listener is None:
                        reply(425, "use PASV first")
                        continue
                    reply(150, "here comes the listing")
                    dconn, _ = data_listener.accept()
                    body = self._render_list(arg)
                    dconn.sendall(body.encode())
                    dconn.close()
                    data_listener.close()
                    data_listener = None
                    reply(226, "listing done")
                elif cmd == "RETR":
                    if data_listener is None:
                        reply(425, "use PASV first")
                        continue
                    path = self._norm(arg)
                    if path not in self.files:
                        reply(550, "no such file")
                        data_listener.close()
                        data_listener = None
                        continue
                    reply(150, "sending")
                    dconn, _ = data_listener.accept()
                    dconn.sendall(self.files[path])
                    dconn.close()
                    data_listener.close()
                    data_listener = None
                    reply(226, "transfer done")
                elif cmd == "STOR":
                    if data_listener is None:
                        reply(425, "use PASV first")
                        continue
                    path = self._norm(arg)
                    reply(150, "ready for data")
                    dconn, _ = data_listener.accept()
                    chunks = []
                    while True:
                        c = dconn.recv(4096)
                        if not c:
                            break
                        chunks.append(c)
                    dconn.close()
                    data_listener.close()
                    data_listener = None
                    blob = b"".join(chunks)
                    self.files[path] = blob
                    self.uploaded[path] = blob
                    reply(226, "stored")
                elif cmd == "DELE":
                    path = self._norm(arg)
                    if path in self.files:
                        del self.files[path]
                        self.deleted.append(path)
                        reply(250, "deleted")
                    else:
                        reply(550, "no such file")
                elif cmd == "QUIT":
                    reply(221, "bye")
                    return
                else:
                    reply(200, "ok")  # tolerate SYST/FEAT/PWD/OPTS/etc.
        except OSError:
            pass
        finally:
            if data_listener is not None:
                data_listener.close()
            try:
                f.close()
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _norm(arg: str) -> str:
        return arg.strip()

    def _render_list(self, target: str) -> str:
        """Unix `ls -l`-style lines for files under `target`."""
        target = self._norm(target).rstrip("/")
        rows = []
        seen_dirs = set()
        for path, blob in sorted(self.files.items()):
            parent, _, name = path.rpartition("/")
            if parent == target:
                rows.append(
                    f"-rw-r--r-- 1 root root {len(blob)} Feb 20 01:34 {name}")
            elif parent.startswith(target + "/"):
                sub = parent[len(target) + 1:].split("/")[0]
                if sub not in seen_dirs:
                    seen_dirs.add(sub)
                    rows.append(
                        f"drwxr-xr-x 2 root root 4096 Feb 20 01:34 {sub}")
        return "".join(r + "\r\n" for r in rows)
