"""The controller's whole file surface: what exists, per verb and per type.

fileType is NOT a file type: it is an opcode whose meaning depends on the VERB
it is passed to. The same integer names different things in FileUpload,
FileDownload and FileDelete (read from the SDK's call sites):

    n   FileUpload(n, name)            FileDownload(n, name)     FileDelete(n, name)
    --  -----------------------------  ------------------------  -------------------
     0  Lua program                    Lua program               Lua program
     1  SOFTWARE UPGRADE PACKAGE       rblog.tar.gz  (the log)   --
     2  slave/joint/ctrl/end FIRMWARE  alldatasource.tar.gz      --
     3  --                             fr_user_data.tar.gz       --
     5  joint all-parameter FIRMWARE   --                        --
     6  OS KERNEL image                --                        --
    10  end-axle open-protocol Lua     --                        --
    11  open-protocol device Lua       open-protocol device Lua  one open Lua
    12  --                             --                        ALL open Luas
    20  trajectory J file (.txt)       --                        trajectory J file

Point tables have no fileType: they use PointTableUpload/PointTableDownload, a
different transfer service on the same two ports.

Framing. Ports 20010/20011 carry two framings; picking the wrong one corrupts
a transfer rather than failing it:

    Lua / everything through FileUpload
        "/f/b" + %10d total + 32-char md5   header 46, total = size + 46 + 4, cap 500 MB
    Point table
        "/f/b" + %08d total + 32-char md5   header 44, total = size + 16 + 32, cap 2 MB

On download the vendor's own parsers disagree on header width (generic reads
[4:14]/[14:46], point-table reads [4:12]/[12:44]), so _receive does not guess:
it tries the kind's expected width, falls back to the other, and accepts
neither until the md5 matches.

Only three verbs exist on the wire (upload, download, delete) plus the
point-table switch: no rename, copy, move, mkdir, stat, or directory listing.
"""
from __future__ import annotations

import hashlib
import socket
import time
from dataclasses import dataclass, field
from typing import Any

HEAD = b"/f/b"
TAIL = b"/b/f"

# Header widths, named so a bare "46" never ends up in a slice.
GENERIC_HEADER = 46      # "/f/b" + %10d + md5
POINT_TABLE_HEADER = 44  # "/f/b" + %08d + md5
TRAILER = len(TAIL)

# The wire caps, from the SDK. Both are on the FRAMED total, not the payload.
WIRE_CAP_GENERIC = 500 * 1024 * 1024
WIRE_CAP_POINT_TABLE = 2 * 1024 * 1024

# What this gateway will buffer from a download, whatever the header claims.
# The check runs on the DECLARED size before the body is read, so an absurd
# declared length costs one header, not a gigabyte of RAM.
DOWNLOAD_CAP = 64 * 1024 * 1024


class FileError(RuntimeError):
    """A file operation that failed for a reason the caller can act on."""


class RefusedFileType(FileError):
    """A file kind FWS will not transfer. See SAFETY.md."""


@dataclass(frozen=True)
class Kind:
    """One file kind, and exactly which verbs the wire supports for it."""

    name: str
    describes: str
    extension: str
    # None means "the wire has no such verb for this kind".
    upload_type: int | None = None
    download_type: int | None = None
    delete_type: int | None = None
    # An RPC that must be called BEFORE FileDownload opens the port (two steps:
    # calling only prepare returns 0 and then 20011 never appears).
    download_prepare: str | None = None
    # An RPC that must be called AFTER the bytes land. For Lua this is the
    # compiler.
    upload_commit: str | None = None
    # Point tables do not use FileUpload/FileDownload at all.
    upload_rpc: str = "FileUpload"
    download_rpc: str = "FileDownload"
    header: int = GENERIC_HEADER
    wire_cap: int = WIRE_CAP_GENERIC
    # What FWS allows, which is far below the wire cap on purpose.
    max_bytes: int = 512 * 1024
    binary: bool = False
    fixed_name: str | None = None
    verified_on_hardware: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ops(self) -> list[str]:
        out = []
        if self.upload_type is not None or self.upload_rpc != "FileUpload":
            out.append("upload")
        if self.download_type is not None or self.download_rpc != "FileDownload":
            out.append("download")
        if self.delete_type is not None:
            out.append("delete")
        return out


