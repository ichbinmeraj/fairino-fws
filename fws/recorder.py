"""A flight recorder for the arm, and telemetry you can take away.

WHY. When something goes wrong on a cell running undocumented firmware, the
question is always the same: what was the arm doing just before? The audit
trail says what was COMMANDED; it does not say where the arm actually was,
how fast, or what the wrist felt. That evidence existed for a tenth of a
second and was gone.

WHAT. A rolling buffer of the last N seconds of telemetry, kept in memory and
dumped beside the audit trail the moment a fault latches -- so the evidence
survives the event that destroyed the context. Plus explicit start/stop
recording, because "capture the next ten minutes while I reproduce this" is
the other half of the same need.

A minute of 10 Hz frames is a few hundred kilobytes. On a Pi that is
nothing, and it is the difference between diagnosing a fault and guessing at
it.

FORMAT. JSON Lines: one frame per line, greppable, streamable, and readable
by pandas in one call. CSV is offered for the columns that are always
present, because a spreadsheet is still how a lot of shop-floor analysis
actually happens.
"""
from __future__ import annotations

import contextlib
import csv
import io
import json
import pathlib
import threading
import time
from collections import deque
from typing import Any

# 10 Hz for 60 s. Long enough to hold the approach that preceded a fault,
# short enough that it never matters on a Pi.
DEFAULT_SECONDS = 60.0
SAMPLE_HZ = 10.0

# The fields worth a column in CSV. Everything else stays in the JSONL.
CSV_FIELDS = ("ts", "joints", "tcp", "ft", "joint_torque",
              "error_main", "error_sub", "program_state")


def _flatten(frame: dict[str, Any]) -> dict[str, Any]:
    """Six-element vectors become j1..j6 style columns for the CSV."""
    out: dict[str, Any] = {}
    for key in CSV_FIELDS:
        v = frame.get(key)
        if isinstance(v, (list, tuple)) and len(v) == 6:
            prefix = {"joints": "j", "tcp": "tcp", "ft": "ft",
                      "joint_torque": "torque"}.get(key, key)
            for i, item in enumerate(v, start=1):
                out[f"{prefix}{i}"] = item
        else:
            out[key] = v
    return out


class FlightRecorder:
    """The rolling buffer, the fault dump, and explicit recordings."""

    def __init__(self, data_dir: pathlib.Path,
                 seconds: float = DEFAULT_SECONDS) -> None:
        self.dir = pathlib.Path(data_dir) / "recordings"
        self.window_s = seconds
        self._ring: deque[dict[str, Any]] = deque(
            maxlen=int(seconds * SAMPLE_HZ))
        self._lock = threading.Lock()
        self._recording: str | None = None
        self._sink = None
        self._frames_recorded = 0
        self.dumps = 0
        self.last_dump: str | None = None
        self.errors = 0

    # -- ingest ------------------------------------------------------------
    def feed(self, frame: dict[str, Any]) -> None:
        """One sample. Called from a sampling thread; never raises."""
        if not frame.get("joints"):
            return                       # nothing to record yet
        row = {"ts": time.time(),
               **{k: v for k, v in frame.items() if k != "ts"}}
        with self._lock:
            self._ring.append(row)
            if self._sink is not None:
                try:
                    self._sink.write(json.dumps(row, default=str) + "\n")
                    self._frames_recorded += 1
                except OSError as e:
                    # A full disk must not take the gateway down. Stop
                    # recording, count it, and say so through health.
                    self.errors += 1
                    self.last_dump = f"recording stopped: {e}"
                    with contextlib.suppress(Exception):
                        self._sink.close()
                    self._sink = None
                    self._recording = None

    # -- the fault dump ----------------------------------------------------
    def dump(self, why: str) -> str | None:
        """Write the whole rolling window out. Returns the file name.

        Called when a fault latches: the seconds BEFORE the fault are the
        ones worth having, and they are already in the ring.
        """
        with self._lock:
            rows = list(self._ring)
        if not rows:
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(rows[-1]["ts"]))
        name = f"fault-{stamp}.jsonl"
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self.dir / name
            with path.open("w") as fh:
                fh.write(json.dumps({"_meta": {"why": why,
                                               "frames": len(rows),
                                               "window_s": self.window_s}}) +
                         "\n")
                for row in rows:
                    fh.write(json.dumps(row, default=str) + "\n")
        except OSError as e:
            self.errors += 1
            self.last_dump = f"dump failed: {e}"
            return None
        self.dumps += 1
        self.last_dump = name
        return name

    # -- explicit recordings -----------------------------------------------
    def start(self, name: str) -> str:
        safe = "".join(c for c in name if c.isalnum() or c in "._-")[:64]
        if not safe:
            raise ValueError("a recording needs a name of letters or digits")
        with self._lock:
            if self._recording:
                raise RuntimeError(
                    f"already recording to {self._recording}; stop it first")
            self.dir.mkdir(parents=True, exist_ok=True)
            filename = f"{safe}.jsonl"
            self._sink = (self.dir / filename).open("w")
            self._recording = filename
            self._frames_recorded = 0
        return filename

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self._recording:
                raise RuntimeError("not recording")
            name, frames = self._recording, self._frames_recorded
            with contextlib.suppress(Exception):
                self._sink.close()
            self._sink, self._recording = None, None
        return {"recording": name, "frames": frames}

    # -- reading back ------------------------------------------------------
    def list(self) -> list[dict[str, Any]]:
        if not self.dir.exists():
            return []
        out = []
        for p in sorted(self.dir.glob("*.jsonl")):
            stat = p.stat()
            out.append({"name": p.name, "bytes": stat.st_size,
                        "modified": stat.st_mtime})
        return out

    def read(self, name: str) -> str:
        path = self._resolve(name)
        return path.read_text()

    def as_csv(self, name: str) -> str:
        """The always-present columns, flattened, for a spreadsheet."""
        rows = []
        for line in self._resolve(name).read_text().splitlines():
            if not line.strip():
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "_meta" in frame:
                continue
            rows.append(_flatten(frame))
        if not rows:
            return ""
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        return buf.getvalue()

    def delete(self, name: str) -> None:
        self._resolve(name).unlink()

    def _resolve(self, name: str) -> pathlib.Path:
        """Names come from URLs, so a path escape is the obvious attack."""
        path = (self.dir / name).resolve()
        if path.parent != self.dir.resolve() or not path.name.endswith(
                ".jsonl"):
            raise FileNotFoundError(name)
        if not path.exists():
            raise FileNotFoundError(name)
        return path

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "buffered_frames": len(self._ring),
                "window_s": self.window_s,
                "recording": self._recording,
                "fault_dumps": self.dumps,
                "last": self.last_dump,
                "errors": self.errors,
                "dir": str(self.dir),
            }
