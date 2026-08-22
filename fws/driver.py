"""Robot driver: raw XML-RPC on port 20003 (does not use fairino-python-sdk).

Hard rules enforced here: no XML-RPC introspection (system.listMethods powers
off the controller); every jog is bounded by max_dis; explicit socket timeouts
on every call.
"""
from __future__ import annotations

import http.client
import threading
import xmlrpc.client
from dataclasses import dataclass
from typing import Any, ClassVar

from .access import full_access

FORBIDDEN = ("system.listMethods", "system.methodHelp", "system.methodSignature")

# Commands that must never reach the wire. Enforced here in the driver, below
# the HTTP passthrough gate, so nothing that imports this class can route
# around it. Kept as a literal floor that holds even if the generated registry
# is regenerated, reclassified, or absent.
REFUSED = frozenset({
    "ShutDownRobotOS",          # halts the OS; no reboot exists, needs mains
    "SetCtrlFirmwareUpgrade",   # firmware write
    "SetEndFirmwareUpgrade",    # firmware write
    "SetJointFirmwareUpgrade",  # firmware write
    "SoftwareUpgrade",          # firmware write
    "SetSysServoBootMode",      # puts EtherCAT slaves into boot mode
    "SlaveFileWrite",           # the primitive under the firmware writes
    "GetLuaList",               # reported to wedge the RPC channel
    # GetLuaListPrepare/GetLuaNameWithID are the real wire calls GetLuaList
    # wraps, each in an uncapped retry loop that can wedge the RPC channel.
    "GetLuaListPrepare",
    "GetLuaNameWithID",
    # Firmware writers also refused by the generated registry, kept here as a
    # hard floor.
    "JointAllParamUpgrade",     # firmware write, joint parameter block
    "KernelUpgrade",            # firmware write, OS kernel
    "SetEncoderUpgrade",        # firmware write, encoder
})


class RobotError(RuntimeError):
    """Base for wire errors. Subclasses distinguish controller faults
    from transport failures."""


