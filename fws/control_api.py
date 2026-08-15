"""Control-layer API: identity, state, I/O, frames, payload, execution.

Every write goes through the control lock; routes that depend on a firmware
feature check the capability map first and return 501 if it is absent.
"""
from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .access import full_access
from .driver import RobotError
from .protocol.error_codes import ERROR_CODES, describe

router = APIRouter(prefix="/api/v1", tags=["control-layer"])

# Program state as the controller reports it.
PROGRAM_STATE = {1: "stopped", 2: "running", 3: "paused"}


class DigitalOutputRequest(BaseModel):
    value: int = Field(ge=0, le=1)
    confirm: bool = Field(
        default=False,
        description="required: an output can actuate a gripper or a tool")


class AnalogOutputRequest(BaseModel):
    value: float = Field(ge=0, le=100, description="percent")
    confirm: bool = Field(default=False)


class PayloadRequest(BaseModel):
    mass_kg: float = Field(ge=0, le=50)
    load_num: int = Field(default=0, ge=0, le=14,
                          description="payload slot; 0 is the default")
    confirm: bool = Field(default=False)


class ToolFrameRequest(BaseModel):
    """Define a tool (TCP) frame.

    `offset` is the tool centre point relative to the FLANGE centre, in
    mm and degrees: [x, y, z, rx, ry, rz].
    """

    offset: list[float] = Field(min_length=6, max_length=6)
    frame_type: int = Field(default=0, ge=0, le=1,
                            description="0 = tool frame, 1 = sensor frame")
    install: int = Field(default=0, ge=0, le=1,
                         description="0 = on the robot end, 1 = external")
    tool_id: int = Field(default=0, ge=0, le=15)
    load_num: int = Field(default=0, ge=0, le=14)
    confirm: bool = Field(default=False)


class WorkFrameRequest(BaseModel):
    """Define a work object frame, relative to `ref_frame`."""

    offset: list[float] = Field(min_length=6, max_length=6)
    ref_frame: int = Field(default=0, ge=0, le=14)
    confirm: bool = Field(default=False)


class SpeedRequest(BaseModel):
    percent: float = Field(gt=0, le=100)


