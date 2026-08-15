"""Typed, gated invocation of the generated command registry.

Gate by command class rather than a feature flag:

    class    lock                confirm
    refused  never callable      --       also refused in fws/driver.py
    motion   motion lease held   yes      can move the arm/axis/tooling
    unknown  motion lease held   yes      unclassified, treated as worst case
    config   config lease held   no       changes controller state
    stop     none                no       stop must never be lockable
    read     none                no       open

Composite/local commands are never callable (not a single wire call). A lease
must be HELD (not merely unheld) so the lapse handler can stop motion the
generic invoker started if the client stops renewing.
"""
from __future__ import annotations

import ast
from typing import Any

from .access import full_access
from .driver import REFUSED as DRIVER_REFUSED
from .protocol.commands import COMMANDS, SDK_DEFECTS, VERIFIED_COMMANDS
from .protocol.recovered_rpcs import RECOVERED

# class -> (control-lock domain that must be HELD, confirmation required)
REQUIREMENTS: dict[str, tuple[str | None, bool]] = {
    "read": (None, False),
    "stop": (None, False),
    "config": ("config", False),
    "motion": ("motion", True),
    "unknown": ("motion", True),
}

# Hazards that add requirements on top of the class.
HAZARD_REQUIREMENTS: dict[str, tuple[str | None, bool]] = {
    "blocks-the-rpc-lock": ("config", True),
    # Classifies as config (moves nothing) but turns a safety guard off (comms-
    # loss watchdog, soft limits, collision detection), so confirmation is
    # required on top of the config lease.
    "weakens-a-safety-guard": ("config", True),
}

# Commands a typed FWS route already owns, with the bound that route enforces.
# The generic invoker refuses these and names the route instead, because the
# wrapper IS the safety property:
#   driver.jog()  clamps max_dis and vel; reached raw, the bound is advisory
#                 (max_dis is what stops the arm if this process dies mid-move).
#   SetAO         needs percent x 40.95 on the wire; raw, it commands ~2.4%.
#   FileUpload /  open the transfer port and expect the framed TCP exchange in
#   FileDownload  fws/files_wire.py; called alone they leave a half-open
#                 transfer on the service.
TYPED_ROUTE_OWNED: dict[str, str] = {
    "StartJOG": "POST /api/v1/motion/jog (joint) or "
                "POST /api/v1/motion/jog/linear (Cartesian)",
    "SetAO": "PUT /api/v1/io/analog/outputs/{index}",
    "SetToolAO": "PUT /api/v1/io/analog/outputs/{index}",
    "SetLoadWeight": "PUT /api/v1/robot/payload",
    "SetLoadCoord": "PUT /api/v1/robot/payload",
    "FileUpload": "PUT /api/v1/files/{kind}/{name}",
    "FileDownload": "GET /api/v1/files/{kind}/{name}",
    "FileDelete": "DELETE /api/v1/files/{kind}/{name}",
    "LuaUpLoadUpdate": "PUT /api/v1/programs/{name}",
    "PointTableUpload": "PUT /api/v1/points/tables/{name}",
    "PointTableDownload": "GET /api/v1/points/tables/{name}",
    "SetToolCoord": "PUT /api/v1/frames/tool/{frame_id}",
    "SetWObjCoord": "PUT /api/v1/frames/work/{frame_id}",
    # ResetAllError classifies stop (ungated), but clearing a latched fault
    # lets motion happen, so it gets its own typed route which reports the
    # fault codes back afterwards. A reset does not clear the underlying
    # condition: an arm on a soft limit re-faults the moment you jog into it.
    "ResetAllError": "POST /api/v1/errors/reset",
}

GATING_MATRIX: list[dict[str, Any]] = [
    {"class": "refused", "callable": False, "lock": None, "confirm": None,
     "why": "SAFETY.md. Enforced here and again in fws/driver.py, so no "
            "caller can route around it."},
    {"class": "motion", "callable": True, "lock": "motion", "confirm": True,
     "why": "can move the arm, an external axis, a mobile base or tooling, "
            "or release energy such as a welding arc."},
    {"class": "unknown", "callable": True, "lock": "motion", "confirm": True,
     "why": "the classifier found no positive evidence either way. Not safe "
            "by default: gated exactly like motion."},
    {"class": "config", "callable": True, "lock": "config", "confirm": False,
     "why": "changes controller state without moving anything."},
    {"class": "stop", "callable": True, "lock": None, "confirm": False,
     "why": "never gated: a lock that can block the stop path is a hazard."},
    {"class": "read", "callable": True, "lock": None, "confirm": False,
     "why": "open. Reached only by positive proof -- a query docstring AND "
            "an SDK body that returns a payload."},
]


