"""A true directory listing, from the vendor's own backup archive.

GetLuaList is refused (it can wedge the RPC channel), so FWS enumerates the
controller via DataPackageDownloadPrepare + FileDownload(3, ...), whose
fr_user_data.tar.gz archive is the controller's file tree. The archive is
expensive (~4 s, ~90 KB) so it is cached with a TTL; each response reports its
age. It is a snapshot, not a live view.
"""
from __future__ import annotations

import io
import tarfile
import threading
import time
from dataclasses import dataclass
from typing import Any

# Where the controller keeps things, as seen inside fr_user_data.tar.gz.
# `/fruser/` -- the path every RPC uses -- is this directory.
USER_DIR = "root/web/file/user/"
POINT_TABLE_DIR = "root/web/file/points/point_table/"
TEMPLATE_DIR = "root/web/file/template/"


@dataclass(frozen=True)
class Version:
    """One saved revision of a program. The pendant keeps history in a
    directory beside each program (user/force_test/*.lua)."""

    saved_at: str          # the pendant's own name, e.g. YYYY-MM-DD-HH-MM
    size: int
    path: str


@dataclass(frozen=True)
class Entry:
    name: str
    size: int
    kind: str
    path: str
    # The teach pendant autosaves a timestamped copy on every edit, so much of
    # the Lua directory is version history rather than programs. Flagged so a
    # UI can fold it away.
    looks_like_autosave: bool
    versions: tuple[Version, ...] = ()
    has_taught_points: bool = False


def _is_autosave(name: str) -> bool:
    """Detect the pendant's autosave naming scheme, e.g. YYYY-MM-DD-HH-MM.lua."""
    stem = name.rsplit(".", 1)[0]
    parts = stem.split("-")
    return (len(parts) == 5 and all(p.isdigit() for p in parts)
            and len(parts[0]) == 4)


def parse_archive(blob: bytes) -> list[Entry]:
    """Every file the archive shows, classified by where it lives."""
    out: list[Entry] = []
    # program stem -> saved revisions, and whether it has a point file
    history: dict[str, list[Version]] = {}
    points: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        members = [m for m in tar.getmembers() if m.isfile()]

    for m in members:
        if not m.name.startswith(USER_DIR):
            continue
        rest = m.name[len(USER_DIR):]
        if "/" not in rest:
            continue
        stem, leaf = rest.split("/", 1)
        if leaf.endswith(".lua"):
            history.setdefault(stem, []).append(
                Version(leaf[:-4], m.size, m.name))
        elif leaf.endswith(".db"):
            points.add(stem)

    for m in members:
            path = m.name
            if path.startswith(USER_DIR) and path.endswith(".lua"):
                name = path[len(USER_DIR):]
                # A nested path is the program's own history, gathered above.
                if "/" in name:
                    continue
                stem = name[:-4]
                out.append(Entry(
                    name, m.size, "lua", path, _is_autosave(name),
                    versions=tuple(sorted(history.get(stem, []),
                                          key=lambda v: v.saved_at)),
                    has_taught_points=stem in points))
            elif path.startswith(POINT_TABLE_DIR) and path.endswith(".db"):
                out.append(Entry(path[len(POINT_TABLE_DIR):], m.size,
                                 "point_table", path, False))
            elif path.startswith(TEMPLATE_DIR) and path.endswith(".lua"):
                out.append(Entry(path[len(TEMPLATE_DIR):], m.size,
                                 "template", path, False))
    return sorted(out, key=lambda e: (e.kind, e.name))


class ControllerListing:
    """Cached true listing. One fetch serves every kind."""

    def __init__(self, download, ttl_s: float = 120.0):
        self._download = download
        self.ttl_s = ttl_s
        self._lock = threading.Lock()
        self._entries: list[Entry] | None = None
        self._fetched_at: float = 0.0
        self._inflight = False
        self.last_error: str | None = None

    def _fresh(self) -> bool:
        return (self._entries is not None
                and time.monotonic() - self._fetched_at < self.ttl_s)

    def get(self, *, refresh: bool = False) -> tuple[list[Entry] | None, dict]:
        with self._lock:
            if self._fresh() and not refresh:
                return self._entries, self._meta("cache")
            if self._inflight:
                # Never block: a second caller gets whatever is cached, marked
                # stale, rather than waiting out another 4 s download.
                return self._entries, self._meta("another fetch in progress")
            self._inflight = True

        # Lock released across the download, for the same reason as
        # LogFetcher: this runs in FastAPI's bounded sync worker pool and
        # nothing may queue in front of the stop route.
        try:
            blob = self._download()
            entries = parse_archive(blob)
            err = None
        except Exception as e:
            entries, err = None, f"{type(e).__name__}: {e}"

        with self._lock:
            self._inflight = False
            if entries is not None:
                self._entries, self._fetched_at, self.last_error = (
                    entries, time.monotonic(), None)
                return entries, self._meta("fetched")
            self.last_error = err
            return self._entries, self._meta(f"fetch failed: {err}")

    def _meta(self, source: str) -> dict[str, Any]:
        age = (None if self._entries is None
               else round(time.monotonic() - self._fetched_at, 1))
        return {
            "source": source,
            "age_s": age,
            "ttl_s": self.ttl_s,
            "authoritative": self._entries is not None,
            "method": ("DataPackageDownloadPrepare + FileDownload(3, "
                       "fr_user_data.tar.gz), parsed for its file tree. "
                       "GetLuaList is refused -- it is reported to wedge the "
                       "RPC channel -- so this is how FWS enumerates."),
            "caveat": ("a snapshot, not a live view: a file created after the "
                       "archive was built is invisible until the next fetch"),
        }