def build(get_driver, get_settings, get_caps, get_control, audit) -> APIRouter:
    """Resolve dependencies at call time so create_app can rebind them."""

    def _lock(domain: str, token: str | None) -> None:
        control = get_control()
        lease = control.held_by(domain)
        if lease is None:
            return
        ok, reason = control.check(domain, token)
        if ok:
            return
        raise HTTPException(428 if not token else 423, reason)

    def _cap(feature: str) -> None:
        try:
            get_caps().require(feature)
        except RobotError as e:
            raise HTTPException(501, str(e)) from e

    def _ok(result: Any, method: str) -> list:
        if isinstance(result, list):
            if result[0] != 0:
                raise HTTPException(502, f"{method} returned error {result[0]}")
            return result[1:]
        if result != 0:
            raise HTTPException(502, f"{method} returned error {result}")
        return []

    # ------------------------------------------------------------- errors
    @router.get("/errors")
    def current_errors():
        """The controller's current fault, decoded to code, meaning and
        what to check."""
        d = get_driver()
        try:
            main, sub = d.error_code()
        except RobotError as e:
            raise HTTPException(503, str(e)) from e
        return {
            "faulted": bool(main or sub),
            "main": describe(main) if main else None,
            "sub": describe(sub) if sub else None,
            "raw": {"main": main, "sub": sub},
        }

    @router.get("/errors/codes")
    def list_error_codes(q: str | None = None, limit: int = 300):
        """The whole decoded table, searchable."""
        items = [describe(c) for c in sorted(ERROR_CODES)]
        if q:
            needle = q.lower()
            items = [i for i in items
                     if needle in (i.get("description") or "").lower()
                     or needle in (i.get("process") or "").lower()
                     or needle == str(i["code"])]
        return {"total": len(ERROR_CODES), "matched": len(items),
                "source": "Fairino command manual V3.9.8, section 10",
                "caveat": ("This controller runs v3.8.5.1, for which no "
                           "manual is published. Meanings are documented "
                           "for V3.9.8 unless marked as observed here."),
                "codes": items[:limit]}

    @router.get("/errors/codes/{code}")
    def explain_code(code: int):
        return describe(code)

    # ---------------------------------------------------------- discovery
    @router.get("/capabilities")
    def capabilities():
        """What this controller actually supports, probed rather than assumed."""
        return get_caps().as_dict()

    @router.post("/capabilities/refresh")
    def capabilities_refresh():
        get_caps().probe()
        return get_caps().as_dict()

    @router.get("/robot")
    def robot_identity():
        """Identity: model, firmware, mounting, and the sub-device versions."""
        d = get_driver()
        caps = get_caps()
        out: dict[str, Any] = {}
        v = _ok(d._call("GetSoftwareVersion"), "GetSoftwareVersion")
        out["model"], out["software"], out["os"] = v[0], v[1], v[2]
        if caps.has("identity.install_angle"):
            a = _ok(d._call("GetRobotInstallAngle"), "GetRobotInstallAngle")
            out["install_angle_deg"] = a
        if caps.has("identity.slave_firmware"):
            out["slave_firmware"] = _ok(
                d._call("GetSlaveFirmVersion"), "GetSlaveFirmVersion")
        if caps.has("identity.slave_hardware"):
            out["slave_hardware"] = _ok(
                d._call("GetSlaveHardVersion"), "GetSlaveHardVersion")
        out["axes"] = 6
        return out

    # -------------------------------------------------------------- state
    @router.get("/robot/state")
    def robot_state():
        """Consolidated robot state.

        Motion and pose come from the 8083 stream; the rest via RPC. Telemetry
        age is reported as `stale`.
        """
        from . import app as gw
        d = get_driver()
        snap = gw.telemetry.snapshot()
        # None, not 0: a failed fault query must not read as "no fault"
        # (0 == healthy).
        main: int | None = None
        sub: int | None = None
        try:
            main, sub = d.error_code()
            rpc_ok = True
        except RobotError:
            rpc_ok = False

        prog = None
        if get_caps().has("program.state"):
            try:
                code = _ok(d._call("GetProgramState"), "GetProgramState")[0]
                prog = {"code": code,
                        "name": PROGRAM_STATE.get(code, "unknown")}
            except (RobotError, HTTPException):
                prog = None

        age = None if not snap.get("ts") else round(
            __import__("time").time() - snap["ts"], 3)
        return {
            "joints_deg": snap.get("joints"),
            "tcp_pose": snap.get("tcp"),
            "force_torque": snap.get("ft"),
            "joint_torque_nm": snap.get("joint_torque"),
            # 20-byte fixed field, so it truncates. Enough to notice the loaded
            # program changed; `program.name` below is authoritative
            # (GetLoadedProgram).
            "loaded_program_truncated": snap.get("loaded_program_truncated"),
            "telemetry": {
                "connected": snap.get("connected", False),
                "age_s": age,
                "stale": age is None or age > 1.0,
                "frames": snap.get("frames", 0),
                "bad_checksum": snap.get("bad_checksum", 0),
            },
            "fault": {
                "main": main, "sub": sub,
                # null == UNKNOWN; a caller treating null as falsey treats it
                # as "not confirmed clear".
                "faulted": bool(main or sub) if rpc_ok else None,
                "rpc_responding": rpc_ok,
                "explain": describe(main) if main else None,
                "note": None if rpc_ok else (
                    "the fault query failed, so `faulted` is null: FWS does "
                    "NOT know whether the controller is faulted"),
            },
            "program": prog,
            "control": get_control().holders(),
            # Liveness of the disconnect watchdog.
            "control_watchdog": get_control().watchdog(),
        }

    # ---------------------------------------------------------------- I/O
    @router.get("/io/digital/inputs/{index}")
    def digital_input(index: int):
        _cap("io.digital_in")
        v = _ok(get_driver()._call("GetDI", index, 0), "GetDI")
        return {"index": index, "value": int(v[0])}

    @router.get("/io/analog/inputs/{index}")
    def analog_input(index: int):
        _cap("io.analog_in")
        v = _ok(get_driver()._call("GetAI", index, 0), "GetAI")
        return {"index": index, "value": float(v[0])}

    @router.get("/io/tool/digital/inputs/{index}")
    def tool_digital_input(index: int):
        _cap("io.tool_digital_in")
        v = _ok(get_driver()._call("GetToolDI", index, 0), "GetToolDI")
        return {"index": index, "value": int(v[0])}

    @router.put("/io/digital/outputs/{index}")
    def set_digital_output(index: int, req: DigitalOutputRequest,
                           x_fws_control_token: str | None = Header(default=None)):
        """Set a digital output. Confirmation required: a DO commonly
        drives a gripper, clamp or tool changer."""
        _lock("motion", x_fws_control_token)
        if not (req.confirm or full_access()):
            raise HTTPException(400, (
                "setting a digital output can actuate a gripper or tool; "
                "resend with confirm=true"))
        _ok(get_driver()._call("SetDO", index, req.value, 0, 0), "SetDO")
        audit("io.digital_output", index=index, value=req.value)
        return {"index": index, "value": req.value}

    @router.put("/io/analog/outputs/{index}")
    def set_analog_output(index: int, req: AnalogOutputRequest,
                          x_fws_control_token: str | None = Header(default=None)):
        _lock("motion", x_fws_control_token)
        if not (req.confirm or full_access()):
            raise HTTPException(400, "resend with confirm=true")
        # The wire takes a 12-bit DAC count, not a percent:
        # SetAO(id, value * 40.95, block). 100% -> 4095.
        _ok(get_driver()._call("SetAO", index, req.value * 40.95, 0), "SetAO")
        audit("io.analog_output", index=index, percent=req.value)
        return {"index": index, "percent": req.value,
                "dac_count": round(req.value * 40.95)}

    # ------------------------------------------------------------- frames
    @router.get("/frames/tool")
    def tool_frame():
        """Active tool (TCP) frame: which one, and its offset from the flange."""
        _cap("frames.tool_number")
        d = get_driver()
        num = _ok(d._call("GetActualTCPNum", 0), "GetActualTCPNum")[0]
        out: dict[str, Any] = {"active": int(num)}
        if get_caps().has("frames.tool_offset"):
            out["offset"] = _ok(d._call("GetTCPOffset", 0), "GetTCPOffset")
        if get_caps().has("frames.flange_pose"):
            out["flange_pose"] = _ok(
                d._call("GetActualToolFlangePose", 0), "GetActualToolFlangePose")
        return out

    @router.get("/frames/work")
    def work_frame():
        """Active work object frame."""
        _cap("frames.wobj_number")
        d = get_driver()
        num = _ok(d._call("GetActualWObjNum", 0), "GetActualWObjNum")[0]
        out: dict[str, Any] = {"active": int(num)}
        if get_caps().has("frames.wobj_offset"):
            out["offset"] = _ok(d._call("GetWObjOffset", 0), "GetWObjOffset")
        return out

    @router.put("/frames/tool/{frame_id}")
    def set_tool_frame(frame_id: int, req: ToolFrameRequest,
                       x_fws_control_token: str | None = Header(default=None)):
        """Define a tool frame.

        The tool frame is where the controller believes the working point is; a
        wrong value silently shifts every later move and this gateway's own
        pre-flight. Tool frames are numbered 1-15 (work object frames 0-14).
        The active frame is not selected here: Fairino passes tool and
        workpiece numbers with each move.
        """
        _lock("config", x_fws_control_token)
        if not 1 <= frame_id <= 15:
            raise HTTPException(422, (
                "tool frame ids are 1-15 (work object frames are 0-14; the "
                "ranges differ)"))
        if not (req.confirm or full_access()):
            raise HTTPException(400, (
                "defining a tool frame changes where the controller believes "
                "the working point is. Every later move -- and this gateway's "
                "own pre-flight checks -- would shift with it, silently. "
                "Resend with confirm=true"))
        # Wire: SetToolCoord(id, t_coord, type, install, toolID, loadNum)
        _ok(get_driver()._call("SetToolCoord", frame_id,
                               [float(v) for v in req.offset],
                               req.frame_type, req.install,
                               req.tool_id, req.load_num), "SetToolCoord")
        audit("frames.tool", frame_id=frame_id, offset=req.offset,
              frame_type=req.frame_type, install=req.install)
        return {"frame_id": frame_id, "offset": req.offset,
                "note": "pass this tool number with each motion command"}

    @router.put("/frames/work/{frame_id}")
    def set_work_frame(frame_id: int, req: WorkFrameRequest,
                       x_fws_control_token: str | None = Header(default=None)):
        """Define a work object frame. Redefining one silently relocates
        every position expressed in it."""
        _lock("config", x_fws_control_token)
        if not 0 <= frame_id <= 14:
            raise HTTPException(422, (
                "work object frame ids are 0-14 (tool frames are 1-15)"))
        if not (req.confirm or full_access()):
            raise HTTPException(400, (
                "redefining a work object frame relocates every position "
                "expressed in it. Resend with confirm=true"))
        # Wire: SetWObjCoord(id, coord, refFrame)
        _ok(get_driver()._call("SetWObjCoord", frame_id,
                               [float(v) for v in req.offset],
                               req.ref_frame), "SetWObjCoord")
        audit("frames.work", frame_id=frame_id, offset=req.offset,
              ref_frame=req.ref_frame)
        return {"frame_id": frame_id, "offset": req.offset,
                "ref_frame": req.ref_frame}

    # ------------------------------------------------------------ payload
    @router.get("/robot/payload")
    def get_payload():
        _cap("payload.mass")
        d = get_driver()
        out = {"mass_kg": _ok(d._call("GetTargetPayload", 0),
                              "GetTargetPayload")[0]}
        if get_caps().has("payload.cog"):
            out["cog_mm"] = _ok(d._call("GetTargetPayloadCog", 0),
                                "GetTargetPayloadCog")
        return out

    @router.put("/robot/payload")
    def set_payload(req: PayloadRequest,
                    x_fws_control_token: str | None = Header(default=None)):
        """Set payload mass. Confirmation required: payload feeds the
        dynamic model and collision detection."""
        _lock("config", x_fws_control_token)
        if not (req.confirm or full_access()):
            raise HTTPException(400, (
                "payload feeds collision detection; a wrong value degrades a "
                "safety-relevant function. Resend with confirm=true"))
        # SetLoadWeight(loadNum, weight). Load 0 is the default payload slot.
        _ok(get_driver()._call("SetLoadWeight", req.load_num, req.mass_kg),
            "SetLoadWeight")
        audit("robot.payload", mass_kg=req.mass_kg, load_num=req.load_num)
        return {"mass_kg": req.mass_kg, "load_num": req.load_num}

    # -------------------------------------------------------------- speed
    @router.put("/robot/speed")
    def set_speed(req: SpeedRequest,
                  x_fws_control_token: str | None = Header(default=None)):
        """Global speed override, percent."""
        _lock("motion", x_fws_control_token)
        _ok(get_driver()._call("SetSpeed", int(req.percent)), "SetSpeed")
        audit("robot.speed", percent=req.percent)
        return {"percent": req.percent}

    # ---------------------------------------------------- recovered from RPC
    @router.get("/robot/velocity")
    def velocity():
        """Actual and commanded velocity, joint and Cartesian.

        Read over XML-RPC (the SDK's state struct is the wrong shape on this
        firmware). Actual vs commanded distinguishes "not moving" from "told
        not to move", which the 8083 stream cannot.
        """
        d = get_driver()
        out: dict[str, Any] = {}
        for key, method, args in (
            ("joint_deg_s", "GetActualJointSpeedsDegree", (1,)),
            ("tcp_actual", "GetActualTCPSpeed", ()),
            ("tcp_commanded", "GetTargetTCPSpeed", ()),
            ("tcp_actual_composite", "GetActualTCPCompositeSpeed", ()),
            ("tcp_commanded_composite", "GetTargetTCPCompositeSpeed", ()),
        ):
            try:
                out[key] = _ok(d._call(method, *args), method)
            except (RobotError, HTTPException):
                out[key] = None
        out["composite_units"] = "[linear mm/s, angular deg/s]"
        out["source"] = "XML-RPC; not available from the 433-byte stream"
        return out

    @router.get("/robot/pose/flange")
    def flange_pose():
        """Flange pose, before the tool transform (unlike /state, which
        reports the TCP)."""
        try:
            pose = _ok(get_driver()._call("GetActualToolFlangePose", 1),
                       "GetActualToolFlangePose")
        except RobotError as e:
            raise HTTPException(503, str(e)) from e
        return {"flange": [round(v, 4) for v in pose],
                "units": "[x, y, z] mm, [rx, ry, rz] deg",
                "note": "before the tool transform; /state reports the TCP"}

    @router.get("/robot/frames/active")
    def active_frames():
        """Which tool and work frame the pose numbers are expressed in."""
        d = get_driver()
        out: dict[str, Any] = {}
        for key, method in (("tool", "GetActualTCPNum"),
                            ("work", "GetActualWObjNum")):
            try:
                out[key] = _ok(d._call(method, 1), method)[0]
            except (RobotError, HTTPException, IndexError):
                out[key] = None
        return out

    @router.get("/motion/queue")
    def motion_queue():
        """How many motion commands remain queued. Zero with motion_done
        false means the last is executing."""
        d = get_driver()
        try:
            depth = _ok(d._call("GetMotionQueueLength"),
                        "GetMotionQueueLength")[0]
        except (RobotError, HTTPException, IndexError) as e:
            raise HTTPException(503, f"motion queue unavailable: {e}") from e
        done = None
        with contextlib.suppress(RobotError):
            done = bool(_ok(d._call("GetRobotMotionDone"),
                            "GetRobotMotionDone")[0])
        return {"queued": depth, "motion_done": done}

    @router.get("/gripper")
    def gripper():
        """Gripper feedback where available. `fitted` is inferred from a
        non-zero reading (an absent gripper answers with zeros)."""
        d = get_driver()
        out: dict[str, Any] = {}
        for key, method in (("position", "GetGripperCurPosition"),
                            ("speed", "GetGripperCurSpeed"),
                            ("current", "GetGripperCurCurrent"),
                            ("voltage", "GetGripperVoltage"),
                            ("temperature", "GetGripperTemp")):
            try:
                v = _ok(d._call(method), method)
                out[key] = {"id": v[0], "value": v[1]} if len(v) > 1 else None
            except (RobotError, HTTPException):
                out[key] = None
        out["fitted"] = any(
            isinstance(v, dict) and v.get("value") for v in out.values())
        out["note"] = ("`fitted` is inferred from a non-zero reading; an "
                       "absent gripper answers with zeros, not an error")
        return out

    return router