# A Lua source file large enough to matter is a mistake, not a program.
# 512 KB matches programs_api's limit so the two agree.
MAX_LUA_BYTES = 512 * 1024

KINDS: dict[str, Kind] = {
    "lua": Kind(
        name="lua",
        describes="a Lua program the controller can load and run",
        extension=".lua",
        upload_type=0, download_type=0, delete_type=0,
        upload_commit="LuaUpLoadUpdate",
        max_bytes=MAX_LUA_BYTES,
        verified_on_hardware=True,
        notes=(
            "LuaUpLoadUpdate compiles the program and returns only 0 or -1. "
            "The compiler's real verdict goes to the controller log; FWS "
            "fetches it on rejection.",
        ),
    ),
    "point_table": Kind(
        name="point_table",
        describes="a taught-point database (.db) a Lua program moves between",
        extension=".db",
        upload_rpc="PointTableUpload",
        download_rpc="PointTableDownload",
        header=POINT_TABLE_HEADER,
        wire_cap=WIRE_CAP_POINT_TABLE,
        # The cap is on the framed total, so the body allowance is the cap
        # less the 44-byte header and the 4-byte trailer.
        max_bytes=WIRE_CAP_POINT_TABLE - POINT_TABLE_HEADER - TRAILER,
        binary=True,
        verified_on_hardware=False,
        notes=(
            "No fileType and no FileDelete verb: a point table can be "
            "uploaded, downloaded and switched, never deleted.",
            "Upload framing is 44 bytes with an 8-digit size. The download "
            "width is unresolved; see this module's docstring.",
            "SetPointToDatabase is a silent no-op on v3.8.5.1, so no point "
            "table can be CREATED on this controller.",
        ),
    ),
    "open_lua": Kind(
        name="open_lua",
        describes="an open-protocol Lua driver for a peripheral device",
        extension=".lua",
        upload_type=11, download_type=11, delete_type=11,
        upload_commit="CtrlOpenLuaUpLoadCheck",
        max_bytes=MAX_LUA_BYTES,
        verified_on_hardware=False,
        notes=(
            "The only kind with a complete upload/download/delete triple "
            "besides plain Lua.",
            "Never exercised on hardware by this project; if v3.8.5.1 lacks "
            "CtrlOpenLuaUpLoadCheck the commit step faults -506, which is a "
            "clean failure and not a corrupt one.",
            "Deleting ALL of them at once is fileType 12 and is refused: one "
            "call that removes every peripheral driver, with no listing to "
            "say what was lost, does not belong behind a URL.",
        ),
    ),
    "controller_log": Kind(
        name="controller_log",
        describes="the controller's own log bundle (rblog.tar.gz)",
        extension=".tar.gz",
        download_type=1,
        download_prepare="RbLogDownloadPrepare",
        fixed_name="rblog.tar.gz",
        binary=True,
        max_bytes=0,          # download-only
        verified_on_hardware=True,
        notes=(
            "Download only. Note that fileType 1 in the OTHER direction is "
            "the software-upgrade staging slot, which is refused.",
            "This is the channel that carries the Lua compiler's verdict, "
            "and it is slow. Fetches are rate-limited; see lua_verdict.py.",
        ),
    ),
}

