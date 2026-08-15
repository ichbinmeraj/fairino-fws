"""The developer full-access switch.

Set once at startup from ``features.full_access`` (``--full-access``). When it
is on, every SOFTWARE guard in FWS steps aside at once: the invoke path stops
requiring a control lease or ``confirm=true`` and will call any of the 594
commands including the refused ones, the per-route confirmations are treated
as already given, the bounded-jog limits and soft-limit pre-flight are lifted,
the disabled feature flags and controller services are turned on, and the
startup safety refusals become warnings.

It defaults OFF, so the package ships safe to anyone who does not opt in and
the test suite keeps exercising the guarded behaviour.

WHAT IT DOES NOT TOUCH. The physical E-stop (ISO 13850) is the only thing
that actually stops the arm, and no software switch changes that: FWS is
not a safety device (SAFETY.md). Full access removes the rails that stop a
mistake from reaching the controller -- a wrong command can then power off or
brick it with no remote recovery, and a runaway move is stopped only by the
physical button. Turn it on only on a cell you control physically.
"""
from __future__ import annotations

_FULL_ACCESS = False


def set_full_access(on: bool) -> None:
    """Latch the switch. Called once from create_app()."""
    global _FULL_ACCESS
    _FULL_ACCESS = bool(on)


def full_access() -> bool:
    """True when every software guard is standing aside."""
    return _FULL_ACCESS
