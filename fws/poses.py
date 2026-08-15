"""Named poses, stored by the gateway.

WHAT THIS IS NOT. It is not the controller's point table. Those are opaque
`.db` files that move whole (`/api/v1/points/tables/...`), and this firmware
has no way to write a single named point into one -- no RPC exists, and the
Lua path is a confirmed silent no-op. So a taught point had nowhere to live
except a browser's localStorage, which meant production data died with a
profile and was invisible to CI, to teammates, and to every API client.

WHAT THIS IS. A named pose recorded on the gateway: six joint angles, the
TCP pose they correspond to, the tool and work-object numbers they were
taught with, and when. Kept as JSON in `server.data_dir`, so it survives a
restart, backs up with the rest of the deployment, and can be read by
anything that speaks HTTP.

Poses are stored with BOTH representations on purpose. The joint angles are
what a motion command needs; the TCP pose is what a human recognises, and
keeping it lets a reader see that a point moved without solving kinematics.
They are captured together from one telemetry frame so they cannot disagree.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

# Deliberately strict: these names end up in generated Lua and in file paths.
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
STORE_FILENAME = "poses.json"


class PoseError(ValueError):
    """A refusal with a reason the caller can show a human."""


def validate_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise PoseError(
            f"invalid pose name {name!r}: start with a letter, then letters, "
            f"digits, dot, dash or underscore, up to 64 characters. These "
            f"names are written into generated programs.")
    return name


@dataclass
class Pose:
    name: str
    joints: list[float]
    tcp: list[float]
    tool: int = 0
    wobj: int = 0
    note: str = ""
    captured_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Pose:
        return Pose(
            name=d["name"],
            joints=[float(v) for v in d["joints"]],
            tcp=[float(v) for v in d["tcp"]],
            tool=int(d.get("tool", 0)),
            wobj=int(d.get("wobj", 0)),
            note=str(d.get("note", "")),
            captured_at=float(d.get("captured_at", 0.0)),
        )


def _check_six(label: str, values: Any) -> list[float]:
    try:
        out = [float(v) for v in values]
    except (TypeError, ValueError) as e:
        raise PoseError(f"{label} must be six numbers") from e
    if len(out) != 6:
        raise PoseError(f"{label} must be six numbers, got {len(out)}")
    return out


class PoseStore:
    """Named poses on disk. Small, so it is read and written whole."""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = pathlib.Path(path)
        self._lock = threading.Lock()
        self._poses: dict[str, Pose] = {}
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as e:
            # Refusing to start over a corrupt store would take the whole
            # gateway down for a file nothing else needs; refusing to load it
            # silently would look like "someone deleted my points". Keep the
            # bad file, start empty, and say so through health.
            self.load_error = str(e)[:200]
            return
        for entry in raw.get("poses", []):
            try:
                p = Pose.from_dict(entry)
            except (KeyError, TypeError, ValueError):
                continue          # one bad row must not lose the rest
            self._poses[p.name] = p

    load_error: str | None = None

    def _save_locked(self) -> None:
        """Atomic replace: a crash mid-write must not leave a truncated file
        where the taught points used to be."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1,
                   "poses": [p.as_dict() for p in self._poses.values()]}
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent),
                                   prefix=".poses-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)          # never leave a stray .poses-*.tmp
            raise

    # -- operations --------------------------------------------------------
    def list(self) -> list[Pose]:
        with self._lock:
            return sorted(self._poses.values(), key=lambda p: p.name)

    def get(self, name: str) -> Pose:
        with self._lock:
            try:
                return self._poses[name]
            except KeyError:
                raise PoseError(f"no pose named {name!r}") from None

    def save(self, pose: Pose, *, overwrite: bool = False) -> Pose:
        validate_name(pose.name)
        pose.joints = _check_six("joints", pose.joints)
        pose.tcp = _check_six("tcp", pose.tcp)
        with self._lock:
            if pose.name in self._poses and not overwrite:
                raise PoseError(
                    f"{pose.name!r} already exists. A taught pose is "
                    f"production data; resend with overwrite=true to "
                    f"replace it.")
            self._poses[pose.name] = pose
            self._save_locked()
        return pose

    def delete(self, name: str) -> None:
        with self._lock:
            if name not in self._poses:
                raise PoseError(f"no pose named {name!r}")
            del self._poses[name]
            self._save_locked()

    def rename(self, old: str, new: str) -> Pose:
        validate_name(new)
        with self._lock:
            if old not in self._poses:
                raise PoseError(f"no pose named {old!r}")
            if new in self._poses:
                raise PoseError(f"{new!r} already exists")
            pose = self._poses.pop(old)
            pose.name = new
            self._poses[new] = pose
            self._save_locked()
        return pose

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "count": len(self._poses),
                "file": str(self.path),
                "load_error": self.load_error,
            }