class Refusal(Exception):
    """A refusal carrying the HTTP status the route should answer with."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def lookup(name: str):
    cmd = COMMANDS.get(name)
    if cmd is None:
        raise Refusal(404, f"no such command: {name}")
    return cmd


def check_callable(cmd) -> None:
    """The refusals that no lease or confirmation lifts.

    One thing lifts them: features.full_access (fws/access.py). With it on
    every command is callable raw, including the ones that write firmware or
    halt the controller. The driver's REFUSED list lifts on the same switch,
    so this is not merely moved down a layer.
    """
    if full_access():
        return
    owner = TYPED_ROUTE_OWNED.get(cmd.wire_name) or TYPED_ROUTE_OWNED.get(
        cmd.python_name)
    if owner is not None:
        raise Refusal(409, (
            f"{cmd.python_name} is owned by a typed route, which enforces a "
            f"bound this generic path cannot: use {owner}. Reaching it raw "
            f"here would offer the same capability with the safety removed -- "
            f"a raw StartJOG carries no max_dis limit, and max_dis is what "
            f"stops the arm if this process dies mid-move. See SAFETY.md."))
    if cmd.danger == "refused" or cmd.wire_name in DRIVER_REFUSED:
        raise Refusal(403, (
            f"{cmd.python_name} is refused by FWS and cannot be enabled. It "
            f"writes firmware, halts the controller, or is known to wedge the "
            f"RPC channel. fws/driver.py refuses it too, so this is not the "
            f"only thing standing in the way. See SAFETY.md."))
    if not cmd.callable_directly and cmd.python_name in RECOVERED:
        # The registry says local because the SDK reads its own state struct
        # instead of calling out, but the controller answers this name on the
        # wire (see recovered_rpcs), so it is callable.
        return
    if not cmd.callable_directly:
        detail = (f"{cmd.python_name} is '{cmd.kind}', not a single wire call, "
                  f"so passthrough cannot express it correctly.")
        if cmd.wire_sequence:
            detail += f" It performs: {', '.join(cmd.wire_sequence)}."
        if cmd.kind == "local":
            detail += (" A local command makes no XML-RPC call at all -- it is "
                       "a file transfer, a client-side computation, or (like "
                       "ResumeMotion) a raw frame on another channel.")
        raise Refusal(422, detail)


def wire_name_for(cmd) -> str:
    """The method name to put on the wire. A recovered RPC has an
    empty wire_name in the registry, so its own name is used."""
    return cmd.wire_name or (cmd.python_name if cmd.python_name in RECOVERED
                             else cmd.wire_name)


def requirements(cmd) -> tuple[str | None, bool]:
    """(lock domain that must be held, confirmation required) for
    one command; both None for a refused command."""
    if cmd.danger == "refused" or cmd.wire_name in DRIVER_REFUSED:
        return None, False
    domain, confirm = REQUIREMENTS.get(cmd.danger, ("motion", True))
    for hazard in cmd.hazards:
        h_domain, h_confirm = HAZARD_REQUIREMENTS.get(hazard, (None, False))
        confirm = confirm or h_confirm
        # The stricter domain wins; motion outranks config outranks none.
        if h_domain == "motion" or (h_domain == "config" and domain is None):
            domain = h_domain
    return domain, confirm


def gate(cmd, *, confirm: bool, token: str | None, control) -> str:
    """Authorise one invocation; returns the actor to record, or
    raises. Missing prerequisites are reported together."""
    check_callable(cmd)
    if full_access():
        # No lease, no confirmation, no class distinction. The caller still
        # writes the audit line before transmission.
        lease = control.held_by("motion")
        return lease.client_id if lease else "full-access"
    domain, needs_confirm = requirements(cmd)

    missing: list[str] = []
    if domain is not None:
        lease = control.held_by(domain)
        if lease is None:
            missing.append(
                f"hold the '{domain}' control lock (POST /api/v1/control with "
                f'{{"domains": ["{domain}"]}}) and send its token as '
                f"X-FWS-Control-Token")
        else:
            ok, reason = control.check(domain, token)
            if not ok:
                # Held by someone else. Distinct status: the caller cannot fix
                # this by acquiring, they have to wait or break the lease.
                raise Refusal(423, reason)
    if needs_confirm and not confirm:
        missing.append("resend with confirm=true")

    if missing:
        raise Refusal(428, (
            f"{cmd.python_name} is classified '{cmd.danger}'"
            f"{' (' + ', '.join(cmd.hazards) + ')' if cmd.hazards else ''}. "
            f"To call it: {'; '.join(missing)}. "
            f"Basis for that classification: {'; '.join(cmd.basis)}."))

    if domain is None:
        return "anonymous"
    lease = control.held_by(domain)
    return lease.client_id if lease else "anonymous"


# --------------------------------------------------------------- arguments
def _literal(expr: str) -> Any:
    try:
        return ast.literal_eval(expr)
    except (ValueError, SyntaxError):
        return None


def _coerce(cmd, index: int, name: str, declared: str, value: Any) -> Any:
    """Coerce one argument to the type the SDK coerces it to."""
    def bad(expected: str) -> Refusal:
        return Refusal(422, (
            f"{cmd.python_name} argument {index} ({name}): expected "
            f"{expected}, got {type(value).__name__} {value!r}"))

    if value is None:
        # allow_none=True on the proxy means None would go out as <nil>, but
        # the SDK never sends one, so no command is known to accept it.
        raise bad(f"{declared} (null is never sent by the SDK)")
    if isinstance(value, dict):
        raise bad(declared)

    if declared == "const":
        want = _literal(cmd.wire_args[index])
        if want is not None and value != want:
            raise Refusal(422, (
                f"{cmd.python_name} argument {index}: the SDK transmits the "
                f"constant {want!r} here and never anything else, so FWS has "
                f"no evidence the controller accepts {value!r}. Send {want!r}."))
        return want if want is not None else value

    if declared == "int":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError as e:
                raise bad("int") from e
        raise bad("int")

    if declared == "float":
        if isinstance(value, bool):
            raise bad("float")
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError as e:
                raise bad("float") from e
        raise bad("float")

    if declared == "str":
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)):
            return str(value)
        raise bad("str")

    if declared == "list":
        if not isinstance(value, list):
            raise bad("list")
        if any(v is None or isinstance(v, (dict, list)) for v in value):
            raise bad("a flat list of scalars")
        return value

    # "any": the SDK passes the value straight through, so there is no declared
    # type. Scalars and flat lists only; anything else is refused.
    if isinstance(value, list):
        if any(v is None or isinstance(v, (dict, list)) for v in value):
            raise bad("a flat list of scalars")
        return value
    if isinstance(value, (bool, int, float, str)):
        return value
    raise bad("a scalar or a flat list")


def coerce_args(cmd, args: list[Any]) -> list[Any]:
    """Exact arity and per-argument types, checked before anything
    is sent. Arity is exact, not minimum: extra arguments are not
    safely ignored by this controller."""
    # A recovered RPC has no wire_args in the registry; its arity comes from
    # recovered_rpcs.py instead.
    rec = RECOVERED.get(cmd.python_name)
    if rec is not None and not cmd.wire_args:
        if len(args) != rec.arity:
            raise Refusal(422, (
                f"{cmd.python_name} takes exactly {rec.arity} argument(s) on "
                f"this firmware (measured, not read from the SDK -- its "
                f"signature describes a struct read, not a wire call). "
                f"Got {len(args)}."))
        return [int(a) if isinstance(a, bool) else a for a in args]

    if len(args) != cmd.arity:
        raise Refusal(422, (
            f"{cmd.python_name} takes exactly {cmd.arity} argument(s) in wire "
            f"order {list(cmd.wire_args)}, got {len(args)}. Arity is exact: "
            f"extra arguments are not ignored safely by this controller "
            f"too many arguments can dispatch it unintentionally."))
    types = cmd.wire_types
    if len(types) != cmd.arity:
        # Parallel tuples from one generator, so this cannot happen -- but if
        # it ever does, zip() would truncate the ARGUMENT list to match and
        # send a short call. Fall back to unchecked types rather than to a
        # call with arguments missing.
        types = ("any",) * cmd.arity
    return [_coerce(cmd, i, n, t, v)
            for i, (n, t, v) in enumerate(zip(cmd.wire_args, types, args,
                                              strict=True))]


# --------------------------------------------------------------- reporting
def describe(name: str, cmd=None) -> dict[str, Any]:
    """One catalogue entry, including how its class was derived
    (basis and confidence)."""
    c = cmd or COMMANDS[name]
    domain, confirm = requirements(c)
    out: dict[str, Any] = {
        "name": name,
        "wire_name": c.wire_name or None,
        "wire_args": list(c.wire_args),
        "wire_types": list(c.wire_types),
        "arity": c.arity,
        "kind": c.kind,
        "danger": c.danger,
        "basis": list(c.basis),
        "confidence": c.confidence,
        "hazards": list(c.hazards),
        "callable": c.callable_directly,
        "requires_lock": domain,
        "requires_confirm": confirm,
        "verified": name in VERIFIED_COMMANDS,
        "brief": c.brief or None,
    }
    if c.wire_sequence:
        out["wire_sequence"] = list(c.wire_sequence)
    if c.name_mismatch:
        out["name_mismatch"] = True
        out["warning"] = (
            f"the SDK's Python name is {name} but it transmits "
            f"{c.wire_name}; verify before relying on it")
    if name in SDK_DEFECTS:
        out["sdk_defect"] = SDK_DEFECTS[name]
    return out


def warnings_for(name: str, cmd) -> list[str]:
    """What a caller should know about a command it just invoked."""
    out = []
    if name not in VERIFIED_COMMANDS:
        out.append(
            "never exercised on hardware by this project: generation proves "
            "what the SDK sends, not that it works on your firmware")
    if cmd.confidence == "none":
        out.append(
            "classified 'unknown': no positive evidence in the SDK source or "
            "docstring, so it is gated as strictly as motion")
    if cmd.name_mismatch:
        out.append(
            f"the SDK's Python name is {name} but it transmits {cmd.wire_name}")
    if name in SDK_DEFECTS:
        out.append(f"SDK defect: {SDK_DEFECTS[name]}")
    for hazard in cmd.hazards:
        if hazard == "blocks-the-rpc-lock":
            out.append(
                "blocks inside the controller: it holds FWS's single XML-RPC "
                "lock until it returns, and the stop path needs that lock")
    return out
