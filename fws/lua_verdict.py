"""Turn `LuaUpLoadUpdate -> -1` into the compiler error that caused it.

Uploading a Lua program is two steps: push the bytes, then LuaUpLoadUpdate(name)
compiles against the firmware's real function table, returning only 0 or -1.
The verdict behind that number is written to the controller log, fetched via
RbLogDownloadPrepare() + FileDownload(1, "rblog.tar.gz"). The compiler answers
on the web command channel and each reply is logged, giving distinguishable
outcomes (success, unknown function, wrong argument count, missing point) rather
than one bit.

Fetching the log is slow and loads the file-transfer service, so a fetch
happens only after a real compiler rejection, at most one at a time (later
callers share the result), rate- and budget-limited, and never when the
validator looks wedged.

The archive cannot be ordered by filename or mtime (the controller clock runs
backwards across reboots), so the verdict is anchored on content: the last
record in exactly one log file naming the program, or agreement among all
records naming it, otherwise no verdict and the distinct candidates are
returned.
"""
from __future__ import annotations

import io
import re
import tarfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .files_wire import download

# The compiler's reply as it appears in the log. Non-greedy to the closing
# quote: an error_info containing a quote truncates here, which is accepted
# (a truncated message is still the right classification).
RESULT = re.compile(r"LuaResult\('(.*?)'\)")

FIELDS = re.compile(
    r"lua_name:(?P<name>.*?)---line_num:(?P<line>\d+)---error_info:\s*"
    r"(?P<info>.*)", re.S)

# Only a nil-value call means the function is absent; everything else means
# the name resolved to something callable.
NIL_CALL = re.compile(r"attempt to call global (\S+) \(a nil value\)")
BAD_ARITY = re.compile(
    r"bad argument ((?:#\d+\s*)+)to (\S+) \(Error number of parameters\)")
NO_SUCH_DATA = "failed to query the database"

# Per-member and whole-archive ceilings for the in-memory extract. The archive
# is never written to disk (extractall on an untrusted archive is a
# path-traversal primitive); only the text is needed.
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class Verdict:
    """One compiler answer, classified."""

    outcome: str
    lua_name: str | None = None
    line: int | None = None
    function: str | None = None
    arguments: tuple[str, ...] = ()
    error_info: str | None = None
    raw: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "lua_name": self.lua_name,
            "line": self.line,
            "function": self.function,
            "arguments": list(self.arguments) or None,
            "error_info": self.error_info,
            "explains": OUTCOMES.get(self.outcome),
        }


OUTCOMES = {
    "success": "the program compiled",
    "unknown_function": ("the name does not exist on this firmware. The "
                         "vendor manual documents a later release; see "
                         "GET /api/v1/lua/firmware for the 51 absent names"),
    "wrong_argument_count": ("the function exists and the call passes the "
                             "wrong number of arguments. The manual's count "
                             "is wrong for 21 functions; see "
                             "GET /api/v1/lua/conflicts"),
    "needs_a_taught_point": ("the function exists and its point-name lookup "
                             "failed. No taught point can be created on this "
                             "controller, so this "
                             "is a cell limitation, not a syntax error"),
    "rejected": "the compiler rejected the program; see error_info",
}


def parse(payload: str) -> Verdict:
    """Classify one LuaResult payload."""
    payload = payload.strip()
    if payload == "success":
        return Verdict("success", raw=payload)
    m = FIELDS.search(payload)
    if not m:
        return Verdict("rejected", error_info=payload or None, raw=payload)
    name = m.group("name").strip()
    line = int(m.group("line"))
    info = m.group("info").strip()
    common = {"lua_name": name, "line": line, "error_info": info,
              "raw": payload}

    nil = NIL_CALL.search(info)
    if nil:
        return Verdict("unknown_function", function=nil.group(1), **common)
    arity = BAD_ARITY.search(info)
    if arity:
        return Verdict(
            "wrong_argument_count", function=arity.group(2),
            arguments=tuple(arity.group(1).split()), **common)
    if NO_SUCH_DATA in info:
        return Verdict("needs_a_taught_point", **common)
    return Verdict("rejected", **common)


