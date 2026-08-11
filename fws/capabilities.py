"""Runtime capability discovery: probe the controller (read-only) and report
each feature as AVAILABLE, ABSENT, or UNKNOWN.

Probes are getters only, none that move the arm or change configuration.
UNKNOWN is distinct from ABSENT: it means the probe could not reach the
controller, not that the firmware lacks the feature, so require() re-probes on
UNKNOWN rather than reporting a firmware limitation.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .driver import ControllerFault, RobotDriver, RobotError, TransportError

#: The controller answered and said yes.
AVAILABLE = "available"
#: The controller answered and said no. A fact about this firmware.
ABSENT = "absent"
#: Could not ask, or could not read the answer. Not a fact about the firmware.
UNKNOWN = "unknown"

# (feature, method, args). Every entry is a getter with no side effects.
PROBES: tuple[tuple[str, str, tuple], ...] = (
    ("identity.version",        "GetSoftwareVersion", ()),
    ("identity.controller_ip",  "GetControllerIP", ()),
    ("identity.install_angle",  "GetRobotInstallAngle", ()),
    ("identity.slave_hardware", "GetSlaveHardVersion", ()),
    ("identity.slave_firmware", "GetSlaveFirmVersion", ()),
    ("identity.dh_compensation", "GetDHCompensation", ()),

    ("io.digital_in",           "GetDI", (0, 0)),
    ("io.analog_in",            "GetAI", (0, 0)),
    ("io.tool_digital_in",      "GetToolDI", (0, 0)),
    ("io.tool_analog_in",       "GetToolAI", (0, 0)),
    ("io.digital_config",       "GetDIConfig", ()),

    ("frames.tool_number",      "GetActualTCPNum", (0,)),
    ("frames.tool_offset",      "GetTCPOffset", (0,)),
    ("frames.tool_by_id",       "GetToolCoordWithID", (1,)),
    ("frames.wobj_number",      "GetActualWObjNum", (0,)),
    ("frames.wobj_offset",      "GetWObjOffset", (0,)),
    ("frames.flange_pose",      "GetActualToolFlangePose", (0,)),

    ("payload.mass",            "GetTargetPayload", (0,)),
    ("payload.cog",             "GetTargetPayloadCog", (0,)),
    ("payload.by_id",           "GetTargetPayloadWithID", (1,)),

    ("program.state",           "GetProgramState", ()),
    ("program.current_line",    "GetCurrentLine", ()),
    ("program.loaded",          "GetLoadedProgram", ()),

    ("motion.done",             "GetRobotMotionDone", ()),
    ("motion.joint_speeds",     "GetActualJointSpeedsDegree", (0,)),
    ("motion.tcp_speed",        "GetActualTCPCompositeSpeed", (0,)),
    ("motion.joint_config",     "GetRobotCurJointsConfig", (0,)),
    ("motion.default_trans_vel", "GetDefaultTransVel", ()),

    ("safety.joint_limits",     "GetJointSoftLimitDeg", (0,)),
    ("safety.error_code",       "GetRobotErrorCode", ()),

    ("kinematics.forward",      "GetForwardKin", ([0.0] * 6,)),
)


@dataclass
class Capability:
    feature: str
    method: str
    state: str
    detail: str = ""

    @property
    def available(self) -> bool:
        """state == AVAILABLE, so UNKNOWN reads False. Use `state` to
        distinguish ABSENT from UNKNOWN."""
        return self.state == AVAILABLE


@dataclass
class Capabilities:
    """Probed once at startup, cached, refreshable on demand."""

    driver: RobotDriver
    _map: dict[str, Capability] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    probed_at: float | None = None

    def _probe_one(self, feature: str, method: str, args: tuple) -> Capability:
        """Probe one getter, classified into AVAILABLE/ABSENT/UNKNOWN."""
        try:
            result = self.driver._call(method, *args)
        except ControllerFault as e:
            # The controller answered "no". That is a fact about the firmware.
            return Capability(feature, method, ABSENT,
                              str(e).split(": ", 1)[-1][:90])
        except TransportError as e:
            # No answer; says nothing about the firmware.
            return Capability(feature, method, UNKNOWN,
                              f"could not ask: {str(e).split(': ', 1)[-1][:80]}")
        except RobotError as e:
            # A refusal or introspection block (an FWS decision, not the
            # controller's); must not read as a firmware limitation.
            return Capability(feature, method, UNKNOWN,
                              f"FWS did not send this probe: {str(e)[:80]}")

        if isinstance(result, list) and result:
            code = result[0]
            if code == 0:
                return Capability(feature, method, AVAILABLE)
            return Capability(feature, method, ABSENT,
                              f"returned error {code}")
        if isinstance(result, int):
            return (Capability(feature, method, AVAILABLE) if result == 0
                    else Capability(feature, method, ABSENT,
                                    f"returned error {result}"))
        # An empty list, None, or a string: unrecognised, so UNKNOWN rather
        # than assumed fine.
        return Capability(feature, method, UNKNOWN,
                          f"unrecognised reply {type(result).__name__}: "
                          f"{str(result)[:60]}")

    def probe(self) -> dict[str, Capability]:
        found = {f: self._probe_one(f, m, a) for f, m, a in PROBES}
        with self._lock:
            self._map = found
            self.probed_at = time.time()
        return found

    def state(self, feature: str) -> str:
        """AVAILABLE, ABSENT or UNKNOWN. Unprobed is UNKNOWN, not ABSENT."""
        with self._lock:
            cap = self._map.get(feature)
        return cap.state if cap else UNKNOWN

    def has(self, feature: str) -> bool:
        """True only when the controller confirmed the feature.
        UNKNOWN reads False; use `state` to check for ABSENT."""
        return self.state(feature) == AVAILABLE

    def require(self, feature: str) -> None:
        """Raise unless the feature is confirmed present. On UNKNOWN, re-probe
        rather than reporting a firmware limitation."""
        st = self.state(feature)
        if st == AVAILABLE:
            return

        if st == UNKNOWN:
            entry = next((p for p in PROBES if p[0] == feature), None)
            if entry is None:
                raise RobotError(
                    f"'{feature}' is not a probed capability. FWS cannot say "
                    f"whether this controller supports it.")
            cap = self._probe_one(*entry)
            with self._lock:
                self._map[feature] = cap
            if cap.state == AVAILABLE:
                return
            st = cap.state
        else:
            with self._lock:
                cap = self._map[feature]

        if st == ABSENT:
            raise RobotError(
                f"this controller does not support '{feature}' "
                f"({cap.method}: {cap.detail or 'unavailable'}). It is a "
                f"later-firmware feature; see GET /api/v1/capabilities.")
        raise RobotError(
            f"FWS does not know whether this controller supports '{feature}'. "
            f"The probe could not reach it ({cap.method}: {cap.detail}). This "
            f"is NOT evidence the feature is missing -- check the link and "
            f"POST /api/v1/capabilities/refresh.")

    def as_dict(self) -> dict:
        with self._lock:
            caps = dict(self._map)
        groups: dict[str, dict] = {}
        for feature, cap in sorted(caps.items()):
            group, _, leaf = feature.partition(".")
            groups.setdefault(group, {})[leaf] = {
                "available": cap.available,
                "state": cap.state,
                "method": cap.method,
                **({"detail": cap.detail} if cap.detail else {}),
            }
        n = {AVAILABLE: 0, ABSENT: 0, UNKNOWN: 0}
        for c in caps.values():
            n[c.state] = n.get(c.state, 0) + 1
        return {
            "probed_at": self.probed_at,
            "total": len(caps),
            "available": n[AVAILABLE],
            "absent": n[ABSENT],
            "unknown": n[UNKNOWN],
            # Kept for the old shape; sums ABSENT and UNKNOWN (two different
            # things).
            "unavailable": n[ABSENT] + n[UNKNOWN],
            "states": {
                AVAILABLE: "the controller answered and said yes",
                ABSENT: "the controller answered and said no -- a fact about "
                        "this firmware",
                UNKNOWN: "FWS could not ask, or could not read the answer. "
                         "NOT evidence the feature is missing",
            },
            "groups": groups,
        }
