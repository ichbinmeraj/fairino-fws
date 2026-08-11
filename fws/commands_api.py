"""Command catalogue, and the superseded passthrough.

The catalogue is generated from the wire protocol and reports each command's
evidence-based `danger`, `basis` and `confidence`. See tools/generate_commands.py
for the method and fws/invoke.py for what each class costs to call.

POST /api/v1/commands/{name} is superseded by POST /api/v1/invoke/{name}. It
has no control-lock integration, so it serves only the classes that need no
lease (read and stop) and refers everything else to the route that can check
one.

`verified` means someone exercised it on a real controller, not that
generation succeeded.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import invoke as policy
from .driver import RobotDriver, RobotError
from .protocol.commands import COMMANDS, VERIFIED_COMMANDS, summary

router = APIRouter(prefix="/api/v1/commands", tags=["commands"])


class InvokeRequest(BaseModel):
    args: list[Any] = Field(default_factory=list,
                            description="positional arguments, in WIRE order")
    confirm: bool = Field(
        default=False,
        description="required for anything classified as motion")


# Shared with /api/v1/invoke so the two catalogues cannot disagree.
_describe = policy.describe


def build_router(get_driver, get_settings) -> APIRouter:
    """Wire the catalogue to a driver and settings resolved at call time."""

    @router.get("")
    def list_commands(danger: str | None = None, kind: str | None = None,
                      verified: bool | None = None,
                      callable_only: bool = False,
                      q: str | None = None, limit: int = 200):
        """The full catalogue, filterable."""
        names = sorted(COMMANDS)
        if danger:
            names = [n for n in names if COMMANDS[n].danger == danger]
        if kind:
            names = [n for n in names if COMMANDS[n].kind == kind]
        if verified is not None:
            names = [n for n in names
                     if (n in VERIFIED_COMMANDS) is verified]
        if callable_only:
            names = [n for n in names if COMMANDS[n].callable_directly]
        if q:
            needle = q.lower()
            names = [n for n in names if needle in n.lower()]
        return {
            "summary": summary(),
            "matched": len(names),
            "returned": min(len(names), limit),
            "commands": [_describe(n) for n in names[:limit]],
        }

    @router.get("/{name}")
    def get_command(name: str):
        if name not in COMMANDS:
            raise HTTPException(404, f"no such command: {name}")
        return _describe(name)

    @router.post("/{name}")
    def invoke(name: str, req: InvokeRequest):
        """Superseded by POST /api/v1/invoke/{name}. Open classes only.

        Gates, most-refusing first: refused; not a single wire call; argument
        shape; passthrough flag; unverified flag; needs a lease (refused here,
        referred to /api/v1/invoke). The first three share fws/invoke.py with
        the new route.
        """
        settings = get_settings()
        try:
            c = policy.lookup(name)
            policy.check_callable(c)
            args = policy.coerce_args(c, req.args)
        except policy.Refusal as e:
            raise HTTPException(e.status, e.detail) from e

        if not settings.features.enable_command_passthrough:
            raise HTTPException(403, (
                "command passthrough is disabled "
                "(features.enable_command_passthrough = false)"))

        if (name not in VERIFIED_COMMANDS
                and not settings.features.enable_unverified_commands):
            raise HTTPException(403, (
                f"{name} has never been exercised on hardware. Generation "
                f"proves what the SDK sends, not that this works on your "
                f"firmware. Set features.enable_unverified_commands to "
                f"proceed anyway."))

        domain, needs_confirm = policy.requirements(c)
        if domain is not None:
            raise HTTPException(428, (
                f"{name} is classified '{c.danger}' and requires the "
                f"'{domain}' control lock"
                f"{' and confirm=true' if needs_confirm else ''}. This route "
                f"has no control-lock integration and cannot verify a lease, "
                f"so it refuses rather than pretending the lease is not "
                f"needed. Use POST /api/v1/invoke/{name}, which can. Basis "
                f"for the classification: {'; '.join(c.basis)}."))

        driver: RobotDriver = get_driver()
        try:
            result = driver._call(c.wire_name, *args)
        except RobotError as e:
            raise HTTPException(502, str(e)) from e
        return {
            "command": name,
            "wire_name": c.wire_name,
            "danger": c.danger,
            "verified": name in VERIFIED_COMMANDS,
            "warnings": policy.warnings_for(name, c),
            "result": result,
        }

    return router
