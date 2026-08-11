"""Registry of abortable activities: applications register anything
abortable, and the stop path asks the registry rather than naming
concrete classes.

Registration is by weak reference, so a collected runner neither stays alive
nor raises when the stop path iterates.
"""
from __future__ import annotations

import threading
import weakref
from typing import Protocol, runtime_checkable


@runtime_checkable
class Abortable(Protocol):
    """Anything the stop path can ask to halt. request_abort() must
    raise the abort flag WITHOUT transmitting anything to the
    robot."""

    def request_abort(self) -> None: ...


class AbortRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[weakref.ref] = []

    #: How many request_abort() calls threw during the last request_abort_all().
    failed_last_call: int = 0

    def register(self, item: Abortable) -> None:
        with self._lock:
            self._items.append(weakref.ref(item))

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return sum(1 for r in self._items if r() is not None)

    def request_abort_all(self) -> int:
        """Raise every registered abort flag; returns how many were
        reached. Never raises; failed_last_call records how many threw."""
        reached = 0
        failed = 0
        with self._lock:
            live = [r for r in self._items if r() is not None]
            self._items = live
            targets = [r() for r in live]
        for target in targets:
            if target is None:
                continue
            try:
                target.request_abort()
                reached += 1
            except Exception:
                failed += 1
        self.failed_last_call = failed
        return reached
