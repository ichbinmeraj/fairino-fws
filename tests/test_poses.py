"""Named poses stored by the gateway.

A taught point is production data. Before this it lived in one browser's
localStorage, so it died with a profile, could not be reviewed, could not be
backed up, and no API client or CI job could see it. These tests pin the
properties that make it data instead: it survives a restart, it is written
atomically, both representations are captured together so they cannot
disagree, and the program generated from it uses literal targets so the
gateway's own pre-flight still works.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod
from fws.poses import Pose, PoseError, PoseStore


def _client(fake, tmp_path, **over):
    app_mod.create_app(config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
        "server.data_dir": str(tmp_path),
        **over,
    }))
    return TestClient(app_mod.app)


class TestTheStoreItself:
    def test_a_pose_survives_a_restart(self, tmp_path):
        """The whole point: it is on disk, not in a browser."""
        path = tmp_path / "poses.json"
        a = PoseStore(path)
        a.save(Pose("pick", [1, 2, 3, 4, 5, 6], [10, 20, 30, 0, 0, 0]))
        b = PoseStore(path)                       # a fresh process
        assert b.get("pick").joints == [1, 2, 3, 4, 5, 6]

    def test_the_write_is_atomic(self, tmp_path):
        """A crash mid-write must not leave a truncated file where the taught
        points used to be, so the real file is only ever replaced whole."""
        path = tmp_path / "poses.json"
        s = PoseStore(path)
        s.save(Pose("a", [0] * 6, [0] * 6))
        s.save(Pose("b", [1] * 6, [1] * 6))
        assert json.loads(path.read_text())["version"] == 1
        assert not list(tmp_path.glob(".poses-*.tmp")), "no temp file left"

    def test_a_corrupt_store_starts_empty_and_says_so(self, tmp_path):
        """Refusing to boot over this file would take the gateway down for
        something nothing else needs; losing it silently would look like
        someone deleted the points."""
        path = tmp_path / "poses.json"
        path.write_text("{not json")
        s = PoseStore(path)
        assert s.list() == []
        assert s.load_error
        assert path.read_text() == "{not json", "the bad file is kept"

    def test_one_bad_row_does_not_lose_the_others(self, tmp_path):
        path = tmp_path / "poses.json"
        path.write_text(json.dumps({"version": 1, "poses": [
            {"name": "good", "joints": [0] * 6, "tcp": [0] * 6},
            {"name": "bad"},
        ]}))
        s = PoseStore(path)
        assert [p.name for p in s.list()] == ["good"]

    def test_overwrite_is_refused_by_default(self, tmp_path):
        s = PoseStore(tmp_path / "poses.json")
        s.save(Pose("p", [0] * 6, [0] * 6))
        with pytest.raises(PoseError, match="already exists"):
            s.save(Pose("p", [9] * 6, [9] * 6))
        s.save(Pose("p", [9] * 6, [9] * 6), overwrite=True)
        assert s.get("p").joints == [9] * 6

    @pytest.mark.parametrize("bad", ["", "1leading", "has space", "x" * 65,
                                     "semi;colon", "../escape"])
    def test_names_that_would_break_a_program_or_a_path(self, tmp_path, bad):
        """These names end up in generated Lua and in file paths."""
        s = PoseStore(tmp_path / "poses.json")
        with pytest.raises(PoseError):
            s.save(Pose(bad, [0] * 6, [0] * 6))

    def test_six_numbers_or_a_refusal(self, tmp_path):
        s = PoseStore(tmp_path / "poses.json")
        with pytest.raises(PoseError, match="six"):
            s.save(Pose("p", [0, 0], [0] * 6))
        with pytest.raises(PoseError, match="six"):
            s.save(Pose("p", [0] * 6, [0] * 3))


class TestCapturingFromTheLiveArm:
    def test_capture_records_where_the_arm_is(self, fake, tmp_path):
        with _client(fake, tmp_path) as c:
            fake.set_joints([5.0, -85.0, 85.0, -90.0, -90.0, 1.0])
            import time
            time.sleep(0.4)             # let a frame arrive
            r = c.post("/api/v1/poses/home/capture", json={})
            assert r.status_code == 201, r.text
            body = r.json()
            assert abs(body["joints"][0] - 5.0) < 0.01
            assert len(body["tcp"]) == 6

    def test_joints_and_tcp_come_from_one_frame(self, fake, tmp_path):
        """Two reads could straddle a move and store a pose whose halves
        describe different positions -- a silent, dangerous inconsistency."""
        import inspect
        src = inspect.getsource(app_mod.build_poses_api)
        i = src.index("def capture")
        body = src[i:i + 1600]
        assert body.count("snapshot()") == 1, (
            "capture must take ONE telemetry snapshot")

    def test_capture_refuses_a_stale_pose(self, fake, tmp_path):
        """A stale frame is WORSE than none: the snapshot keeps its last
        values when the stream drops, so capturing without checking age
        records where the arm WAS. If it was moved from the pendant
        meanwhile, a later move to this "taught" point goes somewhere nobody
        chose."""
        import time
        with _client(fake, tmp_path) as c:
            app_mod.telemetry.close()
            time.sleep(1.2)             # older than STALE_AFTER_S
            r = c.post("/api/v1/poses/nowhere/capture", json={})
            assert r.status_code == 503
            assert "stale" in r.text

    def test_capture_refuses_when_no_frame_ever_arrived(self, fake, tmp_path,
                                                        monkeypatch):
        """Substituting the snapshot rather than stopping the stream: a live
        reader thread refills the state, which made this race the suite."""
        with _client(fake, tmp_path) as c:
            monkeypatch.setattr(app_mod.telemetry, "snapshot", dict)
            r = c.post("/api/v1/poses/nowhere/capture", json={})
            assert r.status_code == 503
            assert "nothing to capture" in r.text

    def test_capturing_over_a_name_is_refused_without_overwrite(
            self, fake, tmp_path):
        with _client(fake, tmp_path) as c:
            assert c.post("/api/v1/poses/p/capture",
                          json={}).status_code == 201
            assert c.post("/api/v1/poses/p/capture",
                          json={}).status_code == 409
            assert c.post("/api/v1/poses/p/capture",
                          json={"overwrite": True}).status_code == 201


class TestTheHttpSurface:
    def test_list_read_rename_delete(self, fake, tmp_path):
        with _client(fake, tmp_path) as c:
            c.put("/api/v1/poses/a",
                  json={"joints": [0] * 6, "tcp": [1] * 6, "note": "start"})
            assert [p["name"] for p in
                    c.get("/api/v1/poses").json()["poses"]] == ["a"]
            assert c.get("/api/v1/poses/a").json()["note"] == "start"
            assert c.post("/api/v1/poses/a/rename",
                          json={"to": "b"}).status_code == 200
            assert c.get("/api/v1/poses/a").status_code == 404
            assert c.delete("/api/v1/poses/b").status_code == 200
            assert c.get("/api/v1/poses").json()["poses"] == []

    def test_the_listing_says_what_these_are_not(self, fake, tmp_path):
        """Confusing these with the controller's point tables is the obvious
        mistake, so the API says so where a reader will see it."""
        with _client(fake, tmp_path) as c:
            note = c.get("/api/v1/poses").json()["note"]
            assert "point table" in note

    def test_writes_are_audited(self, fake, tmp_path):
        with _client(fake, tmp_path) as c:
            c.put("/api/v1/poses/a", json={"joints": [0] * 6, "tcp": [0] * 6})
            c.delete("/api/v1/poses/a")
            actions = [e["action"] for e in
                       c.get("/api/v1/events").json()["events"]]
            assert "poses.write" in actions
            assert "poses.delete" in actions


class TestGeneratingAProgram:
    def _two_poses(self, c):
        c.put("/api/v1/poses/p1", json={
            "joints": [0, -90, 90, -90, -90, 0], "tcp": [495, 0, -273, -90, -90, 0]})
        c.put("/api/v1/poses/p2", json={
            "joints": [10, -85, 85, -90, -90, 0], "tcp": [480, 90, -240, -90, -90, 0]})

    def test_it_generates_movej_with_the_probed_arity(self, fake, tmp_path):
        """29 flat arguments on this firmware, PROBED. The controller does
        not ignore a wrong count safely, which is the whole reason this is
        generated rather than left to the caller."""
        with _client(fake, tmp_path) as c:
            self._two_poses(c)
            src = c.post("/api/v1/poses/program",
                         json={"poses": ["p1", "p2"]}).json()["source"]
            calls = [ln for ln in src.splitlines() if ln.startswith("MoveJ(")]
            assert len(calls) == 2
            for call in calls:
                assert call.count(",") + 1 == 29

    def test_targets_are_literal_so_the_pre_flight_still_works(
            self, fake, tmp_path):
        """A program referring to point-table names cannot be solved backwards
        before it runs. Literal joint values can."""
        with _client(fake, tmp_path) as c:
            self._two_poses(c)
            src = c.post("/api/v1/poses/program",
                         json={"poses": ["p1", "p2"]}).json()["source"]
            assert "0.000, -90.000" in src

    def test_the_generated_program_actually_compiles(self, fake, tmp_path):
        """The strongest available check without hardware: hand it to the
        simulator's Lua compiler, which rejects what this firmware rejects."""
        with _client(fake, tmp_path) as c:
            self._two_poses(c)
            src = c.post("/api/v1/poses/program",
                         json={"poses": ["p1", "p2"], "dwell_ms": 100},
                         ).json()["source"]
            assert fake.compile_lua("gen.lua", src.encode()) == "success"

    def test_generating_does_not_upload_or_run(self, fake, tmp_path):
        """Generating is safe; running moves the arm. They stay separate
        calls with separate gates."""
        with _client(fake, tmp_path) as c:
            self._two_poses(c)
            before = list(fake.files)
            c.post("/api/v1/poses/program", json={"poses": ["p1"]})
            assert list(fake.files) == before

    def test_an_unknown_pose_is_a_404_not_a_broken_program(
            self, fake, tmp_path):
        with _client(fake, tmp_path) as c:
            self._two_poses(c)
            r = c.post("/api/v1/poses/program",
                       json={"poses": ["p1", "nope"]})
            assert r.status_code == 404
            assert "nope" in r.text
