# Events, recordings & metrics

Reading live state is only half of operating a cell. The other half is knowing
what *changed* — that a program finished, a fault latched, the watchdog stopped
the arm — and being able to reconstruct what the arm was doing when something
went wrong. FWS pushes those changes instead of making you diff snapshots, and
keeps enough history to answer "what happened just before?"

Five surfaces cover this, each aimed at a different consumer:

| Surface | Route | What it is |
|---|---|---|
| Telemetry stream | `GET /ws/state` | ~10 Hz sample of what the robot *is* |
| Event stream | `GET /ws/events` | edge transitions — what *changed* |
| Event stream (SSE) | `GET /api/v1/events/stream` | the same events over `text/event-stream` |
| Audit trail | `GET /api/v1/events` | who commanded what, in memory and optionally on disk |
| Flight recorder | `GET /api/v1/recordings` | rolling telemetry buffer, dumped on fault |
| Metrics | `GET /api/v1/metrics` | Prometheus exposition |

!!! danger "None of this is a safety function"
    FWS is not a safety device. No stream, event, dump, or metric is an
    emergency stop, and nothing here is safety-rated. The only emergency stop is
    the physical button wired per ISO 13850. The watchdog "stop" you will see on
    the event stream is a software **functional** stop — a convenience that
    depends on a network link, a host, and a Python process, any of which can
    fail. Keep the physical E-stop within reach. See
    [`SAFETY.md`](safety.md).

---

## Authentication

There is one rule and it applies to every surface on this page. When
`auth.api_keys_file` is configured (required for any non-loopback bind), a key
is needed; on a default loopback deployment it is not. Two open exceptions
exist because an unreachable stop is worse than a nuisance one:
`POST /api/v1/motion/stop` and `GET /api/v1/system/health` never require a key.

How the key is passed differs by transport:

- **HTTP** (SSE, audit, metrics, recordings) — the `X-API-Key` header.
- **WebSocket** (`/ws/state`, `/ws/events`) — a `?key=...` query parameter,
  because a browser cannot set a header on a WebSocket.

!!! warning "Metrics and live state are not open"
    `/api/v1/metrics` carries live joint positions, so it is deliberately
    **not** on the always-open list — it needs a key like any other live-state
    route. Likewise `/ws/state` and `/ws/events` require the key when one is
    configured; only stop and health stay open.

---

## `GET /ws/state` — the telemetry stream

A sample of what the robot *is*, pushed at the rate the controller pushes its
own 8083 frame (~10 Hz). Every message is a JSON object with the same shape:

| field | type | meaning |
|---|---|---|
| `connected` | bool | the gateway's view of the 8083 link. `false` while the socket is open means FWS is up and the robot link is not |
| `joints` | float[6] | joint angles, degrees |
| `tcp` | float[6] | TCP pose: x, y, z (mm), rx, ry, rz (deg) |
| `force` | float[6] | wrist force/torque: fx, fy, fz (N), tx, ty, tz (Nm) |
| `joint_torque` | float[6] | per-joint torque, Nm |
| `program_state` | int | controller program state |
| `error_main`, `error_sub` | int | latched fault codes, 0 when clear |
| `limits` | [lo, hi][6] | joint soft limits with the gateway's margin |
| `frames`, `bad_checksum` | int | frames parsed / dropped as corrupt |
| `ts` | float \| null | the frame's own timestamp; `null` before the first frame |
| `age_s` | float \| null | how old that frame is at send time |

The message is built by *spreading* the parsed frame, so it is a **superset**
of what `GET /api/v1/state` returns — never a subset. A field present over REST
but missing here would be a trap, because the socket is what you use when you
care about being current.

!!! note "Use `age_s`, not value diffs, to detect staleness"
    A parked arm sends identical frames, so equal consecutive values do not mean
    the stream stopped. `age_s` is the frame's age at send time; treat a growing
    `age_s` (or more than ~500 ms of silence) as stale even before the socket
    itself notices.

One naming difference: REST `/state` reports this link as `stream_connected`;
on the socket it is `connected` (the socket spreads the raw frame, which uses
the shorter name). Same fact, two labels — a test pins the equivalence so it
cannot drift.

```bash
# no auth (loopback dev)
websocat ws://127.0.0.1:8000/ws/state

# with a key configured
websocat 'ws://host:8000/ws/state?key=YOUR_KEY'
```

### Reconnect

