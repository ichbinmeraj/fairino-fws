# Examples

Runnable programs that show the whole flow, not fragments. Every one runs
against the simulator — no robot, no config, no credentials:

```bash
pip install --pre fairino-fws
python examples/01_read_state.py
```

Each script starts its own gateway through `fws.testing.gateway()`, so there
is nothing to launch in another terminal. To point one at a **real** cell
instead, start `fws` yourself and pass the URL:

```bash
fws --robot-ip 192.168.57.2          # in one terminal
python examples/01_read_state.py --url http://localhost:8000
```

> Against real hardware these scripts move the arm. Read `SAFETY.md` first,
> stand where you can reach the E-stop, and understand that FWS is not a
> safety device.

| Script | What it shows |
|---|---|
| `01_read_state.py` | Connect, read live state, follow telemetry over the WebSocket |
| `02_jog_safely.py` | Take a control lease, keep it alive, jog within bounds, hit a refusal on purpose |
| `03_pick_and_place.py` | The full program loop: generate Lua, upload, validate, load, run, watch |
| `04_handle_faults.py` | Trip a fault, read what it means, clear it, and re-probe capabilities |

They are ordered: each assumes the one before it.
