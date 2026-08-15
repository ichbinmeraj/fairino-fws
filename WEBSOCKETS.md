# The WebSocket streams

FWS has two WebSocket endpoints. Neither appears in `/openapi.json` — FastAPI
does not describe WebSockets — so they are documented here, and their shape is
pinned by tests (`tests/test_ws_state.py`, `tests/test_eventbus.py`) so this
file cannot quietly fall out of date.

Both are under the same auth as the REST API: when `auth.api_keys_file` is
configured, pass the key as a query parameter (`?key=...`), because a browser
cannot set a header on a WebSocket. Stop and health stay open; live state does
not.

---

## `GET /ws/state` — telemetry, ~10 Hz

A sample of what the robot *is*, pushed at the rate the controller pushes its
own 8083 frame. Every message is a JSON object with the same shape:

| field | type | meaning |
|---|---|---|
| `connected` | bool | the gateway's view of the 8083 link. `false` while the socket is open means FWS is up and the robot link is not — a distinction a plain "disconnected" would lose |
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

Use `age_s` rather than diffing values to tell a fresh frame from a stale
repeat: a parked arm sends identical frames, so equal values do not mean the
stream stopped.

The message is built by *spreading* the parsed frame, so it is a superset of
what `GET /api/v1/state` returns — never a subset. A field that exists over
REST but not here would be a trap, because the socket is what you use when you
care about being current.

One naming difference to know: the REST `/state` route reports this link as
`stream_connected`; on the socket it is `connected` (the socket spreads the
raw frame, which uses the shorter name). Same fact, two labels — a test pins
the equivalence so it cannot drift into two different meanings.

### Reconnect

The stream can drop (network, an FWS restart, the controller reclaiming its
single-client 8083 slot). A client should reconnect with capped exponential
backoff — 500 ms doubling to 10 s — rather than hammering, and treat >500 ms
of silence as stale even before the socket itself notices. The bundled client
(`fws.client`) and the console both do this.

---

## `GET /ws/events` — edge transitions, pushed

What *changed*, not what is. One message per event; a client wanting to know a
program finished should not have to diff `/ws/state` samples to find out.

Every message has `kind`, `seq`, `ts`, and fields specific to the kind:

| kind | when | extra fields |
|---|---|---|
| `audit.*` | any audited command (`audit.motion.jog`, `audit.watchdog.stop`, …) | the audit record's fields |
| `fault.latched` | a fault appears | `main`, `sub` |
| `fault.cleared` | the fault clears | `main`, `sub` |
| `program.state` | program state changes | `was`, `now` |
| `telemetry.down` / `.up` | the 8083 link drops / returns | — |
| `recording.dumped` | the flight recorder wrote a fault dump | `file` |
| `keepalive` | ~1 s of quiet | — |

Filter with `?topics=` — a comma-separated set of prefixes (`fault`, `motion`,
`watchdog`) or exact kinds. No filter means everything.

Two things to rely on:

- **`keepalive`** is sent when nothing is happening, so a quiet stream is
  distinguishable from a dead one. It is not an event — ignore it.
- **`dropped`** may appear on any event. It means this subscriber fell behind
  and the gateway discarded that many older events to avoid blocking a robot
  command. A gap is never silent: you always learn you missed something.

The same events are available as Server-Sent Events at
`GET /api/v1/events/stream` — same payloads, `text/event-stream` framing — for
anything that would rather not hold a WebSocket open (curl, a shell script, an
`EventSource`).