The stream can drop — network, an FWS restart, or the controller reclaiming its
single-client 8083 slot. The contract for a client is capped exponential
backoff: start at **500 ms and double to a 10 s ceiling** rather than
hammering, and treat more than 500 ms of silence as stale. The bundled client
(`fws.client`) and the console both implement this; if you write your own,
match it.

---

## `GET /ws/events` — edge transitions

What *changed*, not what is. One message per event, so a client that wants to
know a program finished does not have to diff `/ws/state` samples to find out.
Two things go onto this bus: every audit record (so "who commanded what"
arrives as it happens), and the edge transitions the gateway is uniquely placed
to notice.

Every message carries `kind`, `seq`, `ts`, and fields specific to the kind:

| kind | when | extra fields |
|---|---|---|
| `audit.*` | any audited command (`audit.motion.jog`, `audit.watchdog.stop`, …) | the audit record's fields |
| `fault.latched` | a fault appears | `main`, `sub` |
| `fault.cleared` | the fault clears | `main`, `sub` |
| `program.state` | program state changes | `was`, `now` |
| `telemetry.down` / `telemetry.up` | the 8083 link drops / returns | — |
| `recording.dumped` | the flight recorder wrote a fault dump | `file`, `main`, `sub` |
| `keepalive` | ~1 s of quiet | — |

Filter with `?topics=` — a comma-separated set of prefixes (`fault`, `motion`,
`watchdog`) or exact kinds (`fault.latched`). No filter means everything.

```bash
websocat 'ws://127.0.0.1:8000/ws/events?topics=fault,watchdog'
```

Two guarantees to rely on:

!!! note "A quiet stream is not a dead stream"
    `keepalive` is sent after ~1 s of silence, so a quiet stream is
    distinguishable from a broken one. It is not an event — ignore it.

!!! warning "A gap is never silent — watch for `dropped`"
    Backpressure is the whole design of the bus. Each subscriber has a bounded
    queue of 256 events; a subscriber that falls behind has its **oldest**
    events discarded so that a slow consumer can never block a robot command.
    When that happens, the count of discarded events rides along as a `dropped`
    field on the next event that subscriber receives, and then resets. If you
    see `dropped`, you missed exactly that many events — you will never lose
    events silently, but you can lose them.

### Why edges are detected here

Before this existed, every integrator diffed the 10 Hz stream to infer edges,
and each got the corners slightly different — and the watchdog stop existed only
as a line on stdout, so nothing could react to the single most important thing
the gateway does. Detecting edges once, in one place, means one definition of
"the fault cleared."

!!! note "The first reading is a baseline, not a transition"
    Fault, program-state, and link edges are computed against the previous
    reading. The first poll after startup only establishes the baseline, so if
    FWS starts while the arm is *already* faulted, that pre-existing fault emits
    no `fault.latched` and triggers no dump — there is no prior "clear" state to
    transition from.

---

## `GET /api/v1/events/stream` — the same events as SSE

The identical event payloads, framed as Server-Sent Events, for anything that
would rather not hold a WebSocket open — `curl`, a shell script, a browser
`EventSource`. Same `?topics=` filter, same `keepalive` and `dropped` semantics.

```bash
curl -N http://127.0.0.1:8000/api/v1/events/stream
# filtered
curl -N 'http://127.0.0.1:8000/api/v1/events/stream?topics=fault'
```

Each event arrives as an SSE frame whose `event:` line is the kind and whose
`data:` line is the JSON payload; keepalives arrive as SSE comment lines
(`: keepalive`).

!!! warning "SSE authenticates by header only"
    Under a configured key, the SSE stream needs the `X-API-Key` header
    (`curl -H 'X-API-Key: ...'`). A browser `EventSource` cannot set headers and
    cannot pass a key here — in an authenticated deployment, use `/ws/events`
    with `?key=` from the browser instead.

---

## `GET /api/v1/events` — the audit trail

The audit trail records commands and state changes — not reads. It answers "who
commanded what," which is a different question from where the arm actually was
(that is the flight recorder, below).

```bash
curl http://127.0.0.1:8000/api/v1/events
curl 'http://127.0.0.1:8000/api/v1/events?limit=50&action=motion'
```

Returns `{"count": <total>, "events": [...]}`, newest first. `action` filters by
prefix (`motion`, `watchdog`, `recording`, …). Each event carries a monotonic
`seq`, a `ts`, the `action`, an `actor`, and the command's own detail fields.

