# Safety

FWS sits between a network and a machine that can move and can injure someone.
Read this before connecting it to a robot.

## FWS is not a safety device

No endpoint in FWS is an emergency stop. The only emergency stop is the
physical button wired per ISO 13850. FWS carries no safety-rated function and
is **not** certified to ISO 10218, ISO/TS 15066, IEC 61508 or ISO 13849.

The stop endpoints are software-implemented **functional** stops. They depend
on a network link, a host, a Python process and controller firmware — any of
which can fail at the moment you need them. They are a convenience, not a
protective measure. Keep the physical E-stop within reach.

## The stop path

`POST /api/v1/motion/stop` is never authenticated, never gated, and always
returns 200. A client whose key is wrong, expired or fumbled must still be able
to stop the arm; a stop that can fail teaches people to retry instead of
reaching for the physical button.

Its span of control is the motion FWS itself started — jogs, program-space
moves, and its own path runners. It does **not** stop motion started from the
teach pendant, a Lua program already running on the controller, or another
client on the robot network. That limit is a property of the system, not a bug.

## What FWS refuses to do

These refusals are enforced in code, in the driver, below the API:

1. **XML-RPC introspection** (`system.listMethods` and friends), which can put
   the controller into a bad state.
2. **Firmware and OS writes** — the firmware-upgrade paths, the joint-parameter
   upgrade, the EtherCAT slave boot mode and the raw slave-file write. FWS will
   not become a remote flashing tool. The refusal is derived and tested so it
   cannot drift from the command registry it backstops.
3. **Controller shutdown**, by default. `ShutDownRobotOS` switches the
   controller off with no API to switch it back on, so it stays on the refusal
   list. A single opt-in endpoint (`POST /system/shutdown`, disabled by
   default) exists behind the control lock and two explicit confirmations;
   recovery still requires a person at the machine.
4. **Raw byte writes to robot ports.** Liveness probes connect and close only.

## The robot network is hostile by design

A Fairino controller exposes FTP, telnet and an unauthenticated `qconn` on the
same network as the arm. FWS cannot change that. The practical consequence:
**anyone who can reach the robot network already controls the robot
completely, with or without FWS.**

FWS defends the boundary between *your* network and the robot network. That is
why it binds to loopback by default and refuses to start on a non-loopback
address without authentication configured. It does not expose the robot network
and does not proxy it.

## Deployment expectations

FWS assumes:

- it runs on a host with the robot network on one interface and your network on
  another;
- that host is the only thing on the robot network besides the controller;
- it binds to `127.0.0.1` and is reached over an SSH tunnel, **or** binds
  elsewhere with authentication enabled — it refuses the unsafe combination
  rather than warning about it.

If you put the robot network on your office network, no configuration of FWS
makes that safe.
