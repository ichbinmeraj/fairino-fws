"""The control-layer surface: discovery, state, I/O, frames, payload."""
from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod
from fws.capabilities import Capabilities


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
        app_mod.capabilities.probe()
        app_mod.control._leases.clear()
        yield c
        app_mod.control._leases.clear()


class TestCapabilityDiscovery:
    def test_probe_finds_what_exists_and_what_does_not(self, driver):
        caps = Capabilities(driver)
        found = caps.probe()
        assert caps.has("io.digital_in")
        assert caps.has("frames.tool_offset")
        # Later-firmware additions: absent here, as on a real v3.8.5.1 unit.
        assert not caps.has("frames.tool_by_id")
        assert not caps.has("payload.by_id")
        assert not caps.has("io.digital_config")
        assert len(found) > 25

    def test_probing_is_read_only(self, driver, fake):
        """A capability probe must not mutate state."""
        Capabilities(driver).probe()
        mutating = {"SetDO", "SetAO", "SetLoadWeight", "StartJOG",
                    "RobotEnable", "Mode", "MoveL", "SetSpeed"}
        assert not (mutating & {c[0] for c in fake.calls})

    def test_endpoint_groups_and_counts(self, client):
        d = client.get("/api/v1/capabilities").json()
        assert d["available"] + d["unavailable"] == d["total"]
        assert {"io", "frames", "payload", "program"} <= set(d["groups"])

    def test_absent_feature_gives_501_naming_it(self, client):
        """A route whose feature is missing answers 501 naming it."""
        from fws.driver import RobotError

        class AllAbsent:
            def has(self, feature): return False

            def require(self, feature):
                raise RobotError(
                    f"this controller does not support '{feature}' "
                    f"(GetDI: unavailable). It is a later-firmware feature; "
                    f"see GET /api/v1/capabilities.")

        saved = app_mod.capabilities
        app_mod.capabilities = AllAbsent()
        try:
            r = client.get("/api/v1/io/digital/inputs/0")
            assert r.status_code == 501
            body = r.json()["detail"]
            assert "io.digital_in" in body
            assert "later-firmware" in body, "must tell the user WHY"
        finally:
            app_mod.capabilities = saved


class TestIdentityAndState:
    def test_identity(self, client):
        d = client.get("/api/v1/robot").json()
        assert d["software"] == "v3.8.5.1"
        assert d["axes"] == 6

    def test_consolidated_state(self, client):
        d = client.get("/api/v1/robot/state").json()
        assert len(d["joints_deg"]) == 6
        assert d["fault"]["faulted"] is False
        assert d["telemetry"]["connected"] is True
        assert "stale" in d["telemetry"]

    def test_state_reports_staleness_rather_than_hiding_it(self, client,
                                                           monkeypatch):
        monkeypatch.setattr(app_mod.telemetry, "snapshot", lambda: {})
        d = client.get("/api/v1/robot/state").json()
        assert d["telemetry"]["stale"] is True

    def test_state_survives_a_faulted_controller(self, client, fake):
        """State still answers when the RPC channel is faulted."""
        fake.latch_fault(1, 22)
        d = client.get("/api/v1/robot/state").json()
        assert d["joints_deg"] is not None
        assert d["fault"]["faulted"] is True


class TestIO:
    def test_read_digital_and_analog_inputs(self, client, fake):
        fake.state.di[3] = 1
        assert client.get("/api/v1/io/digital/inputs/3").json()["value"] == 1
        assert client.get("/api/v1/io/analog/inputs/0").json()["value"] == 0.0

    def test_output_requires_confirmation(self, client, fake):
        """A DO commonly drives a gripper. It is not passive."""
        before = len(fake.calls)
        r = client.put("/api/v1/io/digital/outputs/2", json={"value": 1})
        assert r.status_code == 400
        assert "gripper" in r.json()["detail"]
        assert len(fake.calls) == before, "must not reach the robot"

    def test_confirmed_output_is_written_and_audited(self, client, fake):
        r = client.put("/api/v1/io/digital/outputs/2",
                       json={"value": 1, "confirm": True})
        assert r.status_code == 200
        assert fake.state.do[2] == 1
        actions = [e["action"] for e in
                   client.get("/api/v1/events").json()["events"]]
        assert "io.digital_output" in actions

    def test_output_respects_the_control_lock(self, client):
        client.post("/api/v1/control",
                    json={"client_id": "other", "domains": ["motion"]})
        r = client.put("/api/v1/io/digital/outputs/2",
                       json={"value": 1, "confirm": True})
        assert r.status_code == 428


