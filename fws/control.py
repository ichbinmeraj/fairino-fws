"""Single-writer control lock and client-liveness watchdog.

Domain-scoped leases (motion, config, program). Stop, reads, and kinematics
are never lockable. Client disconnect is lease expiry: the holder stops
renewing, the lease lapses, and lapsing issues the stop. The lock prevents
contradictory commands; it is not a substitute for the physical E-stop.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field

DOMAINS = ("motion", "config", "program")
DEFAULT_TTL_S = 30.0
MIN_TTL_S = 5.0
MAX_TTL_S = 600.0


@dataclass
class Lease:
    token: str
    client_id: str
    domains: tuple[str, ...]
    acquired_at: float
    expires_at: float
    renewals: int = 0

    def as_dict(self, *, redact: bool = True) -> dict:
        d = {
            "client_id": self.client_id,
            "domains": list(self.domains),
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
            "renewals": self.renewals,
            "expires_in_s": round(self.expires_at - time.time(), 2),
        }
        if not redact:
            d["token"] = self.token
        return d


@dataclass
class ControlLock:
    """Leases over named domains, with a lapse callback (the disconnect watchdog).

    on_lapse fires when a lease expires without renewal, called outside the
    internal lock so a stop cannot block a concurrent acquire. The callback is
    guarded per lease, the reap loop cannot die, and watchdog() publishes
    liveness so "no lapses fired" and "nothing is watching" do not look alike.
    """

    on_lapse: object = None            # callable(reason: str, lease: Lease)
    _leases: dict[str, Lease] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _last_reap: float | None = None
    _reap_errors: int = 0
    _last_reap_error: str | None = None
    _lapse_errors: int = 0
    _last_lapse_error: str | None = None

    # ------------------------------------------------------------- lifecycle
    def start(self, interval_s: float = 1.0) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._reap_loop, args=(interval_s,), daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

    def _reap_loop(self, interval_s: float) -> None:
        while not self._stop.wait(interval_s):
            try:
                self.reap()
            except Exception as e:
                # This thread is the disconnect watchdog; letting it exit
                # removes the guarantee silently, so errors are counted and
                # published by watchdog() instead.
                self._reap_errors += 1
                self._last_reap_error = f"{type(e).__name__}: {e}"

    def reap(self) -> list[Lease]:
        """Expire lapsed leases and fire the watchdog for each."""
        now = time.time()
        lapsed: list[Lease] = []
        seen: list[int] = []
        with self._lock:
            for domain, lease in list(self._leases.items()):
                if lease.expires_at <= now:
                    del self._leases[domain]
                    # Deduplicate by identity: one Lease covers several
                    # domains, and firing the watchdog once per domain would
                    # issue several stops for a single lapse.
                    if id(lease) not in seen:
                        seen.append(id(lease))
                        lapsed.append(lease)
        # Outside the lock on purpose: on_lapse issues a stop, which can block
        # on the RPC channel. Holding the lock through that would stall every
        # other client, including one trying to take over.
        for lease in lapsed:
            if callable(self.on_lapse):
                try:
                    self.on_lapse("lease expired without renewal", lease)
                except Exception as e:
                    # Per lease, not per batch: one failing callback must not
                    # deny the watchdog to every other lapsed lease behind it.
                    self._lapse_errors += 1
                    self._last_lapse_error = f"{type(e).__name__}: {e}"
        self._last_reap = now
        return lapsed

    def watchdog(self) -> dict:
        """Whether the watchdog thread is alive and whether it has been failing."""
        alive = bool(self._thread and self._thread.is_alive())
        age = (None if self._last_reap is None
               else round(time.time() - self._last_reap, 2))
        return {
            "running": alive,
            "last_reap_age_s": age,
            "reap_errors": self._reap_errors,
            "last_reap_error": self._last_reap_error,
            "lapse_callback_errors": self._lapse_errors,
            "last_lapse_callback_error": self._last_lapse_error,
            # A failed lapse callback means a stop was NOT issued for a
            # holder that went away. Counted cumulatively and never reset:
            # a stop that did not happen is not something to age out of the
            # health signal.
            "healthy": (alive and self._reap_errors == 0
                        and self._lapse_errors == 0),
            "means": ("when this is not running, leases still EXPIRE for the "
                      "purposes of who holds what -- but no stop is issued "
                      "when a holder goes away, which is the point of a "
                      "lease. Treat it as loss of the disconnect watchdog. "
                      "Error counts are cumulative and never reset."),
        }

    # ---------------------------------------------------------------- queries
    def holders(self) -> dict[str, dict]:
        now = time.time()
        with self._lock:
            return {d: le.as_dict() for d, le in self._leases.items()
                    if le.expires_at > now}

    def held_by(self, domain: str) -> Lease | None:
        with self._lock:
            lease = self._leases.get(domain)
            return lease if lease and lease.expires_at > time.time() else None

    def check(self, domain: str, token: str | None) -> tuple[bool, str]:
        """(allowed, reason). Absence of a holder means the domain is free."""
        lease = self.held_by(domain)
        if lease is None:
            return True, "domain is unheld"
        if token and secrets.compare_digest(token, lease.token):
            return True, "token holds this domain"
        return False, (f"domain '{domain}' is held by {lease.client_id} "
                       f"until {lease.expires_at:.0f}")

    # --------------------------------------------------------------- mutation
    def acquire(self, client_id: str, domains: list[str],
                ttl_s: float = DEFAULT_TTL_S) -> Lease:
        bad = [d for d in domains if d not in DOMAINS]
        if bad:
            raise ValueError(f"unknown domain(s) {bad}; valid: {list(DOMAINS)}")
        if not domains:
            raise ValueError("at least one domain is required")
        ttl_s = max(MIN_TTL_S, min(MAX_TTL_S, ttl_s))

        now = time.time()
        with self._lock:
            for d in domains:
                held = self._leases.get(d)
                if (held and held.expires_at > now
                        and held.client_id != client_id):
                    raise Conflict(d, held)
            # The same client re-acquiring while its own lease is still live
            # (it restarted, or lost the token) replaces that lease outright:
            # the old token stops working and the new one takes over. No
            # other client could hold these domains, so no stop is involved.
            # Before this, a crashed client locked itself out for a full TTL.
            for d in domains:
                held = self._leases.get(d)
                if held and held.client_id == client_id:
                    for dd in held.domains:
                        if self._leases.get(dd) is held:
                            del self._leases[dd]
            lease = Lease(token=secrets.token_urlsafe(32), client_id=client_id,
                          domains=tuple(domains), acquired_at=now,
                          expires_at=now + ttl_s)
            for d in domains:
                self._leases[d] = lease
        return lease

    def renew(self, token: str, ttl_s: float = DEFAULT_TTL_S) -> Lease | None:
        ttl_s = max(MIN_TTL_S, min(MAX_TTL_S, ttl_s))
        with self._lock:
            # One Lease object is shared by every domain it covers, so
            # iterating values() would renew it once per domain and inflate
            # the renewal count. Deduplicate by identity -- Lease is a mutable
            # dataclass and therefore unhashable, so a set() will not do.
            seen: list[int] = []
            for lease in self._leases.values():
                if id(lease) in seen:
                    continue
                seen.append(id(lease))
                if secrets.compare_digest(token, lease.token):
                    lease.expires_at = time.time() + ttl_s
                    lease.renewals += 1
                    return lease
        return None

    def release(self, token: str) -> bool:
        """Explicit release; does NOT fire the watchdog (a client that
        said goodbye has not disconnected)."""
        with self._lock:
            found = False
            for domain, lease in list(self._leases.items()):
                if secrets.compare_digest(token, lease.token):
                    del self._leases[domain]
                    found = True
            return found

    def break_lock(self, domain: str) -> Lease | None:
        """Administrative override for a stuck lease; does NOT fire the watchdog."""
        with self._lock:
            return self._leases.pop(domain, None)


class Conflict(RuntimeError):
    def __init__(self, domain: str, holder: Lease):
        self.domain = domain
        self.holder = holder
        super().__init__(f"domain '{domain}' is held by {holder.client_id}")
