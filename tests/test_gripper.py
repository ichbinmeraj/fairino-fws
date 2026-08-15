"""The typed gripper route.

Open-and-close is a top-five request for any cobot gateway, and reaching it
used to mean POST /invoke/MoveGripper with a ten-argument list in wire order
and no bounds on any of them -- one of which is how hard it squeezes.

The honest position, which these tests pin: every argument is bounded, the
wire order is the documented one, a gripper that is not fitted refuses
instead of silently accepting, and the response says plainly that the call
is unverified on this firmware. Refusing to implement it helps nobody;
implying it is proven would be worse.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod


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


def _lease(c):
    app_mod.control._leases.clear()
    tok = c.post("/api/v1/control", json={"client_id": "g"}).json()["token"]
    return {"X-FWS-Control-Token": tok}


class TestTheGates:
    def test_no_lease_is_a_428(self, fake):
        with _client(fake) as c:
            app_mod.control._leases.clear()
            r = c.post("/api/v1/gripper/command",
                       json={"position": 50, "confirm": True})
            assert r.status_code == 428
            assert "gripper is motion" in r.text

    def test_no_confirm_is_a_400(self, fake):
        with _client(fake) as c:
            h = _lease(c)
            r = c.post("/api/v1/gripper/command", headers=h,
                       json={"position": 50})
            assert r.status_code == 400
            assert "a hand" in r.text, (
                "the refusal names the actual consequence")

    @pytest.mark.parametrize("body", [
        {"position": 101}, {"position": -1},
        {"position": 50, "speed": 0}, {"position": 50, "speed": 200},
        {"position": 50, "force": 0}, {"position": 50, "force": 999},
        {"position": 50, "index": 0},
    ])
    def test_out_of_range_arguments_are_refused(self, fake, body):
        """force is how hard it squeezes. A typo must not become a crush."""
        with _client(fake) as c:
            h = _lease(c)
            r = c.post("/api/v1/gripper/command", headers=h,
                       json={**body, "confirm": True})
            assert r.status_code == 422


class TestTheProbeGate:
    def test_a_missing_gripper_refuses_instead_of_doing_nothing(self, fake):
        """This controller answers gripper getters with zeros when none is
        fitted, so a command would be accepted and silently do nothing."""
        with _client(fake) as c:
            h = _lease(c)
            app_mod.capabilities._map["gripper.position"] = type(
                "C", (), {"state": "absent"})()
            fake.calls.clear()
            r = c.post("/api/v1/gripper/command", headers=h,
                       json={"position": 50, "confirm": True})
            assert r.status_code == 409
            assert "no gripper is fitted" in r.text
            assert not [x for x in fake.calls if x[0] == "MoveGripper"]

    def test_the_probe_can_be_overridden(self, fake):
        """The probe can be wrong for a cell; a refusal you cannot get past
        is its own bug."""
        with _client(fake) as c:
            h = _lease(c)
            app_mod.capabilities._map["gripper.position"] = type(
                "C", (), {"state": "absent"})()
            r = c.post("/api/v1/gripper/command?force_probe=false", headers=h,
                       json={"position": 50, "confirm": True})
            assert r.status_code == 200, r.text


class TestWhatReachesTheWire:
    def test_the_documented_ten_argument_order(self, fake):
        with _client(fake) as c:
            h = _lease(c)
            fake.calls.clear()
            r = c.post("/api/v1/gripper/command", headers=h,
                       json={"position": 70, "speed": 40, "force": 30,
                             "confirm": True})
            assert r.status_code == 200, r.text
            call = next(x for x in fake.calls if x[0] == "MoveGripper")
            args = call[1]
            assert len(args) == 10, f"ten arguments, got {len(args)}"
            assert args[0] == 1              # index
            assert args[1] == 70             # pos
            assert args[2] == 40             # vel
            assert args[3] == 30             # force

    def test_it_does_not_block(self, fake):
        """A blocking gripper call holds the RPC lock for the whole grip,
        which would make a stop impossible."""
        with _client(fake) as c:
            h = _lease(c)
            fake.calls.clear()
            c.post("/api/v1/gripper/command", headers=h,
                   json={"position": 50, "confirm": True})
            args = next(x for x in fake.calls if x[0] == "MoveGripper")[1]
            assert args[5] == 0, "block must be 0"

    def test_no_rotation_is_sent(self, fake):
        """The last three arguments belong to a rotating gripper. Sending a
        rotation to one that does not rotate is not an accident to have."""
        with _client(fake) as c:
            h = _lease(c)
            fake.calls.clear()
            c.post("/api/v1/gripper/command", headers=h,
                   json={"position": 50, "confirm": True})
            args = next(x for x in fake.calls if x[0] == "MoveGripper")[1]
            assert args[7:] == (0.0, 0, 0)

    def test_activate_reaches_the_controller(self, fake):
        with _client(fake) as c:
            h = _lease(c)
            fake.calls.clear()
            r = c.post("/api/v1/gripper/activate", headers=h,
                       json={"confirm": True})
            assert r.status_code == 200, r.text
            assert [x for x in fake.calls if x[0] == "ActGripper"]

    def test_both_routes_are_audited(self, fake):
        with _client(fake) as c:
            h = _lease(c)
            c.post("/api/v1/gripper/activate", headers=h,
                   json={"confirm": True})
            c.post("/api/v1/gripper/command", headers=h,
                   json={"position": 50, "confirm": True})
            actions = [e["action"] for e in
                       c.get("/api/v1/events").json()["events"]]
            assert "gripper.activate" in actions
            assert "gripper.command" in actions


class TestItSaysWhatItDoesNotKnow:
    def test_the_response_admits_the_call_is_unverified(self, fake):
        """MoveGripper is `documented`, not `measured`, on this firmware.
        Saying so is the difference between a tool and a trap."""
        with _client(fake) as c:
            h = _lease(c)
            body = c.post("/api/v1/gripper/command", headers=h,
                          json={"position": 50, "confirm": True}).json()
            assert body["verified"] is False
            assert "never been exercised" in body["note"]

    def test_the_registry_still_calls_it_documented_not_measured(self):
        """If someone measures it on hardware and updates the registry, the
        note above should change too -- this fails when that happens."""
        from fws.protocol.commands import COMMANDS
        assert COMMANDS["MoveGripper"].confidence == "documented", (
            "MoveGripper is now measured: update the route's 'verified' note")
