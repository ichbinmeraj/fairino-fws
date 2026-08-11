"""Where the Lua surface and the XML-RPC surface disagree.

FWS talks to a Fairino controller two ways, and they are NOT the same API:

    XML-RPC on 20003     what the gateway calls from outside. Signatures are
                         machine-extracted from the vendor SDK's own source,
                         so they are what the SDK demonstrably transmits.

    FR Lua on the box    what a program calls from inside. Signatures come
                         from a PDF manual, for a LATER firmware than ours,
                         with OCR damage and self-contradictions.

Same names, different contracts. This module records every divergence we have
evidence for, because the alternative is a developer reading one manual and
calling the other API.

The most consequential entry is FT_SpiralSearch, where the same five
arguments are accepted in a different ORDER on each side. Nothing rejects a
swapped call; it just presses with the wrong number.

Nothing here is inferred from a name looking similar. Each entry cites where
the evidence came from.
"""
from __future__ import annotations

from .commands import COMMANDS
from .lua_functions import ARITY_CONFLICTS, LUA_FUNCTIONS


def both() -> list[str]:
    """Names present on both sides. Presence is not equivalence."""
    return sorted(set(LUA_FUNCTIONS) & set(COMMANDS))


def lua_only() -> list[str]:
    """In a Lua program only -- unreachable from the gateway, by any route."""
    return sorted(set(LUA_FUNCTIONS) - set(COMMANDS))


def rpc_only() -> list[str]:
    """Callable from the gateway only -- a Lua program cannot reach these."""
    return sorted(set(COMMANDS) - set(LUA_FUNCTIONS))


# --------------------------------------------------------------- divergences
# Same name, same arguments, DIFFERENT ORDER. Verified twice: the wire order
# is read from the SDK source, the Lua order from the manual's prototype.
ARGUMENT_ORDER_CONFLICTS: dict[str, dict[str, object]] = {
    "FT_SpiralSearch": {
        "rpc_order": ["rcs", "ft", "dr", "max_t_ms", "max_vel"],
        "lua_order": ["rcs", "dr", "ft", "max_t_ms", "max_vel"],
        "swapped": ["ft", "dr"],
        "rpc_evidence": "fairino-python-sdk Robot.py: "
                        "self.robot.FT_SpiralSearch(rcs, ft, dr, max_t_ms, max_vel)",
        "lua_evidence": "FRLua manual table 3-219: "
                        "FT_SpiralSearch(rcs, dr, ft, max_t_ms, max_vel)",
        "consequence": (
            "ft is a force threshold in newtons (0..100); dr is a feed rate "
            "per revolution in mm (default 0.7). Swapping them commands a "
            "0.7 N stop threshold with a 100 mm/rev feed -- a search that "
            "spirals outward fast and barely reacts to contact. Neither side "
            "rejects the call: both arguments are floats in range."),
    },
}

# The manual's prototype and the manual's own worked examples disagree on how
# many arguments these take. FWS will not GENERATE a call it cannot count.
# See ARITY_CONFLICTS in lua_functions.py for the measured counts.
REFUSE_TO_GENERATE: dict[str, str] = {
    "FT_Control": (
        "prototype lists 21 arguments; the manual's own examples use 23, 26, "
        "35, 36 and 37. The spread is explained by select/force_torque/gain "
        "each being 6-vectors that some examples expand inline and others do "
        "not -- but no reading of the manual reproduces every example. "
        "FT_Control turns on constant-force control, which MOVES THE ARM "
        "against a surface. Write the call by hand, against your own "
        "firmware, and prove it at low force."),
    "FT_Guard": (
        "prototype lists 26 arguments; the manual's worked example passes 27. "
        "An OCR-eaten comma makes an example SHORTER, never longer, so this "
        "is a real contradiction. FT_Guard arms collision protection -- a "
        "mis-set threshold is a guard that does not trip."),
}