!!! note "What `actor` is, and is not"
    Most records — the typed routes (`motion.*`, `robot.enable`, `poses.*`,
    program and recording actions) — are recorded with `actor: "anonymous"`,
    even when a key was used. Only two carry a meaningful actor: a
    `watchdog.stop` names the lapsed lease's `client_id`, and a raw
    `POST /invoke/{name}` names whoever the gate resolved. Do not read `actor`
    as "which API key ran this" for the typed routes.

!!! warning "Secrets are never written to the trail"
    A `token` field is stripped from every record before it is stored, and API
    keys never appear by value. Do not rely on the trail to tell you a raw
    credential; by design it cannot.

### In memory vs. durable

The trail is always held in memory, bounded to the most recent **2000** events;
past that, the oldest roll off. It survives a restart **only** if you configure
a file sink:

```toml
[audit]
file = "audit.jsonl"   # relative names resolve under server.data_dir
```

or `FWS_AUDIT__FILE=audit.jsonl`. The file is append-only JSON Lines.

!!! note "Durability is stated, not implied — check health"
    A failing sink (a full disk, a bad path) must never break a robot command,
    so a write failure is counted rather than raised. `GET /api/v1/system/health`
    surfaces the truth in its audit block:

    - `durable` — `true` only when a file is configured **and** no writes have
      failed. `false` means a restart loses the trail.
    - `file`, `in_memory`, `capacity` — where it writes and how full memory is.
    - `sink_errors`, `sink_last_error` — the count and text of write failures.

    A `durable: false` on a deployment you expected to persist is the signal that
    the on-disk trail has quietly stopped being written.

Every audited command is also pushed live as an `audit.*` event on the bus (see
`/ws/events` above), so you can watch commands happen rather than polling for
them.

---

## `GET /api/v1/recordings` — the flight recorder

When something goes wrong on a cell running undocumented firmware, the question
is always "what was the arm doing just before?" The audit trail says what was
*commanded*; it does not say where the arm actually was, how fast, or what the
wrist felt. The flight recorder keeps that evidence.

It has two modes.

### The automatic fault dump

The recorder keeps a rolling in-memory buffer of the last **60 s** of telemetry
at 10 Hz (about 600 frames — a few hundred kilobytes, nothing on a Pi). The
moment a fault latches, the whole window is written to disk beside the audit
trail, so the seconds *before* the fault — the ones worth having — survive the
event that destroyed the context. No route triggers it; nobody is at a keyboard
when a fault lands.

The dump is a JSONL file named `fault-YYYYMMDD-HHMMSS.jsonl` (UTC), whose first
line is a `_meta` record (`why`, `frames`, `window_s`) and whose remaining lines
are one telemetry frame each. A `recording.dumped` event (carrying the `file`
name and the fault codes) is published on the bus as it happens.

!!! note "Fault detection is polled at 2 Hz"
    Faults are polled on a background thread every 500 ms, while telemetry is
    sampled into the ring at 10 Hz. The dump therefore captures the full window
    up to the moment of *detection*, which can trail the fault latching in the
    controller by up to ~0.5 s. The pre-fault approach you want is in the buffer
    regardless; just don't read the last frame's timestamp as the exact instant
    the fault occurred.

### Explicit recordings

For "capture the next ten minutes while I reproduce this," start and stop a
recording by hand. Frames stream to the file as they arrive.

```bash
# start (201); name is letters, digits, dot, dash, underscore, max 64 chars
curl -X POST http://127.0.0.1:8000/api/v1/recordings/start \
  -H 'Content-Type: application/json' -d '{"name":"approach-test"}'

# stop — returns {"recording":"approach-test.jsonl","frames":<n>}
curl -X POST http://127.0.0.1:8000/api/v1/recordings/finish
```

The name is sanitised (anything outside letters, digits, `. _ -` is dropped,
then truncated to 64) and gets a `.jsonl` extension. Only one recording runs at
a time — starting a second returns **409**; an empty name returns **422**; a
disk that cannot be opened returns **507**.

### Reading recordings back

```bash
# list — includes recorder health (buffered_frames, fault_dumps, errors, dir)
curl http://127.0.0.1:8000/api/v1/recordings

# download as JSON Lines (default)
curl http://127.0.0.1:8000/api/v1/recordings/approach-test.jsonl

# download as CSV (the always-present columns, flattened)
curl 'http://127.0.0.1:8000/api/v1/recordings/approach-test.jsonl?format=csv'

# delete
curl -X DELETE http://127.0.0.1:8000/api/v1/recordings/approach-test.jsonl
```

