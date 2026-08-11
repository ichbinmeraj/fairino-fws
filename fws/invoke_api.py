"""The callable command surface: POST /api/v1/invoke/{name}.

Every command is callable, gated by fws/invoke.py and audited. Catalogue
endpoints report each command's classification and how it was derived. This is
the escape hatch for commands FWS does not model, not a replacement for the
typed routes, which pre-flight motion.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from . import invoke as policy
from .driver import RobotError
from .protocol.commands import COMMANDS, VERIFIED_COMMANDS, summary

PREFIX = "/api/v1/invoke"


class InvokeRequest(BaseModel):
    args: list[Any] = Field(
        default_factory=list,
        description="positional arguments in WIRE order, which is NOT always "
                    "the SDK's Python signature order -- see wire_args")
    confirm: bool = Field(
        default=False,
        description="required for motion-class and unknown-class commands")


def build(get_driver, get_control, audit) -> APIRouter:
    """Resolve dependencies at call time so create_app can rebind them.

    The router is built here, not at module level: a module-level router would
    accumulate a second copy of every route on each build() call, and the first
    registration would win.
    """
    router = APIRouter(prefix=PREFIX, tags=["invoke"])

    @router.get("")
    def list_commands(danger: str | None = None, kind: str | None = None,
                      confidence: str | None = None,
                      callable_only: bool = False,
                      verified: bool | None = None,
                      q: str | None = None, limit: int = 200):
        """The catalogue, with each command's class and how it was derived."""
        names = sorted(COMMANDS)
        if danger:
            names = [n for n in names if COMMANDS[n].danger == danger]
        if kind:
            names = [n for n in names if COMMANDS[n].kind == kind]
        if confidence:
            names = [n for n in names if COMMANDS[n].confidence == confidence]
        if callable_only:
            names = [n for n in names if COMMANDS[n].callable_directly]
        if verified is not None:
            names = [n for n in names
                     if (n in VERIFIED_COMMANDS) is verified]
        if q:
            needle = q.lower()
            names = [n for n in names if needle in n.lower()]
        return {
            "summary": summary(),
            "classification": {
                "method": "evidence from the SDK source and the vendor's own "
                          "documentation, never the command name alone",
                "see": "GET /api/v1/invoke/policy",
            },
            "matched": len(names),
            "returned": min(len(names), limit),
            "commands": [policy.describe(n) for n in names[:limit]],
        }

    # Registered before /{name} so "policy" is not swallowed as a command name.
    @router.get("/policy")
    def gating_policy():
        """What each class costs to call, and where the classes come from."""
        return {
            "matrix": policy.GATING_MATRIX,
            "also_never_callable": {
                "composite": "several wire calls; sending one of them is not "
                             "the command. MoveL is four calls.",
                "local": "no XML-RPC call at all -- a file transfer, a "
                         "client-side computation, or a raw frame on another "
                         "channel.",
            },
            "hazards": {
                "blocks-the-rpc-lock":
                    "blocks inside the controller. FWS serialises the whole "
                    "XML-RPC channel through one lock, so the stop path "
                    "queues behind it. Adds confirm and at least a config "
                    "lease, whatever the class.",
            },
            "arity": "exact, never minimum. Too few arguments are "
                     "rejected by the controller; too many may be silently "
                     "accepted, which can dispatch a zero-argument command "
                     "that should not run.",
            "evidence_grades": {
                "measured": "observed on this controller, or read out of the "
                            "SDK body",
                "documented": "the vendor's Chinese @brief, the vendor's Lua "
                              "manual chapter, or the SDK's own safety guard",
                "inferred": "name patterns. They can only ADD danger here, "
                            "never remove it",
                "none": "no evidence either way -- class 'unknown', gated "
                        "like motion",
            },
            "not_covered": "this route cannot stop what it starts. "
                           "POST /api/v1/motion/stop issues ImmStopJOG and "
                           "StopMotion, which do not stop everything reachable "
                           "here. The motion lease is what gives the "
                           "disconnect watchdog something to act on.",
        }

    @router.get("/{name}")
    def get_command(name: str):
        if name not in COMMANDS:
            raise HTTPException(404, f"no such command: {name}")
        out = policy.describe(name)
        out["warnings"] = policy.warnings_for(name, COMMANDS[name])
        return out

    @router.post("/{name}")
    def invoke(name: str, req: InvokeRequest,
               x_fws_control_token: str | None = Header(default=None)):
        """Send one command, once, with its arguments validated first.

        Checks in most-refusing order: exists, refused, callable, argument
        shape, then lock and confirmation. Shape is checked before the lock,
        before anything is transmitted.
        """
        try:
            cmd = policy.lookup(name)
            policy.check_callable(cmd)
            args = policy.coerce_args(cmd, req.args)
            actor = policy.gate(cmd, confirm=req.confirm,
                                token=x_fws_control_token,
                                control=get_control())
        except policy.Refusal as e:
            raise HTTPException(e.status, e.detail) from e

        # Recorded before the call, so a command that wedges or dies mid-flight
        # still leaves a record of what was sent.
        event = audit(f"invoke.{cmd.danger}", actor=actor, command=name,
                      wire_name=policy.wire_name_for(cmd), args=args,
                      danger=cmd.danger, confirm=req.confirm)

        try:
            result = get_driver()._call(policy.wire_name_for(cmd), *args)
        except RobotError as e:
            raise HTTPException(502, str(e)) from e
        return {
            "command": name,
            "wire_name": policy.wire_name_for(cmd),
            "danger": cmd.danger,
            "basis": list(cmd.basis),
            "confidence": cmd.confidence,
            "args_sent": args,
            "verified": name in VERIFIED_COMMANDS,
            "warnings": policy.warnings_for(name, cmd),
            "audit_seq": event.get("seq") if isinstance(event, dict) else None,
            "result": result,
        }

    return router
