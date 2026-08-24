"""The Lua catalogue: what a program running on the controller can call.

278 functions with prototypes quoted from the vendor manual, with known manual
errors marked. Not a capability report: the manual documents a later firmware
than v3.8.5.1, so a function appearing here means only that Fairino documented
it. Availability on a given controller is a runtime question.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .lua_lint import fatal, lint_text
from .protocol.lua_bridge import (
    ARGUMENT_ORDER_CONFLICTS,
    REFUSE_TO_GENERATE,
    both,
    divergence,
    lua_only,
    rpc_only,
    summary,
)
from .protocol.lua_firmware import (
    LUA_FIRMWARE,
    absent_on,
    arity_disagrees_on,
    availability,
    needs_a_taught_point,
    probed_versions,
)
from .protocol.lua_functions import (
    ARITY_CONFLICTS,
    LUA_FUNCTIONS,
    MANUAL_ERRATA,
    by_section,
    resolve,
)

router = APIRouter(prefix="/api/v1/lua", tags=["lua"])

# Firmware whose measurements ship with FWS; other controllers report
# `measured: null`.
PROBED_FIRMWARE = "v3.8.5.1"


def _describe(name: str) -> dict:
    f = LUA_FUNCTIONS[name]
    out: dict = {
        "name": name,
        "section": f.section,
        "prototype": f.prototype or None,
        "arity": f.arity,
        "brief": f.brief or None,
        "also_in_rpc": name in set(both()),
    }
    if f.prototype == "":
        out["prototype_note"] = ("the manual's table for this function could "
                                 "not be parsed into a single signature; read "
                                 "the manual directly before calling it")
    if name in ARITY_CONFLICTS:
        out["manual_arity_conflict"] = ARITY_CONFLICTS[name]
    if name in ARGUMENT_ORDER_CONFLICTS:
        out["argument_order_conflict"] = ARGUMENT_ORDER_CONFLICTS[name]
    if name in REFUSE_TO_GENERATE:
        out["refuse_to_generate"] = REFUSE_TO_GENERATE[name]
    a = availability(name, PROBED_FIRMWARE)
    if a is None:
        # Not probed is not absent.
        out["measured"] = None
    else:
        out["measured"] = {
            "firmware": PROBED_FIRMWARE,
            "present": a.present,
            "status": a.status,
            "probed_arity": a.probed_arity,
            "manual_arity_accepted": a.manual_arity_accepted,
            "detail": a.detail,
        }
    return out


@router.get("/functions")
def list_functions(section: str | None = None, q: str | None = None,
                   conflicts_only: bool = False, limit: int = 400):
    """The catalogue, filterable. `section` matches the manual's
    numbering as a prefix."""
    names = sorted(LUA_FUNCTIONS)
    if section:
        names = [n for n in names
                 if LUA_FUNCTIONS[n].section.startswith(section)]
    if q:
        needle = q.lower()
        names = [n for n in names if needle in n.lower()
                 or needle in LUA_FUNCTIONS[n].brief.lower()]
    if conflicts_only:
        flagged = (set(ARITY_CONFLICTS) | set(ARGUMENT_ORDER_CONFLICTS)
                   | set(REFUSE_TO_GENERATE))
        names = [n for n in names if n in flagged]
    return {
        "summary": summary(),
        "matched": len(names),
        "returned": min(len(names), limit),
        "functions": [_describe(n) for n in names[:limit]],
    }


@router.get("/firmware")
def firmware():
    """What one real controller accepts, as opposed to what Fairino documented.

    Measured with the upload validator (a one-line program, no motion or
    execution). `absent` lists documented names that do not exist on this
    firmware.
    """
    return {
        "probed_versions": probed_versions(),
        "firmware": PROBED_FIRMWARE,
        "absent": absent_on(PROBED_FIRMWARE),
        "arity_disagrees_with_manual": arity_disagrees_on(PROBED_FIRMWARE),
        "needs_a_taught_point": needs_a_taught_point(PROBED_FIRMWARE),
        "coverage": "complete: all 282 catalogued names",
        "method": ("the Lua compiler's own verdict, read from the controller "
                   "log via RbLogDownload. Nothing was loaded or executed."),
        "absent_means": ("the compiler said 'attempt to call global X (a nil "
                         "value)'. Names that merely failed a database lookup "
                         "are PRESENT and listed under needs_a_taught_point."),
    }


@router.get("/sections")
def sections():
    """The manual's own grouping -- motion, IO, welding, force, and the rest."""
    return {"sections": by_section()}


