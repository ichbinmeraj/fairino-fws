"""Prometheus metrics, from the counters FWS already keeps.

A cell gateway on a Pi is exactly the thing a plant scrapes, and every
number here already existed -- in `/system/health`, in the control lock's
watchdog, in the telemetry reader. What was missing was a format anything
could read without writing a bespoke exporter first.

Deliberately hand-rendered rather than pulling in `prometheus_client`. The
gateway has four runtime dependencies and the protocol layer imports nothing
outside the standard library; a metrics endpoint is not worth spending that
on, and the text format is a dozen lines.

NAMING follows the Prometheus convention -- `fws_` prefix, base units,
`_total` on counters -- because a dashboard someone else wrote should work
against this without translation.
"""
from __future__ import annotations

import time
from typing import Any

_STARTED = time.time()


def _line(name: str, value: Any, labels: str = "") -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        value = int(value)
    return f"{name}{labels} {value}\n"


def render(*, telemetry_snapshot: dict[str, Any], errors: dict[str, Any],
           watchdog: dict[str, Any], audit_health: dict[str, Any],
           bus_health: dict[str, Any], recorder_health: dict[str, Any],
           capabilities: dict[str, Any] | None,
           lock_holders: dict[str, Any]) -> str:
    """The whole exposition, as text.

    Every argument is a plain dict the caller already has, so this module
    imports nothing from the rest of the gateway and can be tested alone.
    """
    out: list[str] = []
    add = out.append

    add("# HELP fws_up 1 when the gateway is serving.\n")
    add("# TYPE fws_up gauge\n")
    add(_line("fws_up", 1))

    add("# HELP fws_uptime_seconds Seconds since this process started.\n")
    add("# TYPE fws_uptime_seconds gauge\n")
    add(_line("fws_uptime_seconds", round(time.time() - _STARTED, 1)))

    # -- the robot link ----------------------------------------------------
    add("# HELP fws_telemetry_connected 1 when the 8083 stream is up.\n")
    add("# TYPE fws_telemetry_connected gauge\n")
    add(_line("fws_telemetry_connected",
              bool(telemetry_snapshot.get("connected"))))

    add("# HELP fws_telemetry_frames_total Frames parsed from the stream.\n")
    add("# TYPE fws_telemetry_frames_total counter\n")
    add(_line("fws_telemetry_frames_total", telemetry_snapshot.get("frames")))

    # A rising bad_checksum with a flat frames count is a wire problem; the
    # two together are what make either number meaningful.
    add("# HELP fws_telemetry_bad_checksum_total Frames dropped as corrupt.\n")
    add("# TYPE fws_telemetry_bad_checksum_total counter\n")
    add(_line("fws_telemetry_bad_checksum_total",
              telemetry_snapshot.get("bad_checksum")))

    ts = telemetry_snapshot.get("ts")
    if ts:
        add("# HELP fws_telemetry_age_seconds Age of the newest frame.\n")
        add("# TYPE fws_telemetry_age_seconds gauge\n")
        add(_line("fws_telemetry_age_seconds", round(time.time() - ts, 3)))

    # -- the robot itself --------------------------------------------------
    add("# HELP fws_robot_faulted 1 when a fault is latched.\n")
    add("# TYPE fws_robot_faulted gauge\n")
    add(_line("fws_robot_faulted",
              bool(errors.get("main")) or bool(errors.get("sub"))))

    add("# HELP fws_robot_error_code The latched main fault code, 0 if none.\n")
    add("# TYPE fws_robot_error_code gauge\n")
    add(_line("fws_robot_error_code", errors.get("main") or 0))

    joints = telemetry_snapshot.get("joints") or []
    if joints:
        add("# HELP fws_joint_position_degrees Live joint positions.\n")
        add("# TYPE fws_joint_position_degrees gauge\n")
        for i, v in enumerate(joints, start=1):
            add(_line("fws_joint_position_degrees", v, f'{{joint="j{i}"}}'))

    torques = telemetry_snapshot.get("joint_torque") or []
    if torques:
        add("# HELP fws_joint_torque_nm Live joint torques.\n")
        add("# TYPE fws_joint_torque_nm gauge\n")
        for i, v in enumerate(torques, start=1):
            add(_line("fws_joint_torque_nm", v, f'{{joint="j{i}"}}'))

    # -- the safety layer --------------------------------------------------
    # The watchdog being unhealthy means a client that disconnects mid-move
    # may NOT trigger a stop, and that is worth paging someone about.
    add("# HELP fws_control_watchdog_healthy 1 when the lease reaper runs.\n")
    add("# TYPE fws_control_watchdog_healthy gauge\n")
    add(_line("fws_control_watchdog_healthy", bool(watchdog.get("healthy"))))

    add("# HELP fws_control_watchdog_errors_total Reap/callback failures.\n")
    add("# TYPE fws_control_watchdog_errors_total counter\n")
    add(_line("fws_control_watchdog_errors_total",
              (watchdog.get("reap_errors") or 0)
              + (watchdog.get("lapse_callback_errors") or 0)))

    add("# HELP fws_control_lock_held 1 when a domain is leased.\n")
    add("# TYPE fws_control_lock_held gauge\n")
    for domain in ("motion", "config", "program"):
        add(_line("fws_control_lock_held",
                  bool(lock_holders.get(domain)), f'{{domain="{domain}"}}'))

    # -- the record --------------------------------------------------------
    add("# HELP fws_audit_events_total Audit events recorded in memory.\n")
    add("# TYPE fws_audit_events_total gauge\n")
    add(_line("fws_audit_events_total", audit_health.get("in_memory")))

    add("# HELP fws_audit_durable 1 when the trail survives a restart.\n")
    add("# TYPE fws_audit_durable gauge\n")
    add(_line("fws_audit_durable", bool(audit_health.get("durable"))))

    add("# HELP fws_audit_sink_errors_total Failed writes to the audit file.\n")
    add("# TYPE fws_audit_sink_errors_total counter\n")
    add(_line("fws_audit_sink_errors_total",
              audit_health.get("sink_errors") or 0))

    add("# HELP fws_events_published_total Events pushed to subscribers.\n")
    add("# TYPE fws_events_published_total counter\n")
    add(_line("fws_events_published_total", bus_health.get("published") or 0))

    add("# HELP fws_event_subscribers Current event-stream subscribers.\n")
    add("# TYPE fws_event_subscribers gauge\n")
    add(_line("fws_event_subscribers", bus_health.get("subscribers") or 0))

    add("# HELP fws_recorder_dumps_total Flight-recorder dumps on fault.\n")
    add("# TYPE fws_recorder_dumps_total counter\n")
    add(_line("fws_recorder_dumps_total",
              recorder_health.get("fault_dumps") or 0))

    add("# HELP fws_recorder_recording 1 while an explicit recording runs.\n")
    add("# TYPE fws_recorder_recording gauge\n")
    add(_line("fws_recorder_recording",
              bool(recorder_health.get("recording"))))

    # -- what this controller can do ---------------------------------------
    if capabilities:
        add("# HELP fws_capabilities Probed features by state.\n")
        add("# TYPE fws_capabilities gauge\n")
        for state in ("available", "absent", "unknown"):
            add(_line("fws_capabilities", capabilities.get(state) or 0,
                      f'{{state="{state}"}}'))

    return "".join(out)
