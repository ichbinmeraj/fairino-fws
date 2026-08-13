"""API-key authentication. Only meaningful when FWS binds beyond loopback; a
non-loopback bind without it is refused at startup.

Stop is never authenticated (an unreachable stop is worse than a nuisance
stop). Keys are compared in constant time. Enforcement keys off `configured`
(a file was named), not `enabled` (keys were parsed): a named file with zero
usable keys refuses every request rather than serving unauthenticated.
"""
from __future__ import annotations

import hashlib
import pathlib
import secrets

# Paths reachable without a key, whatever the configuration says.
# Stop must never require credentials. Health must be probeable by an
# orchestrator that holds none.
ALWAYS_OPEN = [
    "/api/v1/motion/stop",
    "/api/v1/system/health",
    "/docs",
    "/redoc",
    "/openapi.json",
]


def register_open_path(prefix: str) -> None:
    """Declare a prefix reachable without a key.

    For assets that must load *in order to* obtain or send a credential: a
    page that asks for a key cannot itself require that key. Data stays
    protected -- only the named prefix opens -- so a package mounting a UI
    registers its static prefix and nothing more.
    """
    if not prefix.startswith("/"):
        raise ValueError(f"open path must start with '/': {prefix!r}")
    p = "/" + prefix.strip("/")
    # Reject anything that would open the API. Both directions matter: a
    # prefix UNDER /api ("/api/v1/motion") opens real endpoints, and a prefix
    # that /api starts WITH ("/", "/ap", "/a") opens it via the startswith
    # match in is_open_path.
    if p == "/" or p.startswith("/api") or "/api".startswith(p):
        raise ValueError(f"refusing to open the API surface: {prefix!r}")
    if p not in ALWAYS_OPEN:
        ALWAYS_OPEN.append(p)


def parse_key_file(path: pathlib.Path) -> dict[str, str]:
    """Map digest -> audit label for every usable line in the file (single
    source of the parsing rule)."""
    labels: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # "key" or "key  label-for-audit-log"
        key, _, label = line.partition(" ")
        labels[hashlib.sha256(key.encode()).hexdigest()] = (
            label.strip() or "unlabelled")
    return labels


class KeyStore:
    """API keys loaded from a file (one per line, '#' comments ignored), held
    as SHA-256 digests."""

    def __init__(self, path: pathlib.Path | None):
        self.path = path
        self._digests: set[str] = set()
        self._labels: dict[str, str] = {}
        if path is not None:
            self.load()

    def load(self) -> int:
        if self.path is None or not self.path.exists():
            self._digests = set()
            return 0
        labels = parse_key_file(self.path)
        self._digests = set(labels)
        self._labels = labels
        return len(labels)

    def __len__(self) -> int:
        return len(self._digests)

    @property
    def configured(self) -> bool:
        """A key file was named. Gates enforcement: a named file with zero keys
        locks everyone out, not in."""
        return self.path is not None

    @property
    def enabled(self) -> bool:
        """Usable keys were loaded. Reports state; does NOT gate enforcement."""
        return bool(self._digests)

    def identify(self, key: str | None) -> str | None:
        """Return the key's audit label, or None if invalid. Constant-time
        comparison over all digests to avoid leaking key count."""
        if not key or not self._digests:
            return None
        digest = hashlib.sha256(key.encode()).hexdigest()
        found: str | None = None
        for known in self._digests:
            if secrets.compare_digest(digest, known):
                found = self._labels.get(known, "unlabelled")
        return found


def is_open_path(path: str) -> bool:
    # Segment-aware: an open "/console" matches "/console" and "/console/x"
    # but NOT "/consolexyz". A bare startswith would let a registered prefix
    # accidentally open a longer sibling path.
    return any(path == p or path.startswith(p + "/") for p in ALWAYS_OPEN)
