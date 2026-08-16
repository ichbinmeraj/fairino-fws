# Control lease & safety model

This page describes how FWS arbitrates who may command the robot, how it stops
gateway-initiated motion, and what those mechanisms are — and are not.

## Safety stance

!!! danger "FWS is not a safety device"
    No endpoint in FWS is an emergency stop. The only emergency stop is the
    physical button wired per ISO 13850. FWS carries no safety-rated function
    and is **not** certified to ISO 10218, ISO/TS 15066, IEC 61508 or
    ISO 13849.

The stop endpoints are software-implemented **functional** stops. They depend
on a network link, a host, a Python process and controller firmware — any of
which can fail at the moment you need them. They are a convenience, not a
protective measure. Keep the physical E-stop within reach.

The control lease described below prevents contradictory commands from two
clients. It is not a substitute for the physical E-stop, and losing it never
makes the machine safe on its own.

!!! note "Firmware under test"
    Behaviour here is described against Fairino controller firmware
    `v3.8.5.1`. The gateway-side logic (lease arbitration, the watchdog, the
    stop dispatch and its standstill check) is exercised in the test suite. The
    controller's real response to a stop command — how quickly the arm comes to
    rest — is a firmware property FWS does not measure and does not promise.

## The control lease

FWS is single-writer per domain. Reads, kinematics and the stop path are never
lockable; commands that move or reconfigure the robot are.

### Three domains

A lease is held over one or more named domains:

| Domain | Covers |
|---|---|
| `motion` | enable, jog, Cartesian jog, moves — anything that drives the arm |
| `config` | writes to configuration-class controller files (e.g. point tables) |
| `program` | writes to program-class controller files |

An **unheld** domain is free: a single client can command it with no lease at
all. The lease exists to stop a *second* client from issuing contradictory
commands while a first is driving. Only the `motion` domain has a
disconnect-stop consequence (see the watchdog, below).

A single lease can cover several domains and is identified by one token.
Acquiring `["motion", "config"]` returns one token that holds both; releasing
it releases both.

### TTL and heartbeat

A lease has a time-to-live. It expires at `acquired_at + ttl_s` unless renewed.

| Bound | Value |
|---|---|
| Default TTL | 30 s |
| Minimum TTL | 5 s |
| Maximum TTL | 600 s |

A TTL outside `[5, 600]` on acquire is rejected (`422`); on heartbeat it is
clamped into range. To hold a lease, renew it before it expires:

```bash
curl -sX POST 'http://127.0.0.1:8000/api/v1/control/heartbeat?ttl_s=30' \
  -H 'X-FWS-Control-Token: <token>'
```

Heartbeat extends the lease to `now + ttl_s` and returns the lease. If the
token is unknown — including because the lease already lapsed — heartbeat
returns `404`. A `404` here means the watchdog has probably already stopped
your motion; treat the next command as unsafe until you re-acquire.

!!! warning "Keep the TTL short and heartbeat faster than it"
    The TTL is your disconnect deadline. A 30 s TTL means a client that dies
    mid-move can keep the arm authorised for up to 30 s before the watchdog
    reaps the lease and stops it. Choose the shortest TTL your network latency
    tolerates and heartbeat well inside it.

### Acquire

```bash
curl -sX POST http://127.0.0.1:8000/api/v1/control \
  -H 'Content-Type: application/json' \
  -d '{"client_id": "cell-01", "domains": ["motion"], "ttl_s": 30}'
```

On success the response is `201` and includes the `token` — the one time it is
returned unredacted. Save it; send it as the `X-FWS-Control-Token` header on
every write:

```json
{
  "client_id": "cell-01",
  "domains": ["motion"],
  "acquired_at": 1755400000.0,
  "expires_at": 1755400030.0,
  "renewals": 0,
  "expires_in_s": 30.0,
  "token": "…"
}
```

Failure modes:

- **`423 Locked`** — a requested domain is already held by another client. The
  body carries the current holder's `client_id` and expiry.
