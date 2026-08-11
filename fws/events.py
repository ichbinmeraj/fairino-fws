"""Audit log of commands and state changes (not reads).

Never records secrets: API keys are identified by their audit label, control
tokens are truncated. Bounded in memory with an optional file sink.
"""
from __future__ import annotations

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
            except OSError:
                # A failing sink must never break a robot command; the event
                # is still in memory.
                pass
        return event

    def recent(self, limit: int = 100, action: str | None = None) -> list[dict]:
        with self._lock:
            items = list(self._events)
        if action:
            items = [e for e in items if e["action"].startswith(action)]
        return items[-limit:][::-1]

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)
