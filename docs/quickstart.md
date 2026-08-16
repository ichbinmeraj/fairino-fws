# Quickstart

Zero to your first jog, against the built-in simulator — no robot, no controller, no configuration. Everything below runs on one machine and moves nothing physical. When you point the same code at real hardware, the arm moves; the last section says what to read first.

## Install

FWS needs Python 3.11+ (3.11 is the Raspberry Pi OS Bookworm system Python and the documented deployment target).

```bash
pip install --pre fairino-fws
```

!!! note "Why `--pre`"
    FWS is early alpha. Every published release is a pre-release, so `pip` will not install it without `--pre` — a bare `pip install fairino-fws` finds nothing. The same flag is needed to upgrade. While pre-1.0 you may prefer to pin an exact alpha so an install is reproducible:

    ```bash
    pip install fairino-fws==0.1.0a14
    ```

The package ships the gateway, the `fws` command, the Python client, and an in-process simulator. It has no client-side dependency to reimplement — see below.

## Run the gateway against the simulator

```bash
fws --simulator
```

This starts the whole stack — a simulated controller, the driver, telemetry, and the HTTP + WebSocket app — with no hardware attached. The simulator is the same test double the suite runs against; it reproduces the firmware quirks FWS was built on (StartJOG's wire argument order, the >270 ms jog start latency, `error 14` while faulted, the 433-byte telemetry frame). It is deliberately stricter than the robot about argument counts, so a malformed command fails here instead of moving a real arm.

Nothing moves in simulator mode: there is no robot to move.

By default FWS binds to `127.0.0.1:8000`. It refuses to start on a non-loopback address unless authentication is configured — reaching it from another host is an SSH-tunnel decision covered on the deployment page, not something to do casually.

Interactive API docs are served at <http://127.0.0.1:8000/docs>, and the full machine-readable spec at `/openapi.json`.

## Read state over plain HTTP

Every route lives under `/api/v1`. State is a single GET — no lease, no key, no client library:

```bash
curl http://127.0.0.1:8000/api/v1/state
```

The response carries live telemetry plus the freshness of the stream it came from:

```json
{
  "joints": [0.0, -90.0, 90.0, 0.0, 90.0, 0.0],
  "tcp": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
  "force": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
  "error_main": 0,
  "error_sub": 0,
  "stream_connected": true,
  "age_s": 0.031
}
```

The values above are illustrative — the exact numbers depend on the simulator's pose. The field names are exact. `age_s` is how old the telemetry sample is; `stream_connected` is whether the gateway is receiving the controller's stream at all. Both are `null`/`false` if it is not, which is the honest answer rather than a stale reading dressed up as fresh.

## Drive it from Python

`pip install --pre fairino-fws` ships `fws.client.FwsClient`, so you do not reimplement the control-lease handshake — acquire, heartbeat on a timer, notice a failed renewal, release. It is dependency-free (`urllib`, not `requests`) because a cell controller should not inherit a dependency tree.

With `fws --simulator` still running in the other terminal, run this against it:

```python
from fws.client import FwsClient, Refused

with FwsClient("http://localhost:8000") as fws:
    # Reading commands nothing, so it is safe against any cell.
    print(fws.state()["joints"])

    # Take the motion lease for the duration of this block. control()
    # acquires it, heartbeats in the background at a third of the TTL, and
    # releases it cleanly on the way out. Hold every domain the block needs.
    with fws.control("motion", client_id="quickstart"):
        fws.enable()                                     # servo power on
        fws.jog(joint=1, direction=1, step=5.0, vel=10.0)  # within bounds
        fws.wait_until_idle(timeout=20)

        # Ask for something out of bounds on purpose. jog_max_deg defaults
        # to 15, so this is refused -- and the refusal NAMES the limit
        # rather than silently clamping the arm somewhere you did not ask.
        try:
            fws.jog(joint=1, direction=1, step=90.0)
        except Refused as e:
            print("refused:", e.detail)

        fws.stop()          # functional stop; never needs a lease or key
    # lease released here, cleanly -- so the watchdog does not fire
```

A few things that are load-bearing, not incidental:

- **`direction` is `1` or `0`, never `-1`.** That is the controller's convention, not a typo.
- **`enable()` is confirmed for you.** Enabling servo power is a stated-consequence action; the client sends the confirmation because you called the method that means it. Other consequential calls surface a `NeedsConfirm` you must answer explicitly.
- **The lease is a dead-man's switch.** If your process dies mid-jog and stops heartbeating, the gateway's watchdog stops the motion it started. An *explicit* release (leaving the `with` block) does **not** fire the watchdog — a client that said goodbye has not disconnected. If a heartbeat ever fails, the next command raises `LeaseLost` rather than letting you keep commanding an arm the watchdog is about to stop.
- **`stop()` is always open** — no key, no lease, normally returns 200 (except in read-only mode, which refuses every non-GET). A client whose key is wrong must still be able to stop the arm.

!!! danger "This code moves the arm on real hardware"
    Against the simulator nothing moves. Against a real controller, `jog()` moves joint 1. Before you point this at a robot, read [Safety](safety.md) and stand where you can reach the physical E-stop.

!!! warning "FWS is not a safety device"
    No endpoint in FWS is an emergency stop. `motion/stop` and the client's `stop()` are software **functional** stops: they depend on a network link, a host, a Python process, and controller firmware, any of which can fail at the moment you need them. Their span of control is only the motion FWS itself started — not the teach pendant, not a Lua program already running on the controller, not another client on the robot network. The **only** emergency stop is the physical button wired per ISO 13850. FWS carries no safety-rated function and is not certified to ISO 10218, ISO/TS 15066, IEC 61508 or ISO 13849. Keep the physical E-stop within reach.

## Next steps

- **The `examples/` directory** has four runnable programs that show whole flows, not fragments. Each starts its own simulated gateway, so they need no robot and no configuration:

    | Script | What it shows |
    |---|---|
    | `examples/01_read_state.py` | Connect, read live state, capabilities, health |
    | `examples/02_jog_safely.py` | Take a control lease, keep it alive, jog within bounds, hit a refusal on purpose |
    | `examples/03_pick_and_place.py` | The full program loop: generate Lua, upload, validate, load, run, watch |
    | `examples/04_handle_faults.py` | Trip a fault, read what it means, clear it, re-probe capabilities |

    ```bash
    python examples/01_read_state.py
    ```

    Pass `--url http://localhost:8000` to run any of them against a gateway you started yourself — including one connected to a real arm. Read `SAFETY.md` first if you do.

- **Real hardware:** when you are ready to leave the simulator, the [Deployment](reference-and-deployment.md) page covers `fws --robot-ip`, the loopback-plus-tunnel network model, and authentication. Do not skip it — the robot network is hostile by design, and no configuration of FWS makes an office-network cell safe.