"""FTP client for the controller's file service (port 21).

Built on `ftplib`; every method raises ServiceError. A Lua file written over
FTP bypasses the controller's compile-and-register step, so it produces no
verdict and may be unknown to the controller until a rescan.
"""
from __future__ import annotations

import contextlib
import ftplib
import io
from dataclasses import dataclass

from . import ServiceAuthError, ServiceError, ServiceUnavailable

# User-program directory on the live controller.
DEFAULT_ROOT = "/fruser"


@dataclass(frozen=True)
class FtpEntry:
    name: str
    size: int
    is_dir: bool
    raw: str


def _parse_list_line(line: str) -> FtpEntry | None:
    """Parse one Unix-style `LIST` line; an unrecognised line returns None.

    e.g. `drwxr-xr-x  2 root root 4096 Feb 20 01:34 force_test`
    """
    parts = line.split(maxsplit=8)
    if len(parts) < 9:
        return None
    perms, name = parts[0], parts[8]
    # A symlink line is `l... name -> target`; maxsplit=8 puts "name -> target"
    # in the last field. Keep the link's own name, not the arrow and target.
    if perms.startswith("l") and " -> " in name:
        name = name.split(" -> ", 1)[0]
    if name in (".", ".."):
        return None
    try:
        size = int(parts[4])
    except ValueError:
        size = 0
    return FtpEntry(name=name, size=size, is_dir=perms.startswith("d"),
                    raw=line)


class FtpClient:
    """One FTP session. Context-managed; not held across requests."""

    def __init__(self, host: str, port: int = 21, *, user: str = "",
                 password: str = "", timeout_s: float = 8.0,
                 root: str = DEFAULT_ROOT):
        self.host = host
        self.port = port
        self.user = user or "anonymous"
        self.password = password
        self.timeout_s = timeout_s
        self.root = root
        self._ftp: ftplib.FTP | None = None

    def __enter__(self) -> FtpClient:
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        ftp = ftplib.FTP()
        try:
            ftp.connect(self.host, self.port, timeout=self.timeout_s)
        except OSError as e:
            raise ServiceUnavailable(
                f"FTP {self.host}:{self.port} unreachable: {e}") from e
        except ftplib.all_errors as e:
            # A bad greeting (e.g. `421 Too many connections`) is an error_temp
            # / error_perm, not an OSError -- it must not escape unwrapped.
            ftp.close()
            raise ServiceUnavailable(
                f"FTP {self.host}:{self.port} refused the connection: "
                f"{e}") from e
        try:
            ftp.login(self.user, self.password)
        except ftplib.error_perm as e:
            ftp.close()
            raise ServiceAuthError(f"FTP login rejected: {e}") from e
        except ftplib.all_errors as e:
            ftp.close()
            raise ServiceError(f"FTP login failed: {e}") from e
        self._ftp = ftp

    def close(self) -> None:
        if self._ftp is not None:
            try:
                self._ftp.quit()
            except ftplib.all_errors:
                # QUIT failed (already-dropped connection); force the socket
                # shut regardless. Nothing to do if that fails too.
                with contextlib.suppress(ftplib.all_errors):
                    self._ftp.close()
            finally:
                self._ftp = None

    def _require(self) -> ftplib.FTP:
        if self._ftp is None:
            raise ServiceError("FTP client is not connected")
        return self._ftp

    def _abs(self, path: str | None) -> str:
        if not path:
            return self.root
        if path.startswith("/"):
            return path
        return f"{self.root.rstrip('/')}/{path}"

    def list(self, path: str | None = None) -> list[FtpEntry]:
        """Directory listing."""
        ftp = self._require()
        target = self._abs(path)
        lines: list[str] = []
        try:
            ftp.retrlines(f"LIST {target}", lines.append)
        except ftplib.all_errors as e:
            raise ServiceError(f"FTP LIST {target} failed: {e}") from e
        data_lines = [ln for ln in lines
                      if ln.strip() and not ln.lower().startswith("total ")]
        entries = [e for e in (_parse_list_line(ln) for ln in lines) if e]
        # Lines returned but none parsed means an unexpected format; raise
        # rather than return [], which would read as an empty directory.
        if data_lines and not entries:
            raise ServiceError(
                f"FTP LIST {target} returned {len(data_lines)} line(s) but "
                f"none matched the expected Unix `ls -l` format, so the "
                f"listing could not be read. This is NOT an empty directory. "
                f"First line: {data_lines[0][:80]!r}")
        return sorted(entries, key=lambda e: (not e.is_dir, e.name))

    def download(self, path: str) -> bytes:
        """Fetch a file's bytes."""
        ftp = self._require()
        target = self._abs(path)
        sink = io.BytesIO()
        try:
            ftp.retrbinary(f"RETR {target}", sink.write)
        except ftplib.error_perm as e:
            raise ServiceError(f"FTP cannot read {target}: {e}") from e
        except ftplib.all_errors as e:
            raise ServiceError(f"FTP RETR {target} failed: {e}") from e
        return sink.getvalue()

    def upload(self, path: str, content: bytes) -> int:
        """Write bytes to a file; returns the number of bytes sent."""
        ftp = self._require()
        target = self._abs(path)
        try:
            ftp.storbinary(f"STOR {target}", io.BytesIO(content))
        except ftplib.error_perm as e:
            raise ServiceError(f"FTP cannot write {target}: {e}") from e
        except ftplib.all_errors as e:
            raise ServiceError(f"FTP STOR {target} failed: {e}") from e
        return len(content)

    def delete(self, path: str) -> None:
        ftp = self._require()
        target = self._abs(path)
        try:
            ftp.delete(target)
        except ftplib.all_errors as e:
            raise ServiceError(f"FTP DELE {target} failed: {e}") from e
