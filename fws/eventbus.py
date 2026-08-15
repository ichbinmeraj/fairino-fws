"""Push events instead of making clients diff snapshots.

WHAT THIS SOLVES. To learn that a program finished, a fault latched, or the
watchdog stopped the arm, a client had two options: poll, or diff the 10 Hz
telemetry stream and infer the edge itself. Every integrator wrote that
inference again, and each one got the edge cases slightly different. The
watchdog stop was worse than that -- it existed only as a line on stdout, so
nothing could react to the single most important thing the gateway does.

WHAT IT IS. One in-process fan-out. Two kinds of message go in:

  * every audit record (the same lines GET /api/v1/events returns), so
    "who commanded what" arrives as it happens rather than on request;
  * edge transitions the gateway is uniquely placed to notice -- a fault
    latching or clearing, program state changing, telemetry going stale or
    coming back.

BACKPRESSURE IS THE WHOLE DESIGN. A subscriber that stops reading must never
slow down a robot command. Each subscriber gets a bounded queue; when it
fills, the OLDEST events are dropped and the drop is counted, and the count
rides along on the next event that subscriber receives. A consumer therefore
always knows it missed something -- silent loss would be worse than the
polling it replaces. `publish()` never blocks and never raises.
"""
from __future__ import annotations

import contextlib
import queue
import threading
import time
from typing import Any

# Deep enough to ride out a GC pause or a slow render; shallow enough that a
# dead consumer cannot hold megabytes of history hostage.
QUEUE_DEPTH = 256


class Subscription:
    """One consumer's view of the bus. Iterate it; close it when done."""

    def __init__(self, bus: EventBus, topics: frozenset[str] | None) -> None:
        self._bus = bus
        self._q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=QUEUE_DEPTH)
        self._closed = threading.Event()
        self.topics = topics
        self.dropped = 0

    def _offer(self, event: dict[str, Any]) -> None:
        """Never blocks. Drops the OLDEST on overflow, and counts it."""
        while True:
            try:
                self._q.put_nowait(event)
                return
            except queue.Full:
                try:
                    self._q.get_nowait()
                    self.dropped += 1
                except queue.Empty:      # drained by the reader meanwhile
                    continue

    def get(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Next event, or None on timeout/close.

        The returned event carries `dropped` when this subscriber has missed
        anything, so a consumer can never mistake a gap for quiet.
        """
        try:
            event = self._q.get(timeout=timeout)
        except queue.Empty:
            return None
        if self.dropped:
            event = {**event, "dropped": self.dropped}
            self.dropped = 0
        return event

    def close(self) -> None:
        self._closed.set()
        self._bus.unsubscribe(self)

    @property
    def closed(self) -> bool:
        return self._closed.is_set()


class EventBus:
    def __init__(self) -> None:
        self._subs: list[Subscription] = []
        self._lock = threading.Lock()
        self._seq = 0
        self.published = 0

    def subscribe(self, topics: list[str] | None = None) -> Subscription:
        """Subscribe, optionally to a prefix set ('motion', 'watchdog', ...)."""
        sub = Subscription(self, frozenset(topics) if topics else None)
        with self._lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, kind: str, **fields: Any) -> dict[str, Any]:
        """Fan out one event. Never blocks, never raises.

        Called from the request path and from the fault poller, so an
        exception here would take down a robot command for the sake of a
        notification.
        """
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "ts": time.time(), "kind": kind,
                     **fields}
            self.published += 1
            targets = [s for s in self._subs
                       if s.topics is None
                       or kind.split(".", 1)[0] in s.topics
                       or kind in s.topics]
        for sub in targets:
            # A broken subscriber is that subscriber's problem, never the
            # robot command's.
            with contextlib.suppress(Exception):
                sub._offer(event)
        return event

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "subscribers": len(self._subs),
                "published": self.published,
                "queue_depth": QUEUE_DEPTH,
                "backpressure": ("oldest events are dropped per-subscriber "
                                 "and counted; the count rides on the next "
                                 "event that subscriber receives"),
            }


class EdgeDetector:
    """Turn polled state into transitions, once, in one place.

    Every integrator was writing this against the 10 Hz stream and getting
    the corners slightly different. Doing it here means one definition of
    "the fault cleared".
    """

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self._faulted: bool | None = None
        self._program: int | None = None
        self._stream_ok: bool | None = None

    def fault(self, main: int, sub: int) -> None:
        now = bool(main) or bool(sub)
        if self._faulted is None:
            self._faulted = now          # first reading is not a transition
            return
        if now != self._faulted:
            self._faulted = now
            self.bus.publish("fault.latched" if now else "fault.cleared",
                             main=main, sub=sub)

    def program_state(self, state: int | None) -> None:
        if state is None or state == self._program:
            return
        was, self._program = self._program, state
        if was is not None:
            self.bus.publish("program.state", was=was, now=state)

    def stream(self, connected: bool) -> None:
        if self._stream_ok is None:
            self._stream_ok = connected
            return
        if connected != self._stream_ok:
            self._stream_ok = connected
            self.bus.publish("telemetry.up" if connected
                             else "telemetry.down")
