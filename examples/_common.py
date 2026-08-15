"""Shared plumbing for the examples: connect to a gateway, or start one.

Every example takes `--url` to run against a gateway you started yourself
(including one talking to a real robot). With no `--url` it starts a
simulated gateway through `fws.testing.gateway()` and tears it down after.

Deliberately dependency-free -- urllib, not requests -- so the examples run
straight after `pip install fairino-fws` with nothing else installed.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import urllib.error
import urllib.request


class Client:
    """The smallest useful FWS client: HTTP plus the control-lease dance."""

    def __init__(self, url: str, api_key: str | None = None,
                 controller=None) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.token: str | None = None
        # The fake robot, when this example started a simulated gateway;
        # None when --url points at a gateway someone else is running. It is
        # how an example scripts a condition (a fault, a pose) that no API
        # would sensibly offer.
        self.controller = controller

    def request(self, method: str, path: str, body=None):
        headers = {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if self.token:
            headers["X-FWS-Control-Token"] = self.token
        req = urllib.request.Request(self.url + path, data=data,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            detail = None
            with contextlib.suppress(Exception):
                detail = json.loads(raw)
            return e.code, detail

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body=None):
        return self.request("POST", path, body)

    def put(self, path, body=None):
        return self.request("PUT", path, body)

    def take_control(self, domains=("motion",), client_id="example"):
        """Acquire a lease. FWS stops the arm if a holder stops renewing, so
        anything that commands motion holds one."""
        status, body = self.request(
            "POST", "/api/v1/control",
            {"client_id": client_id, "domains": list(domains), "ttl_s": 30})
        if status != 201:
            raise SystemExit(f"could not take control ({status}): {body}")
        self.token = body["token"]
        return self.token

    def heartbeat(self):
        return self.request("POST", "/api/v1/control/heartbeat")

    def release(self):
        if self.token:
            self.request("DELETE", "/api/v1/control")
            self.token = None


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
        yield Client(args.url, args.api_key), args
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
    with gateway() as g:
        yield Client(g.url, controller=g.controller), args


def show(label: str, value) -> None:
    """Print a labelled value the same way in every example."""
    print(f"  {label:<22} {value}")


def step(n: int, title: str) -> None:
    print(f"\n[{n}] {title}", file=sys.stdout, flush=True)
