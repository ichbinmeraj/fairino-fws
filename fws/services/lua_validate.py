"""Client for the controller's internal Lua validator (port 8060).

Offers a connect-and-close reachability probe. validate() is present but
disabled: no verified request framing exists for this port, and a dead client
on 8060 can stall the controller's upload path until a power cycle. Validate
Lua instead by uploading a program and reading the compiler verdict from the
log.
"""
from __future__ import annotations

import contextlib
import socket
import time

from . import ServiceError


class LuaValidateClient:
    def __init__(self, host: str, port: int = 8060, *, timeout_s: float = 8.0):
        self.host = host
        self.port = port
        self.timeout_s = timeout_s

    def probe_health(self) -> dict:
        """Connect-and-close reachability probe with inverted semantics.

        8060 is a single-client internal listener: a refused connection means
        the Lua validator is attached and healthy; a successful connection
        means nobody is attached (the impaired state).
        """
        started = time.monotonic()
        try:
            s = socket.create_connection((self.host, self.port),
                                         timeout=self.timeout_s)
        except ConnectionRefusedError:
            # Refused == socket claimed by a live client: the healthy state for
            # this single-client port, and the only OSError that means it.
            return {
                "validator_attached": True,
                "healthy": True,
                "host": self.host,
                "port": self.port,
                "probe_s": round(time.monotonic() - started, 3),
                "note": ("8060 refused the connection, which for this single-"
                         "client internal port means the Lua validator is "
                         "attached and healthy. Uploads that rely on it "
                         "should compile."),
            }
        except OSError as e:
            # Timed out / unreachable / reset: says nothing about the
            # validator, so report unknown rather than folding into "healthy".
            return {
                "validator_attached": None,
                "healthy": None,
                "host": self.host,
                "port": self.port,
                "probe_s": round(time.monotonic() - started, 3),
                "warning": (f"could not reach 8060 to judge validator health "
                            f"({type(e).__name__}: {e}). This is NOT evidence "
                            f"the validator is attached OR detached -- the "
                            f"controller may be unreachable."),
            }
        # CONNECT SUCCEEDED == nobody is attached to the validator socket.
        with contextlib.suppress(OSError):
            s.close()
        return {
            "validator_attached": False,
            "healthy": False,
            "host": self.host,
            "port": self.port,
            "probe_s": round(time.monotonic() - started, 3),
            "warning": ("8060 ACCEPTED a connection, which for this single-"
                        "client internal port means NO client is attached to "
                        "the Lua validator. The controller's own validation "
                        "is impaired and an upload may hang. Consider a "
                        "restart before uploading Lua."),
        }

    def validate(self, source: str) -> dict:
        """Compile-check `source` without uploading. Currently disabled.

        No verified request framing exists for port 8060, and a dead client on
        this port can stall the controller's upload path. Validate by upload.
        """
        raise ServiceError(
            "direct Lua validation over port 8060 is not enabled: FWS has no "
            "verified request framing for this port, and a dead client on "
            "8060 can stall the controller's upload path. Use the proven "
            "path instead -- PUT /api/v1/files/lua/{name} and read the "
            "compiler verdict "
            "(GET /api/v1/files/-/verdicts) -- which validates by upload and "
            "does not risk 8060. To enable this method, capture the request "
            "framing from a real controller first.")
