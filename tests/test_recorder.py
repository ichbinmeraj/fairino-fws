"""The flight recorder: what the arm was doing before the fault.

The audit trail says what was COMMANDED. It does not say where the arm
actually was, how fast, or what the wrist felt -- evidence that existed for
a tenth of a second and was then gone. On firmware nobody documents, that is
the difference between diagnosing a fault and guessing at it.

These tests pin the properties that make it trustworthy: the dump captures
the seconds BEFORE the fault (not after), recording never takes the gateway
down when the disk fills, and a name from a URL cannot escape the directory.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod
from fws.recorder import FlightRecorder


def _client(fake, tmp_path):
    app_mod.create_app(config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
        "server.data_dir": str(tmp_path),
    }))
    return TestClient(app_mod.app)


def _frame(j0=0.0):
    return {"joints": [j0, -90, 90, -90, -90, 0],
            "tcp": [495, 0, -273, -90, -90, 0],
            "ft": [0, 0, 0, 0, 0, 0],
            "joint_torque": [0, 0, 0, 0, 0, 0]}


class TestTheRollingWindow:
    def test_it_keeps_the_last_n_seconds_and_no_more(self, tmp_path):
        rec = FlightRecorder(tmp_path, seconds=1.0)     # 10 frames at 10 Hz
        for i in range(50):
            rec.feed(_frame(i))
        assert rec.health()["buffered_frames"] == 10

    def test_a_frame_with_no_pose_is_not_recorded(self, tmp_path):
        """Before the stream delivers, there is nothing worth keeping."""
        rec = FlightRecorder(tmp_path)
        rec.feed({"connected": False})
        assert rec.health()["buffered_frames"] == 0

    def test_the_dump_holds_what_came_BEFORE(self, tmp_path):
        """The whole point: at the moment a fault lands, the useful history
        is already past. A dump that started recording at the fault would
        capture only the aftermath."""
        rec = FlightRecorder(tmp_path, seconds=5.0)
        for i in range(20):
            rec.feed(_frame(i))
        name = rec.dump("fault 1/22")
        lines = (rec.dir / name).read_text().splitlines()
        meta = json.loads(lines[0])["_meta"]
        assert meta["why"] == "fault 1/22"
        assert meta["frames"] == 20
        first = json.loads(lines[1])
        assert first["joints"][0] == 0, "the OLDEST frame is present"

    def test_dumping_an_empty_buffer_returns_nothing(self, tmp_path):
        assert FlightRecorder(tmp_path).dump("nothing yet") is None


class TestExplicitRecordings:
    def test_start_write_stop_read(self, tmp_path):
        rec = FlightRecorder(tmp_path)
        name = rec.start("repro")
        for i in range(5):
            rec.feed(_frame(i))
        result = rec.stop()
        assert result == {"recording": "repro.jsonl", "frames": 5}
        rows = [json.loads(x) for x in rec.read(name).splitlines() if x]
        assert [r["joints"][0] for r in rows] == [0, 1, 2, 3, 4]

    def test_two_recordings_at_once_are_refused(self, tmp_path):
        rec = FlightRecorder(tmp_path)
        rec.start("one")
        with pytest.raises(RuntimeError, match="already recording"):
            rec.start("two")

    def test_stopping_when_not_recording_is_refused(self, tmp_path):
        with pytest.raises(RuntimeError, match="not recording"):
            FlightRecorder(tmp_path).stop()

    def test_a_name_of_punctuation_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="letters or digits"):
            FlightRecorder(tmp_path).start("///")

    def test_a_failing_sink_stops_recording_without_taking_down_the_gateway(
            self, tmp_path):
        """A full disk must not stop the robot. Recording is diagnostics."""
        rec = FlightRecorder(tmp_path)
        rec.start("doomed")

        class Broken:
            def write(self, _):
                raise OSError("No space left on device")

            def close(self):
                pass

        rec._sink = Broken()
        rec.feed(_frame())                    # must not raise
        health = rec.health()
        assert health["errors"] == 1
        assert health["recording"] is None, "recording stopped cleanly"

    def test_csv_flattens_the_vectors(self, tmp_path):
        rec = FlightRecorder(tmp_path)
        rec.start("csv")
        rec.feed(_frame(7.5))
        rec.stop()
        csv = rec.as_csv("csv.jsonl")
        assert "j1" in csv.splitlines()[0]
        assert "7.5" in csv.splitlines()[1]


class TestNamesFromUrlsCannotEscape:
    @pytest.mark.parametrize("bad", ["../../etc/passwd", "../secret.jsonl",
                                     "nope.txt", "sub/dir.jsonl"])
    def test_a_path_escape_is_a_not_found(self, tmp_path, bad):
        rec = FlightRecorder(tmp_path)
        with pytest.raises(FileNotFoundError):
            rec.read(bad)


class TestOverHttp:
    def test_the_lifecycle_through_the_api(self, fake, tmp_path):
        with _client(fake, tmp_path) as c:
            assert c.post("/api/v1/recordings/start",
                          json={"name": "run1"}).status_code == 201
            time.sleep(0.5)                  # the sampler runs at 10 Hz
            stopped = c.post("/api/v1/recordings/finish")
            assert stopped.status_code == 200
            assert stopped.json()["frames"] > 0, "the sampler fed it"
            names = [r["name"] for r in
                     c.get("/api/v1/recordings").json()["recordings"]]
            assert "run1.jsonl" in names
            assert c.get("/api/v1/recordings/run1.jsonl").status_code == 200
            assert c.get("/api/v1/recordings/run1.jsonl?format=csv"
                         ).status_code == 200
            assert c.delete("/api/v1/recordings/run1.jsonl").status_code == 200

    def test_a_missing_recording_is_a_404(self, fake, tmp_path):
        with _client(fake, tmp_path) as c:
            assert c.get("/api/v1/recordings/nope.jsonl").status_code == 404

    def test_a_second_start_is_a_conflict(self, fake, tmp_path):
        with _client(fake, tmp_path) as c:
            c.post("/api/v1/recordings/start", json={"name": "a"})
            assert c.post("/api/v1/recordings/start",
                          json={"name": "b"}).status_code == 409
            c.post("/api/v1/recordings/finish")

    def test_health_reports_the_recorder(self, fake, tmp_path):
        with _client(fake, tmp_path) as c:
            r = c.get("/api/v1/system/health").json()["recorder"]
            assert r["window_s"] > 0
            assert "recordings" in r["dir"]

    def test_a_fault_dumps_the_window_by_itself(self, fake, tmp_path):
        """Nobody is at a keyboard when the fault lands."""
        with _client(fake, tmp_path) as c:
            time.sleep(0.6)                  # let the ring fill
            fake.trip_fault(1, 22)
            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                dumps = [r["name"] for r in
                         c.get("/api/v1/recordings").json()["recordings"]
                         if r["name"].startswith("fault-")]
                if dumps:
                    body = c.get(f"/api/v1/recordings/{dumps[0]}").text
                    assert "fault 1/22" in body.splitlines()[0]
                    return
                time.sleep(0.2)
            pytest.fail("a latched fault did not dump the flight recorder")
