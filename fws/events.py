"""Audit log of commands and state changes (not reads).

Never records secrets: API keys are identified by their audit label, control
tokens are truncated. Bounded in memory with an optional file sink.
"""
from __future__ import annotations

import contextlib
import json
import pathlib
import threading
import time
from collections import deque
from typing import Any

MAX_IN_MEMORY = 2000


class AuditLog:
    def __init__(self, path: pathlib.Path | None = None,
                 maxlen: int = MAX_IN_MEMORY):
        self.path = path
        self._events: deque[dict] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0
        # Sink failures are counted, not raised, so health can say the
        # durable trail stopped being written.
        self.sink_errors = 0
        self.sink_last_error: str | None = None
        # Set by the app to the event bus's publish, so every audited command
        # is also pushed. A plain attribute rather than a constructor
        # argument: the log is built before the bus and must work without it.
        self.on_record = None

    def record(self, action: str, *, actor: str = "anonymous",
               **detail: Any) -> dict:
        with self._lock:
            self._seq += 1
            event = {
                "seq": self._seq,
                "ts": time.time(),
                "action": action,
                "actor": actor,
                **{k: v for k, v in detail.items() if k != "token"},
            }
            self._events.append(event)
        if self.path is not None:
            try:
                with self.path.open("a") as fh:
                    fh.write(json.dumps(event) + "\n")
            except OSError as e:
                # A failing sink must never break a robot command; the event
                # is still in memory, and the failure is counted.
                self.sink_errors += 1
                self.sink_last_error = str(e)[:200]
        if self.on_record is not None:
            # A notification must never break the command it describes.
            with contextlib.suppress(Exception):
                self.on_record(f"audit.{action}", **{
                    k: v for k, v in event.items() if k not in ("ts", "seq")})
        return event

    def health(self) -> dict[str, Any]:
        """What the durable trail is doing, for GET /system/health."""
        return {
            "in_memory": len(self._events),
            "capacity": self._events.maxlen,
            "file": str(self.path) if self.path else None,
            # False means a restart loses the trail. Stated rather than
            # implied: "file": null is easy to read past.
            "durable": self.path is not None and self.sink_errors == 0,
            "sink_errors": self.sink_errors,
            "sink_last_error": self.sink_last_error,
        }

    def recent(self, limit: int = 100, action: str | None = None) -> list[dict]:
        with self._lock:
            items = list(self._events)
        if action:
            items = [e for e in items if e["action"].startswith(action)]
        return items[-limit:][::-1]

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)
