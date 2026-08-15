"""A fake Fairino controller, faithful to the quirks that actually bite.

Lets CI and client developers run against something that behaves like the real
machine (controller software v3.8.5.1) without owning one. Reproduced quirks:

  * StartJOG wire order (ref, nb, dir, vel, acc, max_dis), max_dis a hard bound
  * >270 ms jog start latency
  * position getters return error 14 while faulted, though telemetry keeps flowing
  * port 8083 serves exactly one client
  * file-transfer ports appear ~250 ms after the matching RPC, not before
  * GetDO faults -506; FT_GetForceTorqueRCS requires an argument
  * FT_GetForceTorqueOrigin answers error 3 at every arity
  * joint torque in milli-N·m in the frame, N·m over RPC
  * a soft-limit violation latches fault main=1 sub=22 until ResetAllError
  * an unreachable IK target returns error 112
  * LuaUpLoadUpdate compiles and returns only 0 or -1; the real verdict goes to
    the controller log, whose filenames/mtimes do not order it
  * a wedged Lua validator returns -1 at a fixed ~4.09 s

It is stricter than the real controller in validating argument counts, and
weaker in that its Lua compiler only recognises a call that is a whole
statement on its own line.
"""
from __future__ import annotations

import contextlib
import io
import re
import socket
import struct
import tarfile
import threading
import time
import xmlrpc.client
import xmlrpc.server
from collections.abc import Sequence
from dataclasses import dataclass, field

from . import kinematics

# Frame layout for the v3.8.5.1 telemetry frame.
FRAME_LEN = 433
DATA_LEN = 426
JOINTS_OFF = 8
TCP_OFF = 56
TORQUE_OFF = 108
FT_OFF = 184
# The frame carries milli-newton-metres; the RPC reports N·m. The fake emits
# the frame's units so a parser that forgets to divide fails here.
TORQUE_SCALE = 1000.0

SOFT_LIMITS = [
    (-175.0, 175.0), (-265.0, 85.0), (-160.0, 160.0),
    (-265.0, 85.0), (-175.0, 175.0), (-360.0, 360.0),
]

# Jog rate: 0.54 deg/s per 1% velocity.
DEG_PER_SEC_PER_PCT = 0.54
MM_PER_SEC_PER_PCT = 10.0

# ---------------------------------------------------------------- Lua compiler
#
# What the upload validator knows on v3.8.5.1. Names map to the accepted arity
# RANGE (minimum and maximum, optional parameters between). Only the arity is
# used; neither the fake nor the controller type-checks.
#
#   MoveL     32 and 33 accepted, 34 rejected
#   WaitMs    1 accepted; 0 and 4 rejected
#   FT_Control  exactly 24 (manual says 21)
#   FT_Guard    exactly 26        FT_Click  exactly 6
#   PrintMsg  absent (documented in the manual, not on this firmware)
LUA_BUILTINS: dict[str, tuple[int, int]] = {
    "WaitMs": (1, 1),
    # PROBED on v3.8.5.1 at arity 29 (protocol/lua_firmware.py). It was
    # missing here, so the fake answered "attempt to call global MoveJ (a
    # nil value)" for a function the firmware really has.
    "MoveJ": (29, 29),
    "MoveL": (32, 33),
    "PTP": (1, 20),
    "SetDO": (2, 4),
    "FT_Control": (24, 24),
    "FT_Guard": (26, 26),
    "FT_Click": (6, 6),
    # Present, but every call fails its point-name lookup on a cell with no
    # taught points (that is not the same as absent).
    "Lin": (11, 11),
    "ARC": (1, 40),
    "Circle": (1, 40),
}
LUA_NEEDS_A_TAUGHT_POINT = frozenset({"Lin", "ARC", "Circle"})

# A call that is a whole statement (see the module docstring's stated weakness).
LUA_CALL = re.compile(r"^([A-Za-z_]\w*)\s*\((.*)\)\s*;?$")

# A wedged validator answers at a fixed ~4.09 s (the retry cycle on a dead web
# socket, not a validation result).
WEDGED_VALIDATOR_SECONDS = 4.09


def _lua_log_line(payload: str, seq: int) -> str:
    """One log line in the controller's own shape: the compiler's answer wrapped
    in "/f/b ... /b/f" framing and named PointTableUpdateLuaResult."""
    return (f"2015-01-06 09:58:{seq % 60:02d}.449 INFO RcvCmdThread_Web rcv "
            f"cmd is /f/bIII{seq}III845III142III"
            f"PointTableUpdateLuaResult('{payload}')III/b/f")


def _split_lua_args(text: str) -> list[str]:
    """Top-level comma split, so `MoveL({1,2})` counts as one argument."""
    if not text.strip():
        return []
    args, depth, current = [], 0, ""
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(current)
            current = ""
            continue
        current += ch
    args.append(current)
    return args


class Fault(xmlrpc.client.Fault):
    """An XML-RPC fault, as the controller raises them.

    Must subclass xmlrpc.client.Fault: SimpleXMLRPCServer wraps any other
    exception as generic fault code 1, which would hide the very codes a
    client needs to distinguish (-502 wrong arity, -506 no such method).
    """

    def __init__(self, code: int, message: str):
        super().__init__(code, message)

    @property
    def code(self) -> int:
        return self.faultCode


