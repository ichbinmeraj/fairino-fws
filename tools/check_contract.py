#!/usr/bin/env python3
"""Keep the committed API contract honest. Run from CI and by hand.

    python tools/check_contract.py            # fail if openapi.json is stale
    python tools/check_contract.py --write     # regenerate it after a change
    python tools/check_contract.py --since REF # classify changes vs a git ref

The first form is the CI gate: it fails if the live app's surface has drifted
from the committed openapi.json, so the surface can only change through a
commit that a reviewer sees. The second is what you run after intentionally
changing a route. The third prints whether the pending change is additive or
breaking, so a breaking change is a decision rather than a surprise.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

# Import the app without a robot: create_app() against defaults never touches
# the controller until startup, and we never start it.
from fws import app as app_mod
from fws import contract

SPEC_PATH = pathlib.Path(__file__).resolve().parent.parent / "openapi.json"


def _live_spec() -> dict:
    return contract.snapshot(app_mod.app)


def _write() -> int:
    SPEC_PATH.write_text(contract.dumps(_live_spec()))
    print(f"wrote {SPEC_PATH}")
    return 0


def _check() -> int:
    """Fail if the app's SURFACE has drifted from the committed spec.

    Compared semantically, not byte-for-byte: FastAPI/pydantic emit
    cosmetically different JSON on different dependency sets, so a byte match
    would fail on one CI runner and pass on another for reasons that are not
    contract changes. The surface -- the operations and their required inputs
    -- is what a client depends on, and that must match everywhere.
    """
    import json
    if not SPEC_PATH.exists():
        print("openapi.json is missing. Run: "
              "python tools/check_contract.py --write", file=sys.stderr)
        return 1
    committed = json.loads(SPEC_PATH.read_text())
    changes = contract.classify_changes(committed, _live_spec())
    if not changes["breaking"] and not changes["additive"]:
        print("openapi.json surface is up to date")
        return 0
    print("openapi.json is STALE -- the app's surface has changed.\n"
          "If that was intentional, run:\n"
          "    python tools/check_contract.py --write\n"
          "and commit the result. If not, you changed the API by accident.",
          file=sys.stderr)
    for kind in ("breaking", "additive"):
        for line in changes[kind]:
            print(f"  [{kind}] {line}", file=sys.stderr)
    return 1


def _since(ref: str) -> int:
    import json
    try:
        old_text = subprocess.check_output(
            ["git", "show", f"{ref}:openapi.json"], text=True,
            stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        print(f"no openapi.json at {ref}; treating everything as additive")
        old_text = contract.dumps({"paths": {}})
    changes = contract.classify_changes(json.loads(old_text), _live_spec())
    for line in changes["additive"]:
        print(f"  [additive] {line}")
    for line in changes["breaking"]:
        print(f"  [BREAKING] {line}")
    if changes["breaking"]:
        print(f"\n{len(changes['breaking'])} breaking change(s). Pre-1.0 this "
              f"is allowed, but it must be deliberate and in the changelog. "
              f"See VERSIONING.md.")
        return 2      # distinct from a stale-file failure
    print("\nno breaking changes")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--write", action="store_true",
                   help="regenerate openapi.json from the current app")
    p.add_argument("--since", metavar="REF",
                   help="classify changes against openapi.json at a git ref")
    args = p.parse_args(argv)
    if args.write:
        return _write()
    if args.since:
        return _since(args.since)
    return _check()


if __name__ == "__main__":
    raise SystemExit(main())
