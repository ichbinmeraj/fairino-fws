"""The Python client, exercised against a real gateway over real HTTP.

The gateway's pitch is "drive the robot from any language, no vendor SDK".
That was true of the wire and a half-truth in practice: every integrator
reimplemented the same control-lease state machine, and three copies already
existed in this project alone. So the tests that matter here are the lease
ones -- acquire, heartbeat, release cleanly, and above all NOTICE when the
lease is lost, because a client that keeps commanding a robot it no longer
holds is about to have the arm stopped underneath it.

Uses the public harness, like a customer would.
"""
from __future__ import annotations

import threading
import time

import pytest

from fws.client import (
    FwsClient,
    HeldByAnother,
    LeaseLost,
    NeedsConfirm,
    NeedsLease,
    Refused,
)
from fws.testing import gateway


class TestReading:
    def test_state_and_health(self):
        with gateway() as g, FwsClient(g.url) as fws:
            assert len(fws.state()["joints"]) == 6
            assert "warnings" in fws.health()

    def test_capabilities_keeps_the_three_states(self):
        """`unknown` is not `absent`, and a client library that flattened
        them would undo the point of probing."""
        with gateway() as g, FwsClient(g.url) as fws:
            caps = fws.capabilities()
            assert {"available", "absent", "unknown"} <= set(caps)

    def test_the_urdf_comes_back_as_text(self):
        with gateway() as g, FwsClient(g.url) as fws:
            assert fws.model_urdf(visuals="none").startswith("<?xml")

    def test_an_unreachable_gateway_says_so_plainly(self):
        fws = FwsClient("http://127.0.0.1:1")     # nothing listens here
        with pytest.raises(Exception) as e:
            fws.state()
        assert "could not reach" in str(e.value)


class TestRefusalsCarryTheirReason:
    def test_a_missing_lease_raises_NeedsLease(self):
        # /motion/move is one of the few routes that needs the lease to be
        # HELD rather than merely un-held by someone else -- most routes
        # allow single-client operation. enable_movel is on so the flag
        # check does not 403 before the lease check is reached.
        with gateway(**{"features.enable_movel": True}) as g, \
                FwsClient(g.url) as fws:
            with pytest.raises(NeedsLease) as e:
                fws.post("/api/v1/motion/move",
                         {"pose": [400, 0, 200, -90, 0, 0], "confirm": True})
            assert e.value.status == 428

    def test_a_missing_confirm_raises_NeedsConfirm_with_the_consequence(self):
        with gateway() as g, FwsClient(g.url) as fws:
            with fws.control("motion"), pytest.raises(NeedsConfirm) as e:
                fws.post("/api/v1/robot/enable", {"enable": True})
            # The gateway's own wording, not a status code to look up.
            assert "confirm" in str(e.value)

    def test_another_holder_raises_HeldByAnother(self):
        with gateway() as g:
            a, b = FwsClient(g.url), FwsClient(g.url)
            with a.control("motion", client_id="first"):
                with pytest.raises(HeldByAnother) as e:
                    b.post("/api/v1/control",
                           {"client_id": "second", "domains": ["motion"]})
                assert e.value.status == 423

    def test_refused_is_the_base_so_one_except_catches_all(self):
        assert issubclass(NeedsLease, Refused)
        assert issubclass(HeldByAnother, Refused)
        assert issubclass(NeedsConfirm, Refused)


