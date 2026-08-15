"""Pushed events, and the backpressure that makes them safe to push.

A notification channel wired into a robot's command path is a liability
unless one property holds: a subscriber that stops reading must not slow
down, block, or break a command. These tests pin that first, because it is
the property that would otherwise be discovered on a shop floor.

The rest pin the point of the thing: an edge arrives once, when it happens,
so no client has to diff 10 Hz samples to notice a fault cleared.
"""
from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod
from fws.eventbus import QUEUE_DEPTH, EdgeDetector, EventBus


def _client(fake):
    app_mod.create_app(config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
    }))
    return TestClient(app_mod.app)


class TestASlowConsumerCannotHurtTheRobot:
    def test_publishing_never_blocks_on_a_full_queue(self):
        """The publisher is the request path. If it blocked here, one dead
        browser tab would stall a motion command."""
        bus = EventBus()
        bus.subscribe()                       # subscribed, never read
        started = time.monotonic()
        for i in range(QUEUE_DEPTH * 4):
            bus.publish("test.spam", i=i)
        assert time.monotonic() - started < 2.0, "publish must not block"

    def test_overflow_drops_the_oldest_and_counts_it(self):
        bus = EventBus()
        sub = bus.subscribe()
        for i in range(QUEUE_DEPTH + 25):
            bus.publish("test.n", i=i)
        first = sub.get(timeout=1)
        # The oldest went, so the first thing read is NOT i=0.
        assert first["i"] > 0
        assert first["dropped"] == 25, "the gap is reported, not hidden"

    def test_the_drop_count_is_reported_once_then_cleared(self):
        bus = EventBus()
        sub = bus.subscribe()
        for i in range(QUEUE_DEPTH + 5):
            bus.publish("test.n", i=i)
        assert sub.get(timeout=1)["dropped"] == 5
        assert "dropped" not in sub.get(timeout=1)

    def test_a_broken_subscriber_does_not_break_publish(self):
        bus = EventBus()
        sub = bus.subscribe()

        def explode(_event):
            raise RuntimeError("this subscriber is broken")

        sub._offer = explode
        bus.publish("test.one")              # must not raise
        assert bus.published == 1

    def test_publish_is_safe_from_many_threads(self):
        bus = EventBus()
        sub = bus.subscribe()
        threads = [threading.Thread(target=lambda: [
            bus.publish("test.t", n=n) for n in range(50)]) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert bus.published == 200
        seqs = []
        while (e := sub.get(timeout=0.05)) is not None:
            seqs.append(e["seq"])
        assert len(seqs) == len(set(seqs)), "sequence numbers are unique"


class TestEdgesArriveOnceWhenTheyHappen:
    def test_a_fault_latching_and_clearing_are_two_events(self):
        bus = EventBus()
        sub = bus.subscribe()
        edges = EdgeDetector(bus)
        edges.fault(0, 0)                    # first reading: not a transition
        assert sub.get(timeout=0.05) is None
        edges.fault(1, 22)
        assert sub.get(timeout=1)["kind"] == "fault.latched"
        edges.fault(1, 22)                   # still faulted: no new event
        assert sub.get(timeout=0.05) is None
        edges.fault(0, 0)
        assert sub.get(timeout=1)["kind"] == "fault.cleared"

    def test_the_first_reading_is_never_a_transition(self):
        """Otherwise every restart against a faulted controller announces a
        fault that did not just happen."""
        bus = EventBus()
        sub = bus.subscribe()
        EdgeDetector(bus).fault(1, 22)
        assert sub.get(timeout=0.05) is None

    def test_telemetry_going_down_and_coming_back(self):
        bus = EventBus()
        sub = bus.subscribe()
        edges = EdgeDetector(bus)
        edges.stream(True)
        edges.stream(False)
        assert sub.get(timeout=1)["kind"] == "telemetry.down"
        edges.stream(True)
        assert sub.get(timeout=1)["kind"] == "telemetry.up"

    def test_program_state_changes_carry_both_sides(self):
        bus = EventBus()
        sub = bus.subscribe()
        edges = EdgeDetector(bus)
        edges.program_state(1)
        edges.program_state(2)
        e = sub.get(timeout=1)
        assert (e["was"], e["now"]) == (1, 2)


class TestTopicFiltering:
    def test_a_subscriber_can_take_one_family(self):
        bus = EventBus()
        motion = bus.subscribe(["motion"])
        bus.publish("fault.latched")
        bus.publish("motion.jog")
        e = motion.get(timeout=1)
        assert e["kind"] == "motion.jog", "the fault must not arrive here"
        assert motion.get(timeout=0.05) is None

    def test_no_filter_means_everything(self):
        bus = EventBus()
        sub = bus.subscribe()
        bus.publish("fault.latched")
        bus.publish("motion.jog")
        assert sub.get(timeout=1)["kind"] == "fault.latched"
        assert sub.get(timeout=1)["kind"] == "motion.jog"

    def test_closing_stops_delivery(self):
        bus = EventBus()
        sub = bus.subscribe()
        sub.close()
        bus.publish("test.after")
        assert bus.subscribers == 0
        assert sub.get(timeout=0.05) is None


class TestOverTheWire:
    def test_commands_arrive_on_the_websocket(self, fake):
        with _client(fake) as c, c.websocket_connect("/ws/events") as ws:
            c.post("/api/v1/motion/stop")
            for _ in range(20):
                msg = json.loads(ws.receive_text())
                if msg["kind"] == "audit.motion.stop":
                    break
            else:
                pytest.fail("the stop never arrived on the event socket")

    def test_the_socket_says_it_is_alive_when_nothing_happens(self, fake):
        """Silence and a dead socket look identical to a client otherwise, so
        an idle stream must still say something."""
        with _client(fake) as c, c.websocket_connect("/ws/events") as ws:
            # Nothing is commanded here on purpose. Within a few seconds the
            # stream must produce a keepalive rather than nothing at all.
            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                if json.loads(ws.receive_text())["kind"] == "keepalive":
                    return
            pytest.fail("an idle event stream produced no keepalive")

    def test_the_watchdog_stop_is_pushed(self, fake):
        """The one event most worth reacting to. It used to be a print()."""
        with _client(fake) as c, c.websocket_connect("/ws/events") as ws:
            app_mod.control._leases.clear()
            c.post("/api/v1/control",
                   json={"client_id": "gone", "domains": ["motion"]})
            app_mod._on_lease_lapse("expired",
                                    app_mod.control.held_by("motion"))
            for _ in range(30):
                msg = json.loads(ws.receive_text())
                if msg["kind"] == "audit.watchdog.stop":
                    assert msg["actor"] == "gone"
                    break
            else:
                pytest.fail("the watchdog stop was not pushed")

    def test_health_reports_the_bus(self, fake):
        with _client(fake) as c:
            ev = c.get("/api/v1/system/health").json()["events"]
            assert ev["queue_depth"] == QUEUE_DEPTH
            assert "dropped" in ev["backpressure"]
