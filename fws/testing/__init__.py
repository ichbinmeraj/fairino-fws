"""Test doubles and a test harness for FWS.

Ships with the package deliberately: anyone building a client against FWS can
run it against a fake robot rather than needing hardware.

    from fws.testing import gateway

    with gateway() as g:
        g.get("/api/v1/state")
        g.controller.trip_fault()

`gateway()` runs the whole stack -- fake controller, driver, telemetry, the
FastAPI app -- on ephemeral ports. See fws/testing/harness.py. For pytest,
add `pytest_plugins = ["fws.testing.pytest_plugin"]` to your conftest and use
the `fws_gateway` fixture.

The FakeController scenario API (trip_fault, clear_fault, set_joints,
set_force, corrupt_next_frame) is a frozen surface; the rest of that class is
an implementation detail of the protocol work.
"""
from .fake_controller import FakeController, RobotState
from .harness import Gateway, Response, gateway

__all__ = ["FakeController", "Gateway", "Response", "RobotState", "gateway"]
