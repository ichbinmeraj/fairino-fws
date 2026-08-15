"""Go to a pose: the typed move route.

    POST /api/v1/motion/move

This is the single largest capability a Fairino developer was missing.
Everything it needs already existed -- `driver.move_l` has the 33-element
wire layout worked out, `pathcheck` solves targets backwards, the soft-limit
band and z-floor guards are written, the lease and confirm gates are in
place. What was missing was a route that put them in the right order.

WHY IT IS OFF BY DEFAULT. `features.enable_movel` is false because MoveL's
argument layout produced an unintended ~300 mm motion and a controller fault
on v3.8.5.1. That was a transcription error, and the layout has since been
read carefully from the SDK source -- but "carefully read" is not
"verified", and the difference is 300 mm of arm travel. So the route exists,
is fully guarded, and refuses until someone turns the flag on deliberately,
having read what it says.

THE PRE-FLIGHT, in order, before anything is transmitted:

  1. the pose is six numbers;
  2. inverse kinematics solves it -- an unreachable pose or a singularity is
     refused here, not discovered by the arm;
  3. every solved joint lands inside its soft-limit band with the standoff;
  4. the target TCP is above the configured z-floor;
  5. speed is inside the configured cap;
  6. the caller holds the motion lease and has confirmed.

Then, and only then, one MoveL goes out -- non-blocking, because a blocking
call would hold the RPC lock for the whole move and make a stop impossible.
The route returns immediately with the solved joints; poll
`GET /api/v1/motion/queue` or watch `/ws/events` for completion.

WHAT THIS IS NOT. It is not a path runner. Executing a multi-point trajectory
from outside the controller is the wrong architecture and every motion bug in
this project came from trying it; trajectories belong in Lua on the
controller, which is also how ABB's RWS works. See driver.move_l.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .access import full_access
from .driver import RobotError

PREFIX = "/api/v1"


class MoveRequest(BaseModel):
    pose: list[float] = Field(
        description="target TCP: x, y, z, rx, ry, rz -- mm and degrees")
    # Bounded here because it is about MEANING, not magnitude: these are the
    # frame numbers the controller accepts.
    tool: int = Field(default=0, ge=0, le=15)
    user: int = Field(default=0, ge=0, le=14)
    # The ceiling is enforced in the handler so full_access can lift it.
    vel: float = Field(default=20.0, gt=0,
                       description="percent; bounded by limits.jog_max_vel_pct")
    confirm: bool = Field(
        default=False,
        description="must be true: this moves the arm to an absolute pose")


def build(get_driver, get_settings, get_limits, get_margin,
          get_control, audit) -> APIRouter:
    router = APIRouter(prefix=PREFIX, tags=["motion"])

    def _require_motion(token: str | None) -> None:
        if full_access():
            return
        control = get_control()
        lease = control.held_by("motion")
        if lease is None:
            raise HTTPException(428, (
                "hold the 'motion' control lock (POST /api/v1/control with "
                '{"domains": ["motion"]}) and send its token as '
                "X-FWS-Control-Token. A move must be stoppable by the "
                "watchdog if you disappear mid-motion."))
        ok, reason = control.check("motion", token)
        if not ok:
            raise HTTPException(423, reason)

    @router.post("/motion/move")
    def move(req: MoveRequest,
             x_fws_control_token: str | None = Header(default=None)):
        """Move the TCP to an absolute pose, pre-flighted.

        Every refusal below happens BEFORE anything reaches the wire.
        """
        settings = get_settings()
        if not (settings.features.enable_movel or full_access()):
            raise HTTPException(403, (
                "absolute moves are disabled (features.enable_movel = false). "
                "MoveL's argument layout produced an unintended ~300 mm "
                "motion and a controller fault on software v3.8.5.1. The "
                "layout has since been read from the SDK source and this "
                "route pre-flights every target, but that is not the same as "
                "verified on your hardware. Turn it on only when you can "
                "watch the arm and reach the E-stop."))

        if len(req.pose) != 6:
            raise HTTPException(422, (
                f"pose must be six numbers (x, y, z, rx, ry, rz), got "
                f"{len(req.pose)}"))

        _require_motion(x_fws_control_token)

        if not (req.confirm or full_access()):
            raise HTTPException(400, (
                "this moves the arm to an absolute pose, which may take a "
                "path you did not picture. Resend with confirm=true"))

        if not full_access() and req.vel > settings.limits.jog_max_vel_pct:
            raise HTTPException(422, (
                f"vel exceeds the configured limit "
                f"{settings.limits.jog_max_vel_pct}%"))

        driver = get_driver()

        # 1. Solve it backwards. An unreachable pose or a singularity is
        #    refused here rather than discovered by the arm.
        try:
            joints = driver.inverse_kin(list(req.pose), kind=0, config=-1)
        except RobotError as e:
            raise HTTPException(409, (
                f"blocked: no inverse-kinematics solution for that pose. It "
                f"is unreachable, or near a singularity. Underlying: {e}")
            ) from e

        # 2. Every solved joint inside its band, with the standoff.
        limits, margin = get_limits(), get_margin()
        if limits and not full_access():
            for i, (lo, hi) in enumerate(limits):
                if not (lo + margin <= joints[i] <= hi - margin):
                    raise HTTPException(409, (
                        f"blocked: that pose puts J{i + 1} at "
                        f"{joints[i]:.2f}deg, outside its safe band "
                        f"[{lo + margin:.1f}, {hi - margin:.1f}]."))

        # 3. The floor. Checked against the TARGET, which is the whole point
        #    of having solved it first.
        floor = settings.limits.z_floor_mm
        if floor is not None and not full_access() and req.pose[2] < floor:
            raise HTTPException(409, (
                f"blocked: that pose puts the TCP at Z {req.pose[2]:.1f}mm, "
                f"below the configured floor {floor:.1f}mm."))

        # Recorded BEFORE transmission: if the controller wedges or the arm
        # moves and this process dies, the review still has the line saying
        # what was sent.
        audit("motion.move", pose=list(req.pose), joints=joints,
              tool=req.tool, user=req.user, vel=req.vel)

        try:
            driver.move_l(list(req.pose), joints, tool=req.tool,
                          user=req.user, vel=req.vel)
        except RobotError as e:
            raise HTTPException(502, str(e)) from e

        return {
            "moving": True,
            "target_pose": list(req.pose),
            "target_joints": [round(j, 4) for j in joints],
            "note": ("non-blocking: a blocking call would hold the RPC lock "
                     "for the whole move and make a stop impossible. Poll "
                     "GET /api/v1/motion/queue, or watch /ws/events."),
        }

    return router
