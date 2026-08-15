"""Run a whole FWS gateway, against a fake robot, from one call.

This is the supported way to test a client against FWS without hardware:

    from fws.testing import gateway

    def test_my_cell_logic():
        with gateway() as g:
            assert g.get("/api/v1/state").status_code == 200
            g.controller.trip_fault()          # the arm faults
            ...

`gateway()` starts a FakeController on ephemeral ports, builds the app
against it, and serves it on an ephemeral HTTP port, then tears both down.
What you get back exposes:

    g.url                base URL, e.g. http://127.0.0.1:54321
    g.controller         the FakeController -- the scenario API lives here
                         (trip_fault, clear_fault, set_joints, set_force,
                         corrupt_next_frame)
    g.get/post/put/delete   thin requests-style helpers over urllib, so the
                         harness adds no dependency to your test suite
    g.settings           the resolved Settings, if you need to assert on them

WHY THIS EXISTS. FWS's fake controller is faithful enough to reproduce the
things that actually bite -- StartJOG's wire argument order, the >270 ms jog
start latency, error 14 while faulted, the 433-byte telemetry frame -- but
wiring it to the app took private knowledge that lived only in FWS's own
conftest. Anyone building on FWS had to copy that wiring, and it broke every
time the app's startup changed. Now it is one import, and the pieces it
depends on are covered by FWS's own tests.

The scenario methods on FakeController are a FROZEN surface: everything else
on that class is an implementation detail of the protocol work.

For pytest users, `fws.testing.pytest_plugin` provides the same thing as a
fixture. Enable it with:

    pytest_plugins = ["fws.testing.pytest_plugin"]
"""
from __future__ import annotations

import contextlib
import json as _json
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .fake_controller import FakeController


@dataclass
class Response:
    """Just enough of a response object to assert against."""

    status_code: int
    body: bytes
    headers: dict[str, str]

    def json(self) -> Any:
        return _json.loads(self.body)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


class Gateway:
    """A running gateway plus the fake robot behind it."""

    def __init__(self, url: str, controller: FakeController, settings) -> None:
        self.url = url
        self.controller = controller
        self.settings = settings

    # -- HTTP, over urllib so the harness adds no dependency ---------------
    def request(self, method: str, path: str, *, json: Any = None,
                headers: dict[str, str] | None = None,
                timeout: float = 10.0) -> Response:
        data = None
        hdrs = dict(headers or {})
        if json is not None:
            data = _json.dumps(json).encode()
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(
            self.url + path, data=data, headers=hdrs, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return Response(r.status, r.read(), dict(r.headers))
        except urllib.error.HTTPError as e:
            # A 4xx is an answer, not an exception: FWS says a great deal
            # through its refusals, and a harness that raised on them would
            # make the interesting assertions awkward.
            return Response(e.code, e.read(), dict(e.headers or {}))

    def get(self, path: str, **kw) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw) -> Response:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw) -> Response:
        return self.request("PUT", path, **kw)

    def delete(self, path: str, **kw) -> Response:
        return self.request("DELETE", path, **kw)

    # -- convenience -------------------------------------------------------
    def take_control(self, domains: Sequence[str] = ("motion",),
                     client_id: str = "harness") -> dict[str, str]:
        """Acquire a control lease and return the header to send with it.

        Every commanding route needs this when another client holds the
        domain, and getting the header name wrong is the most common first
        mistake against FWS.
        """
        r = self.post("/api/v1/control",
                      json={"client_id": client_id, "domains": list(domains)})
        if r.status_code != 201:
            raise RuntimeError(f"could not take control: {r.status_code} "
                               f"{r.text}")
        return {"X-FWS-Control-Token": r.json()["token"]}

    def wait_for_telemetry(self, timeout: float = 5.0) -> bool:
        """Block until the 8083 stream has delivered a pose. Returns False on
        timeout rather than raising, so a test can assert on it."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = self.get("/api/v1/state")
            if r.status_code == 200 and r.json().get("joints"):
                return True
            time.sleep(0.05)
        return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def gateway(*, controller: FakeController | None = None,
            wait_for_telemetry: bool = True,
            **settings_overrides: Any):
    """Run a gateway against a fake robot; yield a `Gateway`.

    `settings_overrides` are dotted config keys, exactly as the CLI takes
    them -- e.g. `gateway(**{"limits.jog_max_deg": 90})`. Robot addressing is
    filled in from the fake and cannot be overridden, since pointing this at
    a real robot is precisely what it is for avoiding.

    Pass your own `controller` to configure the fake first (a slower jog
    latency, a specific firmware string); it is started if it is not running
    and left alone on exit, so the caller owns what the caller made.
    """
    import uvicorn

    from .. import app as app_mod
    from .. import config as config_mod

    own_controller = controller is None
    fake = controller or FakeController(jog_start_latency_s=0.05,
                                        transfer_port_delay_s=0.05)
    if own_controller:
        fake.start()
        time.sleep(0.15)          # let the stream thread bind

    # A gateway writes its upload index (and anything else site-specific)
    # into data_dir. Defaulting that to the process's cwd would litter
    # whatever directory a test or an example happened to run from, so the
    # harness gets a temp one unless the caller asks for otherwise.
    tmp = tempfile.TemporaryDirectory(prefix="fws-harness-")
    try:
        settings = config_mod.load(**{
            "server.data_dir": tmp.name,
            **settings_overrides,
            "robot.ip": fake.host,
            "robot.rpc_port": fake.rpc_port,
            "robot.telemetry_port": fake.stream_port,
            "robot.upload_port": fake.upload_port,
            "robot.download_port": fake.download_port,
        })
        port = _free_port()
        app = app_mod.create_app(settings)
        # The control lock is a module global, so a lease taken in one
        # gateway() block is still held in the next one -- which reads as
        # "'motion' is held by someone else" in an unrelated test. Each
        # harness gets a clean lock.
        app_mod.control._leases.clear()
        config = uvicorn.Config(app, host="127.0.0.1", port=port,
                                log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        g = Gateway(f"http://127.0.0.1:{port}", fake, settings)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not server.started:
            time.sleep(0.02)
        if not server.started:
            raise RuntimeError("the gateway did not start within 10s")
        if wait_for_telemetry:
            # Not fatal: a test may be about the stream being absent.
            g.wait_for_telemetry(timeout=3.0)
        try:
            yield g
        finally:
            server.should_exit = True
            thread.join(timeout=10.0)
    finally:
        if own_controller:
            fake.stop()
        tmp.cleanup()
