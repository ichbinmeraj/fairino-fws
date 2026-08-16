# Testing your code against FWS

You can run your cell logic against a fake robot, in CI, with no hardware. FWS ships a test harness (`fws.testing`) that starts the *whole* gateway — fake controller, driver, telemetry, the FastAPI app — on ephemeral ports, hands you a client, and tears it all down again. Your code talks to a real FWS over real HTTP; only the arm is simulated.

The fake is faithful to the quirks that actually bite on controller software **v3.8.5.1** — StartJOG's wire argument order, the jog start latency, `error 14` while faulted, the 433-byte telemetry frame, the Lua compiler's verdicts — so the bugs it catches are the bugs you would otherwise find on hardware. Where it departs from the real controller, it does so on purpose; those departures are listed at the bottom of this page.

## The one call: `fws.testing.gateway()`

`gateway()` is a context manager. It starts a `FakeController` on ephemeral ports, builds the app against it, serves it on an ephemeral HTTP port, and yields a `Gateway`. On exit it stops the server and (if it created the controller) the controller too.

```python
from fws.testing import gateway


def test_my_cell_logic():
    with gateway() as g:
        assert g.get("/api/v1/state").status_code == 200
        g.controller.trip_fault()          # now make your code handle it
```

Everything is ephemeral and self-contained: each block gets a free HTTP port, a fresh fake robot, a temporary `data_dir` (so a test never litters the directory it ran from), and a clean control lock. The control lease is a module global, so the harness clears it on every entry — a lease you took in one `gateway()` block will not read as "held by someone else" in the next.

### What the `Gateway` gives you

| Attribute | What it is |
|---|---|
| `g.url` | Base URL, e.g. `http://127.0.0.1:54321` |
| `g.controller` | The `FakeController` — the scenario API lives here |
| `g.get` / `g.post` / `g.put` / `g.delete` | HTTP helpers over `urllib`, so the harness adds no dependency to your suite |
| `g.settings` | The resolved `Settings`, if you need to assert on them |

The HTTP helpers take `json=`, `headers=`, and `timeout=` (default `10.0`) and return a small `Response` with `.status_code`, `.body`, `.headers`, `.json()`, and `.text`. A 4xx is returned as a `Response`, **not** raised — FWS says a great deal through its refusals, and a harness that raised on them would make the interesting assertions awkward.

Two convenience helpers save you the handshake boilerplate:

```python
# Acquire a control lease and get the header to send with commanding calls.
headers = g.take_control(domains=("motion",))
# -> {"X-FWS-Control-Token": "..."}   send this with every commanding request

# Block until the 8083 stream has delivered a pose. Returns False on timeout
# (so you can assert on it) rather than raising.
assert g.wait_for_telemetry(timeout=5.0)
```

`take_control()` POSTs to `/api/v1/control` and raises `RuntimeError` if the lease is refused. Getting the header name wrong (`X-FWS-Control-Token`) is the most common first mistake against FWS, which is why the helper hands it to you.

### Configuring the gateway

`gateway()` takes dotted config keys as keyword arguments, exactly as the CLI takes them. Because Python keywords cannot contain dots, pass them unpacked:

```python
with gateway(**{"limits.jog_max_deg": 90}) as g:
    ...
```

Robot addressing (`robot.ip`, the RPC/telemetry/transfer ports) is always filled in from the fake and **cannot** be overridden — pointing this at a real robot is precisely what it exists to avoid.

Pass `wait_for_telemetry=False` when the test is *about* the stream being absent; otherwise the harness waits briefly for the first pose so `/api/v1/state` is populated before your test runs.

### Testing against the true jog latency

By default `gateway()` builds a *fast* fake (a 0.05 s jog start latency) so suites run quickly. The real controller's start latency is over 270 ms, and a `FakeController()` constructed on its own reproduces that (its default is 0.30 s). To exercise your code against the real latency, build the controller yourself and pass it in:

```python
from fws.testing import FakeController, gateway


def test_client_does_not_stack_jog_commands():
    with FakeController() as fake:          # start/stop it yourself
        with gateway(controller=fake) as g: # the real >270 ms latency
            ...
```

