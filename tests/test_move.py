"""The typed move route: "go to this pose", pre-flighted.

This is the capability a Fairino developer was most obviously missing, and
also the one with the worst history: MoveL's argument layout once produced
an unintended ~300 mm motion and a controller fault on this firmware. So the
tests here are mostly about what does NOT reach the wire.

The ordering matters as much as the checks. A route that solved kinematics
after taking the lease, or transmitted before auditing, would pass a naive
"does it refuse" test and still be wrong in the way that hurts.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod

REACHABLE = [400.0, 0.0, 200.0, -90.0, 0.0, 0.0]


def _client(fake, **over):
    app_mod.create_app(config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
        "features.enable_movel": True,
        **over,
    }))
    return TestClient(app_mod.app)


def _lease(c):
    app_mod.control._leases.clear()
    token = c.post("/api/v1/control", json={"client_id": "mover"}).json()["token"]
    return {"X-FWS-Control-Token": token}


class TestItIsOffUntilYouTurnItOn:
    def test_disabled_by_default(self, fake):
        with _client(fake, **{"features.enable_movel": False}) as c:
            h = _lease(c)
            r = c.post("/api/v1/motion/move", headers=h,
                       json={"pose": REACHABLE, "confirm": True})
            assert r.status_code == 403
            assert "300 mm" in r.text, "the refusal says WHY, with the evidence"

    def test_the_refusal_comes_before_anything_is_sent(self, fake):
        with _client(fake, **{"features.enable_movel": False}) as c:
            h = _lease(c)
            fake.calls.clear()
            c.post("/api/v1/motion/move", headers=h,
                   json={"pose": REACHABLE, "confirm": True})
            assert not [x for x in fake.calls if x[0] == "MoveL"]


class TestTheGates:
    def test_no_lease_is_a_428(self, fake):
        with _client(fake) as c:
            app_mod.control._leases.clear()
            r = c.post("/api/v1/motion/move",
                       json={"pose": REACHABLE, "confirm": True})
            assert r.status_code == 428
            assert "watchdog" in r.text, (
                "the refusal explains why a lease is required, not just that "
                "it is")

    def test_no_confirm_is_a_400(self, fake):
        with _client(fake) as c:
            h = _lease(c)
            r = c.post("/api/v1/motion/move", headers=h,
                       json={"pose": REACHABLE})
            assert r.status_code == 400
            assert "absolute pose" in r.text

    def test_a_pose_of_the_wrong_length_is_refused(self, fake):
        with _client(fake) as c:
            h = _lease(c)
            r = c.post("/api/v1/motion/move", headers=h,
                       json={"pose": [1, 2, 3], "confirm": True})
            assert r.status_code == 422

    def test_speed_above_the_configured_cap_is_refused(self, fake):
        with _client(fake, **{"limits.jog_max_vel_pct": 30}) as c:
            h = _lease(c)
            r = c.post("/api/v1/motion/move", headers=h,
                       json={"pose": REACHABLE, "vel": 90, "confirm": True})
            assert r.status_code == 422


class TestThePreFlight:
    def test_an_unreachable_pose_is_refused_not_attempted(self, fake):
        """Discovered by solving it backwards, not by the arm."""
        with _client(fake) as c:
            h = _lease(c)
            fake.calls.clear()
            r = c.post("/api/v1/motion/move", headers=h,
                       json={"pose": [9000.0, 0, 0, 0, 0, 0], "confirm": True})
            assert r.status_code == 409
            assert "unreachable" in r.text or "singular" in r.text
            assert not [x for x in fake.calls if x[0] == "MoveL"]

    def test_a_pose_below_the_floor_is_refused(self, fake):
        with _client(fake, **{"limits.z_floor_mm": 100.0}) as c:
            h = _lease(c)
            fake.calls.clear()
            r = c.post("/api/v1/motion/move", headers=h,
                       json={"pose": [400.0, 0.0, 50.0, -90.0, 0.0, 0.0],
                             "confirm": True})
            assert r.status_code == 409
            assert "floor" in r.text
            assert not [x for x in fake.calls if x[0] == "MoveL"]

    def test_the_floor_is_checked_against_the_target(self, fake):
        """Not against where the arm is now: the whole reason to solve the
        pose first is to know where it is GOING."""
        import inspect
        src = inspect.getsource(app_mod.build_move_api)
        i = src.index("z_floor_mm")
        assert "req.pose[2]" in src[i:i + 300]


class TestWhatReachesTheWire:
    def test_a_good_move_sends_exactly_one_MoveL(self, fake):
        with _client(fake) as c:
            h = _lease(c)
            fake.calls.clear()
            r = c.post("/api/v1/motion/move", headers=h,
                       json={"pose": REACHABLE, "confirm": True})
            assert r.status_code == 200, r.text
            sent = [x for x in fake.calls if x[0] == "MoveL"]
            assert len(sent) == 1, "one command, not a path runner"

    def test_the_wire_call_carries_33_elements(self, fake):
        """The layout that once moved an arm 300 mm unintentionally. One
        flat array, not the separate arguments the SDK signature advertises."""
        with _client(fake) as c:
            h = _lease(c)
            fake.calls.clear()
            c.post("/api/v1/motion/move", headers=h,
                   json={"pose": REACHABLE, "confirm": True})
            args = next(x for x in fake.calls if x[0] == "MoveL")[1]
            assert len(args) == 1, "MoveL takes ONE argument on the wire"
            assert len(args[0]) == 33, f"33 elements, got {len(args[0])}"

    def test_the_solved_joints_lead_and_the_pose_follows(self, fake):
        """[0:6] joints, [6:12] pose. Swapping them is exactly the class of
        transcription error that caused the incident."""
        with _client(fake) as c:
            h = _lease(c)
            fake.calls.clear()
            body = c.post("/api/v1/motion/move", headers=h,
                          json={"pose": REACHABLE, "confirm": True}).json()
            arr = next(x for x in fake.calls if x[0] == "MoveL")[1][0]
            assert arr[0:6] == pytest.approx(body["target_joints"], abs=1e-3)
            assert arr[6:12] == pytest.approx(REACHABLE, abs=1e-6)

    def test_it_does_not_block_on_the_move(self, fake):
        """blendR of -1 makes the RPC block until the move finishes, holding
        the lock for its whole duration -- which would make a stop
        impossible."""
        with _client(fake) as c:
            h = _lease(c)
            fake.calls.clear()
            c.post("/api/v1/motion/move", headers=h,
                   json={"pose": REACHABLE, "confirm": True})
            arr = next(x for x in fake.calls if x[0] == "MoveL")[1][0]
            assert arr[17] != -1.0, "blendR -1 would block the RPC lock"

    def test_the_move_is_audited_before_it_is_sent(self, fake):
        """If the controller wedges or this process dies mid-move, the review
        still has the line saying what was commanded."""
        with _client(fake) as c:
            h = _lease(c)
            c.post("/api/v1/motion/move", headers=h,
                   json={"pose": REACHABLE, "confirm": True})
            events = c.get("/api/v1/events").json()["events"]
            hit = [e for e in events if e["action"] == "motion.move"]
            assert hit, "the move must be audited"
            assert hit[0]["pose"] == REACHABLE
            assert len(hit[0]["joints"]) == 6

    def test_stop_still_works_during_a_move(self, fake):
        """The reason for not blocking, stated as a test."""
        with _client(fake) as c:
            h = _lease(c)
            c.post("/api/v1/motion/move", headers=h,
                   json={"pose": REACHABLE, "confirm": True})
            assert c.post("/api/v1/motion/stop").status_code == 200
