"""Program CRUD and execution control.

`GET /programs` lists an FWS-side index of what this gateway uploaded, not the
controller's directory (GetLuaList is quarantined; SDK issue #21 reports it
wedging the RPC channel). Running a program commands unbounded motion, so `run`
requires the motion lock, confirmation and a non-faulted controller.
"""
from __future__ import annotations

import contextlib
import json
import pathlib
import re
import threading
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .driver import RobotError
from .files import TransferError, delete_lua, download_lua, upload_lua
from .pathcheck import validate as validate_path

router = APIRouter(prefix="/api/v1", tags=["programs"])

MAX_PROGRAM_BYTES = 512 * 1024
PROGRAM_STATE = {1: "stopped", 2: "running", 3: "paused"}


class UploadRequest(BaseModel):
    content: str = Field(description="Lua source")
    overwrite: bool = Field(default=False)


class SelectRequest(BaseModel):
    """Choose the program to run, and optionally start it in one call."""

    start: bool = Field(default=False, description="also run it immediately")
    confirm: bool = Field(
        default=False,
        description="required when start=true: a program commands unbounded "
                    "motion")


class RunRequest(BaseModel):
    confirm: bool = Field(
        default=False,
        description="required: a program can command unbounded motion")
    skip_validation: bool = Field(
        default=False,
        description="run without checking the program's motion targets. "
                    "Needed for programs FWS cannot read statically -- point "
                    "names, computed poses, arcs -- and recorded in the audit "
                    "log with the reason the check was skipped")
    validation_note: str = Field(
        default="",
        description="why validation was skipped; recorded in the audit log")


# One ProgramIndex per data directory, shared by every router that manages Lua
# files. Each index caches entries in memory, so two objects over one path
# would drift the moment either writes.
_REGISTRY: dict[str, ProgramIndex] = {}
_REGISTRY_LOCK = threading.Lock()


def program_index(data_dir: str | pathlib.Path) -> ProgramIndex:
    """The one index for `data_dir`. Both programs_api and files_api use it."""
    key = str(data_dir)
    with _REGISTRY_LOCK:
        if key not in _REGISTRY:
            _REGISTRY[key] = ProgramIndex(pathlib.Path(key) / "programs.json")
        return _REGISTRY[key]


