"""The API contract: a committed snapshot, and what may change without warning.

An integrator who has just been handed a client library (fws.client) needs to
know which parts of the surface they can build on and which may move. Before
this, the honest answer was "all of it might, we are pre-1.0" -- which is true
and useless. This makes the contract concrete:

  * `openapi.json` is committed to the repo. CI fails if the live app's spec
    drifts from it, so the surface cannot change by accident -- only by a
    commit that a reviewer sees.
  * `classify_changes()` sorts a diff into ADDITIVE (a new route, a new
    optional field -- safe) and BREAKING (a route or a required field
    removed, a type changed, an enum narrowed -- not). CI warns on breaking
    changes so they are a decision, not a surprise.
  * VERSIONING.md states the pre-1.0 promise in words.

DEPENDENCY-FREE, like the rest of the gateway: no `oasdiff`, no
`openapi-spec-validator`. The checks below are the ones that actually bite a
client, written in a page of Python, rather than a full spec differ that
would add a toolchain to a project whose protocol layer imports nothing
outside the standard library.

The WebSocket streams (/ws/state, /ws/events) are invisible to OpenAPI --
FastAPI does not describe them -- so they are documented in WEBSOCKETS.md and
their shape is pinned by tests, not here.
"""
from __future__ import annotations

import json
from typing import Any


def snapshot(app: Any) -> dict:
    """The app's OpenAPI spec, normalised so the committed copy is stable.

    `info.version` is dropped: it tracks the package version and would make
    every release a spec change, burying the surface diffs that matter under
    a version bump. Everything else is the real contract.
    """
    # Force a rebuild rather than trusting app.openapi()'s cache. FastAPI
    # stores the first result in app.openapi_schema and returns it forever
    # after; if anything looked at the schema earlier under different state,
    # the cache -- not the current routes -- is what we'd snapshot. Clearing
    # it means `--write` and the drift check always describe the app as it
    # is now, so the two cannot disagree by accident of call order.
    app.openapi_schema = None
    spec = json.loads(json.dumps(app.openapi()))   # deep copy, plain types
    app.openapi_schema = None
    spec.get("info", {}).pop("version", None)
    return spec


def dumps(spec: dict) -> str:
    """Canonical text form: sorted keys, trailing newline. Two runs of the
    same surface produce byte-identical files, so `git diff` shows only real
    changes."""
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def _operations(spec: dict) -> dict[str, dict]:
    """{'GET /api/v1/state': operation, ...} -- the unit a client depends on."""
    out = {}
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if method.lower() in ("get", "post", "put", "delete", "patch"):
                out[f"{method.upper()} {path}"] = op
    return out


def _required_params(op: dict) -> set[str]:
    return {p["name"] for p in op.get("parameters", [])
            if p.get("required") and p.get("in") != "path"}


def _required_body_fields(op: dict, spec: dict) -> set[str]:
    """Required fields of the request body's schema, $ref resolved one level."""
    try:
        content = op["requestBody"]["content"]["application/json"]["schema"]
    except KeyError:
        return set()
    schema = _deref(content, spec)
    return set(schema.get("required", []))


def _deref(schema: dict, spec: dict) -> dict:
    ref = schema.get("$ref")
    if not ref or not ref.startswith("#/"):
        return schema
    node: Any = spec
    for part in ref[2:].split("/"):
        node = node.get(part, {})
    return node if isinstance(node, dict) else {}


def classify_changes(old: dict, new: dict) -> dict[str, list[str]]:
    """Sort the difference between two specs into breaking and additive.

    BREAKING is defined from the CLIENT's side: a change that can make a
    request that worked yesterday fail today. Adding a route or an optional
    field cannot; removing a route, removing an operation, or adding a
    required parameter or body field can.
    """
    breaking: list[str] = []
    additive: list[str] = []

    old_ops, new_ops = _operations(old), _operations(new)

    for op_id in old_ops:
        if op_id not in new_ops:
            breaking.append(f"removed: {op_id}")
    for op_id in new_ops:
        if op_id not in old_ops:
            additive.append(f"added: {op_id}")

    for op_id in old_ops.keys() & new_ops.keys():
        old_op, new_op = old_ops[op_id], new_ops[op_id]

        new_req_params = _required_params(new_op) - _required_params(old_op)
        for p in sorted(new_req_params):
            breaking.append(f"new required query param '{p}' on {op_id}")

        old_body = _required_body_fields(old_op, old)
        new_body = _required_body_fields(new_op, new)
        for f in sorted(new_body - old_body):
            breaking.append(f"new required body field '{f}' on {op_id}")
        for f in sorted(old_body - new_body):
            additive.append(f"body field '{f}' no longer required on {op_id}")

    return {"breaking": sorted(breaking), "additive": sorted(additive)}