- **`422`** — an unknown domain name, or an empty domain list.

Then command under the token, release when done:

```bash
curl -sX POST http://127.0.0.1:8000/api/v1/motion/jog \
  -H 'X-FWS-Control-Token: <token>' \
  -H 'Content-Type: application/json' \
  -d '{"joint": 1, "direction": 1, "step": 5, "vel": 10}'

curl -sX DELETE http://127.0.0.1:8000/api/v1/control \
  -H 'X-FWS-Control-Token: <token>'
```

The Python client (`fws.control("motion")`) does the acquire, heartbeat and
release for you; the raw handshake above is what any other language reproduces
from the OpenAPI spec.

`GET /api/v1/control` reports the domains and current holders (tokens
redacted). `DELETE /api/v1/control/{domain}` is an administrative override that
force-releases a stuck lease for one domain.

### 423 vs 428

Both mean "you may not write this domain," but they ask for different fixes:

| Status | Meaning | What to do |
|---|---|---|
| `428 Precondition Required` | The domain is held by another client and you sent **no** control token. | Acquire the lease (or wait for the holder), then send `X-FWS-Control-Token`. |
| `423 Locked` | The domain is held by another client and the token you sent does **not** match. | You do not hold this domain. Do not retry with the same token; the current holder must release, or an administrator must break the lock. |

`428` is the "you forgot to hold the lease" answer; `423` is the "someone else
holds it" answer. An `acquire` that collides with an existing holder also
returns `423`. A domain that is unheld is written without any token and returns
neither.

### Explicit release does not fire the watchdog

`DELETE /api/v1/control` (release) and `DELETE /api/v1/control/{domain}` (break)
remove the lease **without** issuing a stop. A client that says goodbye has not
disconnected; stopping the arm on a clean handoff would be a surprise, and it
would punish the normal path. The watchdog exists only to catch a holder that
*vanished* — see below.

## The disconnect watchdog

Client disconnect is modelled as lease expiry. There is no separate "are you
still there" ping: the holder simply stops heartbeating, the lease lapses, and
**lapsing is what issues the stop.**

A reaper thread runs about once a second. For every lease past its
`expires_at`, it removes the lease and, if that lease held the `motion` domain,
issues a stop through the same path as an explicit stop and records a
`watchdog.stop` entry in the audit trail naming the client that lapsed.

!!! note "Only `motion` triggers a disconnect stop"
    A lapsed lease that held only `config` or `program` frees those domains but
    issues no stop — there is no in-flight arm motion to stop. Only a lapsed
    `motion` lease stops the machine.

The stop fires outside the lock's internal mutex, so a stop that blocks on the
RPC channel cannot stall another client trying to take over. If one lapse
callback fails, the watchdog still runs for every other lapsed lease.

### Watchdog health

`GET /api/v1/system/health` (open, no key required) reports `control_watchdog`:
whether the reaper is running, how long since its last pass, and cumulative
reap and lapse-callback error counts. Health raises a warning when it is
unhealthy.

!!! warning "A dead reaper is not the same as a quiet one"
    If the reaper thread is not running, leases still **expire** for the
    purposes of who-holds-what — but no stop is issued when a holder
    disconnects, which is the entire point of a lease. The error counts are
    cumulative and never reset: a stop that did not happen is not something to
    age out of the health signal. Watch this field.

!!! danger "`--full-access` removes the lease and the watchdog"
    Developer full access (`features.full_access`) turns off the control lease
    entirely. With it on there is no lease, and therefore **no watchdog**: if
    your client dies mid-move, nothing notices and nothing stops. Use it only
    on a cell you control physically, standing where you can reach the E-stop.
    See `SAFETY.md`.

## The stop path

```bash
curl -sX POST http://127.0.0.1:8000/api/v1/motion/stop
```

`POST /api/v1/motion/stop` is **never authenticated** and **never gated by the
control lease.** No API key, no control token. A client whose key is wrong,
expired or fumbled must still be able to stop the arm; a stop that can fail
teaches people to retry instead of reaching for the physical button. It
normally returns `200`.

