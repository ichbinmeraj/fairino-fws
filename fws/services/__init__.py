"""Typed clients for the controller's QNX base services: FTP (21), telnet (23),
qconn (8000) and the internal Lua validator (8060).

Each client opens a short-lived connection per operation and raises
ServiceError (never a bare socket error) so the API layer catches one family.
"""
from __future__ import annotations


class ServiceError(RuntimeError):
    """Base error for controller base-service failures."""


class ServiceAuthError(ServiceError):
    """Service answered and rejected the credentials."""


class ServiceTimeout(ServiceError):
    """Service did not answer or finish in time."""


class ServiceUnavailable(ServiceError):
    """Port could not be reached: refused, reset, or no route."""
