"""A Python client for FWS.

The gateway's pitch is "drive the robot from any language, no vendor SDK".
That is true of the wire protocol and was a half-truth in practice: every
integrator had to reimplement the same control-lease state machine -- acquire,
heartbeat on a timer, notice a failed renewal, release -- and get the same
423/428 distinctions right. Three copies of that logic already existed in this
project alone (the examples, the test harness, the console). This is the one
worth keeping.

    from fws.client import FwsClient

    with FwsClient("http://localhost:8000") as fws:
        print(fws.state()["joints"])

        with fws.control("motion"):          # acquires, heartbeats, releases
            fws.enable()
            fws.jog(joint=1, direction=1, step=5)
        # lease released here, cleanly -- no watchdog stop

DEPENDENCY-FREE, on purpose. urllib, not requests: this ships inside the
gateway package, and a client library that drags a dependency tree into a
cell controller has missed the point. If you would rather generate one, the
OpenAPI spec is served at /openapi.json and committed to this repo.

THE LEASE IS THE PART THAT MATTERS. `control()` runs a heartbeat thread at a
third of the TTL, so two consecutive failures still leave headroom before the
gateway's watchdog stops the arm. If renewal fails anyway it raises on the
next call rather than letting you keep commanding a robot you no longer hold
-- silence there would be the worst possible failure, because the arm is
about to be stopped underneath you and nothing said so.
"""
from __future__ import annotations

import contextlib
import json as _json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Sequence
from typing import Any

DEFAULT_TIMEOUT = 15.0
#: Anything touching the controller's file transfer or services is slow.
SLOW_TIMEOUT = 120.0


class FwsError(Exception):
    """Any failure talking to the gateway."""


class Refused(FwsError):
    """The gateway refused, and said why.

    FWS says a great deal through its refusals -- which bound was exceeded,
    which lease is missing, what the consequence of the command would be. That
    text is the most useful thing in the response, so it is the exception's
    message rather than a status code you have to look up.
    """

    def __init__(self, status: int, detail: Any, path: str) -> None:
        self.status = status
        self.detail = detail
        self.path = path
        text = detail if isinstance(detail, str) else _json.dumps(detail)
        super().__init__(f"{status} on {path}: {text}")


class NeedsLease(Refused):
    """428 -- acquire the control lock first. See `FwsClient.control`."""


class HeldByAnother(Refused):
    """423 -- someone else holds this domain."""


class NeedsConfirm(Refused):
    """400 with a consequence stated. Re-send with confirm=true if you mean it."""


class LeaseLost(FwsError):
    """A heartbeat failed. The gateway's watchdog is about to stop the arm."""