class _Server(xmlrpc.server.SimpleXMLRPCServer):
    """Reports unknown methods as fault -506, the way the controller does (FWS
    relies on that code to tell a missing method from a failed call)."""

    def _dispatch(self, method, params):
        func = self.funcs.get(method)
        if func is None:
            raise Fault(-506, f"Method '{method}' not defined")
        return func(*params)


@dataclass
class RobotState:
    joints: list[float] = field(
        default_factory=lambda: [0.0, -90.0, 90.0, -90.0, -90.0, 0.0])
    ft: list[float] = field(
        default_factory=lambda: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    enabled: bool = False
    manual_mode: bool = True
    error_main: int = 0
    error_sub: int = 0
    program_state: int = 1
    loaded_program: str = ""
    payload_kg: float = 0.0
    # The force sensor's own payload compensation, deliberately separate from
    # payload_kg (the two can disagree on real hardware).
    ft_payload_kg: float = 0.0
    ft_payload_cog: list[float] = field(
        default_factory=lambda: [0.0, 0.0, 0.0])
    ft_active: bool = True
    # Newton-metres, as GetJointTorques reports them. The 8083 frame carries
    # these x1000; see Telemetry.TORQUE_SCALE.
    joint_speed: list[float] = field(
        default_factory=lambda: [0.0] * 6)
    tcp_speed: list[float] = field(
        default_factory=lambda: [0.0] * 6)
    joint_torque: list[float] = field(
        default_factory=lambda: [-0.021, -0.529, -0.213, -0.0298, -0.0013,
                                 -0.0070])
    wobj_num: int = 0
    tool_frames: dict = field(default_factory=dict)
    work_frames: dict = field(default_factory=dict)
    di: dict = field(default_factory=dict)
    do: dict = field(default_factory=dict)
    ai: dict = field(default_factory=dict)
    ao: dict = field(default_factory=dict)
    moving: bool = False

    @property
    def faulted(self) -> bool:
        return self.error_main != 0 or self.error_sub != 0

    def tcp(self) -> list[float]:
        return kinematics.forward(self.joints)


class FakeController:
    """Serves XML-RPC, an 8083 telemetry stream, and file transfer."""

    def __init__(self, host: str = "127.0.0.1", *,
                 jog_start_latency_s: float = 0.30,
                 transfer_port_delay_s: float = 0.25,
                 stream_hz: float = 10.0,
                 software_version: str = "v3.8.5.1"):
        self.host = host
        self.jog_start_latency_s = jog_start_latency_s
        self.transfer_port_delay_s = transfer_port_delay_s
        self.stream_period = 1.0 / stream_hz
        self.software_version = software_version

        self.state = RobotState()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

        self.calls: list[tuple[str, tuple]] = []      # for assertions in tests
        self.shut_down = False                       # ShutDownRobotOS reached
        self._corrupt_frames = 0                     # corrupt_next_frame()
        self._frame_counter = 0
        self._stream_client_connected = False
        self._pending_upload: str | None = None
        self._pending_download: bytes | None = None
        self.files: dict[str, bytes] = {}
        self.point_tables: dict[str, bytes] = {}
        self.backups: dict[str, bytes] = {}
        self.active_point_table: str | None = None
        self._prepared_backup: str | None = None
        self._pending_upload_kind = "lua"
        self._pending_upload_store = "files"
        self._pending_download_header = 46

        # -- open-protocol device Lua (fileType 11), kept apart from plain
        # Lua so a client that routes by type wrongly fails a test.
        self.open_luas: dict[str, bytes] = {}

        # -- the Lua compiler and the log it answers on ------------------
        self.lua_builtins = dict(LUA_BUILTINS)
        self.lua_needs_a_taught_point = set(LUA_NEEDS_A_TAUGHT_POINT)
        # Verdicts from the current run, in order, as log lines.
        self.lua_log: list[str] = []
        # Verdicts from an earlier run, in a log file whose name and mtime both
        # look newer than the live one. Seeded on purpose: a client that picks
        # the log file by filename or mtime gets these, about the wrong program.
        self.stale_lua_log: list[str] = [
            _lua_log_line("success", 41),
            _lua_log_line(
                "lua_name:/fruser/legacy_demo.lua---line_num:7---error_info: "
                "attempt to call global PrintMsg (a nil value)", 42),
        ]
        self.rblog_fetches = 0
        self.lua_validator_wedged = False
        self.wedge_delay_s = WEDGED_VALIDATOR_SECONDS
        self._lua_seq = 1000
        # Which header width a point-table download is framed with. The two
        # vendor parsers disagree, so this is a knob, not a fact. See
        # fws/files_wire.py.
        self.point_table_download_header = 46

        self._rpc = _Server((host, 0), allow_none=True, logRequests=False)
        self.rpc_port = self._rpc.server_address[1]
        self._register()

        self._stream_sock = socket.socket()
        self._stream_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._stream_sock.bind((host, 0))
        self._stream_sock.listen(4)
        self.stream_port = self._stream_sock.getsockname()[1]

        # Persistent transfer listeners on ephemeral ports.
        #
        # Fidelity gap: the real controller does not listen on 20010/20011
        # until FileUpload/FileDownload is called (the port appears ~250 ms
        # later), so a client must retry the connect. The fake keeps them bound
        # throughout, because a client cannot discover an ephemeral port that
        # does not exist yet.
        self._xfer_up = socket.socket()
        self._xfer_up.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._xfer_up.bind((host, 0))
        self._xfer_up.listen(2)
        self._xfer_up_port = self._xfer_up.getsockname()[1]

        self._xfer_down = socket.socket()
        self._xfer_down.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._xfer_down.bind((host, 0))
        self._xfer_down.listen(2)
        self._xfer_down_port = self._xfer_down.getsockname()[1]

    # ------------------------------------------------------------- lifecycle
    def __enter__(self) -> FakeController:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self) -> None:
        for target in (self._rpc.serve_forever, self._serve_stream):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self._rpc.shutdown()
        self._rpc.server_close()
        for sock in (self._stream_sock, self._xfer_up, self._xfer_down):
            with contextlib.suppress(OSError, AttributeError):
                sock.close()

    # ---------------------------------------------------------------- helpers
    def _record(self, name: str, *args) -> None:
        with self._lock:
            self.calls.append((name, args))

    def _blind(self) -> bool:
        """True when position getters should refuse. [PN 3.4]

        Error 14 arrives as a RETURN VALUE ([14, ...]), not as an XML-RPC
        fault -- that is how FWS observed it on the real controller, and a
        client distinguishing the two would break against a fake that raised.
        """
        return self.state.faulted

    def latch_fault(self, main: int, sub: int) -> None:
        with self._lock:
            self.state.error_main = main
            self.state.error_sub = sub
            self.state.moving = False

    # ------------------------------------------------------- scenario API
    # The STABLE surface for tests outside this package. Everything else on
    # this class is an implementation detail that may change with the
    # protocol work; these five names will not, because a customer's CI
    # suite is allowed to depend on them. See fws/testing/harness.py.

    def trip_fault(self, main: int = 1, sub: int = 22) -> None:
        """Put the controller into a fault, the way the arm does.

        While faulted, many getters answer `error 14` as a RETURN VALUE
        rather than raising -- the behaviour that makes 'absent' and
        'suppressed by a fault' indistinguishable, so it is worth testing.
        """
        self.latch_fault(main, sub)

    def clear_fault(self) -> None:
        """Clear the latched fault. The getters start answering again."""
        self.latch_fault(0, 0)

    def set_joints(self, joints: Sequence[float]) -> None:
        """Place the arm. Six degrees; the TCP follows through forward
        kinematics, so telemetry and the position getters stay consistent."""
        vals = [float(j) for j in joints]
        if len(vals) != 6:
            raise ValueError(f"six joint angles required, got {len(vals)}")
        with self._lock:
            self.state.joints = vals

    def set_force(self, ft: Sequence[float]) -> None:
        """Set the wrist force/torque reading: [fx, fy, fz, tx, ty, tz]."""
        vals = [float(v) for v in ft]
        if len(vals) != 6:
            raise ValueError(f"six force/torque values required, got {len(vals)}")
        with self._lock:
            self.state.ft = vals

    def corrupt_next_frame(self, count: int = 1) -> None:
        """Send `count` telemetry frames with a deliberately wrong checksum.

        A client must DROP these, not read them: a corrupt frame carries
        plausible-looking joint angles. FWS counts them in `bad_checksum`.
        """
        with self._lock:
            self._corrupt_frames = max(0, int(count))

    # ------------------------------------------------------------ RPC surface
    def _register(self) -> None:
        r = self._rpc.register_function

        def ok(*payload):
            return [0, *payload]

        # -- the one-way one ---------------------------------------------
        def ShutDownRobotOS(delay=0):
            """Modelled only so full-access tests can prove the call reaches
            the wire. On hardware this powers the controller off with no
            remote way back; the fake records it and stays up."""
            self._record("ShutDownRobotOS", delay)
            self.shut_down = True
            return ok()

        # -- identity ----------------------------------------------------
        def GetSoftwareVersion():
            self._record("GetSoftwareVersion")
            return ok("FR5-V1-002(V6.0)", self.software_version, "V3.8.25-QX")

        def GetSDKVersion():
            return ok("SDK 1.0.0.0")

        def GetControllerIP():
            return ok("192.168.100.155")     # the controller reports this fixed address

        # -- state -------------------------------------------------------
        def GetRobotErrorCode():
            return [0, self.state.error_main, self.state.error_sub]

        def GetRobotMotionDone():
            return ok(0 if self.state.moving else 1)

        def GetActualJointPosDegree(flag):
            if self._blind():
                return [14, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            return ok(*self.state.joints)

        def GetActualTCPPose(flag):
            if self._blind():
                return [14, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            return ok(*self.state.tcp())

        def GetJointSoftLimitDeg(flag):
            out = []
            for lo, hi in SOFT_LIMITS:
                out.extend([lo, hi])
            return ok(*out)

        def GetDefaultTransVel():
            return ok(1000.0)

        def GetProgramState():
            return ok(self.state.program_state)

        def GetLoadedProgram():
            return ok(self.state.loaded_program)

        # -- kinematics --------------------------------------------------
        def GetForwardKin(joints):
            if len(joints) != 6:
                raise Fault(-502, "GetForwardKin expects 6 joint values")
            return ok(*kinematics.forward(list(joints)))

        def GetInverseKin(kind, pose, config):
            if len(pose) != 6:
                raise Fault(-502, "GetInverseKin expects a 6-element pose")
            target = list(pose)
            if kind == 1 or kind == 2:                      # relative, base frame
                target = [self.state.tcp()[i] + pose[i] for i in range(6)]
            try:
                joints = kinematics.inverse(target, self.state.joints)
            except kinematics.Unreachable:
                return [112, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            return ok(*joints)

        # -- control -----------------------------------------------------
        def Mode(state):
            self._record("Mode", state)
            self.state.manual_mode = bool(state)
            return 0

        def RobotEnable(state):
            self._record("RobotEnable", state)
            self.state.enabled = bool(state)
            return 0

        def ResetAllError():
            self._record("ResetAllError")
            with self._lock:
                self.state.error_main = 0
                self.state.error_sub = 0
            return 0

        def StartJOG(ref, nb, dir_, vel, acc, max_dis):
            """Wire order is (ref, nb, dir, vel, acc, max_dis) — not the SDK's
            documented Python order."""
            self._record("StartJOG", ref, nb, dir_, vel, acc, max_dis)
            if self.state.faulted:
                return 14
            if not self.state.enabled:
                return 14
            if ref == 0 and not 1 <= nb <= 6:
                raise Fault(-502, "joint index out of range")
            self._begin_jog(int(ref), int(nb), int(dir_),
                            float(vel), float(max_dis))
            return 0

        def ImmStopJOG():
            self._record("ImmStopJOG")
            self.state.moving = False
            return 0

        def StopJOG(ref):
            self._record("StopJOG", ref)
            self.state.moving = False
            return 0

        def StopMotion():
            self._record("StopMotion")
            self.state.moving = False
            return 0

        def SetSpeed(vel):
            return 0

        def MoveL(args):
            """Strictly validates the 33-element array, so a malformed one fails
            in CI rather than moving a real arm."""
            self._record("MoveL", tuple(args))
            if not isinstance(args, list) or len(args) != 33:
                got = len(args) if hasattr(args, "__len__") else "?"
                raise Fault(-502,
                            f"MoveL expects a 33-element array, got {got}")
            return 0

        # -- force sensor -------------------------------------------------
        def FT_GetConfig():
            return ok(0, 23, 0, 0)

        def FT_GetForceTorqueRCS(*a):
            """Requires an argument despite its docstring saying otherwise."""
            if not a:
                raise Fault(-502, "Format string requests 1 items from array, "
                                  "but array has only 0 items.")
            return ok(*self.state.ft)

        def FT_GetForceTorqueOrigin(*a):
            """Answers error 3 at every arity: there is no uncompensated force
            reading on this firmware."""
            if not a:
                raise Fault(-502, "Format string requests 1 items from array, "
                                  "but array has only 0 items.")
            return [3, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        def FT_Activate(state):
            self._record("FT_Activate", state)
            self.state.ft_active = bool(state)
            return 0

        def FT_SetZero(state):
            self._record("FT_SetZero", state)
            return 0

        def GetForceSensorPayload():
            return ok(self.state.ft_payload_kg)

        def GetForceSensorPayloadCog():
            return ok(*self.state.ft_payload_cog)

        def SetForceSensorPayload(weight):
            self._record("SetForceSensorPayload", weight)
            self.state.ft_payload_kg = float(weight)
            return 0

        def SetForceSensorPayloadCog(x, y, z):
            self._record("SetForceSensorPayloadCog", x, y, z)
            self.state.ft_payload_cog = [float(x), float(y), float(z)]
            return 0

        def GetForceAndTorqueDragState():
            return ok(0, 0)

        def AxleSensorConfigGet():
            return ok(23, 0)

        def GetJointTorques(*a):
            return ok(*self.state.joint_torque)

        # -- files ---------------------------------------------------------
        def FileUpload(file_type, name):
            self._record("FileUpload", file_type, name)
            if file_type in (5, 6):
                raise Fault(-502, "refused: firmware file type")
            self._pending_upload = name
            # fileType 11 is the open-protocol device Lua and lands in its own
            # store. The framing is the generic 46-byte one either way; only
            # the destination differs.
            self._pending_upload_store = "open_luas" if file_type == 11 \
                else "files"
            self._open_transfer_port(upload=True)
            return 0

        def FileDownload(file_type, name):
            self._record("FileDownload", file_type, name)
            self._pending_download_header = 46
            if file_type == 1:
                # The controller log. Two-step rule as for the bundles:
                # RbLogDownloadPrepare builds the archive, this opens the port.
                # fileType 1 in the OTHER direction is the software-upgrade
                # slot (the integer is an opcode, not a type).
                if self._prepared_backup != name:
                    return -1
                self._prepared_backup = None
                self.rblog_fetches += 1
                self._pending_download = self._build_rblog()
                self._open_transfer_port(upload=False)
                return 0
            if file_type in (2, 3):
                if self._prepared_backup != name:
                    return -1        # prepare must come first, for this name
                payload = self.backups.get(name)
                if payload is None and file_type == 3:
                    # The user-data bundle is a REAL archive of the fake's
                    # stores, so ?source=controller listings work against the
                    # simulator exactly as against hardware.
                    payload = self._build_user_data()
                if payload is None:
                    payload = b"fake bundle for " + name.encode()
                self._pending_download = payload
                self._prepared_backup = None
                self._open_transfer_port(upload=False)
                return 0
            store = self.open_luas if file_type == 11 else self.files
            if name not in store:
                return -1
            self._pending_download = store[name]
            self._open_transfer_port(upload=False)
            return 0

        def RbLogDownloadPrepare():
            """Builds the log archive. Does NOT open the transfer port."""
            self._record("RbLogDownloadPrepare")
            self._prepared_backup = "rblog.tar.gz"
            return 0

        def LuaUpLoadUpdate(name):
            """Compiles the uploaded program; returns 0 or -1 only, with the
            verdict itself going to the log."""
            self._record("LuaUpLoadUpdate", name)
            if self.lua_validator_wedged:
                # Not a verdict: the controller is waiting out its retry cycle
                # on a dead web socket. Nothing is written to the log, so a
                # client that fetches it here finds nothing.
                time.sleep(self.wedge_delay_s)
                return -1
            payload = self.compile_lua(name, self.files.get(name, b""))
            self._lua_seq += 1
            self.lua_log.append(_lua_log_line(payload, self._lua_seq))
            return 0 if payload == "success" else -1

        def CtrlOpenLuaUpLoadCheck(name):
            """The open-protocol Lua's commit step, modelled as a plain accept
            (unverified on v3.8.5.1)."""
            self._record("CtrlOpenLuaUpLoadCheck", name)
            return 0

        def AllDataSourceDownloadPrepare():
            """Builds the archive (~14 s) but does NOT open the transfer port;
            FileDownload must be called separately."""
            self._record("AllDataSourceDownloadPrepare")
            self._prepared_backup = "alldatasource.tar.gz"
            return 0

        def DataPackageDownloadPrepare():
            self._record("DataPackageDownloadPrepare")
            self._prepared_backup = "fr_user_data.tar.gz"
            return 0

        def PointTableDownload(name):
            self._record("PointTableDownload", name)
            if name not in self.point_tables:
                return -1
            self._pending_download = self.point_tables[name]
            self._pending_download_header = self.point_table_download_header
            self._open_transfer_port(upload=False)
            return 0

        def PointTableUpload(name):
            """Point tables use a 44-byte header, not the Lua path's 46."""
            self._record("PointTableUpload", name)
            self._pending_upload = name
            self._pending_upload_kind = "point_table"
            self._open_transfer_port(upload=True)
            return 0

        def PointTableSwitch(name):
            self._record("PointTableSwitch", name)
            self.active_point_table = name or None
            return 0

        def FileDelete(file_type, name):
            """The wire call behind the SDK's LuaDelete wrapper. Deleting a
            missing file answers 144 ("the LUA file does not exist"), not
            silent success."""
            self._record("FileDelete", file_type, name)
            store = self.open_luas if file_type == 11 else self.files
            if name not in store:
                return 144
            store.pop(name, None)
            return 0

        def ProgramLoad(name):
            self._record("ProgramLoad", name)
            self.state.loaded_program = f"/fruser/{name}"
            return 0

        def ProgramRun():
            self._record("ProgramRun")
            if self.state.faulted:
                return 14
            self.state.program_state = 2
            return 0

        def ProgramPause():
            self._record("ProgramPause")
            self.state.program_state = 3
            return 0

        def ProgramResume():
            self._record("ProgramResume")
            self.state.program_state = 2
            return 0

        def ProgramStop():
            self._record("ProgramStop")
            self.state.program_state = 1
            return 0


        # -- control-layer surface ----------------------------------------
        # Present or absent to match a real v3.8.5.1 controller: the *WithID
        # and *Config variants are later-firmware additions and must be absent
        # here too, or capability probing would be untestable.
        def GetDI(index, flag):
            return ok(self.state.di.get(int(index), 0))

        def GetAI(index, flag):
            return ok(self.state.ai.get(int(index), 0.0))

        def GetToolDI(index, flag):
            return ok(0)

        def GetToolAI(index, flag):
            return ok(0.0)

        def SetDO(index, value, smooth, block):
            self._record("SetDO", index, value)
            self.state.do[int(index)] = int(value)
            return 0

        def SetAO(index, value, block):
            """The wire receives a 12-bit DAC count (percent x 40.95).

            The fake stores what it was SENT, so a client that forgets the
            scaling fails a test here rather than driving 2.4% of the
            intended output on real hardware.
            """
            self._record("SetAO", index, value)
            self.state.ao[int(index)] = float(value)
            return 0

        def GetActualTCPNum(flag):
            return ok(1)

        def GetTCPOffset(flag):
            return ok(0.0, 0.0, 50.0, 0.0, 0.0, 0.0)

        def GetActualWObjNum(flag):
            return ok(self.state.wobj_num)

        def SetToolCoord(frame_id, coord, type_, install, tool_id, load_num):
            """Wire: SetToolCoord(id, t_coord, type, install, toolID, loadNum).
            Tool frame ids are 1-15 (work object frames are 0-14)."""
            self._record("SetToolCoord", frame_id, tuple(coord), type_,
                         install, tool_id, load_num)
            if not 1 <= int(frame_id) <= 15:
                raise Fault(-502, "tool frame id out of range (1-15)")
            if len(coord) != 6:
                raise Fault(-502, "t_coord must have 6 elements")
            self.state.tool_frames[int(frame_id)] = [float(v) for v in coord]
            return 0

        def SetWObjCoord(frame_id, coord, ref_frame):
            """Wire: SetWObjCoord(id, coord, refFrame). Ids are 0-14."""
            self._record("SetWObjCoord", frame_id, tuple(coord), ref_frame)
            if not 0 <= int(frame_id) <= 14:
                raise Fault(-502, "work object frame id out of range (0-14)")
            if len(coord) != 6:
                raise Fault(-502, "coord must have 6 elements")
            self.state.work_frames[int(frame_id)] = [float(v) for v in coord]
            return 0

        def GetWObjOffset(flag):
            return ok(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        def GetActualToolFlangePose(flag):
            if self._blind():
                return [14, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            return ok(*self.state.tcp())

        def GetTargetPayload(flag):
            return ok(self.state.payload_kg)

        def GetTargetPayloadCog(flag):
            return ok(0.0, 0.0, 0.0)

        def SetLoadWeight(load_num, weight):
            """SetLoadWeight(loadNum, weight) -- two arguments, not one."""
            self._record("SetLoadWeight", load_num, weight)
            self.state.payload_kg = float(weight)
            return 0

        def ActGripper(index, action):
            """Motion-class command not owned by a typed FWS route; a motion
            exemplar the generic invoker will dispatch (StartJOG is
            refused there)."""
            self._record("ActGripper", index, action)
            return 0

        # Recovered RPCs: local in the registry because the SDK reads its own
        # state struct, but the controller answers them. See
        # fws/protocol/recovered_rpcs.py.
        def GetActualTCPSpeed():
            """Arity 0, as on v3.8.5.1."""
            return ok(*self.state.tcp_speed)

        def GetTargetTCPSpeed():
            return ok(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        def GetActualTCPCompositeSpeed():
            return ok(0.0, 0.0)

        def GetTargetTCPCompositeSpeed():
            return ok(0.0, 0.0)

        def GetMotionQueueLength():
            return ok(0)

        def GetGripperCurPosition():
            return ok(1, 0)

        def GetGripperCurSpeed():
            return ok(1, 0)

        def GetGripperCurCurrent():
            return ok(1, 0)

        def GetGripperVoltage():
            return ok(1, 0)

        def GetGripperTemp():
            return ok(1, 0)

        def GetCurrentLine():
            return ok(0)

        def GetActualJointSpeedsDegree(flag):
            return ok(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        def GetRobotCurJointsConfig(flag):
            return ok(2)

        def GetSlaveHardVersion():
            return ok("FR-CB-V0.5", "/", "/", "/", "/", "/", "/", "FR-TOOL")

        def GetSlaveFirmVersion():
            return ok("FR_CTRL_FV2.010.12", "FR_SLAVE_FV1.0")

        def GetDHCompensation():
            return ok(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        def GetRobotInstallAngle():
            return ok(0.0, 0.0)

        for fn in (ShutDownRobotOS,
                   GetSoftwareVersion, GetSDKVersion, GetControllerIP,
                   GetRobotErrorCode, GetRobotMotionDone,
                   GetActualJointPosDegree, GetActualTCPPose,
                   GetJointSoftLimitDeg, GetDefaultTransVel, GetProgramState,
                   GetLoadedProgram, GetForwardKin, GetInverseKin, Mode,
                   RobotEnable, ResetAllError, StartJOG, ImmStopJOG, StopJOG,
                   StopMotion, SetSpeed, MoveL, FT_GetConfig,
                   FT_GetForceTorqueRCS, FT_GetForceTorqueOrigin,
                   FT_Activate, FT_SetZero, GetForceSensorPayload,
                   GetForceSensorPayloadCog, SetForceSensorPayload,
                   SetForceSensorPayloadCog,
                   GetForceAndTorqueDragState, AxleSensorConfigGet,
                   GetJointTorques, FileUpload, FileDownload,
                   LuaUpLoadUpdate, GetDI, GetAI, GetToolDI, GetToolAI,
                   SetDO, SetAO, GetActualTCPNum, GetTCPOffset,
                   GetActualWObjNum, GetWObjOffset,
                   GetActualToolFlangePose, GetTargetPayload, ActGripper,
                   GetActualTCPSpeed, GetTargetTCPSpeed,
                   GetActualTCPCompositeSpeed,
                   GetTargetTCPCompositeSpeed, GetMotionQueueLength,
                   GetGripperCurPosition, GetGripperCurSpeed,
                   GetGripperCurCurrent, GetGripperVoltage,
                   GetGripperTemp,
                   GetTargetPayloadCog, SetLoadWeight, GetCurrentLine,
                   GetActualJointSpeedsDegree,
                   GetActualTCPCompositeSpeed, GetRobotCurJointsConfig,
                   GetSlaveHardVersion, GetSlaveFirmVersion,
                   GetDHCompensation, GetRobotInstallAngle, FileDelete,
                   SetToolCoord, SetWObjCoord,
                   AllDataSourceDownloadPrepare,
                   DataPackageDownloadPrepare, PointTableDownload,
                   PointTableUpload, PointTableSwitch,
                   RbLogDownloadPrepare, CtrlOpenLuaUpLoadCheck,
                   ProgramLoad, ProgramRun, ProgramPause,
                   ProgramResume, ProgramStop):
            r(fn, fn.__name__)

        # Anything not registered above is reported as fault -506 by
        # _Server._dispatch, matching the real controller (GetDO is the known
        # case). Introspection is deliberately not registered: system.listMethods
        # can power a controller off, so the fake must not offer it either.

    # ------------------------------------------------------------------ motion
    def _begin_jog(self, ref: int, nb: int, dir_: int,
                   vel: float, max_dis: float) -> None:
        """Simulate a bounded jog, including its start latency."""
        sign = 1.0 if dir_ else -1.0
        joint = nb - 1

        def run():
            # The arm does not move the instant StartJOG returns; a closed loop
            # that assumes it does will stack commands.
            time.sleep(self.jog_start_latency_s)
            if self._stop.is_set():
                return
            with self._lock:
                self.state.moving = True
            rate = (DEG_PER_SEC_PER_PCT if ref == 0
                    else MM_PER_SEC_PER_PCT) * vel
            travelled = 0.0
            tick = 0.02
            while travelled < max_dis and not self._stop.is_set():
                if not self.state.moving:        # stopped by ImmStopJOG
                    return
                step = min(rate * tick, max_dis - travelled)
                travelled += step
                with self._lock:
                    if ref == 0:
                        new = self.state.joints[joint] + sign * step
                        lo, hi = SOFT_LIMITS[joint]
                        if not lo <= new <= hi:
                            # Soft-limit violation latches a fault and stops
                            # all motion until reset.
                            self.state.joints[joint] = max(lo, min(hi, new))
                            self.state.error_main, self.state.error_sub = 1, 22
                            self.state.moving = False
                            return
                        self.state.joints[joint] = new
                    else:
                        # Cartesian jog: move the TCP, solve back to joints.
                        pose = self.state.tcp()
                        pose[nb - 1] += sign * step
                        try:
                            self.state.joints = kinematics.inverse(
                                pose, self.state.joints)
                        except kinematics.Unreachable:
                            self.state.moving = False
                            return
                time.sleep(tick)
            with self._lock:
                self.state.moving = False

        threading.Thread(target=run, daemon=True).start()

    # --------------------------------------------------------------- telemetry
    def build_frame(self) -> bytes:
        """A 433-byte telemetry frame with a correct checksum."""
        buf = bytearray(FRAME_LEN)
        buf[0:2] = b"\x5a\x5a"
        buf[2] = self._frame_counter & 0xFF
        struct.pack_into("<H", buf, 3, DATA_LEN)
        buf[5] = self.state.program_state
        struct.pack_into("<6d", buf, JOINTS_OFF, *self.state.joints)
        struct.pack_into("<6d", buf, TCP_OFF, *self.state.tcp())
        struct.pack_into("<6d", buf, TORQUE_OFF,
                         *[t * TORQUE_SCALE
                           for t in self.state.joint_torque])
        struct.pack_into("<6d", buf, FT_OFF, *self.state.ft)
        checksum = sum(buf[:5 + DATA_LEN]) & 0xFFFF
        if self._corrupt_frames > 0:
            # Deliberately wrong, for corrupt_next_frame(). The DATA is left
            # plausible on purpose: a client that ignores the checksum reads
            # a believable pose, which is the failure worth testing.
            checksum = (checksum + 1) & 0xFFFF
            self._corrupt_frames -= 1
        struct.pack_into("<H", buf, 5 + DATA_LEN, checksum)
        self._frame_counter += 1
        return bytes(buf)

    def _serve_stream(self) -> None:
        """Port 8083 serves exactly one client; a second connection is accepted
        at TCP level but never receives a frame."""
        while not self._stop.is_set():
            try:
                conn, _ = self._stream_sock.accept()
            except OSError:
                return
            if self._stream_client_connected:
                # Accepted, then ignored. Deliberately not closed.
                continue
            self._stream_client_connected = True
            threading.Thread(target=self._push_frames, args=(conn,),
                             daemon=True).start()

    def _push_frames(self, conn: socket.socket) -> None:
        try:
            with contextlib.suppress(OSError):
                while not self._stop.is_set():
                    conn.sendall(self.build_frame())
                    time.sleep(self.stream_period)
        finally:
            # Release the single-client slot even if the peer vanished, or the
            # fake would refuse every reconnect for the rest of the run.
            self._stream_client_connected = False
            with contextlib.suppress(OSError):
                conn.close()

    # --------------------------------------------------------- Lua compiler
    def compile_lua(self, name: str, source: bytes) -> str:
        """The verdict payload the controller would log for this program:
        success, absent function, wrong argument count, or failed
        point-name lookup."""
        text = source.decode("utf-8", "replace")
        for lineno, raw in enumerate(text.splitlines(), 1):
            line = raw.split("--", 1)[0].strip()
            if not line:
                continue
            m = LUA_CALL.match(line)
            if not m:
                continue
            fn, argtext = m.group(1), m.group(2).strip()
            args = _split_lua_args(argtext)
            where = f"lua_name:/fruser/{name}---line_num:{lineno}---error_info:"
            if fn not in self.lua_builtins:
                return (f"{where} attempt to call global {fn} "
                        f"(a nil value)")
            lo, hi = self.lua_builtins[fn]
            if not lo <= len(args) <= hi:
                return (f"{where} bad argument #{max(len(args), 1)} to {fn} "
                        f"(Error number of parameters)")
            if fn in self.lua_needs_a_taught_point:
                return (f"{where} failed to query the database "
                        f"(the data does not exist)")
        return "success"

    def _build_user_data(self) -> bytes:
        """fr_user_data.tar.gz, built from this fake's CURRENT stores.

        The real controller's archive is its file tree under root/web/file/;
        the fake's equivalent is whatever has been uploaded to it. Built on
        demand rather than seeded, so causality matches hardware: a listing
        taken after an upload shows the upload. Tests needing a specific tree
        override self.backups["fr_user_data.tar.gz"].
        """
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            def add(path: str, body: bytes) -> None:
                info = tarfile.TarInfo(path)
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
            for name, body in sorted(self.files.items()):
                add("root/web/file/user/" + name, body)
            for name, body in sorted(self.point_tables.items()):
                add("root/web/file/points/point_table/" + name, body)
        return buf.getvalue()

    def _build_rblog(self) -> bytes:
        """rblog.tar.gz with the ordering trap intact: the live verdicts are in
        the file with the EARLIER name and mtime, the stale ones in the
        newer-looking file, because the controller clock runs backwards across
        reboots."""
        buf = io.BytesIO()
        members = [
            ("rblog/rblog_2015-01-06_09-58-13.455.log", self.lua_log, 1_000),
            ("rblog/rblog_2026-03-03_16-56-58.504.log", self.stale_lua_log,
             2_000_000_000),
        ]
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for path, lines, mtime in members:
                blob = ("\n".join(lines) + "\n").encode()
                info = tarfile.TarInfo(path)
                info.size = len(blob)
                info.mtime = mtime
                tar.addfile(info, io.BytesIO(blob))
        return buf.getvalue()

    # ------------------------------------------------------------- file ports
    def _open_transfer_port(self, *, upload: bool) -> None:
        """Serve one transfer on the persistent listener."""
        sock = self._xfer_up if upload else self._xfer_down

        def serve():
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            try:
                if upload:
                    data = b""
                    while not data.endswith(b"/b/f"):
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                    # Header widths differ by kind: Lua uses a 10-digit size
                    # (46 bytes total), point tables an 8-digit one (44). A
                    # client that reuses the wrong framing must fail here.
                    if self._pending_upload_kind == "point_table":
                        head, body = data[:44], data[44:-4]
                        # Validate the FORMAT, not just the length: point
                        # tables zero-pad the size to 8 digits, Lua right-aligns
                        # it in 10 with spaces, so "all digits at 4:12"
                        # separates them.
                        size_field = head[4:12]
                        md5_field = head[12:44]
                        if (not head.startswith(b"/f/b")
                                or not size_field.isdigit()
                                or len(md5_field) != 32
                                or not all(c in b"0123456789abcdefABCDEF"
                                           for c in md5_field)):
                            conn.sendall(b"ERROR bad point-table header")
                            self._pending_upload_kind = "lua"
                            return
                        self.point_tables[self._pending_upload] = body
                    else:
                        body = data[46:-4]
                        if self._pending_upload:
                            store = getattr(self, self._pending_upload_store)
                            store[self._pending_upload] = body
                    self._pending_upload_kind = "lua"
                    self._pending_upload_store = "files"
                    conn.sendall(b"SUCCESS")
                else:
                    import hashlib
                    body = self._pending_download or b""
                    # The width is per-transfer: generic downloads are framed
                    # in 46 bytes, and the point-table width is disputed
                    # between the vendor's own two parsers. See
                    # `point_table_download_header`.
                    width = self._pending_download_header
                    total = len(body) + width + 4
                    digits = width - 4 - 32
                    size = (f"{total:{digits}d}" if width == 46
                            else f"{total:0{digits}d}")
                    conn.sendall(b"/f/b" + size.encode()
                                 + hashlib.md5(body).hexdigest().encode()
                                 + body + b"/b/f")
                    self._pending_download_header = 46
            finally:
                conn.close()

        threading.Thread(target=serve, daemon=True).start()

    @property
    def upload_port(self) -> int | None:
        return self._xfer_up_port

    @property
    def download_port(self) -> int | None:
        return self._xfer_down_port
