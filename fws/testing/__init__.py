"""Test doubles for FWS, shipped so clients can run against a fake robot
without hardware."""
from .fake_controller import FakeController, RobotState

__all__ = ["FakeController", "RobotState"]
