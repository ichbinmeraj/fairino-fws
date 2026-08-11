"""Shared fixtures."""
from __future__ import annotations

import time

import pytest

from fws.driver import RobotDriver
from fws.telemetry import Telemetry
from fws.testing import FakeController


def pytest_addoption(parser):
    parser.addoption("--hardware", action="store_true", default=False,
                     help="run tests against a real robot (read-only)")
    parser.addoption("--hardware-motion", action="store_true", default=False,
                     help="run tests that MOVE a real robot")


def pytest_collection_modifyitems(config, items):
    """Gate hardware and motion tests behind opt-in flags."""
    import os

    skip_hw = pytest.mark.skip(reason="needs --hardware")
    skip_motion = pytest.mark.skip(
        reason="needs --hardware-motion, FWS_CELL_IS_CLEAR=1, and no CI env")
    for item in items:
        if "hardware_motion" in item.keywords:
            allowed = (config.getoption("--hardware-motion")
                       and os.environ.get("FWS_CELL_IS_CLEAR") == "1"
                       and not os.environ.get("CI"))
            if not allowed:
                item.add_marker(skip_motion)
        elif "hardware" in item.keywords:
            if not config.getoption("--hardware"):
                item.add_marker(skip_hw)


@pytest.fixture
def fake():
    with FakeController(jog_start_latency_s=0.05,
                        transfer_port_delay_s=0.05) as controller:
        time.sleep(0.15)
        yield controller


@pytest.fixture
def driver(fake):
    return RobotDriver(fake.host, timeout=3.0, port=fake.rpc_port,
                       upload_port=fake.upload_port,
                       download_port=fake.download_port)


@pytest.fixture
def telemetry(fake):
    stream = Telemetry(fake.host, port=fake.stream_port)
    stream.start()
    deadline = time.time() + 3.0
    while time.time() < deadline and not stream.snapshot().get("joints"):
        time.sleep(0.02)
    yield stream
    stream.close()