# Kinds present in the matrix for honesty, never transferred. The reason is
# part of the data.
REFUSED_TYPES: dict[str, dict[str, Any]] = {
    "software_upgrade": {
        "verb": "upload", "file_type": 1,
        "reason": "stages a controller software-upgrade package for "
                  "SoftwareUpgrade(). FWS will not become a remote flashing "
                  "tool. SAFETY.md refusal 2.",
        "sdk_evidence": "Robot.py:12727 SoftwareUpgrade -> __FileUpLoad(1)",
    },
    "slave_firmware": {
        "verb": "upload", "file_type": 2,
        "reason": "joint, control-box and end firmware, and slave config, "
                  "written by SlaveFileWrite -- itself on the driver's "
                  "refused list.",
        "sdk_evidence": "Robot.py:15269, 15293, 15315 -> __FileUpLoad(2)",
    },
    "joint_parameters": {
        "verb": "upload", "file_type": 5,
        "reason": "joint all-parameter package for JointAllParamUpgrade(). "
                  "Wrong joint parameters are a mechanical hazard, not a "
                  "software one.",
        "sdk_evidence": "Robot.py:15335 JointAllParamUpgrade -> __FileUpLoad(5)",
    },
    "os_kernel": {
        "verb": "upload", "file_type": 6,
        "reason": "the QNX kernel image for KernelUpgrade(). There is no "
                  "remote recovery on this hardware; see incident 20.",
        "sdk_evidence": "Robot.py:16499 KernelUpgrade -> __FileUpLoad(6)",
    },
    "axle_open_lua": {
        "verb": "upload", "file_type": 10,
        "reason": "the end-axle open-protocol Lua exists only to be followed "
                  "by SetSysServoBootMode + SlaveFileWrite, both refused in "
                  "the driver. Uploading it alone is pointless; uploading it "
                  "and completing the sequence is a firmware write.",
        "sdk_evidence": "Robot.py:13353 AxleLuaUpload -> __FileUpLoad(10)",
    },
    "all_open_lua": {
        "verb": "delete", "file_type": 12,
        "reason": "deletes every open-protocol device driver in one call. "
                  "There is no listing on this controller, so nobody can see "
                  "what it removed or put it back.",
        "sdk_evidence": "Robot.py:18182 AllOpenLuaDelete -> __FileDelete(12)",
    },
    "trajectory_j": {
        "verb": "upload/delete", "file_type": 20,
        "reason": "recorded trajectory files. Upload and delete exist, "
                  "download does not, and nothing in this project has ever "
                  "seen one. A kind FWS can put on the controller but can "
                  "neither read back nor enumerate is a hole, not a feature.",
        "sdk_evidence": "Robot.py:13766, 13782 -> __FileUpLoad/Delete(20)",
    },
}

# Bundles that come out of backup.py, listed so the matrix is complete.
DOWNLOAD_ONLY_BUNDLES = {
    "datasource": {"file_type": 2, "filename": "alldatasource.tar.gz",
                   "endpoint": "GET /api/v1/backup/datasource"},
    "userdata": {"file_type": 3, "filename": "fr_user_data.tar.gz",
                 "endpoint": "GET /api/v1/backup/userdata"},
}


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def rtn_code(result: Any) -> Any:
    """Fairino answers either a bare code or [code, ...]."""
    return result[0] if isinstance(result, list) else result


def _connect(ip: str, port: int, timeout: float, tries: int = 16) -> socket.socket:
    """Connect with retry: the transfer port is not listening until
    the RPC is made and appears ~250 ms later."""
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
    raise FileError(f"transfer port {port} never opened: {last}")


def _header_at(buf: bytes, width: int) -> int | None:
    """Declared total size if buf carries a well-formed header of
    `width` bytes, else None. The md5 field's shape separates the
    two widths when the size digits alone are ambiguous."""
    if len(buf) < width or not buf.startswith(HEAD):
        return None
    digits = width - len(HEAD) - 32
    try:
        declared = int(buf[4:4 + digits].decode("ascii"))
        digest = buf[4 + digits:width].decode("ascii")
    except (ValueError, UnicodeDecodeError):
        return None
    if len(digest) != 32 or any(c not in "0123456789abcdefABCDEF" for c in digest):
        return None
    if declared < width + TRAILER:
        return None
    return declared


