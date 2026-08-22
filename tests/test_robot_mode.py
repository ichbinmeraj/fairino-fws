"""The mode surface: GET/PUT /api/v1/robot/mode.

The v3.8.5.1 firmware cannot report its auto/manual mode (nothing in the
433-byte telemetry frame, no Get RPC), so GET answers from the last mode the
gateway set this session — honestly labelled — and null before that.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod

MODE = "/api/v1/robot/mode"


def _build_app(fake):
    return app_mod.create_app(config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
    }))


@pytest.fixture
def client(fake):
    _build_app(fake)
    with TestClient(app_mod.app) as c:
        app_mod.control._leases.clear()
        yield c
        app_mod.control._leases.clear()


def _token(client, client_id: str = "mode-test") -> str:
    r = client.post("/api/v1/control",
                    json={"client_id": client_id, "domains": ["motion"]})
    return r.json()["token"]


class TestRead:
    def test_unknown_until_the_gateway_sets_it(self, client):
        """Before the gateway has commanded a mode, null means UNKNOWN."""
        d = client.get(MODE).json()
        assert d["mode"] is None
        assert "no mode read" in d["source"]
        assert "not set the mode" in d["source"]

    def test_reads_are_not_audited(self, client):
        before = client.get("/api/v1/events").json()["count"]
        client.get(MODE)
        assert client.get("/api/v1/events").json()["count"] == before


class TestWrite:
    def test_auto_reaches_the_wire_as_mode_0(self, client, fake):
        r = client.put(MODE, json={"mode": "auto", "confirm": True})
        assert r.status_code == 200
        assert r.json() == {"mode": "auto", "applied": True}
        _, args = next(c for c in reversed(fake.calls) if c[0] == "Mode")
        assert args == (0,), "auto is Mode(0) on the wire"
        assert fake.state.manual_mode is False

    def test_manual_needs_no_confirmation(self, client, fake):
        """Manual DISARMS program starts; the safe direction stays easy."""
        client.put(MODE, json={"mode": "auto", "confirm": True})
        r = client.put(MODE, json={"mode": "manual"})
        assert r.status_code == 200
        assert r.json() == {"mode": "manual", "applied": True}
        _, args = next(c for c in reversed(fake.calls) if c[0] == "Mode")
        assert args == (1,), "manual is Mode(1) on the wire"
        assert fake.state.manual_mode is True

    def test_auto_requires_confirmation(self, client, fake):
        """Auto mode arms remote program starts. It is not passive."""
        r = client.put(MODE, json={"mode": "auto"})
        assert r.status_code == 400
        assert "confirm=true" in r.json()["detail"]
        assert not [c for c in fake.calls if c[0] == "Mode"], (
            "must not reach the robot")

    def test_respects_the_control_lock(self, client, fake):
        _token(client, client_id="other")     # someone else holds motion
        r = client.put(MODE, json={"mode": "auto", "confirm": True})
        assert r.status_code == 428
        r = client.put(MODE, json={"mode": "auto", "confirm": True},
                       headers={"X-FWS-Control-Token": "not-the-holder"})
        assert r.status_code == 423
        assert not [c for c in fake.calls if c[0] == "Mode"], (
            "nothing may be sent while refused")

    def test_the_holder_token_passes(self, client, fake):
        token = _token(client)
        r = client.put(MODE, json={"mode": "auto", "confirm": True},
                       headers={"X-FWS-Control-Token": token})
        assert r.status_code == 200
        assert fake.state.manual_mode is False

    def test_rejects_anything_but_auto_or_manual(self, client):
        assert client.put(MODE, json={"mode": "AUTO"}).status_code == 422
        assert client.put(MODE, json={"mode": 0}).status_code == 422

    def test_writes_are_audited_and_the_token_is_not(self, client):
        token = _token(client)
        client.put(MODE, json={"mode": "manual"},
                   headers={"X-FWS-Control-Token": token})
        events = client.get("/api/v1/events").json()
        assert "robot.mode" in [e["action"] for e in events["events"]]
        assert token not in str(events)


class TestReadReflectsWrites:
    def test_get_reflects_the_last_set(self, client):
        client.put(MODE, json={"mode": "auto", "confirm": True})
        assert client.get(MODE).json() == {
            "mode": "auto", "source": "last-set-by-gateway"}
        client.put(MODE, json={"mode": "manual"})
        assert client.get(MODE).json() == {
            "mode": "manual", "source": "last-set-by-gateway"}

    def test_enable_forces_manual_and_the_record_follows(self, client):
        """POST /robot/enable force-sets manual first; the record must
        follow, or GET would report a stale 'auto'."""
        client.put(MODE, json={"mode": "auto", "confirm": True})
        r = client.post("/api/v1/robot/enable",
                        json={"enable": True, "confirm": True})
        assert r.status_code == 200
        assert client.get(MODE).json() == {
            "mode": "manual", "source": "last-set-by-gateway"}

    def test_the_record_dies_with_the_app_instance(self, client, fake):
        """'This session' means it: a rebuilt app knows nothing."""
        client.put(MODE, json={"mode": "auto", "confirm": True})
        _build_app(fake)
        with TestClient(app_mod.app) as c:
            assert c.get(MODE).json()["mode"] is None


class TestGenericInvokerHandsOff:
    def test_raw_mode_is_redirected_to_the_typed_route(self, client, fake):
        """A raw Mode would make the tracked last-set mode silently stale."""
        token = _token(client)
        r = client.post("/api/v1/invoke/Mode",
                        json={"args": [0], "confirm": True},
                        headers={"X-FWS-Control-Token": token})
        assert r.status_code == 409
        assert "PUT /api/v1/robot/mode" in r.json()["detail"]
        assert not [c for c in fake.calls if c[0] == "Mode"]