@router.get("/conflicts")
def conflicts():
    """Every place the two APIs, or the manual and itself, disagree."""
    return {
        "argument_order": ARGUMENT_ORDER_CONFLICTS,
        "manual_arity": ARITY_CONFLICTS,
        "refused_for_generation": REFUSE_TO_GENERATE,
        "manual_errata": MANUAL_ERRATA,
        "reading": "argument_order entries run and do the wrong thing. "
                   "manual_arity entries cannot be generated safely. "
                   "manual_errata entries fail loudly as a nil call, which "
                   "of the three is the kind you want.",
    }


@router.get("/functions/{name}")
def get_function(name: str):
    """One function, on both sides, with every caveat attached."""
    canonical = resolve(name)
    if canonical is None:
        if name in MANUAL_ERRATA:
            raise HTTPException(404, {
                "message": f"{name} is a misspelling in the vendor manual",
                "detail": MANUAL_ERRATA[name]})
        raise HTTPException(404, f"no such Lua function: {name}")
    out = _describe(canonical)
    if canonical != name:
        out["requested"] = name
        out["errata"] = MANUAL_ERRATA[name]
    d = divergence(canonical)
    if d and d.get("in_rpc"):
        out["rpc"] = d.get("rpc")
        if "arity_differs" in d:
            out["arity_differs"] = d["arity_differs"]
    return out


@router.get("/bridge")
def bridge():
    """Which side each name lives on: `rpc_only` cannot be called
    from Lua, `lua_only` cannot be reached from the gateway."""
    return {"summary": summary(), "in_both": both(),
            "lua_only": lua_only(), "rpc_only": rpc_only()}


class LintRequest(BaseModel):
    source: str = Field(
        description="the Lua program text to check",
        max_length=512 * 1024)
    check_known_functions: bool = Field(
        default=True,
        description="also flag global calls that this firmware's measured "
                    "function table does not have. Off if your program calls "
                    "into something FWS has not probed.")


@router.post("/lint")
def lint_lua(req: LintRequest):
    """Check a program against what this controller family will accept.

    Static and offline: nothing is uploaded, nothing is run, no robot is
    involved. Meant to be called BEFORE an upload, because the controller's own
    answer to a bad program is either one line in a log you have to fetch over
    a slow channel, or -- worse -- accepting it and then silently refusing to
    start it.

    Findings carry a severity:

        error    the program will not run, or is known to break the controller:
                 the '%' and '#' operators this parser predates, os/io/require,
                 a bare PrintMsg (absent on v3.8.5.1), a MoveL that is not 33
                 arguments, a MoveJ that is not 29.
        warning  it will probably run, but nobody has proven it here: string.*
                 appears in the programs that have run only inside error paths
                 that never fired, and error() raised while a move is executing
                 kills that move.

    `ok` is true when nothing fatal was found. Warnings do not clear it on
    their own -- read them and decide.
    """
    known = None
    if req.check_known_functions:
        known = {
            name: entry.documented_arity
            for name, entry in LUA_FIRMWARE.get(PROBED_FIRMWARE, {}).items()
            if entry.present
        }
    findings = lint_text(req.source, known)
    problems = fatal(findings)
    return {
        "ok": not problems,
        "firmware": PROBED_FIRMWARE,
        "errors": len(problems),
        "warnings": len(findings) - len(problems),
        "findings": [
            {"line": f.line, "severity": f.severity, "rule": f.rule,
             "detail": f.detail, "text": f.text.strip()}
            for f in findings
        ],
        "note": (
            "static check only: a clean result means the program breaks none "
            "of the rules FWS knows about, not that it does what you meant."
        ),
    }