!!! warning
    When you pass your own `controller`, the harness does **not** start or stop it — the caller owns what the caller made. Use it as a context manager (`with FakeController() as fake:`) or call `fake.start()` / `fake.stop()` yourself. The default `gateway()` (no `controller=`) manages its own, and its fast latency will let a client that assumes the arm moves the instant `StartJOG` returns pass a test it would fail on hardware.

## The pytest plugin

For pytest, enable the plugin in your `conftest.py`:

```python
pytest_plugins = ["fws.testing.pytest_plugin"]
```

That gives you two fixtures:

- **`fws_gateway`** — function-scoped. Each test gets a running gateway and a fresh fake robot, torn down afterward. A fault or a taught frame leaking between tests is the kind of bug that costs an afternoon, so this is the default.
- **`fws_gateway_session`** — session-scoped. One gateway for the whole run. Faster, but state carries between tests, and you are responsible for resetting what you change.

```python
def test_my_cell_logic(fws_gateway):
    assert fws_gateway.get("/api/v1/state").status_code == 200
    fws_gateway.controller.trip_fault()
    ...
```

## The scenario API (a frozen surface)

The interesting part of a test is driving the robot into the state you want to handle. Five methods on `g.controller` do that:

| Method | Effect |
|---|---|
| `trip_fault(main=1, sub=22)` | Latch a fault. Defaults to the soft-limit violation the arm raises (`main=1`, `sub=22`). While faulted, position getters answer `error 14` (see below); telemetry keeps flowing. |
| `clear_fault()` | Clear the latched fault. The getters start answering again. |
| `set_joints(joints)` | Place the arm — six joint angles in degrees. The TCP follows through forward kinematics, so telemetry and the position getters stay consistent. |
| `set_force(ft)` | Set the wrist force/torque reading: `[fx, fy, fz, tx, ty, tz]`. |
| `corrupt_next_frame(count=1)` | Send `count` telemetry frames with a deliberately wrong checksum. |

!!! note "This is the stable surface"
    These five names are a **frozen** API: a customer's CI suite is allowed to depend on them, and they will not change. Everything else on `FakeController` is an implementation detail of FWS's protocol work and may change without notice — do not build tests on it.

### A worked example

```python
from fws.testing import gateway


def test_cell_survives_a_fault_and_recovers():
    with gateway() as g:
        # A clean fake robot is already streaming telemetry.
        assert g.get("/api/v1/state").status_code == 200

        # Place the arm somewhere specific; telemetry follows.
        g.controller.set_joints([10, -80, 80, -90, -90, 0])

        # Trip the soft-limit fault the arm latches (main=1, sub=22).
        g.controller.trip_fault()

        # While faulted, the controller's position getters answer error 14,
        # but the 8083 stream keeps flowing, so /state still responds.
        # Assert that YOUR logic notices the fault and does the right thing.
        assert g.get("/api/v1/state").status_code == 200

        # Recover. A fault latches until it is cleared (ResetAllError on
        # hardware); clear_fault() is that reset.
        g.controller.clear_fault()
        assert g.get("/api/v1/state").status_code == 200
```

!!! warning "Corrupt frames carry a *plausible* pose"
    `corrupt_next_frame()` flips the checksum but leaves believable joint angles in the frame body — a client that ignores the checksum reads a pose that looks fine. That is the failure worth testing: your code must **drop** these frames, not read them. FWS itself counts them in its `bad_checksum` metric.

## What the simulator faithfully reproduces

On controller software **v3.8.5.1**, the fake reproduces the behaviours that catch clients out:

