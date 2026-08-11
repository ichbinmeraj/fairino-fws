"""Single-writer control lock and the disconnect watchdog."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod
from fws.control import ControlLock


@pytest.fixture
def client(fake):
    app_mod.create_app(config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
    }))
    with TestClient(app_mod.app) as c:
        app_mod.control._leases.clear()
        yield c
        app_mod.control._leases.clear()


class TestStopIsNeverLockable:
    """The single most important property in the lock design."""

    def test_stop_works_while_another_client_holds_motion(self, client):
        client.post("/api/v1/control",
                    json={"client_id": "other", "domains": ["motion"]})
        r = client.post("/api/v1/motion/stop")      # no token at all
        assert r.status_code == 200
        assert r.json()["results"]["ImmStopJOG"] == "ok"

    def test_reads_are_never_locked(self, client):
        client.post("/api/v1/control",
                    json={"client_id": "other", "domains": ["motion"]})
        for path in ("/api/v1/state", "/api/v1/system/health",
                     "/api/v1/robot/limits", "/api/v1/control"):
            assert client.get(path).status_code == 200, path


class TestLockSemantics:
    def test_unheld_domain_allows_writes_without_a_token(self, client):
        """Single-client operation must not require ceremony."""
        r = client.post("/api/v1/motion/jog",
                        json={"joint": 1, "direction": 1, "step": 1.0, "vel": 5.0})
        assert r.status_code != 428

    def test_holder_can_write_with_its_token(self, client):
        token = client.post("/api/v1/control",
                            json={"client_id": "a", "domains": ["motion"]}
                            ).json()["token"]
        r = client.post("/api/v1/motion/jog",
                        json={"joint": 1, "direction": 1, "step": 1.0, "vel": 5.0},
                        headers={"X-FWS-Control-Token": token})
        assert r.status_code != 423

    def test_other_client_gets_428_without_a_token(self, client):
        client.post("/api/v1/control",
                    json={"client_id": "a", "domains": ["motion"]})
        r = client.post("/api/v1/motion/jog",
                        json={"joint": 1, "direction": 1, "step": 1.0, "vel": 5.0})
        assert r.status_code == 428

    def test_other_client_gets_423_with_a_wrong_token(self, client):
        client.post("/api/v1/control",
                    json={"client_id": "a", "domains": ["motion"]})
        r = client.post("/api/v1/motion/jog",
                        json={"joint": 1, "direction": 1, "step": 1.0, "vel": 5.0},
                        headers={"X-FWS-Control-Token": "not-the-token"})
        assert r.status_code == 423

    def test_second_acquire_is_423_and_names_the_holder(self, client):
        client.post("/api/v1/control",
                    json={"client_id": "first", "domains": ["motion"]})
        r = client.post("/api/v1/control",
                        json={"client_id": "second", "domains": ["motion"]})
        assert r.status_code == 423
        assert r.json()["detail"]["holder"]["client_id"] == "first"

    def test_domains_are_independent(self, client):
        client.post("/api/v1/control",
                    json={"client_id": "hmi", "domains": ["config"]})
        r = client.post("/api/v1/control",
                        json={"client_id": "jogger", "domains": ["motion"]})
        assert r.status_code == 201

    def test_release_frees_the_domain(self, client):
        token = client.post("/api/v1/control",
                            json={"client_id": "a", "domains": ["motion"]}
                            ).json()["token"]
        client.request("DELETE", "/api/v1/control",
                       headers={"X-FWS-Control-Token": token})
        assert client.post("/api/v1/control",
                           json={"client_id": "b", "domains": ["motion"]}
                           ).status_code == 201

    def test_a_stuck_lock_can_be_broken_without_the_dead_clients_token(
            self, client):
        client.post("/api/v1/control",
                    json={"client_id": "ghost", "domains": ["motion"], "ttl_s": 600})
        assert client.delete("/api/v1/control/motion").status_code == 200
        assert client.post("/api/v1/control",
                           json={"client_id": "b", "domains": ["motion"]}
                           ).status_code == 201


class TestDisconnectWatchdog:
    """Expiry IS disconnect. One mechanism, so the two cannot drift apart."""

    def test_lapsed_motion_lease_fires_the_stop(self):
        fired = []
        lock = ControlLock(on_lapse=lambda reason, lease: fired.append(lease))
        lease = lock.acquire("dying-client", ["motion"], ttl_s=5.0)
        lease.expires_at = time.time() - 0.01          # simulate a missed renewal
        lock.reap()
        assert len(fired) == 1
        assert fired[0].client_id == "dying-client"

    def test_lapsed_config_lease_does_not_stop_the_robot(self, client, fake):
        """Only `motion` implies the arm may be moving."""
        app_mod.control.acquire("cfg", ["config"], ttl_s=5.0)
        for lease in list(app_mod.control._leases.values()):
            lease.expires_at = time.time() - 0.01
        fake.calls.clear()
        app_mod.control.reap()
        assert "ImmStopJOG" not in [c[0] for c in fake.calls]

    def test_explicit_release_does_not_stop_the_robot(self, client, fake):
        """Saying goodbye is not disconnecting."""
        token = client.post("/api/v1/control",
                            json={"client_id": "a", "domains": ["motion"]}
                            ).json()["token"]
        fake.calls.clear()
        client.request("DELETE", "/api/v1/control",
                       headers={"X-FWS-Control-Token": token})
        app_mod.control.reap()
        assert "ImmStopJOG" not in [c[0] for c in fake.calls]

    def test_heartbeat_extends_the_lease(self, client):
        r = client.post("/api/v1/control",
                        json={"client_id": "a", "domains": ["motion"], "ttl_s": 5})
        token, first = r.json()["token"], r.json()["expires_at"]
        time.sleep(0.05)
        r2 = client.post("/api/v1/control/heartbeat?ttl_s=30",
                         headers={"X-FWS-Control-Token": token})
        assert r2.status_code == 200
        assert r2.json()["expires_at"] > first
        assert r2.json()["renewals"] == 1


class TestTheWatchdogCannotDieSilently:
    """A throwing lapse callback must not silently kill the watchdog thread."""

    @staticmethod
    def _boom(reason, lease):
        raise RuntimeError("watchdog callback failed")

    def _lapse(self, lock, client="c"):
        lease = lock.acquire(client, ["motion"])
        lease.expires_at = time.time() - 1      # TTL is clamped; force it
        return lease

    def test_a_throwing_callback_does_not_kill_the_thread(self):
        lock = ControlLock(on_lapse=self._boom)
        lock.start(0.05)
        try:
            self._lapse(lock, "first")
            time.sleep(0.3)
            assert lock.watchdog()["running"] is True
            # And it is still reaping, which is the assertion that matters.
            self._lapse(lock, "second")
            time.sleep(0.3)
            assert lock.watchdog()["lapse_callback_errors"] == 2
        finally:
            lock.close()

    def test_the_failure_is_counted_not_swallowed(self):
        lock = ControlLock(on_lapse=self._boom)
        lock.start(0.05)
        try:
            self._lapse(lock)
            time.sleep(0.3)
            w = lock.watchdog()
            assert w["lapse_callback_errors"] == 1
            assert "RuntimeError" in w["last_lapse_callback_error"]
        finally:
            lock.close()

    def test_a_failed_callback_makes_the_watchdog_unhealthy(self):
        """It means a stop was NOT issued for a holder that went away."""
        lock = ControlLock(on_lapse=self._boom)
        lock.start(0.05)
        try:
            self._lapse(lock)
            time.sleep(0.3)
            assert lock.watchdog()["healthy"] is False
        finally:
            lock.close()

    def test_one_bad_callback_does_not_deny_the_watchdog_to_others(self):
        """Guarded per lease, not per batch."""
        fired = []
        calls = {"n": 0}

        def flaky(reason, lease):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first one throws")
            fired.append(lease.client_id)

        lock = ControlLock(on_lapse=flaky)
        try:
            a = lock.acquire("a", ["motion"])
            b = lock.acquire("b", ["config"])
            a.expires_at = b.expires_at = time.time() - 1
            lock.reap()
            assert len(fired) == 1, (
                "the second lease's watchdog must still fire")
        finally:
            lock.close()

    def test_a_never_started_lock_reports_not_running(self):
        """`healthy` must not be true merely because nothing has failed."""
        w = ControlLock().watchdog()
        assert w["running"] is False
        assert w["healthy"] is False

    def test_a_running_lock_reports_healthy_and_recent(self):
        lock = ControlLock()
        lock.start(0.05)
        try:
            time.sleep(0.2)
            w = lock.watchdog()
            assert w["healthy"] is True
            assert w["last_reap_age_s"] is not None
            assert w["last_reap_age_s"] < 1.0
        finally:
            lock.close()

    def test_the_meaning_is_published_not_left_to_the_reader(self):
        w = ControlLock().watchdog()
        assert "no stop is issued" in w["means"]
