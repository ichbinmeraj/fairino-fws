"""Open and close the gripper, with bounds.

    POST /api/v1/gripper/activate    power/reset the gripper
    POST /api/v1/gripper/command     move it to a position

Open-and-close is a top-five request for any cobot gateway, and until now
reaching it meant `POST /invoke/MoveGripper` with a ten-argument list in
wire order and no bounds on any of them. The arguments are not innocuous:
`force` is how hard it squeezes, and the two rotation arguments belong to a
rotating gripper that most cells do not have.

WHY IT IS GATED ON A PROBE, not a feature flag. Unlike MoveL, nothing here
has a bad history -- the risk is simply that a gripper may not be fitted, and
this controller answers gripper getters with zeros rather than an error when
one is absent. So a caller who has not fitted one gets a clear refusal
instead of a command that silently does nothing. `?force_probe=false` skips
the check for a cell where the probe is wrong.

WHAT IS UNVERIFIED. MoveGripper has never been exercised on this hardware --
the registry marks it `documented`, not `measured`. The route bounds every
argument, sends the documented wire order, and says plainly in its response
that the call is unverified. That is the honest position: refusing to
implement it helps nobody, and implying it is proven would be worse.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .access import full_access
from .driver import RobotError

PREFIX = "/api/v1"

# Documented ranges. The controller's own gripper configuration may be
# narrower; these keep a typo from becoming a crush.
POS_MIN, POS_MAX = 0, 100          # percent closed
VEL_MIN, VEL_MAX = 1, 100          # percent
FORCE_MIN, FORCE_MAX = 1, 100      # percent


class ActivateRequest(BaseModel):
    index: int = Field(default=1, ge=1, le=8, description="gripper number")
    action: int = Field(default=1, ge=0, le=1,
                        description="1 = activate/reset, 0 = deactivate")
    confirm: bool = Field(default=False)


class GripperRequest(BaseModel):
    index: int = Field(default=1, ge=1, le=8)
    position: int = Field(ge=POS_MIN, le=POS_MAX,
                          description="percent closed: 0 open, 100 closed")
    speed: int = Field(default=50, ge=VEL_MIN, le=VEL_MAX)
    force: int = Field(default=50, ge=FORCE_MIN, le=FORCE_MAX,
                       description="percent of maximum grip force")
    max_time_ms: int = Field(default=30000, ge=0, le=60000)
    confirm: bool = Field(
        default=False,
        description="must be true: a gripper closes on whatever is in it")


def build(get_driver, get_capabilities, get_control, audit) -> APIRouter:
    router = APIRouter(prefix=PREFIX, tags=["gripper"])

    def _gate(token: str | None, confirmed: bool, what: str) -> None:
        if full_access():
            return
        control = get_control()
        lease = control.held_by("motion")
        if lease is None:
            raise HTTPException(428, (
                "hold the 'motion' control lock: a gripper is motion, and a "
                "holder that disappears mid-grip must be stoppable."))
        ok, reason = control.check("motion", token)
        if not ok:
            raise HTTPException(423, reason)
        if not confirmed:
            raise HTTPException(400, (
                f"{what} closes on whatever is in the gripper -- a part, a "
                f"fixture, or a hand. Resend with confirm=true"))

    def _require_fitted(force_probe: bool) -> None:
        """A gripper that is not fitted answers with zeros, not an error, so
        a command to a missing gripper silently does nothing. Say so."""
        if not force_probe or full_access():
            return
        try:
            state = get_capabilities().state("gripper.position")
        except Exception:
            return                        # never block on the probe itself
        if state == "absent":
            raise HTTPException(409, (
                "no gripper is fitted, as far as the controller reports. A "
                "gripper command would be accepted and do nothing. Pass "
                "?force_probe=false if the probe is wrong for this cell."))

    @router.post("/gripper/activate")
    def activate(req: ActivateRequest, force_probe: bool = True,
                 x_fws_control_token: str | None = Header(default=None)):
        """Power up or reset the gripper. Most need this before they move."""
        _gate(x_fws_control_token, req.confirm, "activating a gripper")
        _require_fitted(force_probe)
        # Not `action=`: the audit helper's own first parameter is named
        # that, and the collision is a TypeError at the worst moment.
        audit("gripper.activate", index=req.index, act=req.action)
        try:
            get_driver()._call("ActGripper", int(req.index), int(req.action))
        except RobotError as e:
            raise HTTPException(502, str(e)) from e
        return {"index": req.index, "action": req.action,
                "verified": False,
                "note": "ActGripper is documented, not measured on v3.8.5.1"}

    @router.post("/gripper/command")
    def command(req: GripperRequest, force_probe: bool = True,
                x_fws_control_token: str | None = Header(default=None)):
        """Move the gripper to a position, with every argument bounded.

        The wire order is MoveGripper(index, pos, vel, force, maxtime, block,
        type, rotNum, rotVel, rotTorque). The last three belong to a rotating
        gripper; they are sent as zeros because sending a rotation to a
        gripper that does not rotate is not something to do by accident.
        """
        _gate(x_fws_control_token, req.confirm, "commanding a gripper")
        _require_fitted(force_probe)

        audit("gripper.command", index=req.index, position=req.position,
              speed=req.speed, force=req.force)
        try:
            get_driver()._call(
                "MoveGripper",
                int(req.index), int(req.position), int(req.speed),
                int(req.force), int(req.max_time_ms),
                0,          # block: 0 = non-blocking, so stop stays possible
                0,          # type: 0 = the ordinary parallel gripper
                0.0, 0, 0,  # rotNum, rotVel, rotTorque -- no rotation
            )
        except RobotError as e:
            raise HTTPException(502, str(e)) from e

        return {
            "index": req.index,
            "position": req.position,
            "speed": req.speed,
            "force": req.force,
            "verified": False,
            "note": ("MoveGripper is documented but has never been exercised "
                     "on this firmware. Every argument here is bounded and "
                     "the wire order is the documented one; watch the first "
                     "command on real hardware. Non-blocking, so "
                     "POST /api/v1/motion/stop still works."),
        }

    return router