class ControllerFault(RobotError):
    """The controller answered with an error. Real information about the
    firmware (e.g. -506 = method absent)."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.fault_code = code


class TransportError(RobotError):
    """The request never reached the controller, or no answer came. Says
    nothing about firmware support."""


@dataclass
class Version:
    hardware: str
    software: str
    qnx: str


class _Transport(xmlrpc.client.Transport):
    """XML-RPC transport with a real socket timeout."""

    def __init__(self, timeout: float):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host: Any) -> http.client.HTTPConnection:
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class RobotDriver:
    """Single-writer driver; all calls serialised through one lock
    (20003 may not tolerate concurrent clients)."""

    def __init__(self, ip: str = "192.168.57.2", timeout: float = 5.0,
                 port: int = 20003, upload_port: int = 20010,
                 download_port: int = 20011):
        self.ip = ip
        self.port = port
        # File transfer ports. Configurable because they must be redirectable
        # for testing; on real hardware they are fixed and open on demand.
        self.upload_port = upload_port
        self.download_port = download_port
        # The firmware offers no way to READ the auto/manual mode, so the
        # driver remembers the last mode it successfully commanded. None
        # means "never set by this process", not automatic.
        self.last_set_mode: str | None = None
        self._lock = threading.Lock()
        self._rpc = xmlrpc.client.ServerProxy(
            f"http://{ip}:{port}", transport=_Transport(timeout),
            allow_none=True,
        )

    # -- plumbing ---------------------------------------------------------
    def _call(self, method: str, *args: Any,
              allow_refused: bool = False) -> Any:
        """Send one command. allow_refused is a keyword-only escape hatch
        for the single route (shutdown) that legitimately needs a refused
        command."""
        if not full_access() and (method in FORBIDDEN
                                  or method.startswith("system.")):
            raise RobotError(f"introspection is blocked: {method}")
        if method in REFUSED and not allow_refused and not full_access():
            raise RobotError(
                f"{method} is refused by FWS and will not be sent. It writes "
                f"firmware, halts the controller, or wedges the RPC channel. "
                f"See SAFETY.md. If you are certain, the caller must pass "
                f"allow_refused=True explicitly.")
        with self._lock:
            try:
                return getattr(self._rpc, method)(*args)
            except xmlrpc.client.Fault as e:
                raise ControllerFault(
                    f"{method}: fault {e.faultCode}: {e.faultString}",
                    code=e.faultCode,
                ) from e
            except OSError as e:
                # Includes socket.timeout, ConnectionRefusedError and
                # ConnectionResetError, all OSError subclasses.
                raise TransportError(
                    f"{method}: transport error: {e}") from e

    @staticmethod
    def _ok(result: Any, method: str) -> list[Any]:
        """Fairino returns [errcode, ...]; errcode 0 means success."""
        if isinstance(result, list):
            if result[0] != 0:
                raise RobotError(f"{method} returned error {result[0]}")
            return result[1:]
        if result != 0:
            raise RobotError(f"{method} returned error {result}")
        return []

    # -- read-only --------------------------------------------------------
    def version(self) -> Version:
        hw, sw, qnx = self._ok(self._call("GetSoftwareVersion"), "GetSoftwareVersion")
        return Version(hardware=hw, software=sw, qnx=qnx)

    def joints(self) -> list[float]:
        return self._ok(self._call("GetActualJointPosDegree", 0), "joints")

    def tcp_pose(self) -> list[float]:
        return self._ok(self._call("GetActualTCPPose", 0), "tcp_pose")

    def error_code(self) -> tuple[int, int]:
        r = self._call("GetRobotErrorCode")
        return int(r[1]), int(r[2])

    def motion_done(self) -> bool:
        return bool(self._ok(self._call("GetRobotMotionDone"), "motion_done")[0])

    def forward_kin(self, joints: list[float]) -> list[float]:
        return self._ok(
            self._call("GetForwardKin", [float(j) for j in joints]), "forward_kin"
        )

    # -- state changing ---------------------------------------------------
    def set_mode(self, manual: bool) -> None:
        """0 = automatic, 1 = manual."""
        self._ok(self._call("Mode", 1 if manual else 0), "Mode")
        self.last_set_mode = "manual" if manual else "auto"

    def enable(self, on: bool) -> None:
        """Releases/engages the brakes. Not an emergency stop in either
        direction — the physical E-stop is hardware only."""
        self._ok(self._call("RobotEnable", 1 if on else 0), "RobotEnable")

    def jog(self, joint: int, positive: bool, max_dis: float,
            vel: float) -> None:
        """Bounded single-joint jog. Wire arg order is (ref, nb, dir,
        vel, acc, max_dis) — not the SDK's Python signature order."""
        if not 1 <= joint <= 6:
            raise RobotError("joint must be 1..6")
        if not 0 < max_dis <= 15.0:
            raise RobotError("max_dis must be in (0, 15] degrees")
        if not 0 < vel <= 30.0:
            raise RobotError("vel must be in (0, 30] percent")
        self._ok(
            self._call("StartJOG", 0, joint, 1 if positive else 0,
                       float(vel), 100.0, float(max_dis)),
            "StartJOG",
        )

    # ref values for StartJOG, from the SDK's own documentation:
    #   0 = joint space, 2 = base frame, 4 = tool frame, 8 = workpiece frame
    FRAME_REF: ClassVar[dict[str, int]] = {"base": 2, "tool": 4}

    def jog_linear(self, axis: int, positive: bool, max_dis: float,
                   vel: float, frame: str = "base") -> None:
        """Bounded Cartesian jog. axis 1/2/3 translate X/Y/Z (mm); axis
        4/5/6 rotate X/Y/Z (deg). Wire order StartJOG(ref, nb, dir, vel,
        acc, max_dis)."""
        ref = self.FRAME_REF.get(frame)
        if ref is None:
            raise RobotError(f"unknown frame {frame!r}")
        if not 1 <= axis <= 6:
            raise RobotError("axis must be 1..6")
        cap = 50.0 if axis <= 3 else 15.0      # mm for translation, deg for rotation
        if not 0 < max_dis <= cap:
            raise RobotError(f"max_dis must be in (0, {cap}]")
        if not 0 < vel <= 30.0:
            raise RobotError("vel must be in (0, 30] percent")
        self._ok(
            self._call("StartJOG", ref, axis, 1 if positive else 0,
                       float(vel), 100.0, float(max_dis)),
            "StartJOG",
        )

    def inverse_kin(self, pose: list[float], kind: int = 0,
                    config: int = -1) -> list[float]:
        """Cartesian pose -> joint angles. kind: 0 = absolute base frame,
        1 = relative base, 2 = relative tool. config -1 solves from the
        current joint position."""
        r = self._call("GetInverseKin", int(kind),
                       [float(x) for x in pose], int(config))
        if not isinstance(r, list) or r[0] != 0:
            raise RobotError(f"GetInverseKin returned {r}")
        return [float(x) for x in r[1:7]]

    def move_l(self, pose: list[float], joints: list[float], tool: int = 0,
               user: int = 0, vel: float = 20.0, ovl: float = 100.0,
               blend_r: float = 0.0, acc: float = 0.0) -> None:
        """Linear Cartesian move — a single command, not a path runner.

        On the wire MoveL takes ONE flat 33-element array, not the separate
        arguments its Python signature advertises:

            [0:6]   joint_pos j1..j6      (IK solution for the target pose)
            [6:12]  desc_pos x,y,z,rx,ry,rz
            [12]    tool          [13]  user
            [14]    vel           [15]  acc          [16] ovl
            [17]    blendR        [18]  blendMode
            [19:23] exaxis_pos    [23]  search       [24] offset_flag
            [25:31] offset_pos    [31]  oacc         [32] velAccParamMode

        blend_r -1.0 blocks until the move completes (holding the lock for the
        whole move, making a stop impossible); default 0.0 is non-blocking and
        motion_done() is polled instead.
        """
        if len(pose) != 6 or len(joints) != 6:
            raise RobotError("pose and joints must each have 6 elements")
        if not 0 < vel <= 50.0:
            raise RobotError("vel must be in (0, 50] percent")
        args = (
            [float(j) for j in joints]
            + [float(p) for p in pose]
            + [int(tool), int(user), float(vel), float(acc), float(ovl),
               float(blend_r), 0]          # blendMode 0 = inscribed transition
            + [0.0, 0.0, 0.0, 0.0]         # exaxis_pos
            + [0, 0]                       # search, offset_flag
            + [0.0] * 6                    # offset_pos
            + [100.0, 0]                   # oacc, velAccParamMode 0 = percent
        )
        self._ok(self._call("MoveL", args), "MoveL")

    def stop_motion(self) -> None:
        """Stop a program-space move (MoveL/MoveJ).

        ImmStopJOG does NOT stop these -- it only stops jogs. NOT an
        emergency stop.
        """
        self._ok(self._call("StopMotion"), "StopMotion")

    def set_speed(self, vel: float) -> None:
        """Global speed override, percent."""
        if not 0 < vel <= 100:
            raise RobotError("vel must be in (0, 100]")
        self._ok(self._call("SetSpeed", int(vel)), "SetSpeed")

    def stop(self) -> None:
        """Functional stop of jog motion. NOT an emergency stop."""
        self._ok(self._call("ImmStopJOG"), "ImmStopJOG")

    def reset_errors(self) -> None:
        """Clear latched controller faults.

        Does NOT clear the underlying condition. If the arm is sitting on a
        soft limit, the fault returns the moment you jog further into it --
        you must jog away from the limit after resetting.
        """
        self._ok(self._call("ResetAllError"), "ResetAllError")

    def joint_limits(self) -> list[tuple[float, float]]:
        """Soft limits as [(min, max)] per joint, read from the controller."""
        r = self._call("GetJointSoftLimitDeg", 1)
        if not isinstance(r, list) or r[0] != 0:
            raise RobotError(f"GetJointSoftLimitDeg returned {r}")
        v = r[1:13]
        return [(float(v[2 * i]), float(v[2 * i + 1])) for i in range(6)]
