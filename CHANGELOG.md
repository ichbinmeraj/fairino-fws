# Changelog

All notable changes to FWS are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project follows
[Semantic Versioning](https://semver.org/).

## [0.1.0a1] — unreleased

FWS is an API-only gateway; user interfaces live in separate packages.

### Added

- `fws.cli.main()` accepts a `configure_app` callback, invoked with the built
  application and resolved settings after all startup checks pass and before
  the server starts. This is the seam a separately installed package (such as
  `fairino-fws-console`) mounts extra routes through, without reimplementing
  argument parsing, simulator wiring or the safety checks.

### Removed

- The built-in jog console. `GET /` now returns a JSON service descriptor
  instead of an HTML page. The operator UI moved to its own package,
  `fairino-fws-console`; installing it restores a UI at `/console/`, and the
  gateway itself no longer ships any.

### Fixed

- Capability probing is now **fault-aware**. Many getters (the I/O reads,
  payload, frame-number and position getters) answer `error 14` purely
  because the controller is faulted; the identical call succeeds once the
  fault clears. The probe previously cached every non-zero return code as
  `ABSENT` ("a later-firmware feature"), so a probe that happened to run while
  the controller was faulted reported real features as permanently missing —
  observed on a live FR5 as `14/31 available · 17 absent`. A non-zero code
  read while the controller is faulted is now `UNKNOWN` ("re-probe once
  cleared"), never `ABSENT`; a method that truly does not exist still faults
  with `-506` and stays `ABSENT`. Re-probing the same unit healthy reports
  `28/31 · 3 absent · 0 unknown`.
- `GET /controller/services` no longer hangs for the full `connect_timeout_s`.
  Its liveness probes now use a dedicated, short `services.liveness_timeout_s`
  (default 5 s): a liveness verdict is decided at connect time, so the long
  connect timeout — which may be tuned high for slow FTP transfers — only ever
  delayed the "unreachable" answer. On a controller whose ftpd greets after a
  30 s reverse-DNS timeout this cut the status endpoint from 45 s to ~5 s with
  no change to any verdict.

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
