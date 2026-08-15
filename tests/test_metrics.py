"""Prometheus metrics.

A cell gateway on a Pi is exactly what a plant scrapes. Every number here
already existed inside FWS; what was missing was a format anything could
read. These tests pin the two things that make it usable: the exposition is
valid enough for a real scraper, and the numbers that would page someone --
the watchdog, the corrupt-frame counter, the durable audit trail -- are
actually in it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod
from fws.metrics import render


def _client(fake, **over):
    app_mod.create_app(config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
        **over,
    }))
    return TestClient(app_mod.app)


def _sample():
    return render(
        telemetry_snapshot={"connected": True, "frames": 1234,
                            "bad_checksum": 2,
                            "joints": [1.0, 2, 3, 4, 5, 6],
                            "joint_torque": [0.1, 0, 0, 0, 0, 0]},
        errors={"main": 0, "sub": 0},
        watchdog={"healthy": True, "reap_errors": 0,
                  "lapse_callback_errors": 0},
        audit_health={"in_memory": 7, "durable": False, "sink_errors": 0},
        bus_health={"published": 42, "subscribers": 1},
        recorder_health={"fault_dumps": 0, "recording": None},
        capabilities={"available": 27, "absent": 4, "unknown": 0},
        lock_holders={"motion": {"client_id": "x"}},
    )


def _parse(text):
    """Minimal exposition parser: name{labels} value."""
    out = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, value = line.rpartition(" ")
        out.setdefault(name.strip(), []).append(float(value))
    return out


class TestTheExpositionIsWellFormed:
    def test_every_metric_has_help_and_type(self):
        text = _sample()
        names = {n.split("{")[0] for n in _parse(text)}
        for name in names:
            assert f"# HELP {name} " in text, f"{name} has no HELP"
            assert f"# TYPE {name} " in text, f"{name} has no TYPE"

    def test_every_value_is_a_number(self):
        for name, values in _parse(_sample()).items():
            for v in values:
                assert isinstance(v, float), f"{name} is not numeric"

    def test_counters_are_named_total(self):
        """A dashboard someone else wrote should work against this without
        translation, which means following the convention."""
        text = _sample()
        for line in text.splitlines():
            if line.startswith("# TYPE ") and line.endswith(" counter"):
                assert line.split()[2].endswith("_total"), line

    def test_labels_are_used_for_joints_not_metric_names(self):
        """fws_joint_position_degrees{joint="j1"}, not fws_j1_position: the
        first can be summed and graphed as a family."""
        parsed = _parse(_sample())
        assert 'fws_joint_position_degrees{joint="j1"}' in parsed
        assert len([k for k in parsed
                    if k.startswith("fws_joint_position_degrees")]) == 6

    def test_a_missing_value_is_omitted_not_rendered_as_none(self):
        """`fws_x None` breaks a scraper's parse for the whole page."""
        text = render(
            telemetry_snapshot={}, errors={},
            watchdog={}, audit_health={}, bus_health={},
            recorder_health={}, capabilities=None, lock_holders={})
        assert "None" not in text
        for line in text.splitlines():
            if line and not line.startswith("#"):
                float(line.rpartition(" ")[2])       # parses, or raises


class TestTheNumbersWorthPagingOn:
    def test_the_watchdog_is_exposed(self):
        """An unhealthy watchdog means a client that disconnects mid-move may
        NOT trigger a stop."""
        assert "fws_control_watchdog_healthy" in _parse(_sample())

    def test_corrupt_frames_are_exposed(self):
        parsed = _parse(_sample())
        assert parsed["fws_telemetry_bad_checksum_total"] == [2.0]

    def test_audit_durability_is_exposed(self):
        assert _parse(_sample())["fws_audit_durable"] == [0.0]

    def test_a_latched_fault_shows_as_faulted(self):
        text = render(
            telemetry_snapshot={}, errors={"main": 1, "sub": 22},
            watchdog={}, audit_health={}, bus_health={},
            recorder_health={}, capabilities=None, lock_holders={})
        parsed = _parse(text)
        assert parsed["fws_robot_faulted"] == [1.0]
        assert parsed["fws_robot_error_code"] == [1.0]

    def test_lock_domains_each_get_a_series(self):
        parsed = _parse(_sample())
        assert parsed['fws_control_lock_held{domain="motion"}'] == [1.0]
        assert parsed['fws_control_lock_held{domain="config"}'] == [0.0]


class TestOverHttp:
    def test_it_is_served_as_prometheus_text(self, fake):
        with _client(fake) as c:
            r = c.get("/api/v1/metrics")
            assert r.status_code == 200
            assert "text/plain" in r.headers["content-type"]
            assert "fws_up 1" in r.text

    def test_it_reflects_the_live_gateway(self, fake):
        with _client(fake) as c:
            c.post("/api/v1/motion/stop")           # publishes an event
            parsed = _parse(c.get("/api/v1/metrics").text)
            assert parsed["fws_events_published_total"][0] >= 1
            assert parsed["fws_uptime_seconds"][0] >= 0

    def test_it_needs_a_key_when_auth_is_configured(self, fake, tmp_path):
        """It carries live joint positions, and this gateway treats live
        state as needing a key -- /health and stop are the open ones."""
        keyfile = tmp_path / "keys"
        keyfile.write_text("secret-key example\n")
        with _client(fake, **{"auth.api_keys_file": str(keyfile)}) as c:
            assert c.get("/api/v1/metrics").status_code == 401
            assert c.get("/api/v1/metrics",
                         headers={"X-API-Key": "secret-key"}).status_code == 200
            # The open ones stay open, so a scrape failure is distinguishable
            # from the gateway being down.
            assert c.get("/api/v1/system/health").status_code == 200

    @pytest.mark.parametrize("path", ["/api/v1/metrics"])
    def test_it_survives_an_unreachable_robot(self, fake, path, monkeypatch):
        with _client(fake) as c:
            monkeypatch.setattr(app_mod.capabilities, "as_dict",
                                lambda: (_ for _ in ()).throw(RuntimeError()))
            assert c.get(path).status_code == 200
