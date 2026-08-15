# FWS — Fairino Web Services

A REST + WebSocket gateway for Fairino collaborative robots. FWS puts an
ordinary HTTP API in front of the controller, so you can read live state and
drive the robot from any language — no vendor SDK, no Python dependency in your
client, no direct exposure to the robot network.

It is, in spirit, what ABB's Robot Web Services is for ABB controllers: a
language-agnostic web layer for a robot that did not ship with one.

```bash
pip install --pre fairino-fws   # early alpha — --pre selects the pre-release
fws --simulator                 # run the whole gateway with no robot
```

Then open <http://127.0.0.1:8000/docs> for the interactive API.

---

## Safety

**FWS is not a safety device.** No endpoint is an emergency stop; the only
emergency stop is the physical button wired per ISO 13850. FWS carries no
safety-rated function and is not certified to ISO 10218, ISO/TS 15066,
IEC 61508 or ISO 13849. Its stop endpoints are software functional stops that
depend on a network link, a host, a Python process and controller firmware —
any of which can fail. Read [`SAFETY.md`](SAFETY.md) before connecting it to a
robot.

---

## What it does

- **Live telemetry** — joint angles, TCP pose, force/torque, joint torques,
  program and fault state, over REST and a 10 Hz WebSocket stream.
- **Bounded motion** — jogging with server-side limits and an
  inverse-kinematics pre-flight, on top of the controller's own soft limits.
- **Programs** — upload, load, run/pause/resume/stop Lua programs, with
  whole-path validation before a program is started.
- **Files** — list, read, write (with optimistic concurrency), delete, and
  version history for controller programs and point tables.
- **Force & sensors** — read the force/torque sensor, set payload and centre of
  gravity, zero and activate the sensor.
- **Backup & restore** — full controller backup and verified point-table
  restore.
- **The command surface** — the controller's RPC commands are browsable and,
  where safe, directly callable through a gated invoker.
- **A control lock** — motion / config / program domains held by lease with a
  heartbeat and a disconnect watchdog.

## Requirements

- Python 3.11+ (3.11 is the Raspberry Pi OS Bookworm system Python and the
  documented deployment target)
- A Fairino FR-series controller on the network — or none at all, using
  `--simulator`

## Install

```bash
pip install --pre fairino-fws    # from PyPI (early alpha; --pre selects it)
# or, from source:
pip install .
# or with Docker:
docker compose up
```

## Quick start

Against the simulator (no hardware):

```bash
fws --simulator
curl http://127.0.0.1:8000/api/v1/state
```

Against a real controller:

```bash
fws --robot-ip 192.168.58.2
```

By default FWS binds to `127.0.0.1`. Reach it from another host over an SSH
tunnel, or bind elsewhere **with authentication configured** — it refuses to
start on a non-loopback address without an API-key file.

## Configuration

Configure by file, environment variable, or CLI flag. Copy
[`fws.toml.example`](fws.toml.example) and edit it, or set `FWS_`-prefixed
environment variables (e.g. `FWS_ROBOT__IP=192.168.58.2`). Every setting is
documented inline in the example file.

## The API

All routes are under `/api/v1`. The full, live specification is served at
`/openapi.json`, with interactive docs at `/docs`. Domains:

| Prefix | What it covers |
|---|---|
| `/state`, `/motion`, `/ws/state` | live state, jog, move, stop, preview, the WebSocket stream |
| `/ws/events`, `/events/stream` | pushed events: commands, faults, watchdog stops |
| `/recordings` | telemetry recordings and the automatic fault dump |
| `/robot`, `/io`, `/frames`, `/gripper` | control layer: pose, I/O, frames, payload, gripper |
| `/model` | the measured kinematic model, served as URDF |
| `/poses` | named poses: capture, edit, generate a program from them |
| `/programs`, `/execution` | program CRUD, load, run/pause/resume/stop |
| `/files` | controller file manager |
| `/sensors`, `/force` | force/torque, payload, sensor setup |
| `/backup`, `/points` | backup and point-table restore |
| `/commands`, `/invoke` | the RPC registry and the gated invoker |
| `/system`, `/metrics` | version, health, ports baseline, Prometheus metrics |

Stop and health are never authenticated: a client whose key is wrong or missing
must still be able to stop the arm and probe health.

## Testing your code against FWS

Your cell logic can run against a fake robot in CI. `fws.testing.gateway()`
starts the whole stack — fake controller, driver, telemetry, the app — on
ephemeral ports:

```python
from fws.testing import gateway

def test_my_cell_logic():
    with gateway() as g:
        assert g.get("/api/v1/state").status_code == 200
        g.controller.trip_fault()          # now handle it
```

For pytest, add `pytest_plugins = ["fws.testing.pytest_plugin"]` to your
conftest and take the `fws_gateway` fixture. The scenario API on the fake
(`trip_fault`, `clear_fault`, `set_joints`, `set_force`,
`corrupt_next_frame`) is a stable surface you can depend on.

The simulator reproduces the quirks that actually bite: StartJOG's wire
argument order, the >270 ms jog start latency, `error 14` while faulted, the
433-byte telemetry frame, and a Lua compiler that rejects what this firmware
really rejects.

## Examples

`examples/` has four runnable programs — read state, jog under a lease, the
full program loop, fault handling. They need no robot and no configuration:

```bash
python examples/01_read_state.py
```

Pass `--url http://localhost:8000` to run any of them against a gateway you
started yourself, including one connected to a real arm. Read `SAFETY.md`
first if you do.

## Development

The whole gateway runs against an in-process simulator, so you can develop with
no hardware:

```bash
pip install -e ".[dev]"
pytest -q            # no robot required
ruff check fws tests
fws --simulator
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

FWS is licensed under the [Apache License 2.0](LICENSE) — see also [`NOTICE`](NOTICE).
Copyright © 2026 Meraj Safari.

FWS is an independent project and is not affiliated with or endorsed by
Fairino / FAIR Innovation.