JSONL is one frame per line — greppable, streamable, and loadable by pandas in
one call. CSV is offered for the columns that are always present, because a lot
of shop-floor analysis is still a spreadsheet. Its six-element vectors are
flattened to numbered columns, giving this header order:

```
ts, j1..j6, tcp1..tcp6, ft1..ft6, torque1..torque6, error_main, error_sub, program_state
```

Everything else in a frame stays in the JSONL only.

!!! note "Recording is diagnostics; it never takes the gateway down"
    The recorder samples the telemetry *snapshot* rather than tapping the stream
    reader, so it cannot slow the reader. If a write fails mid-recording (a full
    disk), the recording stops, the error is counted, and the reason appears in
    the recorder's health block (`errors`, `last`) — the gateway keeps serving.

!!! warning "Names come from URLs — path escape is refused"
    Recording names are resolved against the recordings directory and rejected
    unless they land directly inside it and end in `.jsonl`. A name that tries to
    escape the directory returns **404**, not a file outside it.

---

## `GET /api/v1/metrics` — Prometheus

A cell gateway on a Pi is exactly the thing a plant scrapes. Every number here
already existed inside FWS — in health, in the watchdog, in the telemetry reader
— what was missing was a format anything could read. The exposition is
hand-rendered standard-library text (no `prometheus_client` dependency) in the
Prometheus format, `version=0.0.4`.

```bash
curl http://127.0.0.1:8000/api/v1/metrics
# under a configured key
curl -H 'X-API-Key: YOUR_KEY' http://127.0.0.1:8000/api/v1/metrics
```

Naming follows the Prometheus convention — an `fws_` prefix, base units, and
`_total` on counters — so a dashboard someone else wrote works against it
without translation.

| Metric | Type | Meaning |
|---|---|---|
| `fws_up` | gauge | 1 when the gateway is serving |
| `fws_uptime_seconds` | gauge | seconds since the process started |
| `fws_telemetry_connected` | gauge | 1 when the 8083 stream is up |
| `fws_telemetry_frames_total` | counter | frames parsed from the stream |
| `fws_telemetry_bad_checksum_total` | counter | frames dropped as corrupt |
| `fws_telemetry_age_seconds` | gauge | age of the newest frame (only once a frame exists) |
| `fws_robot_faulted` | gauge | 1 when a fault is latched |
| `fws_robot_error_code` | gauge | the latched main fault code, 0 if none |
| `fws_joint_position_degrees` | gauge | live joint positions, labelled `joint="j1".."j6"` |
| `fws_joint_torque_nm` | gauge | live joint torques, labelled `joint="j1".."j6"` |
| `fws_control_watchdog_healthy` | gauge | 1 when the lease reaper runs |
| `fws_control_watchdog_errors_total` | counter | reap and lapse-callback failures |
| `fws_control_lock_held` | gauge | 1 when a domain is leased, labelled `domain="motion\|config\|program"` |
| `fws_audit_events_total` | gauge | audit events currently in memory |
| `fws_audit_durable` | gauge | 1 when the trail survives a restart |
| `fws_audit_sink_errors_total` | counter | failed writes to the audit file |
| `fws_events_published_total` | counter | events pushed to subscribers |
| `fws_event_subscribers` | gauge | current event-stream subscribers |
| `fws_recorder_dumps_total` | counter | flight-recorder dumps on fault |
| `fws_recorder_recording` | gauge | 1 while an explicit recording runs |
| `fws_capabilities` | gauge | probed features by `state="available\|absent\|unknown"` (only when a capability probe has run) |

Two of these are worth alerting on:

!!! warning "Watchdog health is page-worthy"
    `fws_control_watchdog_healthy` going to 0 means a client that disconnects
    mid-move may **not** trigger the functional stop the watchdog would normally
    apply — the disconnect protection is degraded. And read
    `fws_telemetry_bad_checksum_total` against `fws_telemetry_frames_total`: a
    rising bad-checksum count with a flat frame count is a wire problem. Either
    number alone means little; the two together are what make each meaningful.

---

!!! note "How this stays correct"
    The WebSocket streams are invisible to OpenAPI (FastAPI does not describe
    WebSockets), so their shape is pinned by tests (`tests/test_ws_state.py`,
    `tests/test_eventbus.py`) rather than by this page. If the payloads here ever
    disagree with what the socket sends, trust the socket and file it as a docs
    bug.