class TestTheLease:
    def test_the_block_acquires_and_releases(self):
        with gateway() as g, FwsClient(g.url) as fws:
            assert fws.token is None
            with fws.control("motion"):
                assert fws.token
                holders = fws.get("/api/v1/control")["holders"]
                assert holders.get("motion")
            assert fws.token is None
            assert not fws.get("/api/v1/control")["holders"].get("motion")

    def test_releasing_cleanly_does_not_fire_the_watchdog(self):
        """A lease that merely lapses stops the arm. Saying goodbye must not
        -- otherwise every well-behaved client triggers a stop on exit."""
        with gateway() as g, FwsClient(g.url) as fws:
            with fws.control("motion"):
                pass
            actions = [e["action"] for e in
                       fws.get("/api/v1/events")["events"]]
            assert "watchdog.stop" not in actions

    def test_it_takes_every_domain_you_ask_for(self):
        with gateway() as g, FwsClient(g.url) as fws, \
                fws.control("motion", "program", "config"):
            holders = fws.get("/api/v1/control")["holders"]
            assert all(holders.get(d)
                       for d in ("motion", "program", "config"))

    def test_the_heartbeat_keeps_a_short_lease_alive(self):
        """A 3 s TTL beats every second. Without the thread this lease would
        be gone before the block ends."""
        # 5 s is the gateway's minimum TTL; the heartbeat beats every 1.7 s,
        # so surviving 7 s proves the thread is doing its job.
        with gateway() as g, FwsClient(g.url) as fws, \
                fws.control("motion", ttl_s=5.0):
            time.sleep(7.0)
            assert fws.get("/api/v1/control")["holders"].get("motion"), (
                "the heartbeat did not keep the lease")
            fws.enable(False)              # still commanding successfully

    def test_the_heartbeat_renews_to_the_acquired_ttl(self):
        """The heartbeat route defaults to 30 s. If the client did not echo
        the acquired ttl, control(ttl_s>90) would renew to 30 s and lapse
        between beats (period ttl_s/3 > 30) -- stopping the arm. Capture the
        heartbeat request and assert it carries the ttl.

        Uses ttl_s=6 (period 2 s) so a beat is observable within the test;
        the value proven is the same for any ttl."""
        with gateway() as g, FwsClient(g.url) as fws:
            seen = []
            real = fws.post

            def spy(path, *a, **k):
                if "heartbeat" in path:
                    seen.append(path)
                return real(path, *a, **k)

            fws.post = spy
            with fws.control("motion", ttl_s=6.0):
                time.sleep(2.5)          # period 2 s -> at least one beat
            assert seen, "no heartbeat was sent"
            assert all("ttl_s=6" in p for p in seen), seen

    def test_a_lost_lease_raises_instead_of_going_quiet(self):
        """The worst possible failure is silence: the arm is about to be
        stopped by the watchdog and the client thinks it is still in charge."""
        with gateway() as g, FwsClient(g.url) as fws, fws.control("motion"):
            fws._lease_error = RuntimeError("network went away")
            with pytest.raises(LeaseLost) as e:
                fws.jog(joint=1, direction=1)
            assert "watchdog" in str(e.value)

    def test_the_heartbeat_thread_does_not_outlive_the_block(self):
        """Measured across the control block only: the gateway harness runs
        threads of its own, so a count taken outside it means nothing."""
        with gateway() as g, FwsClient(g.url) as fws:
            before = threading.active_count()
            with fws.control("motion", ttl_s=5.0):
                assert threading.active_count() > before, "no heartbeat ran"
            time.sleep(0.3)
            assert threading.active_count() <= before, "the thread leaked"

    def test_close_releases_a_lease_taken_by_hand(self):
        with gateway() as g:
            fws = FwsClient(g.url)
            got = fws.post("/api/v1/control",
                           {"client_id": "manual", "domains": ["motion"]})
            fws.token = got["token"]
            fws.close()
            assert not fws.get("/api/v1/control")["holders"].get("motion")


class TestCommanding:
    def test_jog_moves_the_arm(self):
        with gateway() as g, FwsClient(g.url) as fws, fws.control("motion"):
            fws.enable()
            start = fws.state()["joints"][0]
            fws.jog(joint=1, direction=1, step=5, vel=20)
            assert fws.wait_until_idle(timeout=20)
            assert fws.state()["joints"][0] > start

    def test_stop_needs_no_lease(self):
        """Stop must work for a client whose key is wrong or whose lease is
        gone. That is the whole point of it being open."""
        with gateway() as g, FwsClient(g.url) as fws:
            assert fws.stop()["stop_requested"] is True

    def test_capture_and_generate_a_program(self):
        with gateway() as g, FwsClient(g.url) as fws:
            with fws.control("motion", "config"):
                fws.capture_pose("home")
                fws.capture_pose("away", overwrite=True)
            assert {p["name"] for p in fws.poses()} == {"home", "away"}
            src = fws.program_from_poses(["home", "away"])
            assert src.count("MoveJ(") == 2

    def test_the_whole_program_loop(self):
        with gateway() as g, FwsClient(g.url) as fws, \
                fws.control("motion", "program", "config"):
            fws.capture_pose("p1")
            src = fws.program_from_poses(["p1"])
            fws.upload_program("client_demo.lua", src)
            report = fws.validate_program("client_demo.lua")
            assert report["checked"] >= 1
            fws.run_program("client_demo.lua")
            assert fws.execution()["state"] == "running"


class TestEvents:
    def test_a_command_arrives_on_the_event_stream(self):
        with gateway() as g:
            fws, seen = FwsClient(g.url), []

            def listen():
                for event in fws.events(timeout=15):
                    seen.append(event)
                    if event["kind"] == "audit.motion.stop":
                        return

            t = threading.Thread(target=listen, daemon=True)
            t.start()
            time.sleep(0.5)
            FwsClient(g.url).stop()
            t.join(timeout=10)
            assert any(e["kind"] == "audit.motion.stop" for e in seen), seen

    def test_keepalives_are_not_yielded_as_events(self):
        """They exist so a quiet stream is distinguishable from a dead one.
        Handing them to the caller would make every consumer filter them."""
        import inspect
        src = inspect.getsource(FwsClient.events)
        assert 'startswith("data:")' in src
