# Moving the arm

Every way FWS can command motion, and the guard each one carries. The theme
runs through all of them: nothing moves the arm without a reason the gateway can
audit, and every path that can move the arm can be stopped by the watchdog and
by the functional stop.

!!! danger "FWS is not a safety device"
    No endpoint on this page is an emergency stop. The only emergency stop is
    the physical button wired per ISO 13850. `POST /api/v1/motion/stop` is a
    software **functional** stop of motion FWS itself started; it depends on a
    network link, a host, a Python process and controller firmware, any of
    which can fail. Keep the physical E-stop within reach. Read
    [`SAFETY.md`](safety.md) before connecting to a robot.

## The gates every motion command shares

Three things stand between a request and the arm. Which apply depends on the
route, but the vocabulary is the same everywhere.

**The control lease.** Motion is held by lease so that a client which
disappears mid-move is noticed. Acquire the `motion` domain, send its token as
`X-FWS-Control-Token` on every command, and heartbeat it before its TTL
expires. If the holder lapses while it holds `motion`, the disconnect watchdog
issues a functional stop on its own and records `watchdog.stop` in the audit
trail.

```bash
# Acquire the motion lease. The response includes the token.
curl -X POST http://127.0.0.1:8000/api/v1/control \
  -H 'Content-Type: application/json' \
  -d '{"client_id": "bench-01", "domains": ["motion"], "ttl_s": 30}'

# Heartbeat before ttl_s elapses (default 30 s, min 5, max 600).
curl -X POST http://127.0.0.1:8000/api/v1/control/heartbeat \
  -H 'X-FWS-Control-Token: <token>'
```

The lease rule is not identical across routes, and the difference is
deliberate:

| Route | Lease requirement |
|---|---|
| `POST /motion/jog`, `/motion/jog/linear` | Needed only if **another** client holds `motion` (428 without a token, 423 with a wrong one). If nobody holds it, a single client works leaseless — but then no watchdog protects you. |
| `POST /motion/move` | **Always** required — 428 if the lease is unheld. A move must be stoppable by the watchdog if you disappear mid-motion. |
| `POST /gripper/*` | **Always** required — a gripper is motion. |
| `POST /poses/*` writes | Needs the `config` lease only if another client holds it. |
| `PUT/DELETE /programs/*`, `/execution/*` | Needs the `program` or `motion` lease only if another client holds it. |

!!! note "Prefer holding the lease even when it is optional"
    The leaseless single-client path exists for convenience, but a jog run
    without a lease has no watchdog behind it. If your process dies mid-step,
    nothing notices. Hold `motion` for the duration of any session that moves
    the arm.

**Confirmation.** Commands that move to an absolute pose, close a gripper, run
a program, or enable the arm require `confirm: true` in the body. It is not
decoration — each is a place where a wrong number moves the arm somewhere
nobody pictured.

**Enable.** The arm must be enabled before it will move. Enabling requires the
`motion` lease and `confirm: true`.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/robot/enable \
  -H 'X-FWS-Control-Token: <token>' \
  -H 'Content-Type: application/json' \
  -d '{"enable": true, "confirm": true}'
```

!!! warning "`--full-access` removes all of it"
    `features.full_access` lifts every guard on this page at once: no lease, no
    confirmation, no jog ceiling, no soft-limit standoff, no IK pre-flight.
    Everything below assumes it is **off**, which is the default. See
    [`SAFETY.md`](safety.md) for what you give up.

## Jogging

Jogging is a bounded, discrete step: one call moves one joint (or one Cartesian
axis) a bounded distance, then stops. It is the safest way to move the arm
because every step is pre-flighted against the soft limits before it is sent.

### Joint jog — `POST /api/v1/motion/jog`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/motion/jog \
  -H 'X-FWS-Control-Token: <token>' \
  -H 'Content-Type: application/json' \
  -d '{"joint": 1, "direction": 1, "step": 5, "vel": 10}'
```

| Field | Range | Default | Meaning |
|---|---|---|---|
| `joint` | 1–6 | — | which joint |
| `direction` | **0 or 1** | — | `1` = positive, `0` = negative |
| `step` | > 0, ≤ `limits.jog_max_deg` | 5.0 | degrees |
| `vel` | > 0, ≤ `limits.jog_max_vel_pct` | 10.0 | percent |

!!! note "Why `direction` is `0`/`1`, not `-1`"
    The handler reads direction as truthy (`1` positive, anything falsy
    negative). If the field were merely a signed number, a client sending the
    plausible-looking `-1` would be silently accepted and jog **positive** —
    the opposite of intent. The model rejects anything outside `0`/`1` with a
    422 so that mistake cannot reach the arm.

