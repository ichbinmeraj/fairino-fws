"""Force and torque: sensing, sensor setup, and where FWS stops.

The gateway owns sensor setup (activate, zero, payload, reference frame, read),
all one-shot with no motion. Force-reactive strategies (constant-force,
insertion, surface finding) wrap motion and belong in the Lua program; see
GET /force/strategies.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .access import full_access
from .driver import RobotError
from .protocol.lua_bridge import (
    ARGUMENT_ORDER_CONFLICTS,
    FORCE_LUA_ONLY,
    FORCE_RPC_ONLY,
    REFUSE_TO_GENERATE,
)

router = APIRouter(prefix="/api/v1", tags=["force"])


class PayloadRequest(BaseModel):
    """Payload below the force sensor.

    Distinct from the robot payload at PUT /robot/payload: this feeds the
    sensor's gravity compensation, that feeds the arm's dynamic model. They are
    independent.
    """

    mass_kg: float = Field(ge=0, le=50)
    cog_mm: list[float] | None = Field(
        default=None, min_length=3, max_length=3,
        description="centre of mass [x, y, z] in mm, in the sensor frame")
    confirm: bool = Field(
        default=False,
        description="required: wrong compensation makes every force reading "
                    "wrong, and force readings gate collision detection")


class ZeroRequest(BaseModel):
    confirm: bool = Field(default=False)


class ActivateRequest(BaseModel):
    state: int = Field(ge=0, le=1, description="0 = off, 1 = on")
    confirm: bool = Field(default=False)


def build(get_driver, get_telemetry, get_control, audit) -> APIRouter:
    """Resolve dependencies at call time so create_app can rebind them."""

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

    def _call(method: str, *args: Any) -> list:
        try:
            return _ok(get_driver()._call(method, *args), method)
        except RobotError as e:
            raise HTTPException(503, str(e)) from e

    # ------------------------------------------------------------- sensing
    @router.get("/sensors/force")
    def force():
        """Wrist force/torque from the 8083 stream at ~10 Hz.

        These numbers include the tool's weight and are NOT gravity-compensated:
        "RCS" is the reference coordinate system (a frame transform), not
        compensation.
        """
        ft = get_telemetry().snapshot().get("ft")
        if ft is None:
            raise HTTPException(503, "no force data in the telemetry stream")
        return {
            "force_n": {"fx": ft[0], "fy": ft[1], "fz": ft[2]},
            "torque_nm": {"tx": ft[3], "ty": ft[4], "tz": ft[5]},
            "magnitude_n": round((ft[0] ** 2 + ft[1] ** 2 + ft[2] ** 2) ** 0.5, 4),
            # Not gravity-compensated: "RCS" is a frame transform, and the
            # reading swings with wrist rotation.
            "gravity_compensated": False,
            "what_this_is": ("the sensor reading transformed into the "
                             "reference coordinate system. Tool weight is IN "
                             "these numbers and rotates with the wrist"),
            "frame": "sensor reference frame, set by FT_SetRCS",
            "source": "telemetry-8083 offset 184",
            "raw_available": False,
            "raw_detail": "FT_GetForceTorqueOrigin answers error 3 at every "
                          "arity on v3.8.5.1; the raw block is not in the "
                          "433-byte frame either",
            "if_you_need_gravity_removed": (
                "you must do it yourself, or use the controller's force "
                "CONTROL functions (FT_Control, FT_Guard), which compensate "
                "internally using the payload set at PUT /force/payload. See "
                "GET /force/strategies"),
        }

    @router.get("/sensors/joint_torques")
    def joint_torques():
        """Per-joint torque in N·m from the stream at offset 108.
        Independent of the wrist sensor (from the joint drives)."""
        t = get_telemetry().snapshot().get("joint_torque")
        if t is None:
            raise HTTPException(503, "no torque data in the telemetry stream")
        return {
            "joint_torque_nm": {f"j{i + 1}": t[i] for i in range(6)},
            "source": "telemetry-8083 offset 108, scaled from milli-N·m",
            "cross_checked_against": "GetJointTorques(1)",
        }

    # -------------------------------------------------------------- setup
    @router.get("/force/config")
    def force_config():
        """Everything the controller reports about the sensor."""
        out: dict[str, Any] = {}
        for key, method, args in (
            ("sensor", "FT_GetConfig", ()),
            ("payload_kg", "GetForceSensorPayload", ()),
            ("payload_cog_mm", "GetForceSensorPayloadCog", ()),
            ("drag_state", "GetForceAndTorqueDragState", ()),
            ("axle_sensor", "AxleSensorConfigGet", ()),
        ):
            try:
                out[key] = _ok(get_driver()._call(method, *args), method)
            except (RobotError, HTTPException) as e:
                out[key] = {"unavailable": str(getattr(e, "detail", e))}
        out["sensor_fields"] = ["company", "device", "softversion", "bus"]
        return out

    @router.get("/force/payload")
    def get_payload():
        """The sensor's payload compensation and the robot's, side
        by side. They are set by different commands and can
        disagree."""
        sensor = _call("GetForceSensorPayload")
        cog = _call("GetForceSensorPayloadCog")
        try:
            robot = _call("GetTargetPayload", 1)
        except HTTPException:
            robot = None
        s_kg = sensor[0] if sensor else None
        r_kg = robot[0] if robot else None
        # Surface the disagreement: the two payloads are independent, and there
        # is no uncompensated reading on this firmware to catch a mismatch.
        mismatch = None
        if s_kg is not None and r_kg is not None and abs(s_kg - r_kg) > 0.02:
            mismatch = {
                "sensor_kg": s_kg,
                "robot_kg": r_kg,
                "difference_kg": round(r_kg - s_kg, 4),
                "consequence": (
                    "the force CONTROL functions (FT_Control, FT_Guard) "
                    "compensate using the sensor payload, so a wrong value "
                    "makes a force-guarded move trip at the wrong threshold. "
                    "It does NOT affect GET /sensors/force, which is not "
                    "compensated at all."),
                "how_to_fix": (
                    "set the mass BELOW the sensor with PUT /force/payload -- "
                    "which is not always the robot payload, since a tool "
                    "mounted above the sensor is carried by the arm and not "
                    "seen by it -- then POST /force/zero with the tool "
                    "hanging free and the arm still"),
            }
        return {
            "sensor_payload_kg": s_kg,
            "sensor_payload_cog_mm": cog,
            "robot_payload_kg": r_kg,
            "mismatch": mismatch,
            "note": "sensor_payload feeds gravity compensation for force "
                    "readings; robot_payload feeds the arm's dynamic model "
                    "and collision detection. Setting one does not set the "
                    "other.",
        }

    @router.put("/force/payload")
    def set_payload(req: PayloadRequest,
                    x_fws_control_token: str | None = Header(default=None)):
        """Tell the sensor what hangs below it. Confirmation
        required: a wrong mass biases every force reading."""
        if not (req.confirm or full_access()):
            raise HTTPException(422, "confirm=true required: wrong "
                                     "compensation silently biases every "
                                     "force reading")
        _lock("config", x_fws_control_token)
        _call("SetForceSensorPayload", float(req.mass_kg))
        if req.cog_mm is not None:
            _call("SetForceSensorPayloadCog", *[float(v) for v in req.cog_mm])
        audit("force.payload", mass_kg=req.mass_kg, cog_mm=req.cog_mm)
        return {"sensor_payload_kg": req.mass_kg,
                "sensor_payload_cog_mm": req.cog_mm}

    @router.post("/force/zero")
    def zero(req: ZeroRequest,
             x_fws_control_token: str | None = Header(default=None)):
        """Tare the sensor. Do this with the tool hanging free and
        the arm still, or contact force is baked into the offset."""
        if not (req.confirm or full_access()):
            raise HTTPException(422, "confirm=true required: zero the sensor "
                                     "only with the tool free and the arm "
                                     "still")
        _lock("config", x_fws_control_token)
        _call("FT_SetZero", 1)
        audit("force.zero")
        return {"zeroed": True,
                "warning": "readings are now relative to this pose and this "
                           "contact state"}

    @router.post("/force/activate")
    def activate(req: ActivateRequest,
                 x_fws_control_token: str | None = Header(default=None)):
        """Turn the force sensor on or off. Turning it off silently
        removes force-based protection (guards, compliance,
        insertion)."""
        if not (req.confirm or full_access()):
            raise HTTPException(422, "confirm=true required")
        _lock("config", x_fws_control_token)
        _call("FT_Activate", int(req.state))
        audit("force.activate", state=req.state)
        return {"active": bool(req.state)}

    # --------------------------------------------------------- the boundary
    @router.get("/force/strategies")
    def strategies():
        """What force-reactive motion exists, and why FWS does not run it.

        Constant-force, insertion and surface finding modify how the next move
        behaves; issuing the mode from the gateway and the move from a program
        leaves them unsynchronised. They belong in the Lua program, next to
        their moves.
        """
        return {
            "principle": "force strategies wrap motion, so they run on the "
                         "controller, in the same program as the moves",
            "setup_here": ["POST /force/activate", "POST /force/zero",
                           "PUT /force/payload", "GET /force/config",
                           "GET /sensors/force", "GET /sensors/joint_torques"],
            "strategies_in_lua": {
                "constant_force": "FT_Control",
                "collision_guard": "FT_Guard",
                "spiral_insertion": "FT_SpiralSearch",
                "linear_insertion": "FT_LinInsertion",
                "rotary_insertion": "FT_RotInsertion",
                "surface_finding": "FT_FindSurface",
                "compliance": "FT_ComplianceStart / FT_ComplianceStop",
                "centre_finding": "FT_CalCenterStart / FT_CalCenterEnd",
                "tap_detection": "FT_Click (Lua only)",
                "torque_recording": "TorqueRecordStart / End / Reset (Lua only)",
            },
            "lua_only": list(FORCE_LUA_ONLY),
            "rpc_only": list(FORCE_RPC_ONLY),
            "argument_order_conflicts": ARGUMENT_ORDER_CONFLICTS,
            "refused_for_generation": REFUSE_TO_GENERATE,
            "see_also": "GET /api/v1/lua/functions?section=3.6",
        }

    return router