class TestFramesAndPayload:
    def test_tool_frame(self, client):
        d = client.get("/api/v1/frames/tool").json()
        assert d["active"] == 1
        assert d["offset"][2] == 50.0

    def test_work_frame(self, client):
        assert client.get("/api/v1/frames/work").json()["active"] == 0

    def test_payload_read(self, client):
        assert "mass_kg" in client.get("/api/v1/robot/payload").json()

    def test_payload_write_requires_confirmation(self, client):
        r = client.put("/api/v1/robot/payload", json={"mass_kg": 2.5})
        assert r.status_code == 400
        assert "collision detection" in r.json()["detail"]

    def test_payload_write_is_applied(self, client, fake):
        r = client.put("/api/v1/robot/payload",
                       json={"mass_kg": 2.5, "confirm": True})
        assert r.status_code == 200
        assert fake.state.payload_kg == 2.5


class TestExecution:
    def test_execution_state(self, client):
        d = client.get("/api/v1/execution").json()
        assert d["state"] == "stopped"
        assert "current_line" in d


class TestAudit:
    def test_records_commands_not_reads(self, client):
        client.get("/api/v1/robot/state")
        client.get("/api/v1/io/digital/inputs/0")
        before = client.get("/api/v1/events").json()["count"]
        client.put("/api/v1/robot/speed", json={"percent": 25})
        after = client.get("/api/v1/events").json()
        assert after["count"] == before + 1, "reads must not be logged"
        assert after["events"][0]["action"] == "robot.speed"

    def test_never_records_a_token(self, client):
        r = client.post("/api/v1/control",
                        json={"client_id": "a", "domains": ["motion"]})
        token = r.json()["token"]
        client.put("/api/v1/robot/speed", json={"percent": 30},
                   headers={"X-FWS-Control-Token": token})
        blob = str(client.get("/api/v1/events").json())
        assert token not in blob


class TestWireArgumentTraps:
    """Wire calls must match what the SDK transmits, not the parameter names."""

    def test_analog_output_is_scaled_to_dac_counts(self, client, fake):
        """SetAO scales the value to DAC counts (value * 40.95)."""
        r = client.put("/api/v1/io/analog/outputs/0",
                       json={"value": 100.0, "confirm": True})
        assert r.status_code == 200
        _, args = next(c for c in reversed(fake.calls) if c[0] == "SetAO")
        assert args[1] == pytest.approx(4095.0, abs=1.0), (
            f"sent {args[1]}, expected ~4095 (100% x 40.95)")
        assert r.json()["dac_count"] == 4095

    def test_payload_sends_both_arguments(self, client, fake):
        """SetLoadWeight(loadNum, weight). One argument faults -502."""
        r = client.put("/api/v1/robot/payload",
                       json={"mass_kg": 3.5, "confirm": True})
        assert r.status_code == 200
        _, args = next(c for c in reversed(fake.calls)
                       if c[0] == "SetLoadWeight")
        assert len(args) == 2, "SetLoadWeight takes (loadNum, weight)"
        assert args == (0, 3.5)


class TestErrorCodeDecoding:
    """Faults are decoded to text, not left as bare integers."""

    def test_table_is_populated(self):
        from fws.protocol.error_codes import ERROR_CODES
        assert len(ERROR_CODES) > 150

    def test_codes_we_hit_on_hardware_are_explained(self, client):
        for code in (14, 74, 112, 143):
            d = client.get(f"/api/v1/errors/codes/{code}").json()
            assert d["known"] is True
            assert d["description"]
            assert d["seen_by_fws"], "should carry what we were doing"

    def test_manual_corroborates_our_observations(self):
        """The manual independently corroborates the observed error codes."""
        from fws.protocol.error_codes import ERROR_CODES
        assert "cannot be reached" in ERROR_CODES[112].description
        assert "linear command point" in ERROR_CODES[74].description.lower()

    def test_unknown_code_admits_it(self, client):
        d = client.get("/api/v1/errors/codes/99999").json()
        assert d["known"] is False
        assert d["description"] is None
        assert "no manual is published" in d["note"]

    def test_current_fault_is_decoded(self, client, fake):
        fake.latch_fault(14, 0)
        d = client.get("/api/v1/errors").json()
        assert d["faulted"] is True
        assert d["main"]["description"]
        assert d["raw"]["main"] == 14

    def test_table_is_searchable(self, client):
        d = client.get("/api/v1/errors/codes?q=limit").json()
        assert d["matched"] > 5
        assert "caveat" in d

    def test_state_carries_the_explanation(self, client, fake):
        fake.latch_fault(112, 0)
        d = client.get("/api/v1/robot/state").json()
        assert d["fault"]["explain"]["description"]


