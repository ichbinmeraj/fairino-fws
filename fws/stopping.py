"""The one stop routine.

Every stop in the gateway runs this: the open POST /motion/stop, POST
/safety/stop, the lease watchdog when a client goes quiet, and a forced lease
break. Before this module there were three different stops, and only the one
behind an API key drove the safe outputs. On a paint cell that meant the stop
anybody could reach, and the watchdog that fires when the dashboard dies
mid-spray, both left the gun on.

THE ORDER IS THE POINT.

1. Safe outputs (config: [safety] safe_outputs). A robot that has come to rest
   with the gun still open is not stopped in any sense that matters on a spray
   cell.
2. ProgramStop, so the resident Lua program (RAW_cell.lua) cannot issue
   another move or switch the gun back on. Skipped only when the interpreter
   reads as idle; sent when its state cannot be read.
3. ImmStopJOG, then StopMotion: jogs and program-space moves.
4. Gateway path runners are told to abort (no wire traffic).
5. Safe outputs once more. Between step 1 and step 2 the Lua program was still
   running and could have set the output again. Writing it a second time once
   the program is stopped closes that gap for the price of one call per output.

Every step runs even if an earlier one fails: a controller that refuses one
call may honour the next. Every wire call goes through RobotDriver.stop_call,
which skips the circuit breaker and goes to the front of the driver's lock
queue.

None of this is an emergency stop or a protective stop. It is an operational
stop sent over a network to a controller that has to be listening. The
physical E-stop, the controller's safety inputs and a hardwired spray-enable
interlock are the safety functions; this is the layer above them.
"""
from __future__ import annotations

from typing import Any

from .driver import RobotError

NO_SAFE_OUTPUTS = (
    "no safe outputs are configured ([safety] safe_outputs is empty), so this "
    "stop did not touch the spray gun or any other tool output")


def _drive_outputs(driver, safe_outputs) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for out in safe_outputs:
        label = out.name or f"{out.kind} output {out.index}"
        try:
            if out.kind == "digital":
                driver.set_safe_digital(out.index, int(out.safe_value))
            else:
                driver.set_safe_analog(out.index, out.safe_value)
            results.append({"output": label, "commanded": out.safe_value,
                            "result": "ok"})
        except (RobotError, OSError) as e:
            results.append({"output": label, "commanded": out.safe_value,
                            "result": f"error: {e}"})
    return results


def _stop_program_if_running(driver) -> str:
    """ProgramStop unless the interpreter is known to be idle.

    The state is read on the stop path too (front of the queue). If it cannot
    be read, ProgramStop is sent anyway: not knowing whether the program runs
    is a reason to stop it, not to leave it.
    """
    try:
        rtn = driver.stop_call("GetProgramState")
        code = rtn[1] if isinstance(rtn, list) and len(rtn) > 1 else None
    except (RobotError, OSError):
        code = None
    if code == 1:
        return "not running"
    try:
        driver.program_stop()
        return "ok"
    except (RobotError, OSError) as e:
        return f"error: {e}"


def safe_stop(driver, safe_outputs, abortables=None, *,
              program: bool = True, motion: bool = True) -> dict[str, Any]:
    """Run the stop sequence. Never raises; returns the per-step outcome.

    program / motion select which halves run; both are on except for the
    lease watchdog, which stops only what the lapsed lease was driving. The
    safe outputs are driven whenever either half runs.

    The result has the step results, a flat `failed` list and `ok`. A safe
    output is commanded, never confirmed: this firmware cannot read control-box
    outputs back over 20003.
    """
    results: dict[str, Any] = {}
    results["safe_outputs"] = _drive_outputs(driver, safe_outputs)

    if program:
        results["ProgramStop"] = _stop_program_if_running(driver)
    steps = (("ImmStopJOG", driver.stop),
             ("StopMotion", driver.stop_motion)) if motion else ()
    for name, fn in steps:
        try:
            fn()
            results[name] = "ok"
        except (RobotError, OSError) as e:
            results[name] = f"error: {e}"

    if abortables is not None and motion:
        reached = abortables.request_abort_all()
        failed_aborts = abortables.failed_last_call
        results["runners"] = (
            f"{reached} aborted" if not failed_aborts
            else f"{reached} aborted, {failed_aborts} FAILED to abort -- "
                 f"use the physical stop")

    results["safe_outputs_after_program_stop"] = _drive_outputs(
        driver, safe_outputs)

    failed = [k for k, v in results.items()
              if isinstance(v, str) and (v.startswith("error")
                                         or "FAILED" in v)]
    for key in ("safe_outputs", "safe_outputs_after_program_stop"):
        failed += [f"{key}: {o['output']}" for o in results[key]
                   if o["result"].startswith("error")]
    out: dict[str, Any] = {"results": results, "failed": failed,
                           "ok": not failed}
    if not safe_outputs:
        out["warning"] = NO_SAFE_OUTPUTS
    return out
