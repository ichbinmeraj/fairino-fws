#!/usr/bin/env python3
"""Take control, jog within bounds, and see a refusal on purpose.

THIS MOVES THE ARM when run against real hardware. Read SAFETY.md and stand
where you can reach the E-stop.

The idea worth taking away: a control lease is a dead-man's switch. If this
script dies mid-jog and stops renewing, the gateway's watchdog stops the arm.
`fws.control(...)` acquires it, heartbeats in the background, and releases it
cleanly on the way out -- and an explicit release does NOT fire the watchdog,
because a client that said goodbye has not disconnected.
"""
from __future__ import annotations

from _common import connect, show, step

from fws.client import Refused


def main() -> int:
    with connect("Jog safely under a control lease") as (fws, _args):
        step(1, "Take the motion lease")
        # Acquire, heartbeat at a third of the TTL, release on exit -- all of
        # it inside this block. Two consecutive failed renewals still leave
        # headroom before the watchdog fires.
        with fws.control("motion", client_id="example-jog"):
            show("lease", "held, heartbeating")

            step(2, "Enable the servos")
            fws.enable()
            show("enabled", True)

            step(3, "Jog J1 by 5 degrees, inside the configured bound")
            show("result", fws.jog(joint=1, direction=1, step=5.0, vel=10.0))

            step(4, "Wait for the move to finish")
            # The controller takes >270 ms to even BEGIN, so a client that
            # reads position immediately sees the old pose and concludes
            # nothing happened. wait_until_idle waits that out first.
            show("finished", fws.wait_until_idle(timeout=20))
            show("J1 now", f"{fws.state()['joints'][0]:.3f} deg")

            step(5, "Ask for something out of bounds, on purpose")
            # jog_max_deg defaults to 15. The refusal NAMES the limit rather
            # than silently clamping -- a clamp would move the arm somewhere
            # the caller did not ask for.
            try:
                fws.jog(joint=1, direction=1, step=90.0)
                show("result", "accepted?! the bound is not being enforced")
            except Refused as e:
                show("refused", e.detail)

            step(6, "Stop")
            # Always open: no key, no lease, never blockable. A FUNCTIONAL
            # stop -- the physical E-stop is the only emergency stop.
            show("confirmed", fws.stop()["confirmed"])

        show("lease", "released cleanly, so no watchdog stop")
        print("\nNext: 03_pick_and_place.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
