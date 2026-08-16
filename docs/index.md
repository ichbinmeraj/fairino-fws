# FWS — Fairino Web Services

FWS is a REST + WebSocket gateway for Fairino FR-series collaborative robots. It puts an ordinary HTTP API in front of the controller, so you can read live state and drive the robot from any language — no vendor SDK, no Python dependency in your client, no direct exposure to the robot network.

In spirit it is what ABB's Robot Web Services is for ABB controllers: a language-agnostic web layer for a robot that did not ship with one.

!!! danger "FWS is not a safety device"
    No endpoint in FWS is an emergency stop. The only emergency stop is the physical button wired per ISO 13850. FWS carries no safety-rated function and is **not** certified to ISO 10218, ISO/TS 15066, IEC 61508 or ISO 13849.

    Its stop endpoints are software-implemented **functional** stops. They depend on a network link, a host, a Python process and controller firmware — any of which can fail at the moment you need them. They are a convenience, not a protective measure. Keep the physical E-stop within reach, and read [Safety](safety.md) before connecting FWS to a robot.

## Install and run

```bash
pip install --pre fairino-fws   # early alpha — --pre selects the pre-release
fws --simulator                 # run the whole gateway with no robot
```

Then open <http://127.0.0.1:8000/docs> for the interactive API, or hit it directly:

```bash
curl http://127.0.0.1:8000/api/v1/state
```

`--simulator` runs the full stack — fake controller, driver, telemetry, app — with no hardware. Against a real arm, use `fws --robot-ip 192.168.57.2` instead (`192.168.57.2` is the default, on the user LAN port; the teach port is `192.168.58.2`).

## What it does

- **Live telemetry** — joint angles, TCP pose, force/torque, joint torques, program and fault state, over REST and a 10 Hz WebSocket stream.
- **Bounded motion** — jogging with server-side limits and an inverse-kinematics pre-flight, on top of the controller's own soft limits.
- **Programs** — upload, load, run/pause/resume/stop Lua programs, with whole-path validation before a program is started.
- **Files** — list, read, write (with optimistic concurrency), delete, and version history for controller programs and point tables.
- **Force & sensors** — read the force/torque sensor, set payload and centre of gravity, zero and activate the sensor.
- **Backup & restore** — full controller backup and verified point-table restore.
- **The command surface** — the controller's RPC commands are browsable and, where safe, directly callable through a gated invoker.
- **A control lock** — motion / config / program domains held by lease with a heartbeat and a disconnect watchdog.

!!! note "Stop and health are never authenticated"
    `POST /api/v1/motion/stop` is never authenticated and normally returns 200 (the exception is [read-only mode](control-and-safety.md), which refuses every non-GET). A client whose key is wrong, expired or fumbled must still be able to stop the arm and probe health. Its span of control is the motion FWS itself started — not the teach pendant, a Lua program already running on the controller, or another client on the robot network.

## Where to go next

- **[Quickstart](quickstart.md)** — from `pip install` to reading state and jogging under a lease.
- **[Python client](python-client.md)** — `fws.client.FwsClient` handles the control-lease handshake and heartbeat for you; refusals are typed.
- **[Motion](motion.md)** — jogging, moves, the IK pre-flight, and the functional-stop path.
- **[Events](events-and-observability.md)** — the WebSocket state stream, and pushed events for commands, faults and watchdog stops.
- **[Deployment](reference-and-deployment.md)** — binding, authentication, and the two-network host model FWS expects.

!!! warning "Pre-1.0"
    FWS is early alpha. The API surface does not change silently — `openapi.json` is committed and CI fails on drift — but pin an exact alpha while pre-1.0, e.g. `fairino-fws==0.1.0a14`. FWS is an independent project and is not affiliated with or endorsed by Fairino / FAIR Innovation.