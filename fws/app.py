"""REST + WebSocket gateway in front of a Fairino robot controller.

Owns the single-client 8083 telemetry connection, serialises the XML-RPC
channel behind one lock, and pre-flights motion against soft limits. Binds to
127.0.0.1 by default.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import capabilities as caps_mod
from . import config as config_mod
from .access import full_access, set_full_access
from .auth import KeyStore, is_open_path
from .backup_api import build as build_backup_api
from .capabilities import Capabilities
from .commands_api import build_router
from .control import DOMAINS, MAX_TTL_S, Conflict, ControlLock
from .control_api import build as build_control_api
from .driver import RobotDriver, RobotError
from .eventbus import EdgeDetector, EventBus
from .events import AuditLog
from .files_api import build as build_files_api
from .force_api import build as build_force_api
from .gripper_api import build as build_gripper_api
from .invoke_api import build as build_invoke_api
from .lua_api import router as lua_router
from .metrics import render as render_metrics
from .model_api import build as build_model_api
from .move_api import build as build_move_api
from .poses import PoseStore
from .poses_api import build as build_poses_api
from .programs_api import build as build_programs_api
from .recorder import SAMPLE_HZ, FlightRecorder
from .recorder_api import build as build_recorder_api
from .runners import AbortRegistry
from .services_api import build as build_services_api
from .system_api import build as build_system_api
from .telemetry import Telemetry


# Module-level settings so `uvicorn fws.app:app` keeps working with defaults.
# create_app() rebinds these; route handlers resolve them at call time.
def _pkg_version() -> str:
    """Report the installed package version to /docs and /openapi.json rather
    than a hardcoded string that drifts from the release."""
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("fairino-fws")
    except PackageNotFoundError:  # editable checkout without metadata
        return "0.0.0+dev"


settings = config_mod.load()

_ERROR_POLL_INTERVAL_S = 0.5


@contextlib.asynccontextmanager
async def _lifespan(_: FastAPI):
    """Start telemetry, control and the fault poller; stop them on shutdown.

    The startup safety check runs here so every entry point that starts the
    server enforces it. A single-client 8083 connection left half-open blocks
    other consumers until the controller reclaims the slot.
    """
    problems = settings.check_safe_to_start()
    if problems:
        raise RuntimeError(
            "FWS refuses to start:\n  - " + "\n  - ".join(problems))
    telemetry.start()
    control.start()
    # Probe capabilities once at startup; non-fatal, so an unreachable robot
    # still serves /health and /capabilities.
    threading.Thread(target=_safe_probe, daemon=True).start()
    threading.Thread(target=_error_poller, daemon=True).start()
    threading.Thread(target=_recorder_sampler, daemon=True).start()
    try:
        yield
    finally:
        control.close()
        telemetry.close()


app = FastAPI(
    lifespan=_lifespan,
    title="Fairino Web Services",
    description=(
        "REST + WebSocket gateway for Fairino collaborative robots. "
        "NOTE: no endpoint here is an emergency stop. The physical E-stop is "
        "hardware only. /motion/stop is a functional stop of jog motion."
    ),
    version=_pkg_version(),
)

driver = RobotDriver(settings.robot.ip,
                     timeout=settings.robot.rpc_timeout_s,
                     port=settings.robot.rpc_port,
                     upload_port=settings.robot.upload_port,
                     download_port=settings.robot.download_port)
telemetry = Telemetry(settings.robot.ip, port=settings.robot.telemetry_port)
keys = KeyStore(settings.auth.api_keys_file)
capabilities = Capabilities(driver)
audit = AuditLog()
bus = EventBus()
edges = EdgeDetector(bus)
# Every audited command is also pushed, so a client learns what
# happened as it happens instead of polling for it.
audit.on_record = bus.publish
abortables = AbortRegistry()
poses = PoseStore(settings.server.data_dir / "poses.json")
recorder = FlightRecorder(settings.server.data_dir)


def _stop_program_if_running() -> str:
    """ProgramStop, but only when the interpreter is running or paused."""
    try:
        rtn = driver._call("GetProgramState")
    except RobotError as e:
        return f"state unreadable: {e}"
    code = rtn[1] if isinstance(rtn, list) and len(rtn) > 1 else None
    if code not in (2, 3):
        return "not running"
    try:
        driver._ok(driver._call("ProgramStop"), "ProgramStop")
        return "ok"
    except RobotError as e:
        return f"error: {e}"


def _on_lease_lapse(reason: str, lease) -> None:
    """Disconnect watchdog: a lapsed lease stops what it was driving.

    A running program is stopped through the interpreter (ProgramStop) BEFORE
    the motion cascade: a StopMotion cut into a program's move leaves the
    interpreter half-stopped, and on v3.8.5.1 an upload into that state
    wedged the controller until a reboot (measured 2026-08-19). Motion then
    gets the same cascade as an explicit stop.
    """
    held = set(lease.domains)
    if not (held & {"motion", "program"}):
        return
    results: dict[str, str] = {}
    if "program" in held:
        results["ProgramStop"] = _stop_program_if_running()
    if "motion" in held:
        results.update(_stop_all())
    # The most important line the audit trail can hold: the gateway stopped
    # the arm on its own, because a client went away mid-move. It used to
    # exist ONLY as a print, so an incident review found the arm stopped and
    # nothing saying who or why.
    audit.record("watchdog.stop", actor=lease.client_id, reason=reason,
                 domains=sorted(lease.domains), results=results)
    print(f"[watchdog] {lease.client_id} lapsed ({reason}); "
          f"stop issued: {results}", flush=True)


control = ControlLock(on_lapse=_on_lease_lapse)


def create_app(new_settings: config_mod.Settings | None = None) -> FastAPI:
    """Rebind module globals to a specific configuration before startup.

    Route handlers read these globals at call time, so rebinding is sufficient.
    """
    global settings, driver, telemetry, keys, capabilities, LIMIT_MARGIN
    global poses, recorder
    if new_settings is not None:
        settings = new_settings
        # Latch the developer switch before anything can consult it.
        set_full_access(settings.features.full_access)
        driver = RobotDriver(settings.robot.ip,
                             timeout=settings.robot.rpc_timeout_s,
                             port=settings.robot.rpc_port,
                             upload_port=settings.robot.upload_port,
                             download_port=settings.robot.download_port)
        telemetry = Telemetry(settings.robot.ip,
                              port=settings.robot.telemetry_port)
        keys = KeyStore(settings.auth.api_keys_file)
        capabilities = Capabilities(driver)
        LIMIT_MARGIN = settings.limits.limit_margin_deg
        # The durable sink was supported by AuditLog and wired by nobody, so
        # every trail died with the process.
        audit.path = settings.audit_file()
        poses = PoseStore(settings.server.data_dir / "poses.json")
        recorder = FlightRecorder(settings.server.data_dir)
    return app


def _safe_probe() -> None:
    try:
        found = capabilities.probe()
        n = sum(1 for c in found.values() if c.state == caps_mod.AVAILABLE)
        unknown = sum(1 for c in found.values()
                      if c.state == caps_mod.UNKNOWN)
        # Unknowns are reported separately, not folded into "unavailable".
        extra = (f", {unknown} UNKNOWN (could not ask -- not evidence they "
                 f"are missing)" if unknown else "")
        print(f"[capabilities] {n}/{len(found)} features available on "
              f"this controller{extra}", flush=True)
    except Exception as e:
        print(f"[capabilities] probe failed: {e}", flush=True)


def _recorder_sampler():
    """Feed the flight recorder at 10 Hz, matching the 8083 push rate.

    Samples the telemetry snapshot rather than tapping the parser: the
    recorder must never be able to slow down or break the stream reader.
    """
    period = 1.0 / SAMPLE_HZ
    while True:
        try:
            frame = telemetry.snapshot()
            recorder.feed({**frame,
                           "error_main": _errors.get("main"),
                           "error_sub": _errors.get("sub")})
        except Exception:
            # Recording is diagnostics. It must never take the gateway down.
            pass
        time.sleep(period)


_errors: dict = {"main": 0, "sub": 0, "ok": False}


def _error_poller():
    """Poll latched fault codes at 2 Hz on a background thread."""
    while True:
        try:
            main, sub = driver.error_code()
            _errors.update(main=main, sub=sub, ok=True, msg=None)
            was_faulted = edges._faulted
            edges.fault(main, sub)
            # The seconds BEFORE the fault are the ones worth having, and
            # they are already in the ring. Dump on the rising edge only.
            if edges._faulted and was_faulted is False:
                name = recorder.dump(f"fault {main}/{sub}")
                if name:
                    bus.publish("recording.dumped", file=name,
                                main=main, sub=sub)
        except RobotError as e:
            _errors.update(ok=False, msg=str(e))
        snap = telemetry.snapshot()
        edges.stream(bool(snap.get("connected")))
        edges.program_state(snap.get("program_state"))
        time.sleep(0.5)


# ---------------------------------------------------------------- models
class JogRequest(BaseModel):
    joint: int = Field(ge=1, le=6)
    # Bounded because the handler is truthy: without ge/le, a client sending
    # the plausible-looking -1 would be silently accepted and jog POSITIVE.
    direction: int = Field(ge=0, le=1, description="1 = positive, 0 = negative")
    # The step/vel ceilings are enforced in the handler, not here, so that
    # features.full_access can lift them. The model keeps only the bound that
    # is about meaning rather than magnitude: direction, above.
    step: float = Field(default=5.0, gt=0,
                        description="degrees; bounded by limits.jog_max_deg")
    vel: float = Field(default=10.0, gt=0,
                       description="percent; bounded by limits.jog_max_vel_pct")


class EnableRequest(BaseModel):
    enable: bool
    confirm: bool = Field(default=False, description="must be true to enable")
    mode: str = Field(
        default="manual", pattern="^(manual|auto)$",
        description=("operating mode to leave the controller in after the "
                     "enable: the wire sequence always passes through manual, "
                     "so 'auto' (needed for program starts) is re-applied "
                     "afterwards; the response reports the resulting mode"))


# ---------------------------------------------------------------- system
@app.get("/api/v1/system/health")
def health():
    t = telemetry.snapshot()
    warnings: list[str] = []
    # Checks that could not run; kept distinct from "nothing is wrong".
    not_checked: list[dict] = []

    # A force-compensation mismatch is silent by construction; surface it here.
    try:
        s_kg = driver._call("GetForceSensorPayload")
        r_kg = driver._call("GetTargetPayload", 1)
        if (isinstance(s_kg, list) and isinstance(r_kg, list)
                and s_kg[0] == 0 and r_kg[0] == 0
                and abs(float(s_kg[1]) - float(r_kg[1])) > 0.02):
            warnings.append(
                f"force sensor payload is {s_kg[1]} kg while the arm carries "
                f"{r_kg[1]} kg. The force CONTROL functions compensate with "
                f"the sensor value, so a force-guarded move would trip at the "
                f"wrong threshold. GET /api/v1/sensors/force is unaffected -- "
                f"it is not compensated at all. See GET /api/v1/force/payload")
    except (RobotError, IndexError, ValueError, TypeError) as e:
        not_checked.append({
            "check": "force payload compensation mismatch",
            "why": f"{type(e).__name__}: {e}",
            "means": ("FWS cannot say whether the force compensation is "
                      "correct. This is not a clean result."),
        })

    # If the reap thread is not running, a client that disconnects while
    # holding `motion` never triggers a stop.
    wd = control.watchdog()
    if not wd["healthy"]:
        warnings.append(
            f"control watchdog is not healthy (running={wd['running']}, "
            f"reap_errors={wd['reap_errors']}, "
            f"lapse_callback_errors={wd['lapse_callback_errors']}). A lease "
            f"holder that disconnects may NOT trigger a stop.")

    if not t.get("connected", False):
        warnings.append(
            "the 8083 telemetry stream is not connected, so live position, "
            "force and motion state are unavailable or stale")

    # The durable audit trail. A sink that has started failing is silent by
    # construction -- the API keeps answering and the in-memory deque keeps
    # filling -- so health is the only place it can surface.
    au = audit.health()
    if au["sink_errors"]:
        warnings.append(
            f"the audit file sink has failed {au['sink_errors']} time(s) "
            f"(last: {au['sink_last_error']}). Events are in memory only, so "
            f"a restart loses them.")

    return {
        "audit": au,
        "events": bus.health(),
        "recorder": recorder.health(),
        "warnings": warnings,
        "checks_not_run": not_checked,
        "all_checks_ran": not not_checked,
        "stream_connected": t.get("connected", False),
        "frames": t.get("frames", 0),
        "bad_checksum": t.get("bad_checksum", 0),
        "stream_error": t.get("error"),
        "control_watchdog": wd,
        "config": settings.summary(),
    }


@app.get("/api/v1/system/version")
def version():
    try:
        v = driver.version()
    except RobotError as e:
        raise HTTPException(503, str(e)) from e
    return {"hardware": v.hardware, "software": v.software, "qnx": v.qnx}


@app.get("/api/v1/state")
def state():
    t = telemetry.snapshot()
    try:
        main, sub = driver.error_code()
    except RobotError as e:
        raise HTTPException(503, str(e)) from e
    return {
        "joints": t.get("joints"),
        "tcp": t.get("tcp"),
        "force": t.get("ft"),
        "error_main": main,
        "error_sub": sub,
        "stream_connected": t.get("connected", False),
        "age_s": None if not t.get("ts") else round(time.time() - t["ts"], 3),
    }


# ---------------------------------------------------------------- control


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    """API-key auth, active only when keys are configured.

    Open paths (stop, health) are never authenticated. Gated on `configured`:
    a key file with zero usable keys 401s every request rather than disabling
    auth.
    """
    if not keys.configured or is_open_path(request.url.path):
        return await call_next(request)

    label = keys.identify(request.headers.get("X-API-Key"))
    if label is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=401,
            content={"detail": "missing or invalid X-API-Key"},
            headers={"WWW-Authenticate": "X-API-Key"},
        )
    request.state.api_key_label = label
    return await call_next(request)


# Added after (therefore wrapping) the auth middleware: whether a caller holds
# a key changes nothing about what a read-only gateway will do.
@app.middleware("http")
async def _read_only_middleware(request: Request, call_next):
    """Refuse every state-changing operation when server.read_only is set.

    The rule is by verb, not by route, so a route added later cannot slip
    through an incomplete denylist. This includes POST /motion/stop: a
    gateway that can stop a program someone else started is not read-only.
    The telemetry WebSocket is unaffected -- it is not an HTTP request.
    """
    if settings.server.read_only and request.method not in (
            "GET", "HEAD", "OPTIONS"):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=403,
            content={"detail": (
                "this gateway is running read-only: it observes the robot "
                "and commands nothing, including stop. Use the physical "
                "E-stop for emergencies. Restart without read_only to "
                "enable commanding."
            )},
        )
    return await call_next(request)


# ------------------------------------------------------------- control lock
class AcquireRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=120)
    domains: list[str] = Field(default_factory=lambda: ["motion"])
    ttl_s: float = Field(
        default=30.0, ge=5.0,
        description=(f"seconds before the lease lapses without a heartbeat; "
                     f"values above {MAX_TTL_S:.0f} are clamped and reported "
                     f"as ttl_clamped, not rejected"))


def _require(domain: str, token: str | None) -> None:
    """Gate a write on the control lock: 428 if no token, 423 if held by another."""
    if full_access():
        return                      # the lock is one of the guards taken off
    lease = control.held_by(domain)
    if lease is None:
        return                      # unheld: single-client operation still works
    ok, reason = control.check(domain, token)
    if ok:
        return
    if not token:
        raise HTTPException(428, (
            f"'{domain}' is held by {lease.client_id}; acquire the control "
            f"lock and send X-FWS-Control-Token"))
    raise HTTPException(423, reason)


@app.get("/api/v1/control")
def control_status():
    return {"domains": list(DOMAINS), "holders": control.holders()}


@app.post("/api/v1/control", status_code=201)
def control_acquire(req: AcquireRequest):
    try:
        lease = control.acquire(req.client_id, req.domains, req.ttl_s)
    except Conflict as e:
        raise HTTPException(423, {
            "message": f"'{e.domain}' is already held",
            "holder": e.holder.as_dict(),
        }) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    out = lease.as_dict(redact=False)
    if req.ttl_s > MAX_TTL_S:
        # Say so in the grant rather than refusing: a client that asked for
        # more than the cap and got a 422 was observed to carry on without a
        # token, and every later command failed quietly (2026-08-19).
        out["ttl_clamped"] = {"requested_s": req.ttl_s, "granted_s": MAX_TTL_S}
    return out


@app.post("/api/v1/control/heartbeat")
def control_heartbeat(x_fws_control_token: str | None = Header(default=None),
                      ttl_s: float = 30.0):
    lease = control.renew(x_fws_control_token or "", ttl_s)
    if lease is None:
        raise HTTPException(404, "no lease for that token; it may have lapsed")
    return lease.as_dict()


@app.delete("/api/v1/control", status_code=204)
def control_release(x_fws_control_token: str | None = Header(default=None)):
    control.release(x_fws_control_token or "")
    return None


@app.delete("/api/v1/control/{domain}", status_code=200)
def control_break(domain: str):
    """Force-release a lease (administrative override); does not fire the watchdog."""
    if domain not in DOMAINS:
        raise HTTPException(422, f"unknown domain '{domain}'")
    broken = control.break_lock(domain)
    return {"broken": broken.as_dict() if broken else None}


@app.post("/api/v1/robot/enable")
def robot_enable(req: EnableRequest,
         x_fws_control_token: str | None = Header(default=None)):
    _require("motion", x_fws_control_token)
    if req.enable and not (req.confirm or full_access()):
        raise HTTPException(400, "enabling requires confirm=true")
    audit.record("robot.enable", enable=req.enable, mode=req.mode)
    try:
        # Enabling goes through manual on the wire (the disarming direction).
        # Callers that then expect to start a program need auto back, and
        # several did not know the enable had switched them: report the mode
        # the controller is left in, and re-apply auto when asked to.
        driver.set_mode(manual=True)
        driver.enable(req.enable)
        if req.enable and req.mode == "auto":
            driver.set_mode(manual=False)
    except RobotError as e:
        raise HTTPException(503, str(e)) from e
    return {"enabled": req.enable, "mode": driver.last_set_mode}


LIMIT_MARGIN = settings.limits.limit_margin_deg


def _limits() -> list[tuple[float, float]] | None:
    """Soft limits, read once and cached. None if unavailable."""
    global _LIMITS
    if _LIMITS is None:
        try:
            _LIMITS = driver.joint_limits()
        except RobotError:
            return None
    return _LIMITS


_LIMITS: list[tuple[float, float]] | None = None


@app.get("/api/v1/robot/limits")
def limits():
    lim = _limits()
    if lim is None:
        raise HTTPException(503, "soft limits unavailable")
    t = telemetry.snapshot()
    j = t.get("joints") or [None] * 6
    return {
        "limits": [{"joint": i + 1, "min": lo, "max": hi, "current": j[i],
                    "headroom_neg": None if j[i] is None else round(j[i] - lo, 3),
                    "headroom_pos": None if j[i] is None else round(hi - j[i], 3)}
                   for i, (lo, hi) in enumerate(lim)],
        "margin": LIMIT_MARGIN,
    }


@app.post("/api/v1/motion/jog")
def jog(req: JogRequest,
         x_fws_control_token: str | None = Header(default=None)):
    _require("motion", x_fws_control_token)
    # Pre-flight against soft limits from the 8083 stream: RPC position getters
    # return error 14 while the controller is faulted.
    lim = _limits()
    joints = telemetry.snapshot().get("joints")
    if lim and joints and not full_access():
        lo, hi = lim[req.joint - 1]
        cur = joints[req.joint - 1]
        predicted = cur + (req.step if req.direction else -req.step)
        if predicted > hi - LIMIT_MARGIN or predicted < lo + LIMIT_MARGIN:
            raise HTTPException(409, (
                f"blocked: J{req.joint} would reach {predicted:.3f}deg, "
                f"outside the safe band [{lo + LIMIT_MARGIN:.1f}, "
                f"{hi - LIMIT_MARGIN:.1f}]. Currently at {cur:.3f}deg. "
                f"Jog the other way."))
    if not full_access():
        if req.step > settings.limits.jog_max_deg:
            raise HTTPException(422, f"step exceeds configured limit "
                                     f"{settings.limits.jog_max_deg} deg")
        if req.vel > settings.limits.jog_max_vel_pct:
            raise HTTPException(422, f"vel exceeds configured limit "
                                     f"{settings.limits.jog_max_vel_pct}%")
    audit.record("motion.jog", joint=req.joint, direction=req.direction,
                 step=req.step, vel=req.vel)
    try:
        driver.jog(req.joint, bool(req.direction), req.step, req.vel)
    except RobotError as e:
        raise HTTPException(503, str(e)) from e
    return {"joint": req.joint, "step": req.step, "bounded": True}


class LinearJogRequest(BaseModel):
    axis: int = Field(ge=1, le=6, description="1-3 = X/Y/Z, 4-6 = RX/RY/RZ")
    # Same bound and reason as JogRequest.direction.
    direction: int = Field(ge=0, le=1, description="1 = positive, 0 = negative")
    # Ceilings enforced in the handler so full_access can lift them; see
    # JogRequest.
    step: float = Field(default=10.0, gt=0,
                        description="mm for axes 1-3, degrees for 4-6")
    vel: float = Field(default=10.0, gt=0)
    frame: str = Field(default="base", description="base or tool")


AXIS_NAMES = {1: "X", 2: "Y", 3: "Z", 4: "RX", 5: "RY", 6: "RZ"}


@app.post("/api/v1/motion/jog/linear")
def jog_linear(req: LinearJogRequest,
         x_fws_control_token: str | None = Header(default=None)):
    """Cartesian jog: solved backwards through IK and refused if any joint
    would exceed a soft limit."""
    _require("motion", x_fws_control_token)
    cap = (settings.limits.jog_max_mm if req.axis <= 3
           else settings.limits.rotation_max_deg)
    if not full_access():
        if req.step > cap:
            raise HTTPException(422,
                                f"step must be <= {cap} for axis {req.axis}")
        if req.vel > settings.limits.jog_max_vel_pct:
            raise HTTPException(422, f"vel exceeds configured limit "
                                     f"{settings.limits.jog_max_vel_pct}%")

    delta = [0.0] * 6
    delta[req.axis - 1] = req.step if req.direction else -req.step
    kind = 1 if req.frame == "base" else 2      # relative, base or tool frame

    try:
        target_joints = driver.inverse_kin(delta, kind=kind, config=-1)
    except RobotError as e:
        raise HTTPException(409, (
            f"blocked: no inverse-kinematics solution for a "
            f"{delta[req.axis - 1]:+g} move on {AXIS_NAMES[req.axis]} "
            f"({req.frame} frame). The pose is unreachable or near a "
            f"singularity. Underlying: {e}")) from e

    lim = _limits()
    if lim and not full_access():
        for i, (lo, hi) in enumerate(lim):
            if not (lo + LIMIT_MARGIN <= target_joints[i] <= hi - LIMIT_MARGIN):
                raise HTTPException(409, (
                    f"blocked: moving {AXIS_NAMES[req.axis]} by "
                    f"{delta[req.axis - 1]:+g} would put J{i + 1} at "
                    f"{target_joints[i]:.2f}deg, outside its safe band "
                    f"[{lo + LIMIT_MARGIN:.1f}, {hi - LIMIT_MARGIN:.1f}]."))

    floor = settings.limits.z_floor_mm
    if floor is not None and not full_access():
        # Solve the target FORWARD rather than adding the delta to the
        # current Z: in the tool frame a Z step is not a base-frame Z step,
        # and a floor that only works in one frame is worse than none.
        try:
            predicted = driver.forward_kin(target_joints)
        except RobotError as e:
            # A configured floor that could not be checked must refuse.
            raise HTTPException(409, (
                f"blocked: a z_floor of {floor:.1f}mm is configured but the "
                f"predicted TCP height could not be computed ({e}), so the "
                f"floor could not be checked.")) from e
        if predicted[2] < floor:
            raise HTTPException(409, (
                f"blocked: moving {AXIS_NAMES[req.axis]} by "
                f"{delta[req.axis - 1]:+g} would put the TCP at Z "
                f"{predicted[2]:.1f}mm, below the configured floor "
                f"{floor:.1f}mm."))

    audit.record("motion.jog_linear", axis=AXIS_NAMES[req.axis],
                 direction=req.direction, step=req.step, vel=req.vel,
                 frame=req.frame)
    try:
        driver.jog_linear(req.axis, bool(req.direction), req.step,
                          req.vel, req.frame)
    except RobotError as e:
        raise HTTPException(503, str(e)) from e
    return {
        "axis": AXIS_NAMES[req.axis], "frame": req.frame, "step": req.step,
        "predicted_joints": [round(j, 3) for j in target_joints],
    }


@app.post("/api/v1/errors/reset")
def reset_errors():
    """Clear latched faults. The condition that caused them may still hold."""
    audit.record("errors.reset")
    try:
        driver.reset_errors()
        main, sub = driver.error_code()
    except RobotError as e:
        raise HTTPException(503, str(e)) from e
    return {"error_main": main, "error_sub": sub,
            "cleared": main == 0 and sub == 0}



# ----------------------------------------------------------------- stop path
def _confirm_standstill(window_s: float = 1.0, tol: float = 0.05,
                        consecutive: int = 3) -> bool | None:
    """Confirm standstill from the 8083 stream.

    Returns True (standstill seen), False (still moving at window end), or None
    (no telemetry).
    """
    first = telemetry.snapshot().get("joints")
    if first is None:
        return None
    prev, still = first, 0
    deadline = time.time() + window_s
    while time.time() < deadline:
        time.sleep(0.05)
        cur = telemetry.snapshot().get("joints")
        if cur is None:
            return None
        if max(abs(a - b) for a, b in zip(cur, prev, strict=True)) < tol:
            still += 1
            if still >= consecutive:
                return True
        else:
            still = 0
        prev = cur
    return False


def _stop_all() -> dict[str, str]:
    """Issue every stop, each isolated: ImmStopJOG (jogs), StopMotion
    (program moves), runner aborts.

    ImmStopJOG does not stop program-space moves; StopMotion does.
    """
    results: dict[str, str] = {}
    for name, fn in (("ImmStopJOG", driver.stop),
                     ("StopMotion", driver.stop_motion)):
        try:
            fn()
            results[name] = "ok"
        except RobotError as e:
            results[name] = f"error: {e}"
    # Report abort failures rather than discarding them.
    reached = abortables.request_abort_all()
    failed = abortables.failed_last_call
    results["runners"] = (
        f"{reached} aborted" if not failed
        else f"{reached} aborted, {failed} FAILED to abort -- "
             f"use the physical stop")
    return results


@app.post("/api/v1/motion/stop")
def stop():
    """Functional stop of gateway-initiated motion. NOT an emergency or protective stop.

    Stops jogs (ImmStopJOG), program-space moves (StopMotion) and path runners.
    Does not stop pendant motion, controller Lua programs, other LAN clients,
    or passthrough commands. Always returns 200.
    """
    results = _stop_all()
    # Recorded AFTER the stop, unlike every other command: nothing may sit
    # between a stop request and the stop itself.
    audit.record("motion.stop", results=results)
    return {
        "stop_requested": True,
        "results": results,
        "confirmed": _confirm_standstill(),
        "confirmation_source": "telemetry-8083",
    }


@app.post("/api/v1/motion/preview")
def preview(joints: list[float]):
    """Forward kinematics: TCP pose for a given joint configuration."""
    try:
        return {"tcp": driver.forward_kin(joints)}
    except RobotError as e:
        raise HTTPException(503, str(e)) from e


# GET /api/v1/sensors/force is in fws/force_api.py, with the sensor setup it
# depends on.


# ---------------------------------------------------------------- stream
@app.websocket("/ws/state")
async def ws_state(ws: WebSocket):
    # HTTP auth middleware does not run for WebSocket scopes, so enforce the key
    # here. Browsers cannot set WebSocket headers, so it comes as ?key=.
    if keys.configured and keys.identify(ws.query_params.get("key")) is None:
        await ws.close(code=1008)   # policy violation
        return
    await ws.accept()
    try:
        while True:
            t = telemetry.snapshot()
            # Spread the snapshot rather than listing keys, so the stream cannot
            # drift into a subset of the REST view.
            await ws.send_text(json.dumps({
                **{k: v for k, v in t.items() if k != "ts"},
                "force": t.get("ft"),
                "error_main": _errors.get("main"),
                "error_sub": _errors.get("sub"),
                "limits": _limits(),
                # The frame's timestamp and its age at send, so a client can
                # tell a fresh frame from a stale repeat without diffing the
                # values a parked arm sends identically. None before frame one.
                "ts": t.get("ts"),
                "age_s": (None if not t.get("ts")
                          else round(time.time() - t["ts"], 3)),
            }))
            await asyncio.sleep(0.1)     # 10 Hz, matching the 8083 push rate
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    """Edge-triggered events: commands, faults latching and clearing, the
    watchdog stopping the arm.

    /ws/state is a 10 Hz sample of what IS; this is a push of what CHANGED.
    """
    if keys.configured and keys.identify(ws.query_params.get("key")) is None:
        await ws.close(code=1008)
        return
    topics = ws.query_params.get("topics")
    sub = bus.subscribe(topics.split(",") if topics else None)
    await ws.accept()
    try:
        while True:
            # The queue is thread-fed, so read it off the event loop: a
            # blocking get() here would stall every other request.
            event = await asyncio.to_thread(sub.get, 1.0)
            if event is None:
                # Silence and a dead socket otherwise look identical.
                await ws.send_text(json.dumps({"kind": "keepalive"}))
                continue
            await ws.send_text(json.dumps(event, default=str))
    except WebSocketDisconnect:
        pass
    finally:
        sub.close()


@app.get("/api/v1/metrics", response_class=PlainTextResponse)
def metrics():
    """Prometheus exposition of the counters FWS already keeps.

    Deliberately NOT on the always-open list: it carries live joint
    positions, and this gateway treats live state as needing a key.
    """
    caps = None
    with contextlib.suppress(Exception):
        d = capabilities.as_dict()
        caps = {k: d.get(k) for k in ("available", "absent", "unknown")}
    return PlainTextResponse(
        render_metrics(
            telemetry_snapshot=telemetry.snapshot(),
            errors=_errors,
            watchdog=control.watchdog(),
            audit_health=audit.health(),
            bus_health=bus.health(),
            recorder_health=recorder.health(),
            capabilities=caps,
            lock_holders=control.holders(),
        ),
        media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/api/v1/events/stream")
async def events_stream(topics: str | None = None):
    """The same events as Server-Sent Events, for anything that would rather
    not hold a WebSocket open (curl, a shell script, an EventSource)."""
    from fastapi.responses import StreamingResponse

    sub = bus.subscribe(topics.split(",") if topics else None)

    async def gen():
        try:
            yield ": FWS event stream\n\n"
            while True:
                event = await asyncio.to_thread(sub.get, 1.0)
                if event is None:
                    yield ": keepalive\n\n"
                    continue
                yield (f"event: {event['kind']}\n"
                       f"data: {json.dumps(event, default=str)}\n\n")
        finally:
            sub.close()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# Routers resolve driver and settings at call time, so create_app() can rebind
# them without re-registering routes.
app.include_router(build_router(lambda: driver, lambda: settings))
_audit = lambda action, **kw: audit.record(action, **kw)   # noqa: E731
app.include_router(build_control_api(
    lambda: driver, lambda: settings, lambda: capabilities,
    lambda: control, _audit,
))
app.include_router(build_programs_api(
    lambda: driver, lambda: settings, lambda: capabilities,
    lambda: control, _audit,
))
app.include_router(build_force_api(
    lambda: driver, lambda: telemetry, lambda: control, _audit,
))
app.include_router(lua_router)
# Generic invoker and controller file manager. fws/invoke.py owns the gating
# matrix; TYPED_ROUTE_OWNED keeps bounded commands reachable only via the typed
# routes.
app.include_router(build_invoke_api(
    lambda: driver, lambda: control, _audit,
))
app.include_router(build_files_api(
    lambda: driver, lambda: settings, lambda: control, _audit,
))
app.include_router(build_system_api(
    lambda: driver, lambda: settings, lambda: control, _audit,
))
app.include_router(build_backup_api(
    lambda: driver, lambda: settings, lambda: control, _audit,
))
# Named poses, stored by the gateway. Not the controller's point tables --
# this firmware cannot write one named point into a table.
# The measured kinematic model, as URDF. No URDF matched to this firmware
# is published anywhere, and the vendor's is measurably worse.
app.include_router(build_model_api(lambda: driver))
# The gripper: bounded arguments over a documented-but-unmeasured wire
# call, gated on the capability probe so a command to a gripper that is
# not fitted refuses instead of silently doing nothing.
app.include_router(build_gripper_api(
    lambda: driver, lambda: capabilities, lambda: control, _audit,
))
# Absolute moves. Off unless features.enable_movel is set: the wire
# layout once produced an unintended ~300 mm motion on this firmware.
app.include_router(build_move_api(
    lambda: driver, lambda: settings, _limits, lambda: LIMIT_MARGIN,
    lambda: control, _audit,
))
# Telemetry recordings, and the dump taken automatically on a fault.
app.include_router(build_recorder_api(lambda: recorder, _audit))
app.include_router(build_poses_api(
    lambda: driver, lambda: telemetry, lambda: poses, lambda: control, _audit,
))
# Controller QNX base services (FTP/telnet/qconn/8060). Each route is dark
# unless its feature flag is on; privileged ones require auth (enforced at
# startup by config.check_safe_to_start).
app.include_router(build_services_api(
    lambda: settings, lambda: control, _audit,
))


@app.get("/api/v1/events")
def events(limit: int = 100, action: str | None = None):
    """Audit trail of commands and state changes."""
    return {"count": len(audit), "events": audit.recent(limit, action)}


@app.get("/")
def index():
    """Service descriptor.

    FWS is an API-only gateway and ships no user interface. This root exists
    so a bare GET / discovers the API rather than returning 404.
    """
    return {
        "service": "fws",
        "description": "REST + WebSocket gateway for Fairino collaborative robots",
        "api": "/api/v1",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "websocket": "/ws/state",
        "read_only": settings.server.read_only,
        "full_access": settings.features.full_access,
    }
