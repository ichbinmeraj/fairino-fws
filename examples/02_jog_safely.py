#!/usr/bin/env python3
"""Take control, jog within bounds, and see a refusal on purpose.

THIS MOVES THE ARM when run against real hardware. Read SAFETY.md and stand
where you can reach the E-stop.

The important idea: a control lease is a dead-man's switch. If this script
dies mid-jog and stops renewing, the gateway's watchdog stops the arm. That
is why every commanding client holds one.
"""
from __future__ import annotations

import time

from _common import connect, show, step


def main() -> int:
    with connect("Jog safely under a control lease") as (fws, _args):
        step(1, "Take the motion lease")
        fws.take_control(domains=("motion",), client_id="example-jog")
        show("token", "acquired (30 s TTL)")
        # Renew well inside the TTL: two consecutive failures should still
        # leave headroom before the watchdog fires.
        show("renew every", "10 s (TTL/3)")

        step(2, "Enable the servos")
        # Enabling is refused once without confirm, deliberately: the API
        # states the consequence, and the client repeats the request.
        status, body = fws.post("/api/v1/robot/enable", {"enable": True})
        if status == 400:
            show("gateway said", body["detail"] if isinstance(body, dict)
                 else body)
            status, body = fws.post("/api/v1/robot/enable",
                                    {"enable": True, "confirm": True})
        show("enabled", status == 200)

        step(3, "Jog J1 by 5 degrees, inside the configured bound")
        status, body = fws.post("/api/v1/motion/jog",
                                {"joint": 1, "direction": 1,
                                 "step": 5.0, "vel": 10.0})
        show("status", status)
        show("result", body)

        # The controller takes >270 ms to even begin a jog; a client that
        # reads position immediately sees the OLD pose and concludes nothing
        # happened. Wait for motion to finish instead.
        step(4, "Wait for the move to finish")
        for _ in range(40):
            time.sleep(0.1)
            _s, q = fws.get("/api/v1/motion/queue")
            if q and q.get("motion_done"):
                break
        _s, state = fws.get("/api/v1/state")
        show("J1 now", f"{(state.get('joints') or [0])[0]:.3f} deg")

        step(5, "Ask for something out of bounds, on purpose")
        # jog_max_deg defaults to 15. The refusal names the limit rather than
        # silently clamping -- a clamp would move the arm somewhere the
        # caller did not ask for.
        status, body = fws.post("/api/v1/motion/jog",
                                {"joint": 1, "direction": 1,
                                 "step": 90.0, "vel": 10.0})
        show("status", status)
        show("refusal", body)

        step(6, "Stop, then release the lease")
        # Stop is always open: no key, no lease, never blockable. It is a
        # FUNCTIONAL stop -- the physical E-stop is the only emergency stop.
        status, body = fws.post("/api/v1/motion/stop")
        show("stop confirmed", body.get("confirmed") if body else None)
        fws.release()
        show("lease", "released (a clean goodbye, so no watchdog stop)")

        print("\nNext: 03_pick_and_place.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