### Span of control

The stop issues each of the following independently and reports each result:

- `ImmStopJOG` — jogs.
- `StopMotion` — program-space moves. (`ImmStopJOG` does **not** stop these;
  `StopMotion` does.)
- Aborts of FWS's own path runners.

Its span is the motion **FWS itself started.** It does **not** stop:

- motion started from the teach pendant,
- a Lua program already running on the controller,
- another client on the robot network,
- passthrough/raw commands.

That limit is a property of the system, not a bug. When a runner fails to
abort, the response says so explicitly (`"N aborted, M FAILED to abort — use
the physical stop"`).

### Standstill confirmation

The response reports whether the arm was seen to come to rest, read from the
8083 telemetry stream:

```json
{
  "stop_requested": true,
  "results": {"ImmStopJOG": "ok", "StopMotion": "ok", "runners": "1 aborted"},
  "confirmed": true,
  "confirmation_source": "telemetry-8083"
}
```

`confirmed` is:

- `true` — joint positions held within tolerance across consecutive telemetry
  samples inside the confirmation window,
- `false` — still moving when the window closed,
- `null` — no telemetry to judge by.

!!! warning "Confirmation is a check, not an interlock"
    The stop is dispatched first; confirmation only observes the telemetry
    afterward. `confirmed: false` or `null` means FWS could not verify a
    standstill — it does not mean the stop was retried or escalated. If you
    cannot confirm the arm has stopped, use the physical E-stop.

## Read-only mode

`server.read_only` makes the whole gateway an observer. It refuses every
state-changing HTTP request — anything that is not `GET`, `HEAD` or `OPTIONS` —
with `403`. The rule is by verb, not by route, so a route added later cannot
slip through an incomplete denylist.

!!! warning "Read-only refuses the stop too"
    Read-only mode is the one thing that disables `POST /api/v1/motion/stop`: a
    gateway that could stop a program someone else started is not read-only.
    In read-only mode the gateway commands nothing, including stop — **the
    physical E-stop is your only stop.** Restart without `read_only` to enable
    commanding. The telemetry WebSocket is unaffected; it is not an HTTP
    request.

## Authentication

FWS authenticates with an API-key file, and only when one is configured.

- Keys live one per line in the file named by `auth.api_keys_file` (`#`
  comments ignored; an optional label after the key names it in the audit log).
  They are held as SHA-256 digests and compared in constant time.
- Send the key in the `X-API-Key` header. A missing or invalid key gets `401`
  with `WWW-Authenticate: X-API-Key`.

```bash
curl http://127.0.0.1:8000/api/v1/state -H 'X-API-Key: <key>'
```

!!! note "A named file with zero usable keys locks everyone out"
    Enforcement keys off whether a file was *named*, not whether it parsed to
    any usable keys. A key file that is empty or all-comments returns `401` to
    every request rather than silently serving unauthenticated. It fails
    closed.

### Always-open paths

These paths never require a key, whatever the configuration says:

| Path | Why |
|---|---|
| `POST /api/v1/motion/stop` | An unreachable stop is worse than a nuisance stop. |
| `GET /api/v1/system/health` | An orchestrator that holds no key must still be able to probe health. |
| `/docs`, `/redoc`, `/openapi.json` | The interactive API and its spec. |

Matching is segment-aware: an open `/docs` matches `/docs` and `/docs/…` but
not a longer sibling like `/docsxyz`.

!!! note "Auth is the boundary FWS defends, not the robot network"
    FWS binds to `127.0.0.1` by default and refuses to start on a non-loopback
    address without `auth.api_keys_file` configured — it declines the unsafe
    combination rather than warning about it. Authentication guards the
    boundary between *your* network and the robot network. It does nothing
    about the robot network itself, which is hostile by design: anyone who can
    reach it already controls the robot completely, with or without FWS. See
    `SAFETY.md` and `SECURITY.md`.