# Force functions that exist on ONE side only. Not a divergence -- a
# capability boundary, and the reason the split below is what it is.
FORCE_LUA_ONLY = ("FT_Click", "TorqueRecordStart", "TorqueRecordEnd",
                  "TorqueRecordReset")
FORCE_RPC_ONLY = ("FT_Activate", "FT_SetZero", "FT_SetRCS", "FT_GetConfig",
                  "FT_SetConfig", "FT_GetForceTorqueRCS",
                  "FT_GetForceTorqueOrigin", "FT_PdIdenRecord",
                  "FT_PdIdenCompute", "FT_PdCogIdenRecord",
                  "FT_PdCogIdenCompute", "ForceSensorSetSaveDataFlag",
                  "ForceSensorComputeLoad", "ForceSensorAutoComputeLoad",
                  "GetForceSensorPayload", "SetForceSensorPayload",
                  "GetForceSensorPayloadCog", "SetForceSensorPayloadCog",
                  "SetAdmittanceParams", "EndForceDragControl",
                  "GetForceAndTorqueDragState", "SetForceSensorDragAutoFlag",
                  "ForceAndJointImpedanceStartStop")

# The split that falls out of the above, stated plainly because it is the
# thing a developer actually needs to know:
#
#   SETUP is XML-RPC.     Activating the sensor, zeroing it, telling it what
#                         payload hangs below it, setting its reference frame,
#                         reading it. All one-shot, all from the gateway,
#                         all available on v3.8.5.1.
#
#   STRATEGY is Lua.      Constant-force control, insertion searches, surface
#                         finding, compliance. These wrap MOTION and must run
#                         in the same execution context as the moves they
#                         modify -- a force mode set from the gateway and a
#                         move started on the controller are not synchronised
#                         by anything.
#
# The one exception worth stating: the RPC side of the strategy commands
# exists and its signatures are better evidenced than the Lua side's. If you
# must drive a force strategy from outside, the RPC signature is the one to
# trust -- but you still own the synchronisation problem.


def divergence(name: str) -> dict[str, object] | None:
    """Everything FWS knows about one name on both sides. None if unknown."""
    lua, rpc = LUA_FUNCTIONS.get(name), COMMANDS.get(name)
    if lua is None and rpc is None:
        return None
    out: dict[str, object] = {
        "name": name,
        "in_lua": lua is not None,
        "in_rpc": rpc is not None,
    }
    if lua is not None:
        out["lua"] = {"prototype": lua.prototype or None, "arity": lua.arity,
                      "section": lua.section, "brief": lua.brief or None}
    if rpc is not None:
        out["rpc"] = {"wire_name": rpc.wire_name or None,
                      "wire_args": list(rpc.wire_args), "arity": rpc.arity,
                      "kind": rpc.kind, "danger": rpc.danger}
    if name in ARGUMENT_ORDER_CONFLICTS:
        out["argument_order_conflict"] = ARGUMENT_ORDER_CONFLICTS[name]
    if name in ARITY_CONFLICTS:
        out["manual_arity_conflict"] = ARITY_CONFLICTS[name]
    if name in REFUSE_TO_GENERATE:
        out["refuse_to_generate"] = REFUSE_TO_GENERATE[name]
    if (lua is not None and rpc is not None
            and lua.arity is not None and lua.arity != rpc.arity):
        out["arity_differs"] = {"lua": lua.arity, "rpc": rpc.arity,
                                "note": "not necessarily a defect -- the RPC "
                                        "side packs vectors into arrays where "
                                        "Lua expands them inline"}
    return out


def summary() -> dict[str, object]:
    return {
        "lua_functions": len(LUA_FUNCTIONS),
        "rpc_commands": len(COMMANDS),
        "in_both": len(both()),
        "lua_only": len(lua_only()),
        "rpc_only": len(rpc_only()),
        "argument_order_conflicts": sorted(ARGUMENT_ORDER_CONFLICTS),
        "refused_for_generation": sorted(REFUSE_TO_GENERATE),
        "manual_arity_conflicts": sorted(ARITY_CONFLICTS),
    }
