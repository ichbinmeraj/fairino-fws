"""The shared stop routine, the stop's place in the queue, and the lease rule.

Pins the fixes from the 25 Nov 2026 adapters review:
- every stop (open stop, /safety/stop, watchdog, lease break) drives the safe
  outputs first, and again once the program is stopped;
- a stop goes to the front of the driver queue and is never refused by the
  circuit breaker;
- every write needs a lease by default;
- stop confirmation only counts fresh telemetry frames.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import auth
from fws import config as config_mod
from fws.driver import LockBusy, PriorityLock, RobotDriver

GUN = [{"kind": "digital", "index": 3, "safe_value": 0, "name": "spray gun"}]


def _settings(fake, **extra):
    return config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
        **extra,
    })


@pytest.fixture
def gun_client(fake):
    app_mod.create_app(_settings(fake, **{"safety.safe_outputs": GUN}))
    app_mod.control._leases.clear()
    with TestClient(app_mod.app) as c:
        yield c


@pytest.fixture
def strict_client(fake):
    """The shipping default: a lease for every write."""
    app_mod.create_app(_settings(
        fake, **{"safety.require_lease_for_writes": True}))
    app_mod.control._leases.clear()
    with TestClient(app_mod.app) as c:
        yield c
    app_mod.control.require_lease = False


def _names(fake):
    return [name for name, _ in fake.calls]


def _set_do(fake):
    return [i for i, (name, args) in enumerate(fake.calls)
            if name == "SetDO" and tuple(args[:2]) == (3, 0)]


class TestTheOpenStopTurnsTheGunOff:
    def test_safe_outputs_go_first_and_again_after_the_program(
            self, gun_client, fake):
        fake.state.program_state = 2          # RAW_cell.lua running
        fake.calls.clear()
        body = gun_client.post("/api/v1/motion/stop").json()
        names = _names(fake)
        outs = _set_do(fake)
        assert len(outs) == 2, f"gun off before and after; saw {fake.calls}"
        for step in ("ProgramStop", "ImmStopJOG", "StopMotion"):
            assert outs[0] < names.index(step) < outs[1], step
        assert body["ok"] is True
        assert body["results"]["safe_outputs"][0]["result"] == "ok"

    def test_no_configured_output_is_said_out_loud(self, fake):
        app_mod.create_app(_settings(fake))
        with TestClient(app_mod.app) as c:
            body = c.post("/api/v1/motion/stop").json()
            health = c.get("/api/v1/system/health").json()
        assert "no safe outputs" in body["warning"]
        assert any("no safe outputs" in w for w in health["warnings"])

    def test_safety_stop_needs_no_key(self):
        assert auth.is_open_path("/api/v1/safety/stop")
        assert auth.is_open_path("/api/v1/motion/stop")


class TestTheWatchdogAndALeaseBreakTurnTheGunOff:
    def test_a_lapsed_motion_lease_drives_the_safe_output(
            self, gun_client, fake):
        lease = app_mod.control.acquire("dashboard", ["motion"])
        fake.calls.clear()
        app_mod._on_lease_lapse("expired", lease)
        assert _set_do(fake), "the watchdog left the gun as it was"
        assert "StopMotion" in _names(fake)

    def test_a_lapsed_config_lease_still_stops_nothing(self, gun_client, fake):
        lease = app_mod.control.acquire("cfg", ["config"])
        fake.calls.clear()
        app_mod._on_lease_lapse("expired", lease)
        assert not _set_do(fake)

    def test_breaking_a_motion_lease_runs_the_stop(self, gun_client, fake):
        app_mod.control.acquire("dashboard", ["motion"])
        fake.calls.clear()
        r = gun_client.delete("/api/v1/control/motion")
        assert r.status_code == 200
        assert r.json()["stop"] is not None
        assert _set_do(fake) and "StopMotion" in _names(fake), (
            "a broken lease must not leave a moving arm unsupervised")


class TestTheStopIsNotRefusedOrQueued:
    def test_the_breaker_does_not_refuse_a_stop(self, gun_client, fake):
        app_mod.driver._offline_until = time.monotonic() + 60
        try:
            fake.calls.clear()
            body = gun_client.post("/api/v1/motion/stop").json()
        finally:
            app_mod.driver._offline_until = 0.0
        assert "StopMotion" in _names(fake)
        assert body["results"]["StopMotion"] == "ok"

    def test_a_priority_waiter_goes_before_ordinary_waiters(self):
        lock = PriorityLock()
        order: list[str] = []
        lock.acquire()

        def worker(tag, priority):
            lock.acquire(priority=priority)
            order.append(tag)
            lock.release()

        normal = threading.Thread(target=worker, args=("normal", False))
        normal.start()
        time.sleep(0.05)
        stop = threading.Thread(target=worker, args=("stop", True))
        stop.start()
        time.sleep(0.05)
        lock.release()
        normal.join(2)
        stop.join(2)
        assert order == ["stop", "normal"]

    def test_an_ordinary_call_gives_up_on_a_busy_driver(self, fake):
        d = RobotDriver(fake.host, port=fake.rpc_port, lock_wait_s=0.1)
        d._lock.acquire()
        try:
            started = time.monotonic()
            with pytest.raises(LockBusy):
                d._call("GetSoftwareVersion")
            assert time.monotonic() - started < 1.0
        finally:
            d._lock.release()


class TestEveryWriteNeedsALease:
    def test_a_digital_output_write_without_a_lease_is_428(
            self, strict_client):
        r = strict_client.put("/api/v1/io/digital/outputs/3",
                              json={"value": 1, "confirm": True})
        assert r.status_code == 428

    def test_a_jog_without_a_lease_is_428(self, strict_client):
        r = strict_client.post("/api/v1/motion/jog", json={
            "joint": 1, "direction": 1, "step": 1.0, "vel": 5.0})
        assert r.status_code == 428

    def test_with_the_lease_the_write_goes_through(self, strict_client):
        token = strict_client.post("/api/v1/control", json={
            "client_id": "dash", "domains": ["motion"]}).json()["token"]
        r = strict_client.put("/api/v1/io/digital/outputs/3",
                              json={"value": 0, "confirm": True},
                              headers={"X-FWS-Control-Token": token})
        assert r.status_code == 200, r.text

    def test_stop_needs_no_lease_even_when_writes_do(self, strict_client):
        assert strict_client.post("/api/v1/motion/stop").status_code == 200
        assert strict_client.post("/api/v1/safety/stop").status_code == 200


class TestStaleTelemetryConfirmsNothing:
    def test_frozen_frames_are_unknown_not_standstill(self, monkeypatch):
        frozen = {"connected": True, "joints": [0.0] * 6, "frames": 10,
                  "ts": time.time() - 5.0}
        monkeypatch.setattr(app_mod.telemetry, "snapshot", lambda: frozen)
        assert app_mod._confirm_standstill(window_s=0.3) is None

    def test_a_connected_stream_that_stops_advancing_is_not_confirmed(
            self, monkeypatch):
        # Fresh timestamp but the frame counter never moves: no new frame,
        # so nothing has been learned about the arm.
        snap = {"connected": True, "joints": [0.0] * 6, "frames": 10}
        monkeypatch.setattr(app_mod.telemetry, "snapshot",
                            lambda: {**snap, "ts": time.time()})
        assert app_mod._confirm_standstill(window_s=0.3) is None
