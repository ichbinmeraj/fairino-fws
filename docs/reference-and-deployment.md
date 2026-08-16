# Reference & deployment

Two halves. The first is a reference to the API surface, the live specification,
the capability model, the served kinematic model, and the versioning contract.
The second is how to run FWS in earnest — on a Raspberry Pi, configured,
authenticated, and reached from another machine without exposing the robot
network.

!!! danger "FWS is not a safety device"
    No endpoint in FWS is an emergency stop. The only emergency stop is the
    physical button wired per ISO 13850. `POST /api/v1/motion/stop` is a
    software **functional** stop that depends on a network link, a host, a
    Python process and controller firmware — any of which can fail. It stops
    only the motion FWS itself started, and never a Lua program running on the
    controller or a move started from the teach pendant. Keep the physical
    E-stop within reach. See the safety policy before connecting FWS to a robot.

---

## Reference

### The API surface

Every REST route lives under `/api/v1`. The domains:

| Prefix | What it covers |
|---|---|
| `/state`, `/motion`, `/ws/state` | live state, jog, move, stop, preview, the WebSocket stream |
| `/ws/events`, `/events/stream` | pushed events: commands, faults, watchdog stops |
| `/recordings` | telemetry recordings and the automatic fault dump |
| `/robot`, `/io`, `/frames`, `/gripper` | control layer: pose, I/O, frames, payload, gripper |
| `/model` | the measured kinematic model, served as URDF |
| `/poses` | named poses: capture, edit, generate a program from them |
| `/programs`, `/execution` | program CRUD, load, run/pause/resume/stop |
| `/files` | controller file manager |
| `/sensors`, `/force` | force/torque, payload, sensor setup |
| `/backup`, `/points` | backup and point-table restore |
| `/commands`, `/invoke` | the RPC registry and the gated invoker |
| `/system`, `/metrics` | version, health, boot/ports baseline, Prometheus metrics |

Five paths are always reachable without a key, whatever the configuration:
`POST /api/v1/motion/stop`, `GET /api/v1/system/health`, `/docs`, `/redoc`, and
`/openapi.json`. A client whose key is wrong or missing must still be able to
stop the arm and probe health.

### The live specification

The specification is the running application, not this page. It is served
by the gateway itself and is always current:

- `GET /openapi.json` — the machine-readable OpenAPI spec. Generate a client
  in any language from it.
- `/docs` — interactive Swagger UI.
- `/redoc` — a reference-style rendering of the same spec.

```bash
curl http://127.0.0.1:8000/openapi.json | jq '.paths | keys'
```

The WebSocket streams (`/ws/state`, `/ws/events`) are **not** in the OpenAPI
spec — OpenAPI cannot describe WebSockets — so their frames are documented
separately in [WebSocket streams](websockets.md) and pinned by tests.

### Capabilities: what this controller actually supports

Firmware differs between controllers. Rather than assume a feature exists, FWS
probes for it and reports what it found.

- `GET /api/v1/capabilities` — the cached result.
- `POST /api/v1/capabilities/refresh` — re-probe now, then return the result.

Probing runs **once at startup** on a background thread (non-fatal, so an
unreachable robot still serves health and the capability report), is cached,
and is refreshable on demand. Every probe is a getter with no side effects —
nothing that moves the arm or changes configuration.

Each feature is reported in one of three states:

| State | Meaning |
|---|---|
| `available` | The controller answered and said yes. |
| `absent` | The controller answered and said no — a fact about this firmware. |
| `unknown` | FWS could not ask, or could not read the answer. **Not** evidence the feature is missing. |

!!! warning "`unknown` is not `absent`"
    The distinction is load-bearing. A probe that cannot reach the controller,
    or that returns an error while the controller is **faulted** (many getters
    answer `error 14` purely because of a fault; the identical call succeeds
    once it clears), is recorded as `unknown` — never `absent`. `require()`
    re-probes on `unknown` rather than telling an operator their firmware is
    too old when it is merely unreachable or faulted. Treat `unknown` as "ask
    again", not "unsupported": check the link and
    `POST /api/v1/capabilities/refresh`.

