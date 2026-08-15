#!/usr/bin/env python3
"""Read the robot: identity, live state, capabilities, health.

Nothing here commands anything, so it is safe against a live cell. Start
with this one -- if it works, your connection and (if configured) your API
key are right.
"""
from __future__ import annotations

from _common import connect, show, step

from fws.client import FwsError


def main() -> int:
    with connect(__doc__.splitlines()[0]) as (fws, _args):
        step(1, "Who is this?")
        root = fws.get("/")
        show("service", root["service"])
        show("read-only", root["read_only"])
        show("full access", root.get("full_access", False))

        try:
            ident = fws.get("/api/v1/robot")
            show("model", ident.get("model"))
            show("firmware", ident.get("software"))
        except FwsError as e:
            show("identity", f"unavailable ({e})")

        step(2, "Where is it right now?")
        state = fws.state()
        show("joints (deg)", ", ".join(f"{j:8.3f}"
                                       for j in state.get("joints") or []))
        show("TCP (mm, deg)", ", ".join(f"{v:8.2f}"
                                        for v in state.get("tcp") or []))
        show("force (N, Nm)", state.get("force"))

        step(3, "What can this controller actually do?")
        # Probed once at startup against the real firmware, not assumed.
        # Three states -- and `unknown` is NOT `absent`: it means FWS could
        # not ask, which says nothing about your firmware.
        caps = fws.capabilities()
        show("available", caps["available"])
        show("absent", f"{caps['absent']}  (this firmware lacks them)")
        show("unknown", f"{caps['unknown']}  (could not ask -- NOT absent)")
        absent = [f"{group}.{name}"
                  for group, entries in caps["groups"].items()
                  for name, c in entries.items() if c["state"] == "absent"]
        if absent:
            show("missing", ", ".join(absent[:4])
                 + (" ..." if len(absent) > 4 else ""))

        step(4, "The model, for RViz or Foxglove")
        show("urdf bytes", len(fws.model_urdf()))
        show("get it from", f"{fws.url}/api/v1/model/urdf")

        step(5, "Is anything wrong?")
        health = fws.health()
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
