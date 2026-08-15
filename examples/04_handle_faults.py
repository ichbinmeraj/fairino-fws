#!/usr/bin/env python3
"""Faults: detect one, read what it means, clear it, re-probe.

Safe to run: it reads and resets faults, and commands no motion.

The idea worth taking away: on this firmware a fault does not merely set a
flag. While the controller is faulted, many getters answer `error 14` --
which looks exactly like "this firmware does not have that feature". FWS
records those as UNKNOWN rather than ABSENT for that reason, and re-probing
after the fault clears is how you find out the truth.

Against the simulator this trips a fault deliberately so there is something
to look at. Against real hardware it only reports what is already there.
"""
from __future__ import annotations

import time

from _common import connect, show, step


def main() -> int:
    with connect("Detect, explain, clear and re-probe a fault") as (fws, args):
        simulated = not args.url

        step(1, "Current fault state")
        status, err = fws.get("/api/v1/errors")
        show("faulted", err.get("faulted"))
        show("raw codes", err.get("raw"))

        if simulated:
            step(2, "Trip a fault on the simulator")
            # Main 1 / sub 22 is a combination actually seen on the FR5.
            # There is no API for "make the robot fault" -- that would be an
            # odd thing for a gateway to offer -- so this reaches the fake
            # controller directly, which is what the harness exposes it for.
            fws.controller.trip_fault(main=1, sub=22)
            time.sleep(1.0)          # the fault poller runs at 2 Hz
            status, err = fws.get("/api/v1/errors")
            show("faulted", err.get("faulted"))
            show("main", err.get("main"))
            show("sub", err.get("sub"))
        else:
            step(2, "(skipped: not tripping a fault on real hardware)")

        step(3, "What does the code mean?")
        # The catalogue is the V3.9.8 manual applied to a v3.8.5.1 controller,
        # so an entry can be plausible and wrong. FWS says which codes it
        # actually knows rather than implying it knows them all -- `known:
        # false` is the honest answer, not a lookup failure.
        raw = err.get("raw") or {}
        for which in ("main", "sub"):
            code = raw.get(which) or 0
            if not code:
                continue
            status, meaning = fws.get(f"/api/v1/errors/codes/{code}")
            if status != 200:
                show(f"{which} {code}", f"lookup failed ({status})")
                continue
            if meaning.get("known"):
                show(f"{which} {code}", meaning.get("description"))
                if meaning.get("process"):
                    show("", f"-> {meaning['process']}")
                show("", f"source: {meaning.get('source')}")
            else:
                show(f"{which} {code}", "NOT in the published table")
                show("", meaning.get("note", "")[:70] + "...")

        step(4, "What was suppressed while faulted?")
        # This is the important part: a getter refusing during a fault is not
        # evidence the feature is missing.
        status, caps = fws.get("/api/v1/capabilities")
        if status == 200:
            show("available", caps["available"])
            show("unknown", f"{caps['unknown']}  (inconclusive, not absent)")

        step(5, "Clear it")
        status, body = fws.post("/api/v1/errors/reset")
        show("status", status)
        show("result", body)
        time.sleep(1.0)
        status, err = fws.get("/api/v1/errors")
        show("faulted now", err.get("faulted"))

        step(6, "Re-probe, now that the controller can answer")
        status, caps = fws.post("/api/v1/capabilities/refresh")
        if status == 200:
            show("available", caps["available"])
            show("absent", caps["absent"])
            show("unknown", caps["unknown"])

        step(7, "Who did what")
        # Every command above left a line. Set audit.file to keep it across
        # restarts -- in memory it dies with the process.
        status, events = fws.get("/api/v1/events?limit=5")
        for e in (events or {}).get("events", [])[:5]:
            print(f"      {e['action']:<22} {e.get('actor', '')}")
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