The response groups features by domain and counts the states. Note that the
legacy `unavailable` count sums `absent` and `unknown`, which are two different
things — read the per-state counts instead:

```json
{
  "probed_at": 1755400000.0,
  "total": 31,
  "available": 24,
  "absent": 2,
  "unknown": 5,
  "unavailable": 7,
  "groups": { "identity": { "version": { "available": true, "state": "available", "method": "GetSoftwareVersion" } } }
}
```

!!! note "Some probes are unmeasured on hardware"
    A few probes (for example the gripper getters) have not yet been exercised
    on a real arm. A gripper that is not fitted answers the getters with zeros
    rather than an error, so `available` there means the *method* answered, not
    that a gripper is physically present.

### The served model (URDF)

FWS serves a URDF of the arm so RViz, Foxglove, a three.js scene or a digital
shadow work straight off `/ws/state` with nothing else installed. No URDF
matched to this firmware is published anywhere, and the vendor's URDF (for a
different software version) is measurably worse than the controller's own
numbers.

- `GET /api/v1/model` — provenance, the kinematic chain, flange offset, joint
  limits, and a pointer to the URDF.
- `GET /api/v1/model/urdf` — the URDF itself, `application/xml`, in metres (the
  internal model is millimetres; URDF requires metres).

Query parameters on `/model/urdf`:

| Parameter | Default | Effect |
|---|---|---|
| `visuals` | `primitives` | `primitives` draws stand-in cylinders so a viewer shows an arm; `none` omits all visual geometry — use it for anything that computes rather than draws. |
| `name` | `fr5` | the `<robot name="…">` (max 64 chars). |

```bash
# pure kinematics, no stand-in geometry
curl "http://127.0.0.1:8000/api/v1/model/urdf?visuals=none" -o fr5.urdf
```

**Provenance.** The chain was fitted against this controller's own
`GetForwardKin` over 59 sampled joint configurations: 0.0000 mm RMS, 0.000035 mm
worst case. It is an FR5 with a 922 mm reach. The joint `<limit>` values are the
controller's own soft limits when it will report them; when it will not, the
URDF falls back to a full turn per joint rather than inventing a tighter bound
that would make a planner refuse reachable poses. The model is served even when
the robot is unreachable, so you can open it on a laptop with the cell powered
down.

!!! warning "The visuals are a stand-in, and the rendering is unverified"
    The visual geometry is a set of primitive cylinders derived from the link
    lengths — a recognisable arm, not the real shell. The actual collision
    meshes ship with `fairino-fws-console`.

    The *chain* is hardware-verified: the console renders from these same
    numbers and compares its forward kinematics against the controller-reported
    TCP on every frame, holding 0.00 mm on the live arm, and this URDF is a
    transcription of that chain pinned by test. What has **not** been done is an
    end-to-end check of this URDF inside a consumer (RViz, Foxglove, …) against
    the live arm. Treat the rendering as unverified until you have done that
    check yourself.

### Versioning & the contract

FWS is pre-1.0, but the API surface does not change silently.

- The REST surface at `/api/v1`, as described by `/openapi.json`, is under
  contract. A canonical copy of the spec is committed to the repository, and CI
  fails if the running app's surface drifts from it (a semantic comparison, not
  byte-exact). There are no silent surface changes, even before 1.0.
- **Additive changes** — a new route, a new optional field — arrive in any
  release without notice, because they cannot break a client that was not using
  them.
- **Breaking changes** — a removed route, a new required field, a changed type —
  are allowed while pre-1.0 but are never silent: each is called out in the
  changelog and flagged by the contract-classification CI.

!!! note "Pin an exact alpha"
    While pre-1.0, any alpha may carry a breaking change, so pin the exact one
    you tested against:

    ```
    fairino-fws==0.1.0a14
    ```

    Once 1.0 ships, a compatible-release pin (`fairino-fws~=1.0`) will be safe,
    because breaking changes will then require a major-version bump. The full
    additive-vs-breaking table is in [Versioning &amp; contract](versioning.md).