class TestFrameWrites:
    """Defining tool and work-object frames."""

    OFFSET: ClassVar[list[float]] = [0.0, 0.0, 120.0, 0.0, 0.0, 0.0]

    def test_tool_frame_requires_confirmation(self, client, fake):
        r = client.put("/api/v1/frames/tool/1", json={"offset": self.OFFSET})
        assert r.status_code == 400
        assert "silently" in r.json()["detail"]
        assert "SetToolCoord" not in [c[0] for c in fake.calls]

    def test_tool_frame_sends_the_full_wire_signature(self, client, fake):
        """SetToolCoord sends the full six-argument wire signature."""
        r = client.put("/api/v1/frames/tool/2",
                       json={"offset": self.OFFSET, "confirm": True})
        assert r.status_code == 200
        _, args = next(c for c in reversed(fake.calls)
                       if c[0] == "SetToolCoord")
        assert len(args) == 6, "SetToolCoord takes six arguments"
        assert args[0] == 2
        assert list(args[1]) == self.OFFSET
        assert fake.state.tool_frames[2] == self.OFFSET

    def test_id_ranges_differ_between_frame_kinds(self, client):
        """Tool frames are 1-15; work object frames are 0-14."""
        r = client.put("/api/v1/frames/tool/0",
                       json={"offset": self.OFFSET, "confirm": True})
        assert r.status_code == 422
        assert "1-15" in r.json()["detail"]

        r = client.put("/api/v1/frames/tool/16",
                       json={"offset": self.OFFSET, "confirm": True})
        assert r.status_code == 422

        # 0 IS valid for a work object frame.
        r = client.put("/api/v1/frames/work/0",
                       json={"offset": self.OFFSET, "confirm": True})
        assert r.status_code == 200

        r = client.put("/api/v1/frames/work/15",
                       json={"offset": self.OFFSET, "confirm": True})
        assert r.status_code == 422

    def test_offset_must_be_six_elements(self, client):
        r = client.put("/api/v1/frames/tool/1",
                       json={"offset": [0.0, 0.0, 50.0], "confirm": True})
        assert r.status_code == 422

    def test_work_frame_sends_ref_frame(self, client, fake):
        r = client.put("/api/v1/frames/work/3",
                       json={"offset": self.OFFSET, "ref_frame": 2,
                             "confirm": True})
        assert r.status_code == 200
        _, args = next(c for c in reversed(fake.calls)
                       if c[0] == "SetWObjCoord")
        assert len(args) == 3
        assert args[2] == 2

    def test_frame_writes_take_the_config_lock(self, client):
        client.post("/api/v1/control",
                    json={"client_id": "other", "domains": ["config"]})
        r = client.put("/api/v1/frames/tool/1",
                       json={"offset": self.OFFSET, "confirm": True})
        assert r.status_code == 428

    def test_writes_are_audited(self, client):
        client.put("/api/v1/frames/tool/4",
                   json={"offset": self.OFFSET, "confirm": True})
        actions = [e["action"] for e in
                   client.get("/api/v1/events").json()["events"]]
        assert "frames.tool" in actions


class TestRecoveredRPCs:
    """Get* commands classified local but answerable over RPC are recovered."""

    def test_the_measured_set_is_recorded_with_its_call_shape(self):
        from fws.protocol.recovered_rpcs import RECOVERED
        assert len(RECOVERED) == 19
        for name, r in RECOVERED.items():
            assert r.arity in (0, 1), name
            assert r.returns >= 1, name
            assert r.describes, f"{name} has no description"

    def test_absence_is_recorded_too_including_the_two_that_matter(self):
        """E-stop state RPCs are recorded as absent on this firmware."""
        from fws.protocol.recovered_rpcs import ABSENT
        assert "GetRobotEmergencyStopState" in ABSENT
        assert "GetSafetyStopState" in ABSENT

    def test_no_name_is_both_present_and_absent(self):
        from fws.protocol.recovered_rpcs import ABSENT, RECOVERED
        assert not set(RECOVERED) & set(ABSENT)

    def test_every_recovered_name_is_local_in_the_registry(self):
        """Every recovered name is classified local in the registry."""
        from fws.protocol.commands import COMMANDS
        from fws.protocol.recovered_rpcs import RECOVERED
        for name in RECOVERED:
            assert COMMANDS[name].kind == "local", (
                f"{name} is now {COMMANDS[name].kind}; drop it from RECOVERED")

    def test_velocity_reports_actual_and_commanded(self, client):
        """Velocity reports both actual and commanded."""
        d = client.get("/api/v1/robot/velocity").json()
        assert d["joint_deg_s"] is not None
        assert "tcp_actual" in d and "tcp_commanded" in d
        assert "linear" in d["composite_units"]

    def test_flange_is_distinct_from_the_tcp(self, client):
        """The TCP moves when the tool frame changes; the flange does not."""
        flange = client.get("/api/v1/robot/pose/flange").json()["flange"]
        assert len(flange) == 6
        assert "before the tool transform" in \
            client.get("/api/v1/robot/pose/flange").json()["note"]

    def test_active_frames_are_reported(self, client):
        d = client.get("/api/v1/robot/frames/active").json()
        assert set(d) == {"tool", "work"}

    def test_the_motion_queue_is_readable(self, client):
        d = client.get("/api/v1/motion/queue").json()
        assert isinstance(d["queued"], int)

    def test_gripper_absence_is_inferred_and_said_so(self, client):
        """An absent gripper is inferred and the response says so."""
        d = client.get("/api/v1/gripper").json()
        assert d["fitted"] is False
        assert "inferred" in d["note"]
