"""FWS command-line entry point."""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from . import config as config_mod

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .config import Settings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fws",
        description="REST + WebSocket gateway for Fairino collaborative robots.",
        epilog="FWS is not a safety device. No endpoint is an emergency stop. "
               "See SAFETY.md.",
    )
    p.add_argument("--config", type=pathlib.Path,
                   default=os.environ.get("FWS_CONFIG"),
                   help="path to fws.toml")
    p.add_argument("--robot-ip", dest="robot.ip",
                   help="controller address (default 192.168.57.2; the teach "
                        "port is 192.168.58.2)")
    p.add_argument("--bind", dest="server.bind_host",
                   help="bind address (default 127.0.0.1)")
    p.add_argument("--port", dest="server.port", type=int,
                   help="listen port (default 8000)")
    p.add_argument("--read-only", dest="server.read_only",
                   action="store_const", const=True, default=None,
                   help="serve observation only: every non-GET operation is "
                        "refused, including stop. The gateway cannot command "
                        "the arm at all in this mode")
    p.add_argument("--simulator", "--sim", action="store_true",
                   help="run against a built-in simulated controller instead "
                        "of a robot. Needs no hardware; reproduces the "
                        "firmware quirks FWS was built against")
    p.add_argument("--check", action="store_true",
                   help="validate configuration and exit without starting")
    p.add_argument("--print-config", action="store_true",
                   help="print the resolved configuration and exit")
    return p


SIM_BANNER = """
  ┌────────────────────────────────────────────────────────────┐
  │  SIMULATOR.  No robot is connected and nothing will move.   │
  │                                                            │
  │  This is fws.testing.fake_controller: the same double the   │
  │  test suite runs against. It reproduces the quirks that     │
  │  actually bite -- StartJOG's wire argument order, the       │
  │  >270 ms jog start latency, error 14 while faulted, the     │
  │  433-byte telemetry frame, and a Lua compiler that rejects  │
  │  what this firmware really rejects.                         │
  │                                                            │
  │  It is deliberately STRICTER than the robot in one place:   │
  │  it checks argument counts, so a client that gets MoveL     │
  │  wrong fails here instead of moving a real arm 300 mm.      │
  └────────────────────────────────────────────────────────────┘
"""


def main(
    argv: list[str] | None = None,
    *,
    configure_app: Callable[[FastAPI, Settings], None] | None = None,
) -> int:
    """Run the gateway.

    `configure_app` is called with the built application and the resolved
    settings, after every safety check has passed and immediately before the
    server starts. It exists so a separately installed package can add routes
    -- `fairino-fws-console` mounts an operator UI this way -- without having
    to reimplement argument parsing, simulator wiring and the startup checks.
    Its correctness is the caller's business; the gateway does not inspect
    what gets mounted.
    """
    args = build_parser().parse_args(argv)
    overrides = {k: v for k, v in vars(args).items() if "." in k}

    simulator = None
    if args.simulator:
        # Started BEFORE the settings load, because it binds ephemeral ports
        # and the gateway has to be pointed at them.
        from .testing.fake_controller import FakeController
        simulator = FakeController()
        simulator.start()
        overrides.update({
            "robot.ip": simulator.host,
            "robot.rpc_port": simulator.rpc_port,
            "robot.telemetry_port": simulator.stream_port,
            "robot.upload_port": simulator.upload_port,
            "robot.download_port": simulator.download_port,
        })
        print(SIM_BANNER)

    try:
        settings = config_mod.load(
            pathlib.Path(args.config) if args.config else None, **overrides
        )
    except Exception as e:  # config errors must be legible, not tracebacks
        print(f"fws: configuration error: {e}", file=sys.stderr)
        return 2

    if args.print_config:
        print(settings.model_dump_json(indent=2))
        return 0

    problems = settings.check_safe_to_start()
    if problems:
        print("fws: refusing to start:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 3

    if args.check:
        print("configuration OK")
        for k, v in settings.summary().items():
            print(f"  {k:16s} {v}")
        return 0

    for k, v in settings.summary().items():
        print(f"  {k:16s} {v}")
    if not settings.auth.enabled:
        print("  note             loopback only; tunnel with "
              "'ssh -L 8000:localhost:8000 <host>'")

    if simulator is not None:
        print(f"  simulator        rpc {simulator.rpc_port}, "
              f"telemetry {simulator.stream_port}")
        print(f"  try              curl localhost:{settings.server.port}"
              f"/api/v1/state")
        print(f"  docs             http://localhost:{settings.server.port}/docs")

    import uvicorn

    from .app import create_app

    application = create_app(settings)
    if configure_app is not None:
        configure_app(application, settings)

    try:
        uvicorn.run(application,
                    host=settings.server.bind_host,
                    port=settings.server.port)
    finally:
        if simulator is not None:
            simulator.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
