"""pytest fixtures for testing a client against FWS without hardware.

Enable it in your conftest.py:

    pytest_plugins = ["fws.testing.pytest_plugin"]

Then:

    def test_my_cell_logic(fws_gateway):
        assert fws_gateway.get("/api/v1/state").status_code == 200
        fws_gateway.controller.trip_fault()
        ...

`fws_gateway` is function-scoped: each test gets a clean robot, because a
fault or a taught frame leaking between tests is the kind of bug that takes
an afternoon to find. If your suite is slow because of that, use
`fws_gateway_session` and reset what you touch.
"""
from __future__ import annotations

import pytest

from .harness import gateway


@pytest.fixture
def fws_gateway():
    """A running gateway and a fresh fake robot, torn down after the test."""
    with gateway() as g:
        yield g


@pytest.fixture(scope="session")
def fws_gateway_session():
    """One gateway for the whole session. Faster, but state carries between
    tests -- you are responsible for resetting what you change."""
    with gateway() as g:
        yield g