def _receive(driver: Any, kind: Kind, timeout: float) -> bytes:
    """Read one framed file from the download port and verify its
    md5. Reads to the DECLARED size (not the trailer, which a
    payload may contain); width falls back kind->other and the md5
    is the arbiter."""
    other = (POINT_TABLE_HEADER if kind.header == GENERIC_HEADER
             else GENERIC_HEADER)
    s = _connect(driver.ip, getattr(driver, "download_port", 20011), timeout)
    buf = bytearray()
    # Every width that parses, not the first one. The two widths are not
    # mutually exclusive: a 44-byte parse of a real 46-byte header reads 8 of
    # the 10 space-padded size characters as its number and the remaining 2
    # (digits, which are valid hex) as the start of its md5 field, succeeding
    # with a wrong, smaller size. So read to the LARGEST candidate and let md5
    # arbitrate below, the only arbiter a coincidence in the digits cannot fool.
    declared_by: dict[int, int] = {}
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
            if not declared_by:
                for candidate in (kind.header, other):
                    got = _header_at(bytes(buf), candidate)
                    if got is not None:
                        declared_by[candidate] = got
                for candidate, got in declared_by.items():
                    if got > DOWNLOAD_CAP:
                        # Enforced on the DECLARED size, before the body
                        # arrives. Checked per candidate: an ambiguous parse
                        # must not smuggle an over-cap transfer through on the
                        # strength of the other reading being small.
                        raise FileError(
                            f"{kind.name}: controller declared {got} bytes at "
                            f"header width {candidate}, over this gateway's "
                            f"{DOWNLOAD_CAP}-byte download limit; refusing to "
                            f"buffer it")
                if not declared_by and len(buf) > 4 * GENERIC_HEADER:
                    raise FileError(
                        f"no transfer header in the first {len(buf)} bytes: "
                        f"{bytes(buf[:16])!r}")
            if declared_by and len(buf) >= max(declared_by.values()):
                break
    finally:
        s.close()

    if not declared_by:
        raise FileError(f"transfer ended before a header arrived ({len(buf)} bytes)")
    # Any width whose declared size actually arrived is worth arbitrating; a
    # width that over-declared is evidence against itself, not a failure.
    arrived = {w: d for w, d in declared_by.items() if len(buf) >= d}
    if not arrived:
        raise FileError(
            f"transfer ended early: {len(buf)} of "
            f"{min(declared_by.values())} declared bytes")
    width = kind.header if kind.header in arrived else next(iter(arrived))

    # Try the parsed width, then the other. A body whose md5 matches at some
    # width is the only thing returned.
    for candidate in (width, other):
        got = _header_at(bytes(buf), candidate)
        if got is None:
            continue
        digits = candidate - len(HEAD) - 32
        digest = buf[4 + digits:candidate].decode("ascii").lower()
        body = bytes(buf[candidate:got - TRAILER])
        if md5(body) == digest:
            return body
    # Never hand back a payload whose checksum failed (a corrupt file that
    # looks fine gets restored).
    raise FileError(
        f"{kind.name}: md5 mismatch at both header widths ({GENERIC_HEADER} "
        f"and {POINT_TABLE_HEADER}); the transfer is corrupt and is being "
        f"discarded")


def resolve(kind_name: str) -> Kind:
    kind = KINDS.get(kind_name)
    if kind is None:
        refused = REFUSED_TYPES.get(kind_name)
        if refused is not None:
            raise RefusedFileType(f"{kind_name}: {refused['reason']}")
        raise FileError(
            f"unknown file kind {kind_name!r}; FWS transfers "
            f"{sorted(KINDS)}")
    return kind


def check_size(kind: Kind, size: int) -> None:
    """Refuse an oversized file BEFORE anything opens a transfer
    port. FileUpload/PointTableUpload make the controller open
    port 20010; failing after that leaves a half-armed transfer."""
    framed = size + kind.header + TRAILER
    if size > kind.max_bytes:
        raise FileError(
            f"{kind.name} is {size} bytes; FWS accepts up to "
            f"{kind.max_bytes}. The wire would allow {kind.wire_cap} framed, "
            f"but nothing this size is a {kind.name} anyone meant to send")
    if framed > kind.wire_cap:
        raise FileError(
            f"{kind.name} framed is {framed} bytes; the controller accepts "
            f"{kind.wire_cap}")