---

## Deployment

### Target: Raspberry Pi

The documented deployment target is a Raspberry Pi running Raspberry Pi OS
Bookworm, whose system Python is 3.11 — the minimum FWS supports. The Pi sits
with the robot network on one interface and your network on another.

```bash
python3 --version          # expect 3.11.x on Bookworm
pip install --pre fairino-fws
fws --robot-ip 192.168.57.2
```

Install from source or via Docker instead if you prefer:

```bash
pip install .
# or
docker compose up
```

For an always-on install, use the systemd unit shipped at `deploy/fws.service`.
It runs FWS as an unprivileged `fws` user, restarts **cold** on failure (FWS
holds no lock and resumes no motion across a restart — an auto-restart that
resumed motion would be a hazard), and confines the process to loopback and the
robot LAN at the kernel level:

```ini
IPAddressAllow=127.0.0.1/32 192.168.57.0/24
IPAddressDeny=any
```

```bash
sudo cp deploy/fws.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now fws
```

### Configuration

Configure by config file, environment variable, or CLI flag. Precedence is:

```
CLI flags  >  environment  >  config file  >  defaults
```

Every default is the refusing, secure one — you loosen deliberately, not by
accident. Unknown keys are rejected (`extra="forbid"`), so a typo is an error
at startup rather than a setting that silently does nothing.

The same setting three ways — the controller IP. `192.168.57.2` is the
default (the user LAN port); the teach port is `192.168.58.2`.

```bash
fws --robot-ip 192.168.57.2                 # CLI (highest precedence)
FWS_ROBOT__IP=192.168.57.2 fws              # environment
```

```toml
# fws.toml (lowest of the three)
[robot]
ip = "192.168.57.2"
```

Environment variables take the `FWS_` prefix and nest with a double underscore:
`FWS_ROBOT__IP` sets `robot.ip`, `FWS_SERVER__BIND_HOST` sets
`server.bind_host`. Copy `fws.toml.example`, edit it, and point FWS at it:

```bash
fws --config /etc/fws/fws.toml
# or
FWS_CONFIG=/etc/fws/fws.toml fws
```

Two flags help you check a configuration before committing to it:

```bash
fws --check         # validate config and the startup safety gate, then exit
fws --print-config  # print the fully-resolved config and exit (passwords redacted)
```

!!! note "Optional features are off by default"
    Feature flags such as `enable_movel`, `enable_command_passthrough`, and the
    controller-service integrations (FTP, telnet shell, qconn, Lua validator)
    are all off unless you turn them on. `enable_movel` in particular is off
    because MoveL's argument layout produced an unintended ~300 mm motion and a
    controller fault on firmware v3.8.5.1 — verify on the simulator before
    enabling it. `--full-access` turns off **every** software guard at once and
    is only for a cell you control physically; see the safety policy.

!!! warning "Read-only mode also refuses to stop"
    `server.read_only` (or `--read-only`) serves only GET/HEAD/OPTIONS and the
    telemetry WebSocket — including refusing `POST /motion/stop`, because a
    gateway that can stop a program someone else started is not read-only. In
    that mode the physical E-stop is your only stop.

### Authentication

Authentication is by API key, and it is only meaningful once FWS binds beyond
loopback. Keys live in a file — one per line, `#` comments ignored, with an
optional audit label after a space:

```
# /etc/fws/api-keys  (chmod 600, owned by the fws user)
3f9c1b…longrandomkey    ci-runner
a17d90…anotherkey       meraj-laptop
```

Generate a key with real entropy:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Point FWS at the file:

```toml
[auth]
api_keys_file = "/etc/fws/api-keys"
```

Clients send the key in the `X-API-Key` header (WebSocket clients use `?key=…`,
because browsers cannot set headers on a WebSocket handshake):

```bash
curl -H "X-API-Key: 3f9c1b…longrandomkey" http://127.0.0.1:8000/api/v1/state
```

