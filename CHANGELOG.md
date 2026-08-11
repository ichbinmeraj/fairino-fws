# Changelog

All notable changes to FWS are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project follows
[Semantic Versioning](https://semver.org/).

## [0.1.0] — unreleased

First public release.

### Added

- REST + WebSocket gateway for Fairino FR-series controllers.
- Live telemetry over REST and a 10 Hz WebSocket stream: joint angles, TCP
  pose, force/torque, joint torques, program and fault state.
- Bounded jogging with server-side limits and an inverse-kinematics pre-flight,
  on top of the controller's own soft limits.
- Program management: upload, load, run / pause / resume / stop Lua programs,
  with whole-path validation before a program is started.
- Controller file manager: list, read, write with optimistic concurrency,
  delete, and version history.
- Force/torque sensing and sensor setup: payload, centre of gravity, zero,
  activate.
- Full controller backup and verified point-table restore.
- Browsable RPC command registry and a gated invoker for the commands that are
  safe to call directly.
- Control lock over motion / config / program domains, with lease, heartbeat
  and a disconnect watchdog.
- API-key authentication. Loopback-only by default; refuses to start on a
  non-loopback address without an API-key file configured.
- `--simulator` mode: the whole gateway running against an in-process fake, no
  hardware required.
- Runs on Python 3.11–3.13; Docker image and systemd unit provided.
