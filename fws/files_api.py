"""File management for the controller: the file-type matrix and per-file verbs.

`fws/files_wire.py` defines what the firmware can do with a file. On top of the
wire this module adds two things: a rejected Lua upload returns the compiler's
verdict (fetched from the controller log, rate-limited in fws/lua_verdict.py),
and an edit round trip that detects a lost update via `if_match`/412.

There is no controller-side directory listing (GetLuaList is refused; SDK issue
#21). `GET /files/{kind}` returns this gateway's own index of what it uploaded;
a file put there another way is invisible to it but downloads normally by name.
Rename, copy and move do not exist on the wire and are not simulated.
"""
from __future__ import annotations

import base64
import binascii
import contextlib
import json
import pathlib
import re
import threading
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from . import files_wire as wire
from .driver import RobotError
from .files_listing import ControllerListing
from .lua_verdict import LogFetcher, Verdict, find_verdict
from .programs_api import program_index

# A healthy LuaUpLoadUpdate answers in ~0.55 s; a wedged one answers -1 at a
# fixed ~4.09 s (the controller's retry cycle on a dead web socket). A
# threshold anywhere in the gap is safe. Past it, no verdict was logged, so
# there is nothing to fetch.
WEDGE_SECONDS = 3.0

# Control-lock domain per file kind.
LOCK_DOMAIN = {"lua": "program", "open_lua": "program",
               "point_table": "config", "controller_log": None}

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class UploadRequest(BaseModel):
    """Text or base64, never both. Text for source, base64 for binary."""

    content: str | None = Field(
        default=None, description="file contents, for text kinds")
    content_base64: str | None = Field(
        default=None, description="file contents, for binary kinds")
    overwrite: bool = Field(
        default=False,
        description="required to replace a name already in this gateway's "
                    "index. The index is not the controller's directory, so "
                    "this is a weaker check than it looks")
    if_match: str | None = Field(
        default=None,
        description="the md5 you believe you are editing. FWS reads the file "
                    "back first and refuses with 412 if it has changed")
    verify: bool = Field(
        default=False,
        description="read the file back after writing and compare md5. Off "
                    "by default: it doubles traffic on the transfer service "
                    "that wedged in incident 19")


class FileIndex:
    """What this gateway put on the controller, not a directory listing.

    Lua entries live in programs.json (shared with programs_api), so both routes
    manage the same files through one store.
    """

    def __init__(self, path: pathlib.Path):
        self.path = path
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}
        if path.exists():
            try:
                self._items = json.loads(path.read_text())
            except (OSError, ValueError):
                # A corrupt index must not stop startup; the controller holds
                # the real files.
                self._items = {}

    def _save(self) -> None:
        # An index write must never fail a robot operation.
        with contextlib.suppress(OSError):
            self.path.write_text(json.dumps(self._items, indent=2))

    @staticmethod
    def _key(kind: str, name: str) -> str:
        return f"{kind}/{name}"

    # -- the shared Lua store ------------------------------------------------
    #
    # Delegate to the ProgramIndex object, not its file: each index caches
    # entries in memory, so two objects over one path would drift.
    @property
    def _lua(self):
        return program_index(self.path.parent)

    def record(self, kind: str, name: str, **meta: Any) -> None:
        if kind == "lua":
            self._lua.record(name, **meta)
            return
        with self._lock:
            self._items[self._key(kind, name)] = {
                "kind": kind, "name": name, "uploaded_at": time.time(), **meta}
            self._save()

    def forget(self, kind: str, name: str) -> None:
        if kind == "lua":
            self._lua.forget(name)
            return
        with self._lock:
            self._items.pop(self._key(kind, name), None)
            self._save()

    def has(self, kind: str, name: str) -> bool:
        if kind == "lua":
            return any(e["name"] == name for e in self._lua.all())
        with self._lock:
            return self._key(kind, name) in self._items

    def entries(self, kind: str) -> list[dict]:
        if kind == "lua":
            return [{**e, "kind": "lua"} for e in self._lua.all()]
        with self._lock:
            mine = {e["name"]: dict(e) for e in self._items.values()
                    if e.get("kind") == kind}
        return sorted(mine.values(), key=lambda e: e["name"])


def build(get_driver, get_settings, get_control, audit) -> APIRouter:
    """Build the router.

    Created here, not at module level: a module-global router would register
    every route twice if build() were called more than once, and the first
    registration (closed over the first call's driver) would win.
    """
    router = APIRouter(prefix="/api/v1", tags=["files"])
    _indexes: dict[str, FileIndex] = {}
    # Bounded buffer of recent verdicts for GET /files/-/verdicts.
    def _fetch_user_data() -> bytes:
        """The vendor's own backup, used here as a directory listing."""
        from .backup import download_backup
        d = get_driver()
        patient = type(d)(d.ip, timeout=180.0, port=d.port,
                          upload_port=d.upload_port,
                          download_port=d.download_port)
        return download_backup(patient, "userdata", timeout=180.0)["content"]

    listing = ControllerListing(_fetch_user_data)

    _recent: list[dict] = []
    _recent_lock = threading.Lock()
    fetcher = LogFetcher(get_driver)

    def index() -> FileIndex:
        # Resolved per call, not captured at build time, since create_app()
        # rebinds settings.
        key = str(get_settings().server.data_dir)
        if key not in _indexes:
            _indexes[key] = FileIndex(pathlib.Path(key) / "files.json")
        return _indexes[key]

    def _lock(kind_name: str, token: str | None) -> None:
        domain = LOCK_DOMAIN.get(kind_name)
        if domain is None:
            return
        control = get_control()
        if control.held_by(domain) is None:
            return
        ok, reason = control.check(domain, token)
        if not ok:
            raise HTTPException(428 if not token else 423, reason)

    def _kind(kind_name: str) -> wire.Kind:
        try:
            return wire.resolve(kind_name)
        except wire.RefusedFileType as e:
            # 403, not 404: the kind exists and FWS is declining it.
            raise HTTPException(403, str(e)) from e
        except wire.FileError as e:
            raise HTTPException(404, str(e)) from e

    def _safe_name(kind: wire.Kind, name: str) -> str:
        """Reject traversal and anything that is not a plain filename (whitelist)."""
        if kind.fixed_name is not None:
            if name != kind.fixed_name:
                raise HTTPException(
                    404, f"{kind.name} is a single fixed file, "
                         f"{kind.fixed_name!r}")
            return name
        if "/" in name or "\\" in name or ".." in name:
            raise HTTPException(422, "a file name must not contain a path")
        if not SAFE_NAME.match(name):
            raise HTTPException(
                422, "a file name must be ASCII letters, digits, dot, dash "
                     "or underscore, and start with a letter or digit")
        if not name.endswith(kind.extension):
            raise HTTPException(
                422, f"a {kind.name} name must end in {kind.extension}")
        if not name[:-len(kind.extension)]:
            raise HTTPException(422, "the name is only an extension")
        return name

    def _body(kind: wire.Kind, req: UploadRequest) -> bytes:
        if (req.content is None) == (req.content_base64 is None):
            raise HTTPException(
                422, "send exactly one of content or content_base64")
        if req.content is not None:
            if kind.binary:
                raise HTTPException(
                    422, f"{kind.name} is binary; use content_base64")
            return req.content.encode()
        try:
            return base64.b64decode(req.content_base64 or "", validate=True)
        except (binascii.Error, ValueError) as e:
            raise HTTPException(422, f"content_base64 is not valid: {e}") from e

    def _payload(kind: wire.Kind, body: bytes) -> dict[str, Any]:
        if kind.binary:
            return {"content_base64": base64.b64encode(body).decode()}
        # "replace" rather than a 500, so a file with a bad byte is still
        # readable.
        return {"content": body.decode("utf-8", "replace")}

    def _remember(entry: dict) -> None:
        with _recent_lock:
            _recent.append(entry)
            del _recent[:-50]

    def _compiler_verdict(name: str, since: float) -> dict[str, Any]:
        """Look up why the compiler returned -1. Never raises; called
        only after a real rejection."""
        blob, why = fetcher.fetch(covering_since=since)
        if blob is None:
            return {"verdict": None, "looked_up": False, "reason": why}
        try:
            found = find_verdict(blob, name)
        except Exception as e:
            return {"verdict": None, "looked_up": True,
                    "reason": f"the log could not be parsed: {e}"}
        verdict: Verdict | None = found.pop("verdict")
        return {"verdict": verdict.as_dict() if verdict else None,
                "looked_up": True, "reason": why, **found}

    # ------------------------------------------------------------- discovery
    @router.get("/files")
    def file_types():
        """What the controller can do with a file, per kind, with
        evidence. `fileType` is an opcode, not a type; see
        fws/files_wire.py."""
        m = wire.matrix()
        m["listing"] = {
            "available": False,
            "why": ("the controller's own listing, GetLuaList, is refused in "
                    "the driver: the SDK implements it as GetLuaListPrepare "
                    "plus GetLuaNameWithID per file inside uncapped retry "
                    "loops, and it is reported to leave the RPC channel "
                    "wedged until the controller is restarted (SDK issue "
                    "#21)."),
            "instead": ("GET /api/v1/files/{kind} lists what this gateway "
                        "uploaded. A file put there by the teach pendant or "
                        "another client is invisible to it but downloads "
                        "normally by name."),
        }
        m["not_possible"] = {
            "rename": "no rename primitive exists on the wire for any kind",
            "copy": "no copy primitive exists on the wire for any kind",
            "move": "no move primitive, and no directories to move between",
            "list": "see `listing`",
            "delete_point_table": ("FileDelete has no point-table type, and "
                                   "point tables do not use FileDelete at "
                                   "all; they can be uploaded, downloaded "
                                   "and switched, never removed"),
            "size_or_mtime_of_a_file": ("no stat call exists; the only way to "
                                        "learn a file's size is to download "
                                        "it"),
        }
        m["related_endpoints"] = {
            "point_table_switch": "POST /api/v1/points/tables/{name}/switch",
            "backup_bundles": "GET /api/v1/backup/{kind}",
            "lua_firmware_capability": "GET /api/v1/lua/firmware",
        }
        return m

    # Registered before /files/{kind}/{name}, which would otherwise match
    # kind="-".
    @router.get("/files/-/verdicts")
    def verdicts():
        """Recent compiler verdicts and the log-fetch budget, so the
        rate limiting is observable."""
        with _recent_lock:
            recent = list(_recent)
        return {"log_fetch": fetcher.state(), "recent": recent,
                "outcomes": {
                    "success": "compiled",
                    "unknown_function": "no such function on this firmware",
                    "wrong_argument_count": "argument count rejected",
                    "needs_a_taught_point": "point-name lookup failed",
                    "rejected": "rejected for another reason; read error_info",
                }}

    @router.get("/files/{kind}")
    def list_files(kind: str, source: str = "index", refresh: bool = False,
                   include_autosaves: bool = False):
        """List files.

        `source=index` (default) is instant and returns what this gateway
        uploaded; `source=controller` reads the real directory (~4 s, cached)
        from the vendor's user-data backup, which cannot wedge anything. See
        fws/files_listing.py.
        """
        k = _kind(kind)
        if source == "index":
            return {
                "kind": k.name,
                "files": index().entries(k.name),
                "source": "fws-index",
                "complete": False,
                "note": ("What this gateway uploaded. Pass "
                         "?source=controller for the real directory -- this "
                         "index cannot see a file put there by the teach "
                         "pendant, though such a file downloads normally by "
                         "name."),
            }
        if source != "controller":
            raise HTTPException(422, "source must be 'index' or 'controller'")

        entries, meta = listing.get(refresh=refresh)
        if entries is None:
            raise HTTPException(503, {
                "message": "the controller listing could not be fetched",
                **meta})
        mine = {e["name"] for e in index().entries(k.name)}
        files = [e for e in entries if e.kind == k.name]
        hidden = sum(1 for e in files if e.looks_like_autosave)
        if not include_autosaves:
            files = [e for e in files if not e.looks_like_autosave]
        return {
            "kind": k.name,
            "files": [{"name": e.name, "bytes": e.size,
                       "uploaded_by_this_gateway": e.name in mine,
                       "autosave": e.looks_like_autosave,
                       "saved_versions": len(e.versions),
                       "has_taught_points": e.has_taught_points}
                      for e in files],
            "returned": len(files),
            "autosaves_hidden": 0 if include_autosaves else hidden,
            "autosave_note": ("the teach pendant writes a timestamped copy on "
                              "every edit, so most of a real Lua directory is "
                              "version history. Pass ?include_autosaves=true "
                              "to see them."),
            "complete": True,
            **meta,
        }

    @router.get("/files/lua/{name}/versions")
    def program_versions(name: str, refresh: bool = False):
        """Every saved revision of a program.

        The teach pendant keeps history in a directory beside each program
        (`user/force_test/*.lua`). Version content is not reachable over the
        file wire (FileDownload takes a flat name); recover it from
        GET /api/v1/backup/userdata. This route lists what exists.
        """
        k = _kind("lua")
        name = _safe_name(k, name)
        entries, meta = listing.get(refresh=refresh)
        if entries is None:
            raise HTTPException(503, {
                "message": "the controller listing could not be fetched",
                **meta})
        entry = next((e for e in entries
                      if e.kind == "lua" and e.name == name), None)
        if entry is None:
            raise HTTPException(404, {
                "message": f"{name} is not in the controller listing",
                "hint": ("the listing is a snapshot; pass ?refresh=true if the "
                         "program was created recently"),
                **meta})
        return {
            "name": entry.name,
            "bytes": entry.size,
            "has_taught_points": entry.has_taught_points,
            "versions": [{"saved_at": v.saved_at, "bytes": v.size,
                          "archive_path": v.path} for v in entry.versions],
            "count": len(entry.versions),
            "retrieval": ("version CONTENT is not reachable over the file "
                          "wire -- FileDownload takes a flat name and these "
                          "live in a subdirectory. Fetch "
                          "GET /api/v1/backup/userdata and read archive_path."),
            **meta,
        }

    # ------------------------------------------------------------- transfers
    @router.get("/files/{kind}/{name}")
    def download_file(kind: str, name: str):
        """Fetch a file and its md5.

        The md5 is what a client hands back as `if_match` on PUT.
        """
        k = _kind(kind)
        name = _safe_name(k, name)
        if "download" not in k.ops:
            raise HTTPException(
                405, f"the wire has no download verb for {k.name}")
        try:
            out = wire.download(get_driver(), k.name, name)
        except (wire.FileError, RobotError) as e:
            raise HTTPException(404, f"{name}: {e}") from e
        # Repair, not enumeration: this name is now known to exist.
        if not index().has(k.name, name):
            index().record(k.name, name, bytes=out["bytes"], md5=out["md5"],
                           discovered="by download")
        return {"kind": k.name, "name": name, "bytes": out["bytes"],
                "md5": out["md5"], **_payload(k, out["content"])}

    @router.put("/files/{kind}/{name}")
    def upload_file(kind: str, name: str, req: UploadRequest,
                    x_fws_control_token: str | None = Header(default=None)):
        """Upload a file, and for Lua, say why if the compiler refuses it."""
        k = _kind(kind)
        if "upload" not in k.ops:
            raise HTTPException(
                405, f"the wire has no upload verb for {k.name}")
        _lock(k.name, x_fws_control_token)
        name = _safe_name(k, name)
        body = _body(k, req)

        # Before the transfer and the RPC that opens port 20010, so a failure
        # does not leave a half-armed transfer.
        try:
            wire.check_size(k, len(body))
        except wire.FileError as e:
            raise HTTPException(413, str(e)) from e

        if req.if_match is not None:
            # A failed read leaves `current` None, which cannot match, so the
            # write is refused; the message distinguishes a read failure from a
            # missing or changed file.
            current = None
            read_error: str | None = None
            try:
                current = wire.download(get_driver(), k.name, name)["md5"]
            except (wire.FileError, RobotError) as e:
                read_error = f"{type(e).__name__}: {e}"
            if current != req.if_match:
                if read_error:
                    message = (
                        f"FWS could not read {name} to compare against your "
                        f"if_match, so the write was NOT attempted "
                        f"({read_error}). This is not evidence the file is "
                        f"absent or changed -- the READ failed.")
                    advice = ("retry; if it persists, GET the file to see "
                              "whether it is reachable at all")
                elif current is None:
                    message = f"{name} is not on the controller"
                    advice = ("PUT it without if_match to create it")
                else:
                    message = f"{name} is not the version you edited"
                    advice = ("re-download it, merge your change, and PUT "
                              "again with the new md5")
                raise HTTPException(412, {
                    "message": message,
                    "if_match": req.if_match,
                    "current_md5": current,
                    "read_failed": read_error,
                    "advice": advice,
                })
        elif not req.overwrite and index().has(k.name, name):
            raise HTTPException(409, (
                f"{name} is already in this gateway's index; resend with "
                f"overwrite=true. Note the index is not the controller's "
                f"directory, so its absence does not prove the name is free"))

        try:
            info = wire.upload(get_driver(), k.name, name, body)
        except (wire.FileError, RobotError) as e:
            raise HTTPException(502, f"{name}: {e}") from e

        commit = _commit(k, name)
        if req.verify:
            # Read back and compare; off by default. See UploadRequest.
            try:
                back = wire.download(get_driver(), k.name, name)
            except (wire.FileError, RobotError) as e:
                raise HTTPException(
                    502, f"{name} was written but could not be read back: {e}"
                ) from e
            if back["md5"] != info["md5"]:
                raise HTTPException(502, {
                    "message": f"{name} read back with a different md5",
                    "sent_md5": info["md5"], "stored_md5": back["md5"]})
            info["verified_by_readback"] = True

        index().record(k.name, name, bytes=info["bytes"], md5=info["md5"])
        audit("file.upload", kind=k.name, name=name, bytes=info["bytes"],
              md5=info["md5"])
        return {**info, **commit}

    def _commit(k: wire.Kind, name: str) -> dict[str, Any]:
        """Run the kind's commit RPC (for Lua, the compiler) and explain a rejection."""
        if not k.upload_commit:
            return {}
        # Taken before the compile: identifies a wedged validator by elapsed
        # time, and floors the log freshness so a stale archive is not reused.
        started = time.monotonic()
        try:
            rtn = wire.rtn_code(get_driver()._call(k.upload_commit, name))
        except RobotError as e:
            raise HTTPException(
                502, f"{name} was written but {k.upload_commit} failed: {e}"
            ) from e
        elapsed = time.monotonic() - started
        if rtn == 0:
            _remember({"name": name, "at": time.time(), "outcome": "success",
                       "seconds": round(elapsed, 2)})
            return {"compiled": True, "commit": k.upload_commit,
                    "commit_seconds": round(elapsed, 2)}

        if k.upload_commit != "LuaUpLoadUpdate":
            # Only the Lua compiler is known to write a verdict to the log.
            raise HTTPException(422, {
                "message": f"{k.upload_commit}({name}) returned {rtn}",
                "note": ("the file was transferred; the controller declined "
                         "to accept it"),
            })

        if elapsed >= WEDGE_SECONDS:
            # Not a verdict: while the validator is unattached the reply channel
            # is dead, so nothing is written to the log.
            _remember({"name": name, "at": time.time(), "outcome": "wedged",
                       "seconds": round(elapsed, 2)})
            raise HTTPException(503, {
                "message": (f"the controller's Lua validator did not answer "
                            f"{name}: it returned {rtn} after {elapsed:.2f}s"),
                "diagnosis": (
                    "a healthy validation takes ~0.55s and a wedged one "
                    "returns -1 at a fixed ~4.09s, which is the controller "
                    "retrying a dead web-UI socket. It does not self-heal "
                    "and no verdict was written to the log."),
                "recovery": (
                    "reconnecting the teach pendant or web UI may restore the "
                    "reply channel; otherwise the controller needs a restart. "
                    "FWS will not do either for you."),
                "log_fetched": False,
            })

        detail = _compiler_verdict(name, started)
        _remember({"name": name, "at": time.time(),
                   "outcome": (detail["verdict"] or {}).get("outcome",
                                                            "unexplained"),
                   "seconds": round(elapsed, 2),
                   "function": (detail["verdict"] or {}).get("function")})
        audit("file.upload.rejected", kind=k.name, name=name,
              outcome=(detail["verdict"] or {}).get("outcome", "unexplained"))
        raise HTTPException(422, {
            "message": f"the controller's Lua compiler rejected {name}",
            "returned": rtn,
            "file_state": ("the bytes were transferred and are on the "
                           "controller under this name; they are not "
                           "compiled, and if this name held a working "
                           "program it has been overwritten"),
            "verdict_source": "the controller's own log (RbLogDownload)",
            **detail,
        })

    @router.delete("/files/{kind}/{name}")
    def delete_file(kind: str, name: str,
                    x_fws_control_token: str | None = Header(default=None)):
        """Delete a file. Reconciles the index when it has drifted."""
        k = _kind(kind)
        if "delete" not in k.ops:
            raise HTTPException(405, (
                f"the wire has no delete verb for {k.name}, and FWS will not "
                f"fake one"))
        _lock(k.name, x_fws_control_token)
        name = _safe_name(k, name)

        # Never delete the currently loaded program; both delete routes enforce
        # this with a 409.
        guard = "not applicable"
        if k.name == "lua":
            loaded = None
            # Best effort: an unreachable controller must not block delete, but
            # a reachable one that reports this file loaded must stop it.
            # GetLoadedProgram is absent on some firmware (capability
            # `program.loaded`); report when the guard did not run.
            guard = "checked"
            try:
                reply = get_driver()._call("GetLoadedProgram")
                if isinstance(reply, list) and reply[0] == 0:
                    loaded = str(reply[1])
                else:
                    guard = f"NOT CHECKED: GetLoadedProgram returned {reply}"
            except (RobotError, IndexError, TypeError) as e:
                guard = (f"NOT CHECKED: {type(e).__name__}: {e}. The file was "
                         f"deleted without confirming it is not the loaded "
                         f"program.")
            if loaded and loaded.rsplit("/", 1)[-1] == name:
                raise HTTPException(409, (
                    f"{name} is the currently loaded program; load another "
                    f"before deleting it"))

        already_gone = False
        try:
            wire.delete(get_driver(), k.name, name)
        except (wire.FileError, RobotError) as e:
            # 144 is "the LUA file does not exist" -- the state the caller asked
            # for, so reconcile the index instead of failing. Matched as a whole
            # code: "returned 144" is a prefix of "returned 1440".
            if not re.search(r"returned 144\b", str(e)):
                raise HTTPException(502, str(e)) from e
            already_gone = True
        index().forget(k.name, name)
        audit("file.delete", kind=k.name, name=name, already_gone=already_gone,
              loaded_program_guard=guard)
        return {"kind": k.name, "deleted": name,
                "already_absent_on_controller": already_gone,
                "loaded_program_guard": guard}

    return router


__all__ = ["FileIndex", "build"]
