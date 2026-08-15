#!/usr/bin/env python3
"""The full program loop: teach poses, generate Lua, upload, validate, run.

THIS MOVES THE ARM against real hardware. Read SAFETY.md first.

Why a program at all? On this firmware every motion except jogging is a Lua
program running on the controller. There is no "move to pose" call you can
safely stream at cycle rate -- the command channel is single-client and
serialised. So the honest pattern is: capture poses, let the gateway generate
the program, let the controller compile it, run it, watch the telemetry.

The poses are captured LIVE and stored on the GATEWAY, not in this script.
That is what makes them production data: they survive a restart, they can be
reviewed and backed up, and the generated program uses their literal joint
values -- which is what lets the gateway pre-flight every target through
inverse kinematics before anything moves.
"""
from __future__ import annotations

import time

from _common import connect, show, step

from fws.client import Refused

PROGRAM = "example_pick_place.lua"


def main() -> int:
    with connect("Teach poses, generate a program, run it") as (fws, _args):
        # motion to move and capture, program to load and run, config to
        # write poses. Holding a subset makes half of this fail with 423s
        # that look like bugs.
        with fws.control("motion", "program", "config", client_id="pnp"):
            fws.enable()

            step(1, "Teach three poses by moving there and capturing")
            plan = [("home", None), ("above_pick", (1, 1)),
                    ("above_place", (1, 0))]
            for name, jog in plan:
                if jog:
                    joint, direction = jog
                    fws.jog(joint=joint, direction=direction, step=10, vel=20)
                    fws.wait_until_idle(timeout=20)
                fws.capture_pose(name, overwrite=True)
                show(name, [round(v, 1) for v in fws.state()["joints"]])

            step(2, "The gateway generates the program")
            # It owns the MoveJ prototype and asserts its probed 29-argument
            # arity -- this controller does not ignore a wrong count safely.
            src = fws.program_from_poses(
                ["home", "above_pick", "above_place", "home"], speed=20)
            for line in src.splitlines():
                print(f"      {line}")

            step(3, "Upload it -- the controller compiles it")
            try:
                fws.upload_program(PROGRAM, src)
                show("uploaded", PROGRAM)
            except Refused as e:
                # A rejection carries the compiler's REAL complaint, recovered
                # from the controller log; the upload call itself says only
                # 0 or -1.
                show("compiler said", e.detail)
                return 1

            step(4, "Pre-flight every target before anything moves")
            report = fws.validate_program(PROGRAM)
            show("checked", report.get("checked"))
            show("verdict", report.get("verdict", report.get("ok")))
            for problem in (report.get("problems") or [])[:5]:
                print(f"      ! {problem}")
            if report.get("partial"):
                show("note", "PARTIAL -- some targets were not resolvable")

            step(5, "Run it")
            fws.run_program(PROGRAM)
            show("state", fws.execution()["state"])

            step(6, "Watch it go")
            # The simulator accepts, compiles and reports state but does not
            # execute Lua, so the pose below will not change against it. On
            # hardware this is where you watch the arm move.
            for _ in range(12):
                time.sleep(0.25)
                ex, st = fws.execution(), fws.state()
                print(f"      state {ex['state']:<8} line "
                      f"{ex['current_line']:<4} J1 {st['joints'][0]:7.2f}",
                      flush=True)
                if ex["state"] == "stopped":
                    break

            fws.post("/api/v1/execution/stop")

        show("done", "lease released; program left on the controller")
        print("\nNext: 04_handle_faults.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