**The soft-limit pre-flight.** Before sending, the handler predicts where the
joint would land (`current ± step`) and refuses with 409 if that lands outside
the safe band `[min + margin, max - margin]`, where the margin is
`limits.limit_margin_deg`. Current position is read from the 8083 telemetry
stream rather than an RPC getter, because the position getters return `error
14` while the controller is faulted.

`step` and `vel` ceilings are enforced in the handler (422) rather than the
schema, so `full_access` can lift them.

!!! warning "The pre-flight needs telemetry and cached limits"
    The soft-limit check runs only when both the cached soft limits and a live
    joint frame are available. If the telemetry stream is down or the limits
    could not be read, the gateway's pre-flight is skipped and the jog is sent
    unchecked — the controller's own soft limits still apply, but the
    gateway's standoff does not. Check `GET /api/v1/system/health`.

### Cartesian jog — `POST /api/v1/motion/jog/linear`

A linear jog is solved backwards through inverse kinematics and refused if any
joint would leave its band or the TCP would drop below the floor.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/motion/jog/linear \
  -H 'X-FWS-Control-Token: <token>' \
  -H 'Content-Type: application/json' \
  -d '{"axis": 3, "direction": 0, "step": 10, "vel": 10, "frame": "base"}'
```

| Field | Range | Default | Meaning |
|---|---|---|---|
| `axis` | 1–6 | — | 1–3 = X/Y/Z, 4–6 = RX/RY/RZ |
| `direction` | 0 or 1 | — | `1` = positive, `0` = negative |
| `step` | > 0, ≤ `jog_max_mm` (axes 1–3) or `rotation_max_deg` (4–6) | 10.0 | mm for axes 1–3, degrees for 4–6 |
| `vel` | > 0, ≤ `jog_max_vel_pct` | 10.0 | percent |
| `frame` | `base` or `tool` | `base` | reference frame |

Guards, in order, before anything is transmitted:

1. **IK solve.** The delta is solved backwards. An unreachable pose or a
   singularity is refused with 409 here, not discovered by the arm.
2. **Soft-limit band.** Every solved joint must land inside
   `[min + margin, max - margin]`, else 409.
3. **Z-floor.** If `limits.z_floor_mm` is configured, the target joints are
   solved **forward** and the predicted TCP height checked against the floor
   (409 if below). The forward solve is deliberate: in the tool frame a Z step
   is not a base-frame Z step, so adding the delta to the current Z would give
   a floor that only works in one frame. If the height cannot be computed, the
   jog is refused rather than sent unchecked.

The response echoes the axis, frame, step, and the `predicted_joints` the move
was solved to.

## Absolute moves — `POST /api/v1/motion/move`

This sends the TCP to an absolute pose with one non-blocking `MoveL`, fully
pre-flighted. It is the typed replacement for hand-assembling a raw `MoveL`
argument list.

!!! danger "Off by default, and here is why"
    `features.enable_movel` is `false` out of the box, so this route returns
    403 until you turn it on. On software **v3.8.5.1**, `MoveL`'s argument
    layout once produced an **unintended ~300 mm motion and a controller
    fault**. That was a transcription error, and the layout has since been read
    carefully from the SDK source — but *carefully read* is not *verified*, and
    the difference is 300 mm of arm travel. The route exists, is fully guarded,
    and refuses until someone enables the flag deliberately, having read what
    it warns. Turn it on only when you can watch the arm and reach the E-stop.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/motion/move \
  -H 'X-FWS-Control-Token: <token>' \
  -H 'Content-Type: application/json' \
  -d '{"pose": [300, 0, 400, 180, 0, 0], "tool": 0, "user": 0, "vel": 20, "confirm": true}'
```

| Field | Range | Default | Meaning |
|---|---|---|---|
| `pose` | exactly 6 numbers | — | x, y, z, rx, ry, rz — mm and degrees |
| `tool` | 0–15 | 0 | tool frame |
| `user` | 0–14 | 0 | user frame |
| `vel` | > 0, ≤ `jog_max_vel_pct` | 20.0 | percent |
| `confirm` | — | `false` | must be `true` |

Every refusal happens **before** anything reaches the wire:

1. `features.enable_movel` must be on (403 otherwise).
2. `pose` must be exactly six numbers (422).
3. You must hold the `motion` lease — always, not just when contended (428/423).
4. `confirm` must be `true` (400).
5. `vel` must be within the configured cap (422).
6. **IK solve** — an unreachable pose or singularity is refused with 409.
7. **Soft-limit band** — every solved joint inside its band with the standoff, else 409.
8. **Z-floor** — the target's Z must be at or above `limits.z_floor_mm`, else 409.

