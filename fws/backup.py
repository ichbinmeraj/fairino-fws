"""Controller backup and restore: copy the controller's configuration, and
move a point table between machines.

Lua and point-table transfers use DIFFERENT headers; reusing one for the other
corrupts the transfer:

    Lua         "/f/b" + %10d size + 32-char md5  = 46 bytes; cap 500 MB
    PointTable  "/f/b" + %08d size + 32-char md5  = 44 bytes; cap   2 MB

Downloads are keyed by an integer file type; this module names its kinds and
never exposes the integer, since a generic PUT /files/{type}/{name} would be a
remote flashing endpoint:

    0  Lua program          (fws/files.py)
    1  software upgrade on upload; the controller log on download (not the
       point table; point tables use PointTableUpload/Download RPCs)
    2  all data sources     alldatasource.tar.gz
    3  user data package    fr_user_data.tar.gz
    5  joint parameters     REFUSED -- firmware
    6  OS kernel            REFUSED -- firmware
"""
from __future__ import annotations

import hashlib
import socket
from typing import Any

HEAD = b"/f/b"
TAIL = b"/b/f"

# Kind -> (file_type, download_prepare_rpc, filename, upload_rpc)
BACKUP_KINDS: dict[str, dict[str, Any]] = {
    "datasource": {
        "file_type": 2,
        "prepare": "AllDataSourceDownloadPrepare",
        "filename": "alldatasource.tar.gz",
        "describes": "all configured data sources",
    },
    "userdata": {
        "file_type": 3,
        "prepare": "DataPackageDownloadPrepare",
        "filename": "fr_user_data.tar.gz",
        "describes": "the user data package",
    },
}

# There is no fileType for a point table; point tables use the
# PointTableUpload/Download RPCs, not FileUpload with a fileType.
POINT_TABLE_MAX = 2 * 1024 * 1024


class BackupError(RuntimeError):
    pass


class NotOffered(BackupError):
    """The controller declined to start the transfer (distinct from a transfer
    that began and then failed)."""


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _connect(ip: str, port: int, timeout: float = 20.0,
             tries: int = 16) -> socket.socket:
    """Transfer ports open on demand, ~250 ms after the RPC. Retry."""
    import time
    last: Exception | None = None
    for _ in range(tries):
        s = socket.socket()
        s.settimeout(timeout)
        try:
            s.connect((ip, port))
            return s
        except OSError as e:
            last = e
            s.close()
            time.sleep(0.25)
    raise BackupError(f"port {port} never opened: {last}")


def _receive(driver: Any, timeout: float) -> bytes:
    """Read one framed file from the download port and verify its md5."""
    s = _connect(driver.ip, getattr(driver, "download_port", 20011), timeout)
    buf = bytearray()
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 46 and buf.endswith(TAIL):
                break
    finally:
        s.close()

    if not buf.startswith(HEAD):
        raise BackupError(f"bad transfer header: {bytes(buf[:16])!r}")
    declared = int(buf[4:14].decode())
    md5 = buf[14:46].decode()
    body = bytes(buf[46:declared - 4]) if declared <= len(buf) \
        else bytes(buf[46:-4])
    got = _md5(body)
    if got != md5:
        # Never hand back a payload whose checksum failed (a corrupt backup
        # that looks fine gets restored).
        raise BackupError(f"md5 mismatch: declared {md5}, computed {got}")
    return body


def download_backup(driver: Any, kind: str, timeout: float = 60.0) -> dict:
    """Fetch a controller backup bundle. AllDataSourceDownloadPrepare can take
    ~14 s to build its archive, so pass a longer RPC timeout."""
    spec = BACKUP_KINDS.get(kind)
    if spec is None:
        raise BackupError(f"unknown backup kind {kind!r}; "
                          f"choose from {sorted(BACKUP_KINDS)}")
    # Two steps: the prepare call builds the archive; a separate
    # FileDownload(fileType, name) actually opens the transfer port. Calling
    # only prepare returns 0 and then port 20011 never appears.
    rtn = driver._call(spec["prepare"])
    if isinstance(rtn, list):
        rtn = rtn[0]
    if rtn != 0:
        raise BackupError(f"{spec['prepare']} returned {rtn}")

    rtn = driver._call("FileDownload", spec["file_type"], spec["filename"])
    if isinstance(rtn, list):
        rtn = rtn[0]
    if rtn != 0:
        raise BackupError(
            f"FileDownload({spec['file_type']}, {spec['filename']}) "
            f"returned {rtn}")
    body = _receive(driver, timeout)
    return {"kind": kind, "filename": spec["filename"],
            "bytes": len(body), "md5": _md5(body), "content": body}


def download_point_table(driver: Any, name: str,
                         timeout: float = 30.0) -> dict:
    """Fetch a point table (.db).

    Distinguishes two failure classes: the RPC declines and the transfer never
    started (NotOffered, usually the table is absent), versus the RPC succeeds
    but the transfer fails (BackupError, the table exists but no intact copy
    was obtained). Reporting the second as "not found" would invite recreating
    a table that exists, destroying its taught positions.
    """
    rtn = driver._call("PointTableDownload", name)
    if isinstance(rtn, list):
        rtn = rtn[0]
    if rtn != 0:
        raise NotOffered(
            f"PointTableDownload({name}) returned {rtn}: the controller "
            f"declined to send this table. FWS reports the code verbatim -- "
            f"which code means 'no such table' on this firmware is not "
            f"documented by the vendor and has not been measured.")
    body = _receive(driver, timeout)
    return {"name": name, "bytes": len(body), "md5": _md5(body),
            "content": body}


def upload_point_table(driver: Any, name: str, content: bytes,
                       timeout: float = 30.0) -> dict:
    """Send a point table to the controller. Header is 44 bytes (8-digit size),
    not the Lua path's 46 (10-digit); cap 2 MB."""
    if not name.endswith(".db"):
        raise BackupError("point table name must end in .db")
    total = len(content) + 16 + 32
    if total > POINT_TABLE_MAX:
        raise BackupError(
            f"point table is {total} bytes; the controller accepts "
            f"{POINT_TABLE_MAX} ({POINT_TABLE_MAX // 1024 // 1024} MB)")

    rtn = driver._call("PointTableUpload", name)
    if isinstance(rtn, list):
        rtn = rtn[0]
    if rtn != 0:
        raise BackupError(f"PointTableUpload({name}) returned {rtn}")

    s = _connect(driver.ip, getattr(driver, "upload_port", 20010), timeout)
    try:
        header = HEAD + f"{total:08d}".encode() + _md5(content).encode()
        if len(header) != 44:
            raise BackupError(
                f"point-table header is {len(header)} bytes, expected 44")
        s.sendall(header)
        s.sendall(content)
        s.sendall(TAIL)
        reply = s.recv(1024)
    finally:
        s.close()

    if not reply.startswith(b"SUCCESS"):
        raise BackupError(f"controller rejected the point table: {reply[:64]!r}")
    return {"name": name, "bytes": len(content), "md5": _md5(content)}
