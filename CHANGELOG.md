# Changelog

All notable changes to FWS are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project follows
[Semantic Versioning](https://semver.org/).

## [0.1.0a3] — unreleased

### Fixed

Three promises the gateway made and did not keep — the failure mode this
project condemns everywhere else.

- **`limits.z_floor_mm` now protects something.** The floor was configurable
  and documented ("refuse any commanded pose below this TCP height") and
  enforced nowhere: `pathcheck.validate` grew a `z_floor` parameter that no
  call site ever passed, and the Cartesian jog never checked height at all.
  Both routes honour it now. The jog solves its target *forward* rather than
  adding the delta to the current Z — a tool-frame Z step is not a base-frame
  Z step, and a floor that only works in one frame is worse than none — and
  refuses when a configured floor cannot be checked.

- **The audit trail can be durable.** `AuditLog` accepted a file path that
  nothing ever passed, so every trail died with the process. `audit.file`
  (relative paths resolve against `server.data_dir`) wires it, and
  `GET /system/health` reports `file`, `durable` and `sink_errors`. The
  sink's own comment claimed its failures were "visible in health" while
  nothing counted them; they are now counted and warned about.

- **The typed motion routes are audited.** Jog, linear jog, servo enable,
  error reset and stop recorded *nothing* — only the router-based surfaces
  had been given the recorder — so every command the console can send was
  invisible to the trail that exists to answer "who commanded what". Stop
  records after the stop, deliberately: nothing sits between a stop request
  and the stop.

## [0.1.0a2] — 2026-08-15

### Added

- **`features.full_access` / `--full-access`: developer mode.** One switch
  that takes every software guard off at once — no control lease, no
  confirmations, no jog bounds or soft-limit pre-flight, all 594 commands
  callable raw including the ones normally refused at both the HTTP and
  driver layers, the optional feature flags and controller services forced
  on, and the startup safety refusals downgraded to printed warnings.

  It is **off by default**: a default install behaves exactly as before, and
  the guarded behaviour stays under test. When it is on, the startup banner,
  `GET /` and the config summary all say so.

  **This removes the rails that stop a mistake from reaching the
  controller.** A wrong argument can power the controller off one-way or
  write firmware, with no remote way back, and a runaway move is stopped
  only by the physical E-stop. Use it on a cell you control physically. FWS
  remains not a safety device; see SAFETY.md.

- The simulator now answers `?source=controller` file listings from its own
  stores, building a real `fr_user_data.tar.gz` from whatever has been
  uploaded to it. Point tables and pendant-written programs enumerate
  against `fws --simulator` exactly as against hardware.

### Changed

- Jog `step` and `vel` ceilings moved from the request models into the
  handlers, so `full_access` can lift them. `direction` keeps its model
  bound: that one is about meaning (a truthy `-1` jogs positive), not
  magnitude.

## [0.1.0a1] — 2026-08-15

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