def _basename(path: str) -> str:
    """The compiler logs `/fruser/x.lua` sometimes and `x.lua` others."""
    return path.rsplit("/", 1)[-1]


def read_log_archive(blob: bytes) -> dict[str, list[str]]:
    """{log filename: [LuaResult payloads, in file order]}. Read entirely
    in memory; only payload strings leave this function."""
    out: dict[str, list[str]] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tar:
        for member in tar:
            if not member.isfile() or member.size > MAX_MEMBER_BYTES:
                continue
            total += member.size
            if total > MAX_TOTAL_BYTES:
                break
            handle = tar.extractfile(member)
            if handle is None:
                continue
            text = handle.read().decode("utf-8", "replace")
            found = RESULT.findall(text)
            if found:
                out[member.name] = found
    return out


def find_verdict(blob: bytes, lua_name: str) -> dict[str, Any]:
    """The verdict for the most recent compile of lua_name, anchored on
    content not file order. The `anchor` field says which rule fired."""
    per_file = read_log_archive(blob)
    target = _basename(lua_name)

    # 1. The tail of a live log file: the upload was the last thing the
    #    compiler did, so its verdict is the last record written.
    tails = [(fname, parse(payloads[-1]))
             for fname, payloads in per_file.items() if payloads]
    ours = [(f, v) for f, v in tails
            if v.lua_name and _basename(v.lua_name) == target]
    if len(ours) == 1:
        fname, verdict = ours[0]
        return {"verdict": verdict, "anchor": "last record in one log file",
                "unambiguous": True, "log_file": fname,
                "records_examined": sum(len(p) for p in per_file.values())}
    # More than one file ends on this program (an earlier compile finished on
    # the same name). If they agree, which one it is does not matter.
    if len(ours) > 1 and len({v.raw for _, v in ours}) == 1:
        return {"verdict": ours[0][1],
                "anchor": (f"{len(ours)} log files end on this program and "
                           f"agree"),
                "unambiguous": True, "log_file": None,
                "records_examined": sum(len(p) for p in per_file.values())}

    # 2. Every record for this name agrees, so which one is the upload's is
    #    moot. Compare parsed basenames (not a raw substring, which would match
    #    mytest.lua for test.lua or any record quoting the name).
    matching = [v for v in (parse(p)
                            for payloads in per_file.values() for p in payloads)
                if v.lua_name and _basename(v.lua_name) == target]
    distinct = {v.raw for v in matching}
    if matching and len(distinct) == 1:
        return {"verdict": matching[0],
                "anchor": f"all {len(matching)} records for this program agree",
                "unambiguous": True, "log_file": None,
                "records_examined": sum(len(p) for p in per_file.values())}

    # 3. Say so.
    return {
        "verdict": None,
        "anchor": ("no single verdict could be attributed to this upload"
                   if matching else "no verdict for this program is in the log"),
        "unambiguous": False,
        "log_file": None,
        "candidates": sorted(distinct)[:8],
        "records_examined": sum(len(p) for p in per_file.values()),
    }


@dataclass
class FetchStats:
    fetches: int = 0
    served_from_cache: int = 0
    suppressed: dict[str, int] = field(default_factory=dict)
    last_error: str | None = None
    last_fetch_seconds: float | None = None

    def suppress(self, reason: str) -> None:
        self.suppressed[reason] = self.suppressed.get(reason, 0) + 1


