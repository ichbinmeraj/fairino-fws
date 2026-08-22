"""Program CRUD and execution control."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod

LUA = "-- fws test program, no motion\nlocal x = 1\n"


@pytest.fixture
def client(fake, tmp_path):
    app_mod.create_app(config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
        "server.data_dir": str(tmp_path),
    }))
    with TestClient(app_mod.app) as c:
        app_mod.capabilities.probe()
        app_mod.control._leases.clear()
        yield c
        app_mod.control._leases.clear()


class TestNameValidation:
    """Program names reach a filesystem on an industrial controller."""

    @pytest.mark.parametrize("name", [
        "../../etc/passwd.lua", "a/b.lua", "..\\evil.lua",
    ])
    def test_traversal_is_rejected(self, client, name):
        r = client.put(f"/api/v1/programs/{name}", json={"content": LUA})
        assert r.status_code in (404, 422)

    def test_non_lua_is_rejected(self, client):
        r = client.put("/api/v1/programs/thing.txt", json={"content": LUA})
        assert r.status_code == 422
        assert ".lua" in r.json()["detail"]


class TestCRUD:
    def test_upload_download_roundtrip(self, client):
        r = client.put("/api/v1/programs/t1.lua", json={"content": LUA})
        assert r.status_code == 200
        assert r.json()["bytes"] == len(LUA.encode())

        got = client.get("/api/v1/programs/t1.lua")
        assert got.status_code == 200
        assert got.json()["content"] == LUA

    def test_overwrite_requires_the_flag(self, client):
        client.put("/api/v1/programs/t2.lua", json={"content": LUA})
        r = client.put("/api/v1/programs/t2.lua", json={"content": LUA})
        assert r.status_code == 409
        r2 = client.put("/api/v1/programs/t2.lua",
                        json={"content": LUA, "overwrite": True})
        assert r2.status_code == 200

    def test_listing_is_honest_about_being_incomplete(self, client):
        """GetLuaList is quarantined; the response must say so."""
        client.put("/api/v1/programs/t3.lua", json={"content": LUA})
        d = client.get("/api/v1/programs").json()
        assert d["complete"] is False
        assert d["source"] == "fws-index"
        assert "quarantined" in d["note"]
        assert any(p["name"] == "t3.lua" for p in d["programs"])

    def test_delete(self, client, fake):
        client.put("/api/v1/programs/t4.lua", json={"content": LUA})
        r = client.delete("/api/v1/programs/t4.lua")
        assert r.status_code == 200
        assert "FileDelete" in [c[0] for c in fake.calls], (
            "delete goes through FileDelete(0, name); LuaDelete is a "
            "local SDK wrapper with no wire call")
        assert not any(p["name"] == "t4.lua"
                       for p in client.get("/api/v1/programs").json()["programs"])

    def test_cannot_delete_the_loaded_program(self, client):
        client.put("/api/v1/programs/t5.lua", json={"content": LUA})
        client.post("/api/v1/programs/t5.lua/load")
        r = client.delete("/api/v1/programs/t5.lua")
        assert r.status_code == 409
        assert "currently loaded" in r.json()["detail"]


class TestExecution:
    def _loaded(self, client):
        client.put("/api/v1/programs/run.lua", json={"content": LUA})
        client.post("/api/v1/programs/run.lua/load")

    def test_run_requires_confirmation(self, client, fake):
        self._loaded(client)
        r = client.post("/api/v1/execution/run", json={})
        assert r.status_code == 400
        assert "do not apply" in r.json()["detail"]
        assert "ProgramRun" not in [c[0] for c in fake.calls]

    def test_run_refuses_while_faulted(self, client, fake):
        self._loaded(client)
        fake.latch_fault(1, 22)
        r = client.post("/api/v1/execution/run", json={"confirm": True})
        assert r.status_code == 409
        assert "faulted" in r.json()["detail"]

    def test_run_refuses_with_nothing_loaded(self, client, fake):
        fake.state.loaded_program = ""
        r = client.post("/api/v1/execution/run", json={"confirm": True})
        assert r.status_code == 409
        assert "no program is loaded" in r.json()["detail"]

    def test_confirmed_run_starts_and_is_audited(self, client, fake):
        self._loaded(client)
        r = client.post("/api/v1/execution/run", json={"confirm": True})
        assert r.status_code == 200
        assert fake.state.program_state == 2
        actions = [e["action"] for e in
                   client.get("/api/v1/events").json()["events"]]
        assert "execution.run" in actions

    def test_pause_resume_stop(self, client, fake):
        self._loaded(client)
        client.post("/api/v1/execution/run", json={"confirm": True})
        client.post("/api/v1/execution/pause")
        assert fake.state.program_state == 3
        client.post("/api/v1/execution/resume")
        assert fake.state.program_state == 2
        client.post("/api/v1/execution/stop")
        assert fake.state.program_state == 1

    def test_stop_is_never_lockable_or_confirmable(self, client, fake):
        """A stop that can be refused is not a stop."""
        client.post("/api/v1/control",
                    json={"client_id": "other", "domains": ["motion"]})
        r = client.post("/api/v1/execution/stop")
        assert r.status_code == 200
        assert r.json()["results"]["ProgramStop"] == "ok"

    def test_run_respects_the_control_lock(self, client):
        self._loaded(client)
        client.post("/api/v1/control",
                    json={"client_id": "other", "domains": ["motion"]})
        r = client.post("/api/v1/execution/run", json={"confirm": True})
        assert r.status_code == 428


class TestSelectProgram:
    """Choosing which Lua file is active, and optionally starting it."""

    def test_select_loads_without_starting(self, client, fake):
        client.put("/api/v1/programs/sel.lua", json={"content": LUA})
        r = client.post("/api/v1/programs/sel.lua/select", json={})
        assert r.status_code == 200
        assert r.json() == {"selected": "sel.lua", "started": False}
        assert fake.state.loaded_program.endswith("sel.lua")
        assert fake.state.program_state == 1

    def test_select_and_start_needs_confirmation(self, client, fake):
        """It still loads -- but refuses to start, and says so."""
        client.put("/api/v1/programs/sel2.lua", json={"content": LUA})
        r = client.post("/api/v1/programs/sel2.lua/select",
                        json={"start": True})
        assert r.status_code == 400
        assert "now selected but NOT started" in r.json()["detail"]
        assert fake.state.loaded_program.endswith("sel2.lua")
        assert fake.state.program_state == 1

    def test_select_and_start_confirmed(self, client, fake):
        client.put("/api/v1/programs/sel3.lua", json={"content": LUA})
        r = client.post("/api/v1/programs/sel3.lua/select",
                        json={"start": True, "confirm": True})
        assert r.status_code == 200
        assert r.json()["started"] is True
        assert fake.state.program_state == 2


class TestDeleteReconcilesTheIndex:
    """Deleting an already-absent program reconciles the drifting index."""

    def test_deleting_an_already_absent_program_clears_the_index(
            self, client: TestClient, fake):
        client.put("/api/v1/programs/ghost.lua",
                   json={"content": LUA, "overwrite": True})
        assert any(p["name"] == "ghost.lua"
                   for p in client.get("/api/v1/programs").json()["programs"])

        # Something else removes it -- exactly what a teach pendant would do.
        fake.files.pop("ghost.lua", None)

        r = client.delete("/api/v1/programs/ghost.lua")
        assert r.status_code == 200
        assert r.json()["already_absent_on_controller"] is True
        assert not any(p["name"] == "ghost.lua"
                       for p in client.get("/api/v1/programs").json()["programs"])

    def test_a_normal_delete_does_not_claim_it_was_already_absent(
            self, client: TestClient):
        client.put("/api/v1/programs/real.lua",
                   json={"content": LUA, "overwrite": True})
        r = client.delete("/api/v1/programs/real.lua")
        assert r.status_code == 200
        assert r.json()["already_absent_on_controller"] is False

    def test_other_delete_failures_still_fail(self, client: TestClient,
                                              monkeypatch):
        """144 is reconciled. Every other error is still an error."""
        import fws.programs_api as mod
        from fws.driver import RobotError

        client.put("/api/v1/programs/stuck.lua",
                   json={"content": LUA, "overwrite": True})

        def boom(driver, name):
            raise RobotError("FileDelete(stuck.lua) returned 7")

        monkeypatch.setattr(mod, "delete_lua", boom)
        r = client.delete("/api/v1/programs/stuck.lua")
        assert r.status_code == 502
        assert any(p["name"] == "stuck.lua"
                   for p in client.get("/api/v1/programs").json()["programs"])


class TestRunIsGatedOnValidation:
    """A program that cannot reach its first point must not be started."""

    BAD = ("j1,j2,j3,j4,j5,j6,-802.4,-195.1,505.0,174.3,2.9,7.2"
           + ",0" * 21)
    # rx/ry/rz must map to LEGAL wrist joints (e.g. J4 within [-265, 85]).
    GOOD = ("j1,j2,j3,j4,j5,j6,300.0,0.0,400.0,0.0,0.0,0.0"
            + ",0" * 21)

    def _load(self, client, name, body):
        client.put(f"/api/v1/programs/{name}",
                   json={"content": body, "overwrite": True})
        assert client.post(f"/api/v1/programs/{name}/load").status_code == 200

    def test_an_unreachable_target_blocks_the_run(self, client, fake):
        self._load(client, "bad.lua", f"MoveL({self.BAD})\n")
        # No flag needed: the simulator enforces the same 920 mm envelope the
        # real controller does, and this pose is 968 mm from the base.
        r = client.post("/api/v1/execution/run", json={"confirm": True})
        assert r.status_code == 409
        d = r.json()["detail"]
        assert "was NOT started" in d["message"]
        assert d["failures"]
        assert "ProgramRun" not in [c[0] for c in fake.calls], (
            "the program must not have been started")

    def test_the_override_exists_and_is_audited(self, client, fake):
        """The validation override exists and is audited."""
        self._load(client, "bad.lua", f"MoveL({self.BAD})\n")
        r = client.post("/api/v1/execution/run",
                        json={"confirm": True, "skip_validation": True,
                              "validation_note": "point names, checked by hand"})
        assert r.status_code == 200
        assert r.json()["validation"]["skipped"] is True

    def test_a_valid_program_runs(self, client, fake):
        self._load(client, "good.lua", f"MoveL({self.GOOD})\n")
        r = client.post("/api/v1/execution/run", json={"confirm": True})
        assert r.status_code == 200
        assert r.json()["running"] is True
        assert r.json()["validation"]["safe_to_run"] is True

    def test_confirm_is_still_required_first(self, client):
        self._load(client, "good.lua", f"MoveL({self.GOOD})\n")
        assert client.post("/api/v1/execution/run", json={}).status_code == 400

    def test_validate_endpoint_starts_nothing(self, client, fake):
        self._load(client, "good.lua", f"MoveL({self.GOOD})\n")
        r = client.post("/api/v1/programs/good.lua/validate")
        assert r.status_code == 200
        assert "safe_to_run" in r.json()
        assert "ProgramRun" not in [c[0] for c in fake.calls]


class TestUploadRefusedWhileAProgramRuns:
    """Uploading into a running program stops it mid-move on v3.8.5.1, and
    an upload that landed in the half-stopped state after a watchdog stop
    wedged the controller until a reboot (2026-08-19)."""

    def test_running_program_blocks_upload(self, client, fake):
        fake.state.program_state = 2
        r = client.put("/api/v1/programs/RAW_x.lua",
                       json={"content": LUA, "overwrite": True})
        assert r.status_code == 409, r.text
        assert "running" in r.json()["detail"]

    def test_paused_program_blocks_upload(self, client, fake):
        fake.state.program_state = 3
        r = client.put("/api/v1/programs/RAW_x.lua",
                       json={"content": LUA, "overwrite": True})
        assert r.status_code == 409

    def test_stopped_program_allows_upload(self, client, fake):
        fake.state.program_state = 1
        r = client.put("/api/v1/programs/RAW_x.lua",
                       json={"content": LUA, "overwrite": True})
        assert r.status_code in (200, 201), r.text
