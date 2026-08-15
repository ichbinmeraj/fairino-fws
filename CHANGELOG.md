# Changelog

All notable changes to FWS are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project follows
[Semantic Versioning](https://semver.org/).

## [0.1.0a14] — unreleased

### Added

- **API contract discipline.** `openapi.json` is now committed to the repo,
  and CI fails if the running app's surface drifts from it — so the API can
  only change through a commit a reviewer sees, even pre-1.0.
  `tools/check_contract.py` regenerates it (`--write`) and classifies a
  pending change as additive or breaking (`--since <ref>`), dependency-free.
- **`VERSIONING.md`** states the pre-1.0 promise: additive changes without
  notice, breaking changes allowed but never silent (changelog + CI
  classification).
- **`WEBSOCKETS.md`** documents `/ws/state` and `/ws/events`, which OpenAPI
  cannot describe. `/ws/state` gains `ts` and `age_s`, so a client can tell a
  fresh frame from a stale repeat without diffing values a parked arm sends
  identically.

### Fixed

- A test that exercised the `configure_app` seam mounted a route onto the
  shared app and never removed it, leaking it into later views of the API
  surface. It now restores the app's routes.

## [0.1.0a13] — 2026-08-15

### Added

- **A Python client library — `fws.client.FwsClient`.** The gateway's pitch
  is "drive the robot from any language, no vendor SDK", which was true of
  the wire and a half-truth in practice: every integrator reimplemented the
  same control-lease state machine, and three copies existed in this project
  alone.

  ```python
  from fws.client import FwsClient

  with FwsClient("http://localhost:8000") as fws:
      with fws.control("motion"):        # acquires, heartbeats, releases
          fws.enable()
          fws.jog(joint=1, direction=1, step=5)
      # released cleanly — no watchdog stop
  ```

  Dependency-free (urllib, not requests). Typed refusals — `NeedsLease`,
  `HeldByAnother`, `NeedsConfirm`, all subclassing `Refused` — carry the
  gateway's own wording. `control()` heartbeats at a third of the TTL and
  raises `LeaseLost` if renewal fails, rather than letting you keep
  commanding a robot the watchdog is about to stop.

- The four `examples/` now drive this client instead of a private copy;
  lease handling drops from thirty lines to three.

### Fixed

- `wait_until_idle` in the client polled `motion_done` before the controller
  had begun moving (it takes >270 ms to start), read the previous move's
  "done", and returned immediately. It now waits out the start latency.

## [0.1.0a12] — 2026-08-15

### Added

- **The typed gripper route** — `POST /api/v1/gripper/activate` and
  `POST /api/v1/gripper/command`. Open-and-close is a top-five request for
  any cobot gateway, and reaching it meant `POST /invoke/MoveGripper` with a
  ten-argument list in wire order and no bounds on any of them — one of
  which is how hard it squeezes.

  Every argument is bounded, rotation is sent as zeros (sending one to a
  gripper that does not rotate is not an accident to have), and the call is
  non-blocking so stop still works while it grips. Motion lease plus
  confirm; the refusal names the consequence rather than the rule.

  Gated on a **capability probe**, not a feature flag: this controller
  answers gripper getters with zeros when none is fitted, so a command would
  be accepted and silently do nothing. A missing gripper is now a clear 409,
  with `?force_probe=false` for a cell where the probe is wrong.

  `MoveGripper` is *documented*, not *measured*, on this firmware. The
  response says so in a `verified: false` field, and a test fails if someone
  measures it and forgets to update the note.

## [0.1.0a11] — 2026-08-15

### Added

- **`POST /api/v1/motion/move` — go to a pose, pre-flighted.** The single
  largest capability missing from this gateway. Every ingredient already
  existed; what was missing was a route putting them in the right order.

  Before anything is transmitted: the pose is six numbers; inverse
  kinematics solves it, so an unreachable pose or a singularity is refused
  here rather than discovered by the arm; every solved joint lands inside
  its soft-limit band with the standoff; the *target* TCP is above the
  configured floor; speed is inside the cap; the caller holds the motion
  lease and has confirmed. The command is audited *before* transmission, so
  a controller that wedges mid-move still leaves a record.

  Then one `MoveL` goes out, non-blocking — a blocking call would hold the
  RPC lock for the whole move and make a stop impossible.

  **Still off by default.** `features.enable_movel` stays false because this
  layout once produced an unintended ~300 mm motion and a controller fault
  on v3.8.5.1. It has since been read carefully from the SDK source, but
  that is not the same as verified on your hardware, and the difference is
  300 mm of arm travel. The refusal says so, with the evidence.

  It is not a path runner: trajectories belong in Lua on the controller.

## [0.1.0a10] — 2026-08-15

### Added

- **Prometheus metrics** at `GET /api/v1/metrics`. A cell gateway on a Pi is
  exactly what a plant scrapes, and every number already existed inside FWS
  — what was missing was a format anything could read. Exposed: the robot
  link (connected, frames, corrupt frames, frame age), the robot (faulted,
  error code, per-joint position and torque), the safety layer (watchdog
  health and errors, which lock domains are held), the record (audit events,
  durability, sink errors, events published, subscribers, fault dumps) and
  the probed capability counts.

  Hand-rendered rather than adding `prometheus_client` — a text exposition
  is a dozen lines and this gateway keeps four runtime dependencies. It is
  **not** on the always-open list: it carries live joint positions, and live
  state needs a key here. `/health` and stop stay open, so a failed scrape
  is still distinguishable from a dead gateway.

## [0.1.0a9] — 2026-08-15

### Added

- **A flight recorder for the arm** — `GET /api/v1/recordings`,
  `POST /recordings/start`, `POST /recordings/finish`, and per-recording
  download as JSONL or `?format=csv`.

  When something goes wrong on a cell running undocumented firmware, the
  question is always "what was the arm doing just before?" The audit trail
  says what was *commanded*; it never said where the arm actually was, how
  fast, or what the wrist felt. A rolling 60 s window of telemetry is now
  kept in memory and dumped beside the audit trail the moment a fault
  **latches** — on the rising edge, so the file holds the seconds *before*
  the fault. That needs no route: nobody is at a keyboard when a fault lands.

  Diagnostics never cost availability. The sampler reads the telemetry
  snapshot rather than tapping the parser, so it cannot slow the stream
  reader; a write failure stops the recording, counts the error and surfaces
  it in health rather than raising; and a recording name from a URL cannot
  escape the directory.

## [0.1.0a8] — 2026-08-15

### Added

- **Pushed events** — `WS /ws/events` and `GET /api/v1/events/stream` (SSE).
  To learn that a program finished, a fault latched, or the watchdog stopped
  the arm, a client had to poll or diff the 10 Hz telemetry stream and infer
  the edge itself. The watchdog stop was worse: it existed only as a line on
  stdout, so nothing could react to it.

  Two kinds of message go out: every audit record, so "who commanded what"
  arrives as it happens; and the edges the gateway is placed to notice —
  fault latched and cleared, program state changed, telemetry down and back.
  The first reading is never a transition, so restarting against a faulted
  controller does not announce a fault that did not just happen.
  `?topics=motion,fault` filters.

  **A subscriber that stops reading cannot slow down a command.** Each gets a
  bounded queue; on overflow the *oldest* events are dropped and counted, and
  the count rides on that subscriber's next event — so a consumer can never
  mistake a gap for quiet. `publish()` never blocks and never raises.

  The SSE form works from `curl`, so a shell script can react to robot events
  with nothing installed.

## [0.1.0a7] — 2026-08-15

### Added

- **The measured robot model, served as URDF** — `GET /api/v1/model` and
  `GET /api/v1/model/urdf`. No URDF matched to this firmware is published
  anywhere, and the vendor's rounded lengths are measurably worse than this
  controller's own, so RViz, Foxglove or a three.js scene can now draw this
  arm off `/ws/state` with nothing else installed.

  The chain is the measured one (59 `GetForwardKin` samples, 0.0000 mm RMS)
  and the joint limits come from the controller when it answers, falling
  back to a full turn rather than inventing a tighter bound — a planner that
  trusts a fabricated limit refuses reachable poses.

  The visual geometry is a primitive stand-in derived from the link lengths,
  said so in the document itself; `?visuals=none` omits it. The real meshes
  ship with `fairino-fws-console`. The chain is hardware-verified, but this
  *document* has not been checked in a URDF consumer against the live arm,
  and `/api/v1/model` says so.

## [0.1.0a6] — 2026-08-15

### Fixed

- **The watchdog stop leaves an audit line.** The gateway stopping the arm
  on its own — because a motion-lease holder went away mid-move — is the
  single most important thing the audit trail can hold, and it existed only
  as a `print()`. An incident review found the arm stopped and nothing
  saying who or why.

## [0.1.0a5] — 2026-08-15

### Added

- **Named poses, stored by the gateway.** A taught point is production data,
  and until now it lived in one browser's localStorage: it died with a
  profile, could not be reviewed or backed up, and no API client or CI job
  could see it. The controller cannot help — this firmware has no way to
  write a single named point into a point table — so the gateway keeps them.

  ```
  POST   /api/v1/poses/{name}/capture   record where the arm is now
  GET    /api/v1/poses                  list
  GET    /api/v1/poses/{name}           read
  PUT    /api/v1/poses/{name}           write explicitly
  POST   /api/v1/poses/{name}/rename    rename
  DELETE /api/v1/poses/{name}           forget
  POST   /api/v1/poses/program          generate Lua through named poses
  ```

  Kept as JSON in `data_dir` and written atomically, so a crash cannot leave
  a truncated file where the taught points were. A corrupt store starts empty
  and *keeps* the bad file rather than looking like someone deleted the
  points.

  Capture takes one telemetry snapshot, so a pose's joint and Cartesian
  halves cannot describe different positions, and it **refuses a stale
  frame** — the snapshot keeps its last values after the stream drops, so
  capturing without an age check would record where the arm *was*.

  Generated programs use literal joint targets, never point-table names, so
  `POST /programs/{name}/validate` can still solve every target backwards
  before anything moves. Generating never uploads or runs: generating is
  safe, running moves the arm, so they stay separate calls with separate
  gates.

## [0.1.0a4] — 2026-08-15

### Added

- **A supported test harness.** `fws.testing.gateway()` starts the fake
  controller, the driver, telemetry and the app on ephemeral ports and hands
  back a client, so you can test your cell logic against FWS in CI without
  hardware:

  ```python
  from fws.testing import gateway

  with gateway() as g:
      assert g.get("/api/v1/state").status_code == 200
      g.controller.trip_fault()
  ```

  The fake was always the strongest thing FWS shipped, but using it took
  private knowledge that lived only in this repo's test wiring. A pytest
  plugin (`pytest_plugins = ["fws.testing.pytest_plugin"]`, then the
  `fws_gateway` fixture) comes with it, and `FakeController` gains a
  **frozen** scenario API: `trip_fault`, `clear_fault`, `set_joints`,
  `set_force`, `corrupt_next_frame`. Everything else on that class stays an
  implementation detail.

- **`examples/`** — four runnable programs covering reading state, jogging
  under a control lease, the whole generate → upload → validate → load → run
  loop, and fault handling. Each starts its own simulated gateway, or takes
  `--url` to run against a real one. CI runs all four, so they cannot drift
  from the API.

### Fixed

- `MoveJ` was missing from the simulator's Lua builtin table although
  `lua_firmware` records it probed present at arity 29, so the fake rejected
  correct programs with "attempt to call global MoveJ".
- The harness gives each gateway a temporary `data_dir`, so running one no
  longer writes an upload index into the caller's working directory.

## [0.1.0a3] — 2026-08-15

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
