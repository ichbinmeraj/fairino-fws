"""Controller power and recovery.

The only power operation the Fairino API exposes is ShutDownRobotOS, which is
one-way: nothing in the vendor API powers the controller back on. So
`POST /system/shutdown` is named shutdown (never restart), off by default
behind `features.enable_shutdown`, and gated by the control lock and an
explicit confirmation. A network restart of the robot application lives in the
controller-services layer (see `fws.services_api`). `GET /system/recovery`
reports boot progress after a manual power cycle.
"""
from __future__ import annotations

import socket
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .driver import RobotError

router = APIRouter(prefix="/api/v1/system", tags=["system"])

# Ports come up in layers after a power cycle, so a partial set is a controller
# mid-boot, not a broken one.
BOOT_LAYERS: tuple[tuple[str, tuple[int, ...], str], ...] = (
    ("qnx_base", (21, 23, 8000, 8050), "QNX base services, ~26 s after power"),
    ("control", (20002, 20003, 20004), "robot control channels"),
)

# Single-client listeners whose state reads BACKWARDS from every other port:
# refusing means the socket is claimed by a live client (healthy); accepting
# means nobody is attached.
#
# 8083 is deliberately NOT here: it accepts a second connection at TCP level
# and then never sends a frame, so a connect test says nothing about whether
# anyone is consuming it. `stream_connected` in GET /system/health is the
# honest signal there.
CLAIMED_WHEN_HEALTHY: tuple[tuple[int, str], ...] = (
    (8060, "RcvCmdThread_Web -- carries the Lua compiler's verdict"),
    (8061, "FbkStateThread_Web"),
    (8062, "RcvFileThread_Web"),
)


class ShutdownRequest(BaseModel):
    confirm: bool = Field(
        default=False,
        description="required: this is one-way, the controller cannot be "
                    "powered back on through any Fairino API")
    i_have_physical_or_switched_power: bool = Field(
        default=False,
        description="required: confirm you can actually power the cell back "
                    "on. There is no remote path.")
    reason: str = Field(min_length=3, max_length=200,
                        description="recorded in the audit log")


def _port_open(ip: str, port: int, timeout: float = 1.5) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((ip, port)) == 0
    finally:
        s.close()


def build(get_driver, get_settings, get_control, audit) -> APIRouter:
    def _lock(domain: str, token: str | None) -> None:
        control = get_control()
        if control.held_by(domain) is None:
            return
        ok, reason = control.check(domain, token)
        if not ok:
            raise HTTPException(428 if not token else 423, reason)

    @router.get("/recovery")
    def recovery():
        """Is the controller back, and how far through booting is it?
        Answers against the boot baseline, not a single ping."""
        ip = get_driver().ip
        layers: dict[str, Any] = {}
        for name, ports, what in BOOT_LAYERS:
            state = {p: _port_open(ip, p) for p in ports}
            layers[name] = {"describes": what, "ports": state,
                            "up": all(state.values())}
        done = [n for n, v in layers.items() if v["up"]]
        if len(done) == len(BOOT_LAYERS):
            stage, ready = "up", True
        elif not done:
            stage, ready = "down or very early in boot", False
        else:
            stage, ready = f"booting: {', '.join(done)} up", False

        # Read inverted. See CLAIMED_WHEN_HEALTHY.
        internal = {}
        for port, what in CLAIMED_WHEN_HEALTHY:
            accepting = _port_open(ip, port)
            internal[port] = {
                "describes": what,
                "accepting_connections": accepting,
                "client_attached": not accepting,
            }
        unattached = [p for p, v in internal.items() if not v["client_attached"]]

        return {
            "ready": ready,
            "stage": stage,
            "layers": layers,
            "internal_clients": internal,
            "internal_clients_unattached": unattached,
            "reading_internal_clients": (
                "these are SINGLE-CLIENT listeners and read backwards from "
                "every other port here: refusing means the socket is claimed "
                "by a live client, which is healthy. 8060 unattached means "
                "the robot moves and reports normally, but no program can "
                "be uploaded, because the Lua compiler's verdict has "
                "nowhere to go."),
        }

    @router.post("/shutdown")
    def shutdown(req: ShutdownRequest,
                 x_fws_control_token: str | None = Header(default=None)):
        """Shut the controller down. There is no remote way back;
        deliberately not called restart."""
        settings = get_settings()
        if not settings.features.enable_shutdown:
            raise HTTPException(403, (
                "shutdown is disabled (features.enable_shutdown = false). "
                "It is one-way: the Fairino API has ShutDownRobotOS and no "
                "reboot, so nothing here can power the controller back on. "
                "Enable it only if you have switched power or someone at the "
                "machine."))
        if not (req.confirm and req.i_have_physical_or_switched_power):
            raise HTTPException(422, (
                "both confirm and i_have_physical_or_switched_power are "
                "required: after this call the controller is off and no API "
                "can turn it on"))
        _lock("config", x_fws_control_token)

        # Refuse while the arm is moving.
        try:
            if not get_driver().motion_done():
                raise HTTPException(409, "the robot is moving; stop it first")
        except RobotError as e:
            raise HTTPException(503, f"cannot confirm standstill: {e}") from e

        audit("system.shutdown", reason=req.reason)
        try:
            # The one place that passes allow_refused; all guards above ran
            # first.
            get_driver()._call("ShutDownRobotOS", allow_refused=True)
        except RobotError as e:
            # A dropped connection mid-call is a shutdown that worked; a
            # transport error here is not reported as failure or as observed
            # success.
            return {"shutdown_requested": True, "confirmed": False,
                    "detail": f"the call did not return cleanly ({e}); this "
                              f"is expected if the controller went down "
                              f"mid-call. Poll GET /system/recovery.",
                    "recovery": "requires physical or switched power"}
        return {"shutdown_requested": True, "confirmed": True,
                "recovery": "requires physical or switched power",
                "next": "GET /api/v1/system/recovery once power is restored"}

    return router
