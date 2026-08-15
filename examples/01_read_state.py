#!/usr/bin/env python3
"""Read the robot: identity, live state, capabilities, health.

Nothing here commands anything, so it is safe against a live cell. Start
with this one -- if it works, your connection and (if configured) your API
key are right.
"""
from __future__ import annotations

from _common import connect, show, step


def main() -> int:
    with connect(__doc__.splitlines()[0]) as (fws, _args):
        step(1, "Who is this?")
        status, root = fws.get("/")
        show("service", root["service"])
        show("read-only", root["read_only"])
        show("full access", root.get("full_access", False))

        status, ident = fws.get("/api/v1/robot")
        if status == 200:
            show("model", ident.get("model"))
            show("firmware", ident.get("software"))

        step(2, "Where is it right now?")
        status, state = fws.get("/api/v1/state")
        if status != 200:
            print(f"  state unavailable ({status}): {state}")
            return 1
        joints = state.get("joints") or []
        show("joints (deg)", ", ".join(f"{j:8.3f}" for j in joints))
        tcp = state.get("tcp") or []
        show("TCP (mm, deg)", ", ".join(f"{v:8.2f}" for v in tcp))
        show("force (N, Nm)", state.get("force"))

        step(3, "What can this controller actually do?")
        # Probed once at startup against the real firmware, not assumed. Three
        # states: available, absent, unknown -- and unknown is NOT absent.
        status, caps = fws.get("/api/v1/capabilities")
        if status == 200:
            show("available", caps["available"])
            show("absent", f"{caps['absent']}  (this firmware lacks them)")
            show("unknown", f"{caps['unknown']}  (could not ask -- NOT absent)")
            absent = [f"{group}.{name}"
                      for group, entries in caps["groups"].items()
                      for name, c in entries.items()
                      if c["state"] == "absent"]
            if absent:
                show("missing", ", ".join(absent[:4])
                     + (" ..." if len(absent) > 4 else ""))

        step(4, "Is anything wrong?")
        status, health = fws.get("/api/v1/system/health")
        show("stream connected", health.get("stream_connected"))
        show("audit durable", health.get("audit", {}).get("durable"))
        for w in health.get("warnings", []):
            print(f"  ! {w}")
        for c in health.get("checks_not_run", []):
            print(f"  ? {c['check']}: {c['why']}")
        if not health.get("warnings") and not health.get("checks_not_run"):
            show("warnings", "none")

        print("\nNext: 02_jog_safely.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
