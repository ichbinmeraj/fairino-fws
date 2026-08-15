"""Named poses: capture, list, edit, and turn into a runnable program.

    POST   /api/v1/poses/{name}/capture   record where the arm is now
    GET    /api/v1/poses                  list them
    GET    /api/v1/poses/{name}           read one
    PUT    /api/v1/poses/{name}           write one explicitly
    POST   /api/v1/poses/{name}/rename    rename
    DELETE /api/v1/poses/{name}           forget one
    POST   /api/v1/poses/program          generate Lua through named poses

These are the gateway's own poses, not the controller's point tables: this
firmware cannot write a single named point into a table (see fws/poses.py).
Storing them here makes taught points ordinary data -- backed up, diffable,
readable by any client, and usable from CI.

The generated program uses LITERAL joint targets, never point-table names.
That is what lets `POST /programs/{name}/validate` solve every target
backwards through the controller's kinematics before anything moves; a
program referring to named points cannot be checked that way.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .access import full_access
from .driver import RobotError
from .poses import Pose, PoseError, PoseStore

PREFIX = "/api/v1"

# MoveJ takes 29 flat arguments on v3.8.5.1 -- PROBED, not read off a manual
# (protocol/lua_firmware.py). Six joints, six Cartesian, tool, wobj, then
# speed/acc/ovl and the trailing block. The controller does not ignore a
# wrong argument count safely, which is exactly why this is generated here
# rather than left to every caller.
MOVEJ_ARITY = 29

# Frames arrive at 10 Hz. A second of silence is already 10 missed frames,
# which is plenty of margin for a busy Pi and far short of "the arm moved".
STALE_AFTER_S = 1.0


def _movej(p: Pose, speed: float) -> str:
    j = ", ".join(f"{v:.3f}" for v in p.joints)
    c = ", ".join(f"{v:.3f}" for v in p.tcp)
    call = (f"MoveJ({j}, {c}, {p.tool}, {p.wobj}, {speed:g}, 100, 100, "
            f"0, 0, 0, 0, -1, 0, 0, 0, 0, 0, 0, 0)")
    # Cheap insurance against a future edit changing the shape silently.
    n = call.count(",") + 1
    if n != MOVEJ_ARITY:                      # pragma: no cover - guard
        raise RuntimeError(f"generated MoveJ has {n} args, expected "
                           f"{MOVEJ_ARITY}")
    return call


class PoseBody(BaseModel):
    joints: list[float] = Field(description="six joint angles, degrees")
    tcp: list[float] = Field(description="six TCP values, mm and degrees")
    tool: int = Field(default=0, ge=0, le=15)
    wobj: int = Field(default=0, ge=0, le=14)
    note: str = Field(default="", max_length=500)
    overwrite: bool = Field(default=False)


class CaptureBody(BaseModel):
    note: str = Field(default="", max_length=500)
    overwrite: bool = Field(default=False)
    tool: int | None = Field(
        default=None,
        description="tool frame this pose belongs to; read from the "
                    "controller when omitted")
    wobj: int | None = Field(default=None)


class RenameBody(BaseModel):
    to: str


class ProgramBody(BaseModel):
    poses: list[str] = Field(min_length=1,
                             description="pose names, in order of travel")
    speed: float = Field(default=20.0, gt=0, le=100,
                         description="percent of configured speed")
    dwell_ms: int = Field(default=0, ge=0, le=60000,
                          description="pause at each pose")
    name: str | None = Field(default=None,
                             description="upload under this name instead of "
                                         "returning the source")


def build(get_driver, get_telemetry, get_store, get_control, audit) -> APIRouter:
    router = APIRouter(prefix=PREFIX, tags=["poses"])

    def _require_config(token: str | None) -> None:
        """Writing a pose is a config-class change: it is the data a later
        motion command will use."""
        if full_access():
            return
        control = get_control()
        lease = control.held_by("config")
        if lease is None:
            return
        ok, reason = control.check("config", token)
        if not ok:
            raise HTTPException(423, reason)

    def _refuse(e: PoseError, status: int = 422):
        raise HTTPException(status, str(e)) from e

    @router.get("/poses")
    def list_poses():
        store: PoseStore = get_store()
        return {
            "poses": [p.as_dict() for p in store.list()],
            "note": ("These are the gateway's poses, not the controller's "
                     "point tables. This firmware cannot write one named "
                     "point into a table; see /api/v1/points/tables for "
                     "whole-file transfers."),
            **store.health(),
        }

    @router.get("/poses/{name}")
    def get_pose(name: str):
        try:
            return get_store().get(name).as_dict()
        except PoseError as e:
            _refuse(e, 404)

    @router.put("/poses/{name}")
    def put_pose(name: str, body: PoseBody,
                 x_fws_control_token: str | None = Header(default=None)):
        """Write a pose explicitly -- from a CAD position, a calculation, or
        another cell."""
        _require_config(x_fws_control_token)
        try:
            pose = Pose(name=name, joints=body.joints, tcp=body.tcp,
                        tool=body.tool, wobj=body.wobj, note=body.note)
            saved = get_store().save(pose, overwrite=body.overwrite)
        except PoseError as e:
            _refuse(e, 409 if "already exists" in str(e) else 422)
        audit("poses.write", name=name, tool=body.tool, wobj=body.wobj)
        return saved.as_dict()

    @router.post("/poses/{name}/capture", status_code=201)
    def capture(name: str, body: CaptureBody,
                x_fws_control_token: str | None = Header(default=None)):
        """Record where the arm is now.

        Joints and TCP come from ONE telemetry frame, so the two
        representations cannot disagree. Telemetry is used rather than the
        RPC position getters because those answer `error 14` while the
        controller is faulted -- which is exactly when someone is most likely
        to be hand-guiding the arm around and marking positions.
        """
        _require_config(x_fws_control_token)
        snap = get_telemetry().snapshot()
        joints, tcp = snap.get("joints"), snap.get("tcp")
        if not joints or not tcp:
            raise HTTPException(503, (
                "no live pose: the telemetry stream has not delivered a "
                "frame, so there is nothing to capture. Check "
                "GET /api/v1/system/health."))
        # A stale frame is WORSE than no frame here. The snapshot keeps the
        # last values after the stream drops, so capturing without checking
        # age records where the arm WAS -- and if it was moved from the
        # pendant meanwhile, a later move to this "taught" point goes
        # somewhere nobody chose. Frames arrive at 10 Hz.
        age = None if not snap.get("ts") else time.time() - snap["ts"]
        if age is None or age > STALE_AFTER_S:
            raise HTTPException(503, (
                f"refusing to capture from a stale pose: the last telemetry "
                f"frame is {'of unknown age' if age is None else f'{age:.1f}s old'}"
                f" and frames arrive at 10 Hz. The arm may have moved since. "
                f"Check GET /api/v1/system/health."))

        tool, wobj = body.tool, body.wobj
        if tool is None or wobj is None:
            # Best effort: a pose taught against the wrong frame is a silent
            # error later, so record what the controller says if it will say.
            try:
                d = get_driver()
                if tool is None:
                    tool = int(d._call("GetActualTCPNum", 0)[1])
                if wobj is None:
                    wobj = int(d._call("GetActualWObjNum", 0)[1])
            except (RobotError, IndexError, TypeError, ValueError):
                tool = 0 if tool is None else tool
                wobj = 0 if wobj is None else wobj

        try:
            pose = Pose(name=name, joints=list(joints), tcp=list(tcp),
                        tool=tool, wobj=wobj, note=body.note)
            saved = get_store().save(pose, overwrite=body.overwrite)
        except PoseError as e:
            _refuse(e, 409 if "already exists" in str(e) else 422)
        audit("poses.capture", name=name, tool=tool, wobj=wobj)
        return saved.as_dict()

    @router.post("/poses/{name}/rename")
    def rename(name: str, body: RenameBody,
               x_fws_control_token: str | None = Header(default=None)):
        _require_config(x_fws_control_token)
        try:
            pose = get_store().rename(name, body.to)
        except PoseError as e:
            _refuse(e, 404 if "no pose" in str(e) else 409)
        audit("poses.rename", name=name, to=body.to)
        return pose.as_dict()

    @router.delete("/poses/{name}")
    def delete_pose(name: str,
                    x_fws_control_token: str | None = Header(default=None)):
        _require_config(x_fws_control_token)
        try:
            get_store().delete(name)
        except PoseError as e:
            _refuse(e, 404)
        audit("poses.delete", name=name)
        return {"deleted": name}

    @router.post("/poses/program")
    def generate(body: ProgramBody) -> dict[str, Any]:
        """Turn a list of named poses into a runnable Lua program.

        Returns the source. It is deliberately NOT uploaded or run here:
        generating a program is safe, and running one moves the arm, so they
        stay separate calls with separate gates. Upload it with
        `PUT /api/v1/programs/{name}`, check it with `.../validate`, then run
        it through `/api/v1/execution/run`.
        """
        store = get_store()
        try:
            poses = [store.get(n) for n in body.poses]
        except PoseError as e:
            _refuse(e, 404)

        lines = [
            "-- generated by FWS from named poses",
            f"-- poses: {', '.join(body.poses)}",
            "-- Literal joint targets, so POST /programs/{name}/validate can "
            "solve",
            "-- every one of them before anything moves.",
            "",
        ]
        for p in poses:
            lines.append(f"-- {p.name}"
                         + (f"  ({p.note})" if p.note else ""))
            lines.append(_movej(p, body.speed))
            if body.dwell_ms:
                lines.append(f"WaitMs({body.dwell_ms})")
        source = "\n".join(lines) + "\n"

        return {
            "source": source,
            "poses": body.poses,
            "lines": len(lines),
            "next": ("PUT /api/v1/programs/{name} to upload, then "
                     "POST /api/v1/programs/{name}/validate before running"),
        }

    return router
