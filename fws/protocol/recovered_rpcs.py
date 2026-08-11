"""Commands the SDK classifies `local` that the controller answers over RPC.

The registry marks a command `local` when the SDK reads its own state struct
instead of making a wire call, and the generic invoker then refuses it. For a
set of `Get*` commands the controller in fact answers the RPC directly; the 19
that do are recorded here and re-enabled, and the 21 that do not are recorded in
ABSENT.

What this adds: actual and commanded joint/TCP velocity, flange pose, motion
queue length, active tool/work frame, and gripper state.

Note: `GetRobotEmergencyStopState` and `GetSafetyStopState` are ABSENT on this
firmware, so FWS cannot read the E-stop state from the controller.

`arity` and `returns` are measured, not read from the SDK signature.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveredRPC:
    name: str
    arity: int
    returns: int
    describes: str


# name -> how to call it, measured on v3.8.5.1
RECOVERED: dict[str, RecoveredRPC] = {
    "GetActualJointSpeedsDegree": RecoveredRPC(
        "GetActualJointSpeedsDegree", 1, 6, "joint velocity, deg/s"),
    "GetActualTCPSpeed": RecoveredRPC(
        "GetActualTCPSpeed", 0, 6, "TCP velocity, per axis"),
    "GetActualTCPCompositeSpeed": RecoveredRPC(
        "GetActualTCPCompositeSpeed", 0, 2,
        "TCP speed as [linear mm/s, angular deg/s]"),
    "GetTargetTCPSpeed": RecoveredRPC(
        "GetTargetTCPSpeed", 0, 6, "COMMANDED TCP velocity, per axis"),
    "GetTargetTCPCompositeSpeed": RecoveredRPC(
        "GetTargetTCPCompositeSpeed", 0, 2,
        "commanded TCP speed as [linear, angular]"),
    "GetActualToolFlangePose": RecoveredRPC(
        "GetActualToolFlangePose", 1, 6,
        "flange pose, before the tool transform"),
    "GetActualTCPNum": RecoveredRPC(
        "GetActualTCPNum", 1, 1, "active tool frame number"),
    "GetActualWObjNum": RecoveredRPC(
        "GetActualWObjNum", 1, 1, "active work frame number"),
    "GetMotionQueueLength": RecoveredRPC(
        "GetMotionQueueLength", 0, 1, "queued motion commands"),
    "GetJointTorques": RecoveredRPC(
        "GetJointTorques", 1, 6, "joint torque, N.m"),
    "GetGripperCurPosition": RecoveredRPC(
        "GetGripperCurPosition", 0, 2, "gripper position as [id, value]"),
    "GetGripperCurSpeed": RecoveredRPC(
        "GetGripperCurSpeed", 0, 2, "gripper speed as [id, value]"),
    "GetGripperCurCurrent": RecoveredRPC(
        "GetGripperCurCurrent", 0, 2, "gripper current as [id, value]"),
    "GetGripperVoltage": RecoveredRPC(
        "GetGripperVoltage", 0, 2, "gripper voltage as [id, value]"),
    "GetGripperTemp": RecoveredRPC(
        "GetGripperTemp", 0, 2, "gripper temperature as [id, value]"),
    "GetProgramState": RecoveredRPC(
        "GetProgramState", 0, 1, "1 stopped, 2 running, 3 paused"),
    "GetRobotMotionDone": RecoveredRPC(
        "GetRobotMotionDone", 0, 1, "1 when motion has finished"),
    "GetRobotErrorCode": RecoveredRPC(
        "GetRobotErrorCode", 0, 2, "latched fault as [main, sub]"),
    "GetSDKVersion": RecoveredRPC(
        "GetSDKVersion", 0, 1, "the controller's own SDK version string"),
}

# Probed and answered "method not defined" on v3.8.5.1. Recorded because a
# confident absence is as useful as a presence, and two of these matter.
ABSENT: tuple[str, ...] = (
    "GetActualJointAccDegree", "GetAxlePointRecordBtnState",
    "GetCurExAxisCoord", "GetCurExToolCoord", "GetCurToolCoord",
    "GetCurWObjCoord", "GetDO", "GetGripperActivateStatus",
    "GetGripperRotNum", "GetGripperRotSpeed", "GetGripperRotTorque",
    "GetJointDriverTemperature", "GetJointDriverTorque",
    "GetRobotEmergencyStopState",      # no E-stop state on this firmware
    "GetRobotRealTimeState", "GetSDKComState", "GetSafetyCode",
    "GetSafetyStopState",              # nor safety-stop state
    "GetSmarttoolBtnState", "GetSoftwareUpgradeState", "GetToolDO",
)

FIRMWARE = "v3.8.5.1"


def available(name: str) -> RecoveredRPC | None:
    """The measured call shape, or None if it is not a recovered RPC."""
    return RECOVERED.get(name)
