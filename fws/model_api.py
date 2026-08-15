"""Serve the robot's measured model.

    GET /api/v1/model            what the model is and where it came from
    GET /api/v1/model/urdf       the URDF, in metres, as URDF requires

Point RViz, Foxglove or a three.js scene at the URDF, drive it from
`/ws/state`, and you have a digital shadow of this arm with nothing else
installed. No URDF matched to this firmware is published anywhere, and the
vendor's is measurably worse than the controller's own numbers -- see
fws/model.py for the residual.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from . import model as model_mod

PREFIX = "/api/v1"


def build(get_driver) -> APIRouter:
    router = APIRouter(prefix=PREFIX, tags=["model"])

    def _soft_limits():
        """The controller's own joint limits, when it will say.

        Falling back to a full turn rather than inventing a tight limit: a
        planner that trusts a fabricated bound refuses reachable poses, which
        is a harder failure to diagnose than a loose one.
        """
        try:
            return get_driver().joint_limits()
        except Exception:
            # Deliberately broad: serving the model must not depend on the
            # robot being reachable. A developer opening this in RViz on a
            # laptop, with the cell powered down, still gets the geometry.
            return None

    @router.get("/model")
    def describe():
        limits = _soft_limits()
        return {
            **model_mod.MODEL_INFO,
            "chain": [
                {"joint": link.name, "xyz_mm": list(link.xyz_mm),
                 "twist_rad": link.twist_rad}
                for link in model_mod.FR5_CHAIN
            ],
            "flange_mm": list(model_mod.FLANGE_MM),
            "joint_limits_deg": ([list(p) for p in limits] if limits
                                 else None),
            "joint_limits_source": ("the controller" if limits else
                                    "unavailable -- the URDF falls back to a "
                                    "full turn per joint rather than "
                                    "inventing a tighter bound"),
            "urdf": "/api/v1/model/urdf",
        }

    @router.get("/model/urdf", response_class=PlainTextResponse)
    def urdf(visuals: str = Query(
                 default="primitives",
                 description="'primitives' draws stand-in cylinders so a "
                             "viewer shows an arm; 'none' is pure kinematics"),
             name: str = Query(default="fr5", max_length=64)):
        try:
            body = model_mod.urdf(name=name, visuals=visuals,
                                  soft_limits=_soft_limits())
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return PlainTextResponse(body, media_type="application/xml")

    return router