class ProgramIndex:
    """What this gateway uploaded, persisted beside the taught points;
    not the controller's directory."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}
        if path.exists():
            try:
                self._items = json.loads(path.read_text())
            except (OSError, ValueError):
                self._items = {}

    def _save(self) -> None:
        # An index write must never fail a robot operation.
        with contextlib.suppress(OSError):
            self.path.write_text(json.dumps(self._items, indent=2))

    def record(self, name: str, **meta: Any) -> None:
        with self._lock:
            self._items[name] = {"name": name, "uploaded_at": time.time(),
                                 **meta}
            self._save()

    def forget(self, name: str) -> None:
        with self._lock:
            self._items.pop(name, None)
            self._save()

    def all(self) -> list[dict]:
        with self._lock:
            return sorted(self._items.values(), key=lambda x: x["name"])


def _upload_failure(name: str, error: Exception) -> HTTPException:
    """A rejected upload, with the compiler's reason if it can be had.

    `LuaUpLoadUpdate` returns only 0 or -1; the reason goes to the controller
    log, which fws/lua_verdict.py fetches (rate-limited) on compiler rejection.
    """
    text = str(error)
    if "LuaUpLoadUpdate" not in text:
        return HTTPException(502, text)
    detail: dict[str, Any] = {
        "message": f"the controller's Lua compiler rejected {name}",
        "returned": -1,
        "file_state": ("the bytes were transferred and are on the controller "
                       "under this name; they are not compiled, and if this "
                       "name held a working program it has been overwritten"),
        "see_also": "GET /api/v1/files/-/verdicts for the log-fetch budget",
    }
    return HTTPException(422, detail)


def build(get_driver, get_settings, get_caps, get_control, audit) -> APIRouter:
    # Resolved per call, not captured at build time, since create_app rebinds
    # settings.
    def index() -> ProgramIndex:
        return program_index(get_settings().server.data_dir)

    def _lock(domain: str, token: str | None) -> None:
        control = get_control()
        if control.held_by(domain) is None:
            return
        ok, reason = control.check(domain, token)
        if not ok:
            raise HTTPException(428 if not token else 423, reason)

    def _ok(result: Any, method: str) -> list:
        if isinstance(result, list):
            if result[0] != 0:
                raise HTTPException(502, f"{method} returned error {result[0]}")
            return result[1:]
        if result != 0:
            raise HTTPException(502, f"{method} returned error {result}")
        return []

    def _safe_name(name: str) -> str:
        """Reject traversal and anything that is not a plain Lua filename."""
        if "/" in name or "\\" in name or ".." in name:
            raise HTTPException(422, "program name must not contain a path")
        if not name.endswith(".lua"):
            raise HTTPException(422, "program name must end in .lua")
        if not name[:-4] or len(name) > 100:
            raise HTTPException(422, "implausible program name")
        return name

    # ------------------------------------------------------------ programs
    @router.get("/programs")
    def list_programs():
        """Programs uploaded through this gateway; not the controller's
        directory (GetLuaList is quarantined)."""
        return {
            "programs": index().all(),
            "source": "fws-index",
            "complete": False,
            "note": ("Programs uploaded through this gateway only. The "
                     "controller's own listing command (GetLuaList) is "
                     "quarantined: it is reported to wedge the RPC channel "
                     "until the controller is restarted."),
        }

    @router.put("/programs/{name}")
    def upload_program(name: str, req: UploadRequest,
                       x_fws_control_token: str | None = Header(default=None)):
        _lock("program", x_fws_control_token)
        name = _safe_name(name)
        body = req.content.encode()
        if len(body) > MAX_PROGRAM_BYTES:
            raise HTTPException(413, f"program exceeds {MAX_PROGRAM_BYTES} bytes")
        if not req.overwrite and any(p["name"] == name for p in index().all()):
            raise HTTPException(409, (
                f"{name} already exists in this gateway's index; resend with "
                f"overwrite=true"))
        try:
            info = upload_lua(get_driver(), name, body)
        except (TransferError, RobotError) as e:
            # Return the compiler's verdict on rejection; see _upload_failure.
            raise _upload_failure(name, e) from e
        index().record(name, bytes=info["bytes"], md5=info["md5"])
        audit("program.upload", name=name, bytes=info["bytes"],
              md5=info["md5"])
        return info

    @router.get("/programs/{name}")
    def download_program(name: str):
        name = _safe_name(name)
        try:
            body = download_lua(get_driver(), name)
        except (TransferError, RobotError) as e:
            raise HTTPException(404, f"{name}: {e}") from e
        return {"name": name, "bytes": len(body),
                "content": body.decode("utf-8", "replace")}

    @router.delete("/programs/{name}")
    def delete_program(name: str,
                       x_fws_control_token: str | None = Header(default=None)):
        _lock("program", x_fws_control_token)
        name = _safe_name(name)
        loaded = None
        with contextlib.suppress(RobotError, HTTPException):
            loaded = _ok(get_driver()._call("GetLoadedProgram"),
                         "GetLoadedProgram")[0]
        if loaded and loaded.rsplit("/", 1)[-1] == name:
            raise HTTPException(409, (
                f"{name} is the currently loaded program; load another "
                f"before deleting it"))
        already_gone = False
        try:
            delete_lua(get_driver(), name)
        except (TransferError, RobotError) as e:
            # Error 144 is "the LUA file does not exist" -- the state the caller
            # asked for, so reconcile the index instead of failing. Matched as a
            # whole code: "returned 144" is a prefix of "returned 1440".
            if not re.search(r"returned 144\b", str(e)):
                raise HTTPException(502, str(e)) from e
            already_gone = True
        index().forget(name)
        audit("program.delete", name=name, already_gone=already_gone)
        return {"deleted": name, "already_absent_on_controller": already_gone}

    @router.post("/programs/{name}/load")
    def load_program(name: str,
                     x_fws_control_token: str | None = Header(default=None)):
        _lock("program", x_fws_control_token)
        name = _safe_name(name)
        _ok(get_driver()._call("ProgramLoad", name), "ProgramLoad")
        audit("program.load", name=name)
        return {"loaded": name}

    @router.post("/programs/{name}/select")
    def select_program(name: str, req: SelectRequest,
                       x_fws_control_token: str | None = Header(default=None)):
        """Load a program and optionally start it, with the same guards
        as run: motion lock, confirmation, non-faulted controller."""
        _lock("program", x_fws_control_token)
        name = _safe_name(name)
        _ok(get_driver()._call("ProgramLoad", name), "ProgramLoad")
        audit("program.select", name=name, start=req.start)
        out = {"selected": name, "started": False}
        if not req.start:
            return out

        _lock("motion", x_fws_control_token)
        if not req.confirm:
            raise HTTPException(400, (
                f"{name} is now selected but NOT started: running a program "
                f"commands motion this gateway does not bound. Clear the "
                f"cell, then POST /execution/run with confirm=true, or "
                f"repeat this call with confirm=true."))
        d = get_driver()
        main, sub = d.error_code()
        if main or sub:
            raise HTTPException(409, (
                f"{name} selected, but the controller is faulted "
                f"(main {main}, sub {sub}); reset errors before running"))
        _ok(d._call("ProgramRun"), "ProgramRun")
        audit("execution.run", program=name, via="select")
        out["started"] = True
        return out

    def _check_path(name: str) -> dict[str, Any]:
        """Solve every literal motion target in a program. Sends no motion."""
        d = get_driver()
        try:
            src = download_lua(d, name).decode("utf-8", "replace")
        except (TransferError, RobotError) as e:
            raise HTTPException(502, (
                f"cannot validate {name}: its source could not be read "
                f"back from the controller ({e})")) from e

        def ik(pose):
            return d.inverse_kin(pose, kind=0, config=-1)

        current = None
        with contextlib.suppress(RobotError):
            current = d._call("GetActualTCPPose", 1)[1:]
        return validate_path(
            src, inverse_kin=ik, joint_limits=d.joint_limits,
            current_pose=current,
            limit_margin_deg=get_settings().limits.limit_margin_deg)

    @router.post("/programs/{name}/validate")
    def validate_program(name: str):
        """Check a program's motion targets without running it.

        Solves each literal motion target backwards through the controller's
        kinematics against its soft limits. Read `unchecked` before trusting
        `safe_to_run`: point names and computed poses cannot be checked.
        """
        name = _safe_name(name)
        report = _check_path(name)
        audit("program.validate", name=name,
              safe=report["safe_to_run"], failed=report["failed"],
              unchecked=report["unchecked"])
        return {"program": name, **report}

    # ----------------------------------------------------------- execution
    @router.get("/execution")
    def execution():
        d = get_driver()
        code = _ok(d._call("GetProgramState"), "GetProgramState")[0]
        out: dict[str, Any] = {"state": PROGRAM_STATE.get(code, "unknown"),
                               "state_code": code}
        if get_caps().has("program.loaded"):
            out["loaded"] = _ok(d._call("GetLoadedProgram"),
                                "GetLoadedProgram")[0]
        if get_caps().has("program.current_line"):
            out["current_line"] = _ok(d._call("GetCurrentLine"),
                                      "GetCurrentLine")[0]
        return out

    @router.post("/execution/run")
    def run(req: RunRequest,
            x_fws_control_token: str | None = Header(default=None)):
        """Start the loaded program.

        A Lua program commands motion directly on the controller: the gateway's
        jog bounds, step limits and IK pre-flight do not apply.
        """
        _lock("motion", x_fws_control_token)
        d = get_driver()
        if not req.confirm:
            raise HTTPException(400, (
                "running a program commands motion the gateway does not "
                "bound -- FWS's jog limits and kinematics pre-flight do not "
                "apply. Clear the cell, then resend with confirm=true"))
        try:
            main, sub = d.error_code()
        except RobotError as e:
            raise HTTPException(502, f"cannot read fault state: {e}") from e
        if main or sub:
            raise HTTPException(409, (
                f"controller is faulted (main {main}, sub {sub}); reset "
                f"errors before running"))
        loaded = None
        if get_caps().has("program.loaded"):
            loaded = _ok(d._call("GetLoadedProgram"), "GetLoadedProgram")[0]
        if not loaded:
            raise HTTPException(409, "no program is loaded")

        # Pre-flight: solve the program's literal motion targets before running.
        report = None
        if req.skip_validation:
            audit("execution.validation_skipped", program=loaded,
                  reason=req.validation_note or "(none given)")
        else:
            report = _check_path(loaded.rsplit("/", 1)[-1])
            if report["failed"]:
                raise HTTPException(409, {
                    "message": (f"{loaded} was NOT started: "
                                f"{report['verdict']}"),
                    "failures": report["failures"],
                    "opening_transit": report["opening_transit"],
                    "override": ("resend with skip_validation=true and a "
                                 "validation_note if you believe the check is "
                                 "wrong; it is recorded in the audit log"),
                })

        _ok(d._call("ProgramRun"), "ProgramRun")
        audit("execution.run", program=loaded,
              validated=None if report is None else report["complete"],
              checked=None if report is None else report["checked"],
              unchecked=None if report is None else report["unchecked"])
        return {"running": True, "program": loaded,
                "validation": report if report is not None
                else {"skipped": True, "note": req.validation_note}}

    def _simple(action: str, method: str, domain: str = "motion"):
        def handler(x_fws_control_token: str | None = Header(default=None)):
            _lock(domain, x_fws_control_token)
            _ok(get_driver()._call(method), method)
            audit(f"execution.{action}")
            return {"action": action, "ok": True}
        return handler

    router.add_api_route("/execution/pause", _simple("pause", "ProgramPause"),
                         methods=["POST"])
    router.add_api_route("/execution/resume", _simple("resume", "ProgramResume"),
                         methods=["POST"])

    @router.post("/execution/stop")
    def stop_program():
        """Stop the running program. Not lockable or confirmable, like
        every stop in FWS."""
        results: dict[str, str] = {}
        for method in ("ProgramStop", "StopMotion"):
            try:
                _ok(get_driver()._call(method), method)
                results[method] = "ok"
            except (RobotError, HTTPException) as e:
                results[method] = f"error: {e}"
        audit("execution.stop", results=results)
        return {"stop_requested": True, "results": results}

    return router