class LogFetcher:
    """Fetches rblog.tar.gz, rate-limited and never more than once at a
    time. Defaults: at most one fetch per 15 s and 8 per 5-minute window."""

    def __init__(self, get_driver, *, min_interval_s: float = 15.0,
                 budget: int = 8, window_s: float = 300.0,
                 timeout: float = 120.0, enabled: bool = True):
        self._get_driver = get_driver
        self.min_interval_s = min_interval_s
        self.budget = budget
        self.window_s = window_s
        self.timeout = timeout
        self.enabled = enabled
        self.stats = FetchStats()
        # The lock guards this object's BOOKKEEPING only, and is never held
        # across the download itself -- see fetch(). Single-flight is enforced
        # by the _inflight flag instead, so a second caller is told "already
        # in progress" and returns immediately rather than parking a worker
        # from FastAPI's bounded sync pool for up to `timeout` seconds.
        self._lock = threading.Lock()
        self._inflight = False
        self._attempts: list[float] = []
        self._cache: bytes | None = None
        self._cache_started: float = 0.0

    def state(self) -> dict[str, Any]:
        now = time.monotonic()
        recent = [t for t in self._attempts if now - t < self.window_s]
        return {
            "enabled": self.enabled,
            "min_interval_s": self.min_interval_s,
            "budget_per_window": self.budget,
            "window_s": self.window_s,
            "fetches_in_window": len(recent),
            "fetches": self.stats.fetches,
            "served_from_cache": self.stats.served_from_cache,
            "suppressed": dict(self.stats.suppressed),
            "last_fetch_seconds": self.stats.last_fetch_seconds,
            "last_error": self.stats.last_error,
        }

    def _patient_driver(self) -> Any:
        """A separate driver with a long timeout, so the slow fetch does
        not block the main driver's single lock (and the stop path)."""
        from .driver import RobotDriver
        d = self._get_driver()
        return RobotDriver(d.ip, timeout=self.timeout, port=d.port,
                           upload_port=d.upload_port,
                           download_port=d.download_port)

    def fetch(self, *, covering_since: float) -> tuple[bytes | None, str]:
        """An archive built at or after covering_since, or a refusal. A
        cached archive older than covering_since cannot contain the
        record, so it is not offered."""
        if not self.enabled:
            self.stats.suppress("disabled")
            return None, "log fetching is disabled"
        with self._lock:
            if self._cache is not None and self._cache_started >= covering_since:
                self.stats.served_from_cache += 1
                return self._cache, "shared with a fetch already in progress"
            now = time.monotonic()
            self._attempts = [t for t in self._attempts
                              if now - t < self.window_s]
            if self._attempts and now - self._attempts[-1] < self.min_interval_s:
                self.stats.suppress("cooldown")
                wait = self.min_interval_s - (now - self._attempts[-1])
                return None, (
                    f"the controller log was fetched {now - self._attempts[-1]:.0f}s "
                    f"ago; the next fetch is allowed in {wait:.0f}s. This "
                    f"limit exists because repeated file-transfer load wedged "
                    f"the controller once already")
            if len(self._attempts) >= self.budget:
                self.stats.suppress("budget")
                return None, (
                    f"{len(self._attempts)} log fetches in the last "
                    f"{self.window_s:.0f}s is this gateway's ceiling; the "
                    f"upload was still rejected, the reason is just not being "
                    f"looked up")
            if self._inflight:
                # Someone else is downloading. Say so and return; do NOT wait.
                self.stats.suppress("in-flight")
                return None, ("a log fetch is already in progress; the upload "
                              "was still rejected, the reason is just not "
                              "being looked up for this request")
            started = time.monotonic()
            self._attempts.append(started)
            self._inflight = True

        # The lock is released here deliberately: this download takes up to
        # self.timeout and FastAPI runs sync routes in a bounded worker pool,
        # so holding a mutex across it would park a worker per concurrent fetch
        # and delay every other route, including POST /motion/stop. _inflight
        # keeps the single-flight guarantee without the waiting.
        try:
            blob = download(self._patient_driver(), "controller_log",
                            "rblog.tar.gz", timeout=self.timeout)["content"]
        except Exception as e:
            # A failed log fetch must never turn a rejected upload into a 500:
            # the upload's own failure is the news.
            with self._lock:
                self._inflight = False
                self.stats.last_error = f"{type(e).__name__}: {e}"
            return None, f"the controller log could not be fetched: {e}"

        with self._lock:
            self._inflight = False
            self.stats.fetches += 1
            self.stats.last_fetch_seconds = round(time.monotonic() - started, 2)
            self._cache = blob
            self._cache_started = started
        return blob, "fetched"