The command is audited **before** transmission, so a review still has the line
saying what was sent even if the process then dies. The `MoveL` is
non-blocking — a blocking call would hold the RPC lock for the whole move and
make a stop impossible — so the route returns immediately with the solved
`target_joints`. Poll `GET /api/v1/motion/queue` or watch `/ws/events` for
completion.

!!! note "This is not a path runner"
    `/motion/move` sends a single move. Executing a multi-point trajectory from
    outside the controller is the wrong architecture — every motion bug in this
    project came from trying it. Trajectories belong in Lua on the controller,
    which is also how ABB's Robot Web Services works. For a sequence, generate a
    program (see [Named poses](#named-poses)) and run it.

## The gripper

Open and close, with every argument bounded. Two routes: `activate` powers or
resets the gripper (most need this first), and `command` moves it to a
position.

```bash
# Power up / reset
curl -X POST http://127.0.0.1:8000/api/v1/gripper/activate \
  -H 'X-FWS-Control-Token: <token>' \
  -H 'Content-Type: application/json' \
  -d '{"index": 1, "action": 1, "confirm": true}'

# Close fully at half speed, half force
curl -X POST http://127.0.0.1:8000/api/v1/gripper/command \
  -H 'X-FWS-Control-Token: <token>' \
  -H 'Content-Type: application/json' \
  -d '{"index": 1, "position": 100, "speed": 50, "force": 50, "confirm": true}'
```

`command` fields:

| Field | Range | Default | Meaning |
|---|---|---|---|
| `index` | 1–8 | 1 | gripper number |
| `position` | 0–100 | — | percent closed: 0 open, 100 closed |
| `speed` | 1–100 | 50 | percent |
| `force` | 1–100 | 50 | percent of maximum grip force |
| `max_time_ms` | 0–60000 | 30000 | timeout |
| `confirm` | — | `false` | must be `true` |

Both routes require the `motion` lease (a gripper is motion, and a holder that
disappears mid-grip must be stoppable) and `confirm: true` — a gripper closes
on whatever is in it, a part, a fixture, or a hand. The bounds keep a typo from
becoming a crush; the controller's own gripper configuration may be narrower.

**Probe-gated, not flag-gated.** A gripper that is not fitted answers the
controller's gripper getters with zeros rather than an error, so a command to a
missing gripper would be accepted and silently do nothing. Before commanding,
the route checks the capability probe and refuses with 409 if no gripper is
reported. Unlike `MoveL`, nothing here has a bad history — the only risk is a
gripper that is absent — so a probe is the right gate. If the probe is wrong for
your cell, pass `?force_probe=false`.

!!! warning "Documented, not measured"
    `ActGripper` and `MoveGripper` have never been exercised on this hardware —
    the command registry marks them `documented`, not `measured`. The routes
    bound every argument and send the documented wire order, and every response
    carries `"verified": false`. Watch the first command on real hardware. The
    call is non-blocking, so `POST /api/v1/motion/stop` still works.

## Named poses

FWS stores taught poses **gateway-side**, as ordinary data — backed up,
diffable, readable by any client, usable from CI.

!!! note "Why gateway-side and not the controller's point table"
    This firmware cannot write a single named point into a controller point
    table. More importantly, the generated program uses **literal joint
    targets, never point-table names** — and that is exactly what lets
    `POST /programs/{name}/validate` solve every target backwards through the
    controller's kinematics before anything moves. A program referring to named
    points on the controller cannot be checked that way. For whole-file
    transfers see `/api/v1/points/tables`.

Capture where the arm is now:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/poses/pick/capture \
  -H 'Content-Type: application/json' \
  -d '{"note": "pick position", "overwrite": false}'
```

Joints and TCP come from **one** telemetry frame, so the two representations
cannot disagree. Telemetry is used rather than the RPC position getters because
those answer `error 14` while faulted — which is precisely when someone is most
likely to be hand-guiding the arm and marking positions.

!!! warning "A stale pose is worse than no pose"
    The telemetry snapshot keeps its last values after the stream drops.
    Capturing without checking age would record where the arm **was** — and if
    it was moved from the pendant since, a later move to this "taught" point
    goes somewhere nobody chose. Frames arrive at 10 Hz, so capture refuses
    with 503 if the last frame is older than 1 second or of unknown age. The
    `tool` and `wobj` frames are read from the controller when omitted.

The rest of the surface:

| Method & path | Purpose |
|---|---|
| `GET /api/v1/poses` | list stored poses |
| `GET /api/v1/poses/{name}` | read one |
| `PUT /api/v1/poses/{name}` | write one explicitly (from CAD, a calculation, another cell) |
| `POST /api/v1/poses/{name}/rename` | rename |
| `DELETE /api/v1/poses/{name}` | forget one |
| `POST /api/v1/poses/program` | generate a Lua program through named poses |

Writing a pose is a `config`-class change — it is the data a later motion
command will use — so writes need the `config` lease if another client holds
it.

Turn poses into a runnable program:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/poses/program \
  -H 'Content-Type: application/json' \
  -d '{"poses": ["home", "pick", "place"], "speed": 20, "dwell_ms": 500}'
```

This returns the Lua **source** and does not upload or run it: generating a
program is safe, running one moves the arm, so they stay separate calls with
separate gates. The generated `MoveJ` lines carry the full 29-argument layout
this firmware requires (probed, not read off a manual — the controller does not
ignore a wrong argument count safely). Upload the source with
`PUT /api/v1/programs/{name}`, check it with `.../validate`, then run it.

## The program loop

A Lua program runs on the controller and commands motion directly. The
gateway's jog bounds, step limits and IK pre-flight **do not apply** once it is
running — the guard is the whole-path validation done *before* it starts.

!!! warning "A running program is unbounded motion"
    Running a program commands motion FWS does not bound. Clear the cell first.
    `run` requires the `motion` lease, `confirm: true`, and a non-faulted
    controller.

The five steps:

```bash
# 1. Upload. Name must end in .lua; 512 KiB max.
curl -X PUT http://127.0.0.1:8000/api/v1/programs/cycle.lua \
  -H 'X-FWS-Control-Token: <token>' \
  -H 'Content-Type: application/json' \
  -d '{"content": "MoveJ(...)", "overwrite": true}'

# 2. Validate: solve every literal motion target backwards. Sends no motion.
curl -X POST http://127.0.0.1:8000/api/v1/programs/cycle.lua/validate

# 3. Load it into the controller.
curl -X POST http://127.0.0.1:8000/api/v1/programs/cycle.lua/load \
  -H 'X-FWS-Control-Token: <token>'

# 4. Run the loaded program.
curl -X POST http://127.0.0.1:8000/api/v1/execution/run \
  -H 'X-FWS-Control-Token: <token>' \
  -H 'Content-Type: application/json' \
  -d '{"confirm": true}'

# 5. Watch.
curl http://127.0.0.1:8000/api/v1/execution
```

**Upload** (`PUT /programs/{name}`) rejects path traversal and names that do
not end in `.lua`, caps size at 512 KiB (413), and refuses to clobber an
existing name unless `overwrite: true` (409). If the controller's Lua compiler
rejects the source, you get a 422 with the compiler's verdict — note that the
bytes were still transferred and any prior program under that name is
overwritten.

**Validate** (`POST /programs/{name}/validate`) solves each *literal* motion
target through the controller's kinematics against its soft limits and floor,
without sending motion. `run` performs the same check automatically unless you
pass `skip_validation: true` with a `validation_note` (recorded in the audit
log); a failed check blocks the run with 409 and the list of failures.

!!! note "Read `unchecked` before trusting `safe_to_run`"
    Validation can only check literal targets. Point names, computed poses and
    arcs cannot be solved statically — they land in `unchecked`, not `failed`.
    A program that is `safe_to_run` with a non-empty `unchecked` list has parts
    the gateway did not verify.

**Watch** with `GET /api/v1/execution` — it reports `state` (`stopped`,
`running`, `paused`), and the loaded program and current line where the
controller supports it — or subscribe to `/ws/events` for pushed state changes.
`list` (`GET /programs`) shows an FWS-side index of what this gateway uploaded,
not the controller's directory: the controller's own listing command
(`GetLuaList`) is quarantined because it is reported to wedge the RPC channel
until the controller is restarted.

Control it with `POST /execution/pause`, `/execution/resume`, and
`/execution/stop`. `select` (`POST /programs/{name}/select`) loads and, with
`start: true`, runs in one call under the same guards as `run`.

## Stopping

```bash
curl -X POST http://127.0.0.1:8000/api/v1/motion/stop
```

`POST /api/v1/motion/stop` is never authenticated, never gated, and always
returns 200 — a client whose key is wrong or fumbled must still be able to stop
the arm. It issues `ImmStopJOG` (jogs), `StopMotion` (program-space moves) and
aborts FWS's own path runners, then confirms standstill from the telemetry
stream and reports it in `confirmed`.

`POST /api/v1/execution/stop` stops a running program (`ProgramStop` +
`StopMotion`). Like every stop in FWS, it is neither lockable nor confirmable.

!!! danger "What stop cannot reach"
    Its span of control is the motion FWS itself started. It does **not** stop
    motion started from the teach pendant, a Lua program already running on the
    controller before FWS loaded it, or another client on the robot network.
    That limit is a property of the system, not a bug. For anything you cannot
    stop this way, use the physical E-stop.