#!/usr/bin/env python3
"""Faults: react to one as it happens, read what it means, clear it.

Safe to run: it reads and resets faults, and commands no motion.

Two ideas worth taking away.

First, you do not have to poll. `/ws/events` (and its SSE form, used here)
pushes edges as they happen -- a fault latching, the watchdog stopping the
arm. Diffing a 10 Hz sample to notice a transition is what every integrator
used to write, and each got the corners slightly different.

Second, on this firmware a fault does not merely set a flag. While the
controller is faulted, many getters answer `error 14` -- which looks exactly
like "this firmware does not have that feature". FWS records those as UNKNOWN
rather than ABSENT for that reason, and re-probing after the fault clears is
how you find out the truth.
"""
from __future__ import annotations

import threading
import time

from _common import connect, show, step


def main() -> int:
    with connect("React to a fault, explain it, clear it") as (fws, args):
        simulated = not args.url

        step(1, "Listen for events in the background")
        seen: list[dict] = []

        def listen():
            for event in fws.events(timeout=30):
                seen.append(event)
                print(f"      << {event['kind']}", flush=True)
                if event["kind"] == "fault.latched":
                    return

        watcher = threading.Thread(target=listen, daemon=True)
        watcher.start()
        time.sleep(0.5)
        show("listening", "/api/v1/events/stream")

        step(2, "Trip a fault" if simulated else "(not tripping one on real "
                                                "hardware)")
        if simulated:
            # No API for "make the robot fault" -- that would be a strange
            # thing for a gateway to offer. This reaches the fake controller
            # directly, which is what the harness exposes it for.
            fws.controller.trip_fault(main=1, sub=22)
            watcher.join(timeout=10)
            show("pushed", [e["kind"] for e in seen] or "nothing arrived")

        err = fws.errors()
        show("faulted", err["faulted"])

        step(3, "What do the codes mean?")
        # The catalogue is the V3.9.8 manual applied to a v3.8.5.1
        # controller, so an entry can be plausible and wrong. FWS says which
        # codes it actually knows: `known: false` is the honest answer, not
        # a lookup failure.
        raw = err.get("raw") or {}
        for which in ("main", "sub"):
            code = raw.get(which) or 0
            if not code:
                continue
            meaning = fws.get(f"/api/v1/errors/codes/{code}")
            if meaning.get("known"):
                show(f"{which} {code}", meaning.get("description"))
                if meaning.get("process"):
                    show("", f"-> {meaning['process']}")
            else:
                show(f"{which} {code}", "NOT in the published table")

        step(4, "The flight recorder already saved the run-up")
        # Dumped automatically on the rising edge, because the seconds BEFORE
        # a fault are the ones worth having and nobody is at a keyboard when
        # it lands.
        dumps = [r["name"] for r in
                 fws.get("/api/v1/recordings")["recordings"]
                 if r["name"].startswith("fault-")]
        show("dumps", dumps or "none yet")

        step(5, "Clear it, then re-probe")
        show("reset", fws.post("/api/v1/errors/reset"))
        time.sleep(1.0)
        show("faulted now", fws.errors()["faulted"])
        caps = fws.post("/api/v1/capabilities/refresh")
        show("available", caps["available"])
        show("unknown", f"{caps['unknown']}  (inconclusive, not absent)")

        step(6, "Who did what")
        # Set audit.file to keep this across restarts; in memory it dies
        # with the process.
        for e in fws.get("/api/v1/events?limit=5")["events"][:5]:
            print(f"      {e['action']:<22} {e.get('actor', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