- **`StartJOG` wire argument order** — `(ref, nb, dir, vel, acc, max_dis)`, which is *not* the SDK's documented Python order. `max_dis` is a hard bound on the jog.
- **Jog start latency** — the arm does not move the instant `StartJOG` returns (over 270 ms on hardware; see the note above about the harness default).
- **`error 14` while faulted** — position getters (`GetActualJointPosDegree`, `GetActualTCPPose`, and the flange pose) return `error 14` *as a return value*, not as an XML-RPC fault, while telemetry keeps streaming. This makes "absent" and "suppressed by a fault" indistinguishable — worth testing precisely because it is easy to get wrong.
- **The 433-byte telemetry frame** — the exact v3.8.5.1 frame layout, with the header, correct checksum, and units. Joint torque is carried in **milli-newton-metres in the frame** but reported in **N·m over RPC**; a parser that forgets to divide fails here.
- **Port 8083 serves exactly one client** — a second connection is accepted at the TCP level but never receives a frame.
- **The Lua compiler's verdicts** — see below.
- **Assorted RPC facts** — `GetDO` faults `-506` (missing method), `FT_GetForceTorqueRCS` requires an argument despite its docstring, `FT_GetForceTorqueOrigin` answers `error 3` at every arity, an unreachable IK target returns `error 112`, and a soft-limit violation latches `main=1 sub=22` until it is reset.

## The Lua compiler

FWS validates a whole program before it will run it, and the fake reproduces how the real controller answers. `LuaUpLoadUpdate` returns **only `0` or `-1`** — the actual verdict goes to the controller log, whose filenames and mtimes do **not** reliably order it (the controller clock runs backwards across reboots, so an older log can look newer). The fake seeds that ordering trap on purpose: a client that picks the log by filename or mtime gets stale verdicts about the wrong program.

The compiler on this firmware produces one of four verdicts:

| Verdict | When |
|---|---|
| `success` | Every call is known and well-formed |
| `attempt to call global <fn> (a nil value)` | The function is not on this firmware |
| `bad argument #N to <fn> (Error number of parameters)` | The function exists but the argument count is wrong |
| `failed to query the database (the data does not exist)` | A `Lin`/`ARC`/`Circle` call on a cell with no taught points |

The builtins the fake knows on v3.8.5.1, with their accepted arity, mirror what the firmware actually accepts and rejects:

| Function | Arity | Notes |
|---|---|---|
| `WaitMs` | 1 | `0` and higher counts rejected |
| `MoveJ` | 29 | |
| `MoveL` | 32–33 | 34 rejected |
| `PTP` | 1–20 | |
| `SetDO` | 2–4 | |
| `FT_Control` | 24 | The manual says 21; the firmware wants 24 |
| `FT_Guard` | 26 | |
| `FT_Click` | 6 | |
| `Lin` / `ARC` / `Circle` | 11 / 1–40 / 1–40 | Present, but fail the point-name lookup on a cell with no taught points |

`PrintMsg` is documented in the manual but **absent** on this firmware — a program that calls it is rejected. The fake also models the wedged validator (a dead web socket returns `-1` at a fixed ~4.09 s with nothing written to the log), but that knob is part of the implementation detail, not the frozen scenario API.

## Where it is deliberately stricter or weaker

Faithful does not mean identical. Known, intentional departures:

- **Stricter on argument counts.** The fake validates RPC arity where the real controller is looser — `MoveL` insists on a 33-element array, tool frame ids on `1–15`, work-object ids on `0–14`, and so on. A malformed call fails in CI rather than moving a real arm.
- **Weaker Lua parsing.** The compiler only recognises a call that is a whole statement on its own line (matched by a simple regex). Calls nested in expressions or control flow are not analysed, so a program the fake calls `success` is not a guarantee the real compiler agrees.
- **The default jog latency is fast.** As above, `gateway()` uses a 0.05 s start latency for speed; construct your own `FakeController()` for the real >270 ms.
- **File-transfer ports stay bound.** On hardware the transfer ports appear roughly 250 ms *after* the matching `FileUpload`/`FileDownload` RPC, so a client must retry the connect. The fake keeps them bound throughout — a client cannot discover an ephemeral port that does not exist yet — so it does **not** exercise that retry path.

!!! danger "The fake is a test double, never a safety check"
    Passing every test against the simulator tells you your client is well-formed. It tells you nothing about the physical cell. FWS is not a safety device; none of its stops are emergency stops. The only emergency stop is the physical button wired per ISO 13850. Read [Safety](safety.md) before you point any of this at a real arm.