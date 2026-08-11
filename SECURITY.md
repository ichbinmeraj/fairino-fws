# Security policy

FWS sits between a network and a machine that moves. Please read this before
deploying it, and before reporting an issue.

## Reporting a vulnerability

**Do not open a public issue for anything that could be used to move a robot.**

Use GitHub's private vulnerability reporting (Security → Report a
vulnerability) on this repository. Please include the controller software
version (`GET /api/v1/system/version`), what you did, and what happened.

## In scope

- Bypassing the control lock, the refusal list, or the typed-route ownership in
  the invoker.
- Reaching a motion-class command without a held lease or confirmation.
- Anything that makes `POST /api/v1/motion/stop` fail, hang, or queue.
- Reading or writing controller files outside the intended kinds.
- Authentication bypass on a non-loopback deployment, including a WebSocket or
  a controller-services route reachable without a key when authentication is
  configured.

## Out of scope, and why

**The robot network is hostile by design and FWS does not change that.** The
controller exposes FTP, telnet and an unauthenticated root `qconn` on the same
network as the arm. That is the vendor's architecture. The consequence, stated
plainly: **anyone who can reach the robot network already controls the robot
completely, with or without FWS.**

Reports amounting to "the robot network is insecure" are true and already
documented — see `SAFETY.md`. The boundary FWS defends is the one between
*your* network and the robot network, which is why it binds to loopback and
refuses to start non-loopback without authentication configured.

## What FWS is not

**Not a safety device.** No endpoint is an emergency stop, and it is not
certified to any functional-safety standard. A report that FWS does not meet a
safety standard is not a vulnerability; it is the design, and `SAFETY.md` says
so. The stop endpoints are software functional stops that depend on a network
link, a host, a Python process and controller firmware.

## Deployment

FWS binds to `127.0.0.1` by default and is meant to be reached over an SSH
tunnel, or bound elsewhere **with authentication enabled** — startup refuses
the unsafe combination. Privileged controller-services (a root shell, process
control) are off by default and refuse to start without authentication
configured.
