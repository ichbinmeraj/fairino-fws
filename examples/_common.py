"""Shared plumbing for the examples: connect to a gateway, or start one.

Every example takes `--url` to run against a gateway you started yourself
(including one talking to a real robot). With no `--url` it starts a
simulated gateway through `fws.testing.gateway()` and tears it down after.

The examples drive the real client library, `fws.client.FwsClient`, rather
than a private copy -- that duplication is what the library exists to end,
and an example that used something other than the shipped tool would be
teaching the wrong thing.
"""
from __future__ import annotations

import argparse
import contextlib
import sys

from fws.client import FwsClient


class ExampleClient(FwsClient):
    """FwsClient plus a handle on the fake robot, when we started one.

    Example 04 needs to make the robot fault. There is no API for that -- it
    would be a strange thing for a gateway to offer -- so it reaches the fake
    controller directly, which is what the test harness exposes it for.
    """

    controller = None


def parse_args(description: str) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--url", help="a gateway you started yourself; omit to "
                                 "run against a simulated one")
    p.add_argument("--api-key", help="only if the gateway runs with auth")
    return p.parse_args()


@contextlib.contextmanager
def connect(description: str):
    """Yield (client, args). Starts a simulated gateway when no --url."""
    args = parse_args(description)
    if args.url:
        with ExampleClient(args.url, api_key=args.api_key) as fws:
            yield fws, args
        return
    try:
        from fws.testing import gateway
    except ImportError as e:  # pragma: no cover - guidance, not logic
        # Say WHAT failed as well as what to do: swallowing the cause turns
        # "you have a broken install" into "you passed the wrong flag".
        raise SystemExit(
            f"could not import fws.testing ({e}).\n"
            f"Either install the gateway (pip install --pre fairino-fws), "
            f"or point this at a running one with --url.") from e
    print("· no --url given: starting a simulated gateway "
          "(nothing will move)\n")
    with gateway() as g, ExampleClient(g.url) as fws:
        fws.controller = g.controller
        yield fws, args


def show(label: str, value) -> None:
    """Print a labelled value the same way in every example."""
    print(f"  {label:<22} {value}")


def step(n: int, title: str) -> None:
    print(f"\n[{n}] {title}", file=sys.stdout, flush=True)
