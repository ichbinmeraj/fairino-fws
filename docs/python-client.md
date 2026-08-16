# The Python client

`pip install --pre fairino-fws` ships a client, `fws.client.FwsClient`. You do not have to reimplement the control-lease handshake, the heartbeat timer, or the 423/428 bookkeeping — that logic lived in three copies across this project (the examples, the test harness, the console) before it was pulled into one place worth keeping.

```python
from fws.client import FwsClient

with FwsClient("http://localhost:8000") as fws:
    print(fws.state()["joints"])

    with fws.control("motion"):          # acquires, heartbeats, releases
        fws.enable()
        fws.jog(joint=1, direction=1, step=5)
    # lease released here, cleanly -- no watchdog stop
```

The wire protocol is language-agnostic — that is the whole point of the gateway. This client exists only so Python callers get the lease state machine for free. From any other language, generate one from the spec at [`/openapi.json`](#other-languages).

## Dependency-free, on purpose

The client uses `urllib`, not `requests`. It ships *inside* the gateway package, and a client that drags a dependency tree onto a cell controller has missed the point. Everything below — including the Server-Sent Events stream — is built on the standard library, so it works unchanged through the SSH tunnel people actually deploy behind.

## Connecting

```python
FwsClient(url="http://localhost:8000", *, api_key=None, timeout=15.0)
```

- `url` — trailing slash is stripped for you.
- `api_key` — sent as the `X-API-Key` header when set. The gateway binds to loopback by default and only *requires* a key when bound to a non-loopback address; pass one when you configured one.
- `timeout` — per-request, in seconds. Default `15.0` (`DEFAULT_TIMEOUT`). File-transfer and controller-service calls override this internally with `SLOW_TIMEOUT` (120 s); you rarely touch it.

The instance holds one connection's worth of state: the base URL, the optional key, and — once you enter `control()` — a lease token. Use it as a context manager so the lease and heartbeat thread are always torn down:

```python
with FwsClient("http://robot-host:8000", api_key="…") as fws:
    ...
```

`close()` (called by `__exit__`) stops the heartbeat thread and best-effort releases any held lease.

## Reading

None of these need a lease or a key (health and stop are never authenticated by design).

| Method | Route | Returns |
|---|---|---|
| `state()` | `GET /api/v1/state` | Joints, TCP pose, force, faults, and stream freshness in one call |
| `health()` | `GET /api/v1/system/health` | Liveness / readiness |
| `capabilities()` | `GET /api/v1/capabilities` | What this controller actually answers to |
| `errors()` | `GET /api/v1/errors` | Current fault/error state |
| `model_urdf(visuals="primitives")` | `GET /api/v1/model/urdf` | The measured kinematic model as a URDF **string** |

```python
st = fws.state()
print(st["joints"])
```

!!! note "`unknown` is not `absent`"
    In `capabilities()`, a capability reported as `unknown` means FWS could not ask the controller — not that the feature is missing. Treat the two differently.

`model_urdf()` returns the URDF text (not JSON); feed it to RViz or Foxglove. The model is *measured* on the connected controller.

## The control lease

Motion, configuration, and program changes require holding a control lease. `control()` is a context manager that acquires it, renews it on a background thread, and releases it on the way out.

```python
with fws.control("motion"):
    fws.enable()
    fws.jog(joint=1, direction=1, step=5)
    fws.wait_until_idle()
# released here
```

```python
control(*domains, client_id="fws-client", ttl_s=30.0)
```

- `*domains` — the domains to hold (e.g. `"motion"`, `"program"`). Defaults to `["motion"]` if you pass none.
- `client_id` — identifies the holder in the lease record.
- `ttl_s` — lease time-to-live. The heartbeat runs at **a third** of this, so two consecutive failed renewals still leave headroom before the gateway's watchdog fires.

!!! warning "Take every domain the block needs, up front"
    Holding a subset makes the rest of the block fail with `423`s that look like bugs. If a program flow uploads and runs, hold the domains that upload *and* run require — `with fws.control("motion", "program"):` — not one of them.

### Why the heartbeat matters

A lease that merely lapses fires the watchdog, and the watchdog stops the arm. An *explicit* release — which `control()` does in its `finally` block via `DELETE /api/v1/control` — says goodbye cleanly and does not trigger a stop. That is the difference between exiting a `with` block and having the arm stopped underneath you.

The renewal runs in a daemon thread calling `POST /api/v1/control/heartbeat`. If a renewal ever fails, the client does **not** stay silent — silence there would be the worst failure, because the arm is about to be stopped and nothing said so. Instead the failure is stored, and the next lease-requiring command raises `LeaseLost`:

```python
from fws.client import LeaseLost

try:
    with fws.control("motion"):
        while running:
            fws.jog(joint=1, direction=1)   # raises LeaseLost if renewal died
except LeaseLost:
    # the watchdog is about to stop the arm; do not assume that last
    # command arrived. Stop commanding and re-acquire.
    ...
```

`LeaseLost` is checked at the start of every lease-requiring command (`enable`, `jog`, `jog_linear`, `move`, `capture_pose`, `upload_program`, `run_program`). It is deliberately **not** checked by `stop()` — you must always be able to stop.

## Commanding

All of these except `stop()` require a held lease.

### enable

```python
fws.enable(on=True)      # POST /api/v1/robot/enable
```

Servo power. Enabling is confirmed for you — you asked for it, so the client sends `confirm=true`.

### jog

```python
fws.jog(joint=1, direction=1, step=5.0, vel=10.0)   # POST /api/v1/motion/jog
```

One bounded joint jog. `direction` is **`1` or `0`, never `-1`** — that is the controller's convention, not a typo. `step` is in the joint's units; `vel` is a percentage.

### jog_linear

```python
fws.jog_linear(axis=2, direction=1, step=10.0, vel=10.0, frame="base")
# POST /api/v1/motion/jog/linear
```

A bounded Cartesian jog along one `axis`, in the named `frame` (default `"base"`).

### move

```python
fws.move([x, y, z, rx, ry, rz], vel=20.0, tool=0, user=0)   # POST /api/v1/motion/move
```

Go to an absolute pose. Confirmed for you (`confirm=true`).

!!! warning "`move()` needs `features.enable_movel`"
    Absolute moves are gated behind the `enable_movel` feature on the gateway. If it is off, the call is refused. Enable it in the gateway config when you intend to use `move()`.

### stop

```python
fws.stop()      # POST /api/v1/motion/stop
```

A **functional stop**. It needs no lease and no key, and always returns.

!!! danger "This is not an emergency stop"
    `stop()` is a software functional stop. It depends on a network link, a host, a Python process, and controller firmware — any of which can fail. The only emergency stop is the physical button wired per ISO 13850. FWS carries no safety-rated function. Keep the physical E-stop within reach.

    Its span of control is only the motion FWS itself started (jogs, program-space moves, its own path runners). It does **not** stop motion started from the teach pendant, a Lua program already running on the controller, or another client on the robot network.

### wait_until_idle

```python
fws.wait_until_idle(timeout=60.0, poll=0.2)   # polls GET /api/v1/motion/queue
```

Blocks until motion finishes. Returns `True` when idle, `False` on timeout — it does not raise.

!!! note "It waits out the start latency first"
    On this controller (firmware v3.8.5.1) motion does not *begin* for more than 270 ms after the command. A client that polls the done-flag immediately reads the stale `true` left over from before the move, concludes the move already finished, reads the old position, and decides nothing happened. `wait_until_idle()` sleeps `START_LATENCY_S` (0.5 s by default) before believing the flag. Override it with `start_latency=` if you have measured your own.

## Poses

```python
fws.poses()                                     # GET /api/v1/poses -> list[dict]
fws.capture_pose("pick", overwrite=False)       # config lease only if contended
src = fws.program_from_poses(["home", "pick", "place"], speed=20.0)
```

- `capture_pose(name, *, overwrite=False)` records where the arm is *now*, stored on the gateway. Like the other pose writes it needs the `config` lease only if another client is holding it; the client's own `LeaseLost` guard fires only if a lease *you* took has since failed.
- `program_from_poses(names, *, speed=20.0)` generates Lua that moves through the named poses and returns the **source string**. It does not upload or run it — that is the next section.

```python
with fws.control("motion"):
    fws.jog(joint=1, direction=1, step=10)
    fws.wait_until_idle()
    fws.capture_pose("pick", overwrite=True)
```

## Programs

```python
fws.upload_program("demo", source, overwrite=True)   # PUT /api/v1/programs/{name}
fws.validate_program("demo")                          # POST .../validate
fws.run_program("demo")                               # load, then run
fws.execution()                                       # GET /api/v1/execution
```

- `upload_program(name, source, *, overwrite=True)` uploads and compiles. A rejection carries the compiler's *real* complaint, recovered from the controller log — read `.detail` on the raised `Refused`. Requires a lease; uses the slow (120 s) timeout.
- `validate_program(name)` solves every literal motion target with inverse kinematics *before* you run it. No lease needed; slow timeout.
- `run_program(name)` loads the program and starts execution (both confirmed). Requires a lease.
- `execution()` reports current execution state.

```python
with fws.control("motion", "program"):
    src = fws.program_from_poses(["home", "pick", "place"], speed=20)
    fws.upload_program("demo", src)
    fws.validate_program("demo")      # catch unreachable targets before motion
    fws.run_program("demo")
    fws.wait_until_idle()
```

## Events

```python
events(topics=None, timeout=300.0)      # GET /api/v1/events/stream
```

An iterator over pushed events — faults, commands, watchdog stops — as they happen. It is Server-Sent Events over the same `urllib`, so it adds no dependency.

```python
for event in fws.events(topics=["faults", "commands"]):
    print(event)
    if event.get("dropped"):
        # this consumer fell behind; the gateway discarded that many events
        ...
```

Keepalive comments are swallowed for you. A `dropped` field on an event means your consumer fell behind and the gateway had to discard that many. `timeout` is the read timeout on the stream connection.

!!! note "SSE, not the WebSocket streams"
    This is the `/api/v1/events/stream` SSE endpoint. The gateway also exposes WebSocket streams (`/ws/state`, `/ws/events`) that OpenAPI cannot describe; those are documented in [WebSocket streams](websockets.md) and are not wrapped by this client.

## Typed refusals

FWS says a great deal through its refusals — which bound was exceeded, which lease is missing, what the consequence of a command would be. That text is the most useful thing in the response, so it *is* the exception's message. The status is mapped to a specific type so you can branch without parsing strings.

```python
from fws.client import (
    FwsError, Refused, NeedsLease, HeldByAnother, NeedsConfirm, LeaseLost,
)
```

| Exception | HTTP | Base | Meaning | What to do |
|---|---|---|---|---|
| `NeedsLease` | 428 | `Refused` | You are not holding the required control lease | Wrap the call in `control(...)` |
| `HeldByAnother` | 423 | `Refused` | Another client holds this domain — or you hold only a subset | Wait for it, or acquire every domain the block needs |
| `NeedsConfirm` | 400 | `Refused` | The command has a stated consequence | Re-send with `confirm=true` if you mean it |
| `Refused` | other | `FwsError` | Any other refusal | Read `.detail` and act on it |
| `LeaseLost` | — | `FwsError` | A heartbeat failed; the watchdog is about to stop the arm | Stop commanding; re-acquire |

Every `Refused` (and its subclasses) carries `.status`, `.detail`, and `.path`. `LeaseLost` is **not** a `Refused` — it originates client-side from a failed renewal and has no HTTP status. Catch `FwsError` to catch everything, including the transport-level "could not reach" error.

```python
from fws.client import FwsClient, NeedsLease, HeldByAnother, NeedsConfirm

with FwsClient() as fws:
    try:
        fws.jog(joint=1, direction=1)          # no lease held
    except NeedsLease:
        print("acquire the lease first")
    except HeldByAnother as e:
        print("someone else has it:", e.detail)
```

!!! note "When you hit `NeedsConfirm`"
    The typed helpers that carry an obvious consequence — `enable`, `move`, `upload_program`, `run_program` — already send `confirm=true`, because calling them *is* the confirmation. You will generally only see `NeedsConfirm` when calling other routes directly through the raw `get`/`post`/`put`/`delete` methods; re-send with `"confirm": true` in the body once you have read the consequence.

## Escape hatch: raw requests

Every helper is built on four thin methods that handle the token header, JSON encoding, and refusal mapping for you. Use them for routes the client does not wrap yet:

```python
fws.get("/api/v1/metrics")
fws.post("/api/v1/some/route", {"field": "value"})
fws.put("/api/v1/programs/x", {...}, timeout=120.0)
fws.delete("/api/v1/control")
```

They raise the same typed exceptions and attach the lease token automatically when one is held.

## Other languages

The client is a convenience for Python callers, not a requirement. The wire protocol is language-agnostic. Generate an equivalent from the OpenAPI spec:

- Served live at `/openapi.json`, with interactive docs at `/docs`.
- A canonical copy is committed to the repository, and CI fails if the running app drifts from it — so a generated client stays honest.

The REST surface under `/api/v1` is what is under contract. FWS is pre-1.0: additive changes (new routes, new optional fields) arrive in any release, and breaking changes are allowed but never silent — they are recorded in the changelog and flagged by CI. Pin an exact alpha while pre-1.0:

```
fairino-fws==0.1.0a14
```

See `VERSIONING.md` for exactly which changes count as breaking.