def upload(driver: Any, kind_name: str, name: str, content: bytes,
           timeout: float = 20.0) -> dict[str, Any]:
    """Push one file; returns the transfer result but does NOT
    commit it. The commit step (LuaUpLoadUpdate, CtrlOpenLuaUpLoadCheck)
    is left to the caller."""
    kind = resolve(kind_name)
    if "upload" not in kind.ops:
        raise FileError(f"the wire has no upload verb for {kind.name}")
    check_size(kind, len(content))

    digest = md5(content)
    total = len(content) + kind.header + TRAILER
    if kind.upload_rpc == "FileUpload":
        rtn = rtn_code(driver._call("FileUpload", kind.upload_type, name))
    else:
        rtn = rtn_code(driver._call(kind.upload_rpc, name))
    if rtn != 0:
        raise FileError(f"{kind.upload_rpc}({name}) returned {rtn}")

    digits = kind.header - len(HEAD) - 32
    # %10d right-aligns with spaces; %08d zero-pads. Both are the vendor's,
    # and the width is what the receiving service keys off.
    size_field = (f"{total:{digits}d}" if kind.header == GENERIC_HEADER
                  else f"{total:0{digits}d}")
    header = HEAD + size_field.encode() + digest.encode()
    if len(header) != kind.header:
        raise FileError(
            f"built a {len(header)}-byte header for {kind.name}, expected "
            f"{kind.header}")

    s = _connect(driver.ip, getattr(driver, "upload_port", 20010), timeout)
    try:
        s.sendall(header)
        s.sendall(content)
        s.sendall(TAIL)
        reply = s.recv(1024)
    finally:
        s.close()
    if not reply.startswith(b"SUCCESS"):
        raise FileError(f"controller rejected the transfer: {reply[:64]!r}")
    return {"name": name, "kind": kind.name, "bytes": len(content),
            "md5": digest}


def download(driver: Any, kind_name: str, name: str,
             timeout: float = 30.0) -> dict[str, Any]:
    """Fetch one file, md5-verified."""
    kind = resolve(kind_name)
    if "download" not in kind.ops:
        raise FileError(f"the wire has no download verb for {kind.name}")
    if kind.download_prepare:
        # Two steps: the prepare call builds the archive and returns 0; it does
        # not open the port.
        rtn = rtn_code(driver._call(kind.download_prepare))
        if rtn != 0:
            raise FileError(f"{kind.download_prepare} returned {rtn}")
    if kind.download_rpc == "FileDownload":
        rtn = rtn_code(driver._call("FileDownload", kind.download_type, name))
    else:
        rtn = rtn_code(driver._call(kind.download_rpc, name))
    if rtn != 0:
        # -1 is the controller's "no such file" for both download RPCs
        # (SDK 9350, 9543 both map it to ERR_UPLOAD_FILE_NOT_FOUND).
        raise FileError(f"{kind.download_rpc}({name}) returned {rtn}")
    body = _receive(driver, kind, timeout)
    return {"name": name, "kind": kind.name, "bytes": len(body),
            "md5": md5(body), "content": body}


def delete(driver: Any, kind_name: str, name: str) -> None:
    """Remove one file. Wire call is FileDelete(fileType, name);
    LuaDelete/OpenLuaDelete are local SDK wrappers."""
    kind = resolve(kind_name)
    if "delete" not in kind.ops:
        raise FileError(
            f"the wire has no delete verb for {kind.name}; FWS will not "
            f"pretend otherwise")
    rtn = rtn_code(driver._call("FileDelete", kind.delete_type, name))
    if rtn != 0:
        # 144 is "the LUA file does not exist" -- the caller's desired state.
        raise FileError(f"FileDelete({kind.delete_type}, {name}) returned {rtn}")


def matrix() -> dict[str, Any]:
    """The file-type matrix as data, for GET /files."""
    return {
        "kinds": {
            k.name: {
                "describes": k.describes,
                "extension": k.extension,
                "operations": k.ops,
                "file_type": {
                    "upload": k.upload_type, "download": k.download_type,
                    "delete": k.delete_type,
                },
                "upload_rpc": k.upload_rpc if "upload" in k.ops else None,
                "download_rpc": k.download_rpc if "download" in k.ops else None,
                "download_prepare": k.download_prepare,
                "upload_commit": k.upload_commit,
                "header_bytes": k.header,
                "max_bytes": k.max_bytes or None,
                "wire_cap_bytes": k.wire_cap,
                "binary": k.binary,
                "fixed_name": k.fixed_name,
                "verified_on_hardware": k.verified_on_hardware,
                "notes": list(k.notes),
            }
            for k in KINDS.values()
        },
        "refused": REFUSED_TYPES,
        "download_only_bundles": DOWNLOAD_ONLY_BUNDLES,
    }