class FwsClient:
    """One connection's worth of state: the base URL, a key, maybe a lease."""

    def __init__(self, url: str = "http://localhost:8000", *,
                 api_key: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.token: str | None = None
        self._hb: threading.Thread | None = None
        self._hb_stop = threading.Event()
        self._lease_error: BaseException | None = None

    # -- plumbing ----------------------------------------------------------
    def request(self, method: str, path: str, body: Any = None, *,
                timeout: float | None = None) -> Any:
        headers = {}
        data = None
        if body is not None:
            data = _json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if self.token:
            headers["X-FWS-Control-Token"] = self.token
        req = urllib.request.Request(self.url + path, data=data,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(
                    req, timeout=timeout or self.timeout) as r:
                raw = r.read()
                return _json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raw = e.read()
            detail = None
            with contextlib.suppress(Exception):
                detail = _json.loads(raw).get("detail")
            if detail is None:
                detail = raw.decode("utf-8", "replace")
            raise _refusal(e.code, detail, path) from None
        except urllib.error.URLError as e:
            raise FwsError(f"could not reach {self.url}{path}: {e}") from e

    def get(self, path: str, **kw) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw) -> Any:
        return self.request("POST", path, body, **kw)

    def put(self, path: str, body: Any = None, **kw) -> Any:
        return self.request("PUT", path, body, **kw)

    def delete(self, path: str, **kw) -> Any:
        return self.request("DELETE", path, **kw)

    # -- reading -----------------------------------------------------------
    def state(self) -> dict:
        """Joints, TCP, force, faults, stream freshness -- one call."""
        return self.get("/api/v1/state")

    def health(self) -> dict:
        return self.get("/api/v1/system/health")

    def capabilities(self) -> dict:
        """What this controller actually answers to. `unknown` is not
        `absent`: it means FWS could not ask."""
        return self.get("/api/v1/capabilities")

    def errors(self) -> dict:
        return self.get("/api/v1/errors")

    def model_urdf(self, visuals: str = "primitives") -> str:
        """The measured kinematic model. Feed it to RViz or Foxglove."""
        req = urllib.request.Request(
            f"{self.url}/api/v1/model/urdf?visuals={visuals}",
            headers={"X-API-Key": self.api_key} if self.api_key else {})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read().decode()

    # -- the lease ---------------------------------------------------------
    @contextlib.contextmanager
    def control(self, *domains: str, client_id: str = "fws-client",
                ttl_s: float = 30.0) -> Iterator[FwsClient]:
        """Hold the control lock for the duration of the block.

        Acquires, heartbeats in the background, and releases on the way out.
        An explicit release matters: a lease that merely lapses fires the
        watchdog, and the watchdog stops the arm. Saying goodbye does not.

        Take every domain the block needs. Holding a subset makes half of it
        fail with 423s that look like bugs.
        """
        wanted = list(domains) or ["motion"]
        got = self.post("/api/v1/control",
                        {"client_id": client_id, "domains": wanted,
                         "ttl_s": ttl_s})
        self.token = got["token"]
        self._lease_error = None
        self._hb_stop.clear()

        def beat():
            # A third of the TTL: two consecutive failures still leave
            # headroom before the watchdog fires.
            period = max(1.0, ttl_s / 3.0)
            while not self._hb_stop.wait(period):
                try:
                    self.post("/api/v1/control/heartbeat")
                except BaseException as e:
                    self._lease_error = e
                    return

        self._hb = threading.Thread(target=beat, daemon=True)
        self._hb.start()
        try:
            yield self
        finally:
            self._hb_stop.set()
            if self._hb is not None:
                self._hb.join(timeout=5.0)
            self._hb = None
            with contextlib.suppress(FwsError):
                self.delete("/api/v1/control")
            self.token = None

    def _check_lease(self) -> None:
        if self._lease_error is not None:
            raise LeaseLost(
                f"the control lease stopped renewing ({self._lease_error}). "
                f"The gateway's watchdog stops motion when a holder goes "
                f"quiet, so do not assume this command arrived.")

    # -- commanding --------------------------------------------------------
    def enable(self, on: bool = True) -> dict:
        """Servo power. Enabling is confirmed for you -- you asked for it."""
        self._check_lease()
        return self.post("/api/v1/robot/enable", {"enable": on,
                                                  "confirm": True})

    def jog(self, joint: int, direction: int, step: float = 5.0,
            vel: float = 10.0) -> dict:
        """One bounded joint jog. direction is 1 or 0, never -1."""
        self._check_lease()
        return self.post("/api/v1/motion/jog",
                         {"joint": joint, "direction": direction,
                          "step": step, "vel": vel})

    def jog_linear(self, axis: int, direction: int, step: float = 10.0,
                   vel: float = 10.0, frame: str = "base") -> dict:
        self._check_lease()
        return self.post("/api/v1/motion/jog/linear",
                         {"axis": axis, "direction": direction, "step": step,
                          "vel": vel, "frame": frame})

    def move(self, pose: Sequence[float], *, vel: float = 20.0,
             tool: int = 0, user: int = 0) -> dict:
        """Go to an absolute pose. Needs features.enable_movel on the gateway."""
        self._check_lease()
        return self.post("/api/v1/motion/move",
                         {"pose": list(pose), "vel": vel, "tool": tool,
                          "user": user, "confirm": True})

    def stop(self) -> dict:
        """Functional stop. Never needs a lease or a key -- and NOT an
        emergency stop; that is the physical button."""
        return self.post("/api/v1/motion/stop")

    #: This controller takes >270 ms to even begin a move. Anything shorter
    #: than that and `motion_done` is still answering about the PREVIOUS move.
    START_LATENCY_S = 0.5

    def wait_until_idle(self, timeout: float = 60.0, poll: float = 0.2,
                        start_latency: float | None = None) -> bool:
        """Block until motion finishes. False on timeout rather than raising.

        WAITS OUT THE START LATENCY FIRST. This is the trap: motion does not
        begin for >270 ms, so a client that polls `motion_done` immediately
        reads `true` -- left over from before the command -- concludes the
        move already finished, reads the OLD position, and decides nothing
        happened. Waiting a beat before believing the flag is the whole
        difference between this helper working and looking broken.
        """
        grace = (self.START_LATENCY_S if start_latency is None
                 else start_latency)
        time.sleep(grace)
        deadline = time.monotonic() + max(0.0, timeout - grace)
        while time.monotonic() < deadline:
            q = self.get("/api/v1/motion/queue")
            if q and q.get("motion_done"):
                return True
            time.sleep(poll)
        return False

    # -- poses -------------------------------------------------------------
    def poses(self) -> list[dict]:
        return self.get("/api/v1/poses")["poses"]

    def capture_pose(self, name: str, *, overwrite: bool = False) -> dict:
        """Record where the arm is now, on the gateway."""
        self._check_lease()
        return self.post(f"/api/v1/poses/{urllib.parse.quote(name)}/capture",
                         {"overwrite": overwrite})

    def program_from_poses(self, names: Sequence[str], *,
                           speed: float = 20.0) -> str:
        """Generate Lua through named poses. Does not upload or run it."""
        return self.post("/api/v1/poses/program",
                         {"poses": list(names), "speed": speed})["source"]

    # -- programs ----------------------------------------------------------
    def upload_program(self, name: str, source: str, *,
                       overwrite: bool = True) -> dict:
        """Upload and compile. A rejection carries the compiler's real
        complaint, recovered from the controller log."""
        self._check_lease()
        return self.put(f"/api/v1/programs/{urllib.parse.quote(name)}",
                        {"content": source, "overwrite": overwrite,
                         "confirm": True}, timeout=SLOW_TIMEOUT)

    def validate_program(self, name: str) -> dict:
        """Solve every literal motion target backwards before running it."""
        return self.post(
            f"/api/v1/programs/{urllib.parse.quote(name)}/validate",
            timeout=SLOW_TIMEOUT)

    def run_program(self, name: str) -> dict:
        self._check_lease()
        self.post(f"/api/v1/programs/{urllib.parse.quote(name)}/load")
        return self.post("/api/v1/execution/run", {"confirm": True})

    def execution(self) -> dict:
        return self.get("/api/v1/execution")

    # -- events ------------------------------------------------------------
    def events(self, topics: Sequence[str] | None = None,
               timeout: float = 300.0) -> Iterator[dict]:
        """Yield pushed events as they happen: faults, commands, watchdog stops.

        Server-Sent Events over the same urllib, so this adds no dependency
        and works through the SSH tunnel people actually deploy behind.
        Keepalives are swallowed; a `dropped` field on an event means this
        consumer fell behind and the gateway had to discard that many.
        """
        path = "/api/v1/events/stream"
        if topics:
            path += "?topics=" + ",".join(topics)
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        req = urllib.request.Request(self.url + path, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as stream:
            for raw in stream:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue            # comments are keepalives
                with contextlib.suppress(_json.JSONDecodeError):
                    yield _json.loads(line[5:].strip())

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> FwsClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._hb_stop.set()
        if self._hb is not None:
            self._hb.join(timeout=5.0)
            self._hb = None
        if self.token:
            with contextlib.suppress(FwsError):
                self.delete("/api/v1/control")
            self.token = None


def _refusal(status: int, detail: Any, path: str) -> Refused:
    if status == 428:
        return NeedsLease(status, detail, path)
    if status == 423:
        return HeldByAnother(status, detail, path)
    if status == 400:
        return NeedsConfirm(status, detail, path)
    return Refused(status, detail, path)
