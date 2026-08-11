"""Lua file transfer to and from the controller.

XML-RPC FileUpload/FileDownload makes the controller open the transfer port
(20010 upload, 20011 download), then a raw TCP frame follows:
"/f/b" + %10d total_size + 32-char md5 + bytes + "/b/f". total_size includes
the 46-byte header and 4-byte trailer. fileType 0 = Lua. Upload replies
"SUCCESS" and is finalised with LuaUpLoadUpdate(name).
"""
from __future__ import annotations

import hashlib
import socket
from typing import Any

LUA = 0
HEAD = b"/f/b"
TAIL = b"/b/f"


class TransferError(RuntimeError):
    pass


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _connect(ip: str, port: int, timeout: float,
             tries: int = 16) -> socket.socket:
    """Connect with retry: FileUpload/FileDownload make the controller
    open the transfer port, which does not appear instantly."""
    import time
    last = None
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
    raise TransferError(f"port {port} never opened: {last}")


def upload_lua(driver: Any, name: str, content: bytes,
               timeout: float = 20.0) -> dict[str, Any]:
    """Upload `content` as `name` (e.g. 'fws_program.lua')."""
    if not name.endswith(".lua"):
        raise TransferError("name must end in .lua")
    total = len(content) + 46 + 4

    rtn = driver._call("FileUpload", LUA, name)
    if rtn != 0:
        raise TransferError(f"FileUpload({name}) returned {rtn}")

    s = _connect(driver.ip, getattr(driver, 'upload_port', 20010), timeout)
    try:
        header = HEAD + f"{total:10d}".encode() + _md5(content).encode()
        if len(header) != 46:
            raise TransferError(f"header is {len(header)} bytes, expected 46")
        s.sendall(header)
        s.sendall(content)
        s.sendall(TAIL)
        reply = s.recv(1024)
    finally:
        s.close()

    if not reply.startswith(b"SUCCESS"):
        raise TransferError(f"controller rejected upload: {reply[:64]!r}")

    rtn = driver._call("LuaUpLoadUpdate", name)
    if isinstance(rtn, list):
        rtn = rtn[0]
    if rtn != 0:
        raise TransferError(f"LuaUpLoadUpdate({name}) returned {rtn}")
    return {"name": name, "bytes": len(content), "md5": _md5(content)}


def delete_lua(driver: Any, name: str) -> None:
    """Delete a Lua file. Wire call is FileDelete(fileType, name);
    LuaDelete is a local SDK wrapper with no wire call."""
    rtn = driver._call("FileDelete", LUA, name)
    if isinstance(rtn, list):
        rtn = rtn[0]
    if rtn != 0:
        raise TransferError(f"FileDelete({name}) returned {rtn}")


def download_lua(driver: Any, name: str, timeout: float = 20.0) -> bytes:
    """Fetch a Lua file from the controller."""
    rtn = driver._call("FileDownload", LUA, name)
    if rtn != 0:
        raise TransferError(f"FileDownload({name}) returned {rtn}")

    s = _connect(driver.ip, getattr(driver, 'download_port', 20011), timeout)
    buf = bytearray()
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 46 and buf.endswith(TAIL):
                break
    finally:
        s.close()

    if not buf.startswith(HEAD):
        raise TransferError(f"bad header: {bytes(buf[:16])!r}")
    size = int(buf[4:14].decode())
    md5 = buf[14:46].decode()
    body = bytes(buf[46:size - 4]) if size <= len(buf) else bytes(buf[46:-4])
    got = _md5(body)
    if got != md5:
        raise TransferError(f"md5 mismatch: declared {md5}, computed {got}")
    return body
