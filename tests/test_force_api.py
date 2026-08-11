"""Force sensing and sensor setup."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod
from fws.protocol.commands import COMMANDS


@pytest.fixture
def client(fake):
    settings = config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
    })
    app_mod.create_app(settings)
    with TestClient(app_mod.app) as c:
        yield c


class TestSensing:
    def test_force_is_labelled_NOT_gravity_compensated(self, client: TestClient):
        """Force is labelled NOT gravity-compensated."""
        d = client.get("/api/v1/sensors/force").json()
        assert d["gravity_compensated"] is False
        assert "compensated" not in d, (
            "the old, wrong key must not linger -- it read as True")
        assert "IN these numbers" in d["what_this_is"]
        assert "FT_Control" in d["if_you_need_gravity_removed"]
        assert d["raw_available"] is False

    def test_force_reports_magnitude_and_both_triples(self, client: TestClient):
        d = client.get("/api/v1/sensors/force").json()
        assert set(d["force_n"]) == {"fx", "fy", "fz"}
        assert set(d["torque_nm"]) == {"tx", "ty", "tz"}
        f = d["force_n"]
        expect = (f["fx"] ** 2 + f["fy"] ** 2 + f["fz"] ** 2) ** 0.5
        assert d["magnitude_n"] == pytest.approx(expect, abs=1e-3)

    def test_joint_torques_are_named_per_joint(self, client: TestClient):
        d = client.get("/api/v1/sensors/joint_torques").json()
        assert set(d["joint_torque_nm"]) == {f"j{i}" for i in range(1, 7)}

    def test_joint_torques_are_newton_metres_not_milli(self, client: TestClient):
        """Joint torques are converted from milli-N·m to N·m."""
        d = client.get("/api/v1/sensors/joint_torques").json()
        assert all(abs(v) < 50.0 for v in d["joint_torque_nm"].values())


class TestPayload:
    def test_both_payload_settings_are_reported(self, client: TestClient):
        """Both payload settings are reported independently."""
        d = client.get("/api/v1/force/payload").json()
        assert "sensor_payload_kg" in d
        assert "robot_payload_kg" in d
        assert "does not set the other" in d["note"]

    def test_setting_payload_needs_confirmation(self, client: TestClient):
        r = client.put("/api/v1/force/payload", json={"mass_kg": 1.5})
        assert r.status_code == 422
        assert "confirm" in r.json()["detail"]

    def test_confirmed_payload_sets_mass_and_cog(self, client: TestClient, fake):
        r = client.put("/api/v1/force/payload",
                       json={"mass_kg": 1.5, "cog_mm": [1.0, 2.0, 3.0],
                             "confirm": True})
        assert r.status_code == 200
        called = [c[0] for c in fake.calls]
        assert "SetForceSensorPayload" in called
        assert "SetForceSensorPayloadCog" in called

    def test_cog_is_optional(self, client: TestClient, fake):
        r = client.put("/api/v1/force/payload",
                       json={"mass_kg": 0.5, "confirm": True})
        assert r.status_code == 200
        assert "SetForceSensorPayloadCog" not in [c[0] for c in fake.calls]

    def test_negative_mass_is_rejected_before_the_wire(self, client: TestClient,
                                                      fake):
        r = client.put("/api/v1/force/payload",
                       json={"mass_kg": -1.0, "confirm": True})
        assert r.status_code == 422
        # Name the command rather than counting calls: the capability probe
        # runs in the background and writes into fake.calls, so any total is
        # a race.
        assert "SetForceSensorPayload" not in [c[0] for c in fake.calls]


class TestZeroAndActivate:
    def test_zero_needs_confirmation(self, client: TestClient):
        r = client.post("/api/v1/force/zero", json={})
        assert r.status_code == 422

    def test_zero_warns_what_it_baked_in(self, client: TestClient, fake):
        r = client.post("/api/v1/force/zero", json={"confirm": True})
        assert r.status_code == 200
        assert "contact state" in r.json()["warning"]
        assert "FT_SetZero" in [c[0] for c in fake.calls]

    def test_deactivating_needs_confirmation(self, client: TestClient):
        r = client.post("/api/v1/force/activate", json={"state": 0})
        assert r.status_code == 422

    def test_activate_passes_the_state_through(self, client: TestClient, fake):
        r = client.post("/api/v1/force/activate",
                        json={"state": 1, "confirm": True})
        assert r.status_code == 200
        assert r.json()["active"] is True
        assert ("FT_Activate", (1,)) in [(c[0], tuple(c[1])) for c in fake.calls]


class TestStrategyBoundary:
    """There is no POST /force/insert, on purpose. The endpoint says why."""

    def test_the_boundary_is_documented_not_just_absent(self, client: TestClient):
        d = client.get("/api/v1/force/strategies").json()
        assert "run on the controller" in d["principle"]
        assert d["strategies_in_lua"]
        assert d["setup_here"]

    def test_the_refusals_are_carried_through_to_the_api(self, client: TestClient):
        d = client.get("/api/v1/force/strategies").json()
        assert set(d["refused_for_generation"]) == {"FT_Control", "FT_Guard"}

    def test_the_argument_order_conflict_is_reachable_here(self, client: TestClient):
        d = client.get("/api/v1/force/strategies").json()
        c = d["argument_order_conflicts"]["FT_SpiralSearch"]
        assert c["rpc_order"] != c["lua_order"]

    def test_no_endpoint_starts_a_force_strategy(self, client: TestClient):
        """A force strategy from outside is an unsynchronised motion command."""
        paths = client.get("/openapi.json").json()["paths"]
        for p in paths:
            assert "insert" not in p
            assert "compliance" not in p


class TestClassification:
    """Composite detection classifies multi-move routines correctly."""

    def test_the_auto_load_routine_is_not_callable_as_one_command(self):
        c = COMMANDS["ForceSensorAutoComputeLoad"]
        assert c.kind == "composite"
        assert not c.callable_directly
        assert "MoveJ" in c.wire_sequence

    def test_a_safety_check_does_not_make_a_command_composite(self):
        """A leading safety check does not make a command composite."""
        for n in ("StartJOG", "ImmStopJOG", "StopMotion", "SetAO",
                  "ProgramRun", "GetActualTCPPose"):
            assert COMMANDS[n].kind == "simple", n

    def test_the_calibration_routine_that_runs_a_program_is_composite(self):
        c = COMMANDS["PhotoelectricSensorTCPCalibration"]
        assert c.kind == "composite"
        assert "ProgramRun" in c.wire_sequence


class TestPayloadMismatchIsSurfaced:
    """A force-compensation payload mismatch is surfaced, not left silent."""

    def test_agreeing_payloads_report_no_mismatch(self, client, fake):
        fake.state.ft_payload_kg = 0.5
        fake.state.payload_kg = 0.5
        assert client.get("/api/v1/force/payload").json()["mismatch"] is None

    def test_a_disagreement_is_reported_with_its_consequence(self, client, fake):
        fake.state.ft_payload_kg = 0.0
        fake.state.payload_kg = 0.347
        m = client.get("/api/v1/force/payload").json()["mismatch"]
        assert m is not None
        assert m["difference_kg"] == pytest.approx(0.347, abs=1e-3)
        assert "FT_Control" in m["consequence"]
        assert "hanging free" in m["how_to_fix"]

    def test_the_fix_does_not_say_just_copy_the_robot_payload(self, client,
                                                             fake):
        """The fix does not just say to copy the robot payload."""
        fake.state.ft_payload_kg, fake.state.payload_kg = 0.0, 0.347
        m = client.get("/api/v1/force/payload").json()["mismatch"]
        assert "BELOW the sensor" in m["how_to_fix"]

    def test_a_small_difference_is_not_flagged(self, client, fake):
        """Rounding between two settings is not a fault."""
        fake.state.ft_payload_kg, fake.state.payload_kg = 0.500, 0.510
        assert client.get("/api/v1/force/payload").json()["mismatch"] is None