Keys are held as SHA-256 digests and compared in constant time. Stop, health
and the doc endpoints are never authenticated — an unreachable stop is worse
than a nuisance stop, and an orchestrator that holds no key must still be able
to probe health.

!!! warning "The refusing defaults"
    - FWS **refuses to start** on any non-loopback bind with no
      `api_keys_file`. Bind loopback and tunnel, or configure auth — it will
      not warn and serve the unsafe combination.
    - A named key file that parses to **zero** usable keys (emptied, truncated,
      every line commented) is a refusal, not a fallback: FWS `401`s every
      request rather than serving them unauthenticated. Enforcement keys off
      "a file was named", not "keys were loaded".
    - The privileged controller services (telnet shell, qconn) reach a
      root-equivalent path and require auth **even on loopback**; FWS refuses to
      start with them enabled and no key file.

### Reaching it from another machine: the SSH tunnel

FWS binds `127.0.0.1:8000` by default and installs **no CORS handling** by
design — it is not meant to be called from arbitrary browser origins, and the
robot network is never proxied. To reach it from your workstation, forward the
port over SSH rather than binding wider:

```bash
ssh -L 8000:localhost:8000 user@<gateway-host>
# then, locally:
curl http://127.0.0.1:8000/api/v1/state
```

The tunnel gives you a loopback endpoint on your own machine, authenticated by
SSH, with nothing new exposed on either network. Binding to a non-loopback
address is supported — but only with `api_keys_file` configured, and FWS
enforces that at startup.

### The controller network & ports

FWS assumes it runs on a host with the robot network on one interface and your
network on another, and that it is the only thing on the robot network besides
the controller. It talks to the controller on these ports:

| Port | Purpose |
|---|---|
| 20003 | XML-RPC command channel (default `rpc_port`; single-writer — one lock serialises every call) |
| 8083 | telemetry stream (`telemetry_port`); single-client — accepts a second TCP connection but then sends no frame, so a connect test says nothing. `stream_connected` in health is the honest signal |
| 20010 / 20011 | file upload / download (`upload_port` / `download_port`; open on demand, ~250 ms after the RPC) |
| 20002 / 20004 | additional robot control channels |

The controller also exposes base QNX services on the robot LAN. FWS keeps
integrations with all of these **off by default** and gates any it uses behind
its own auth and audit:

| Port | Service | Notes |
|---|---|---|
| 21 | FTP | file get/put; a Lua put this way bypasses the compile-and-register step |
| 23 | telnet | a **root shell** on the controller; requires auth configured |
| 8000 | qconn | QNX target agent, **unauthenticated root by design**; requires auth configured |
| 8060 | Lua validator | compile-check without uploading; the least-proven path |
| 8061 / 8062 | internal single-client listeners | state feedback / file receive |

!!! note "8060–8062 read backwards, and 8000 is two different things"
    Ports 8060–8062 are single-client listeners whose reachability reads
    *inverted*: a **refused** connection means a live client is attached
    (healthy); an **accepting** socket means nobody is consuming it. `8060`
    unattached, for example, means the arm moves and reports normally but no
    program can be uploaded, because the Lua compiler's verdict has nowhere to
    go.

    Note that `8000` appears twice with different meaning: FWS's own gateway
    binds `8000` on the host (loopback), while the controller's qconn is on
    `8000` on the robot LAN — different interfaces, unrelated.

`GET /api/v1/system/recovery` reports the controller against a **boot
baseline** — ports come up in layers after a power cycle (QNX base ~26 s after
power, then the control channels), so a partial set is a controller mid-boot,
not a broken one.

!!! danger "The robot network is hostile by design"
    A Fairino controller exposes FTP, telnet and an unauthenticated qconn on the
    same network as the arm, and FWS cannot change that. **Anyone who can reach
    the robot network already controls the robot completely, with or without
    FWS.** FWS defends the boundary between your network and the robot network —
    it binds loopback, refuses the unsafe bind, and never proxies the robot LAN.
    If you put the robot network on your office network, no configuration of FWS
    makes that safe.
