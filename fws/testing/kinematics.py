"""A small, self-consistent 6-DOF kinematic model for the fake controller
(not an accurate FR5).

Forward and inverse are exact inverses of each other; reachability, joint
limits and singular configurations are real, so the fake can return error 112
the way the controller does.
"""
from __future__ import annotations

import math

D1 = 152.0      # base to shoulder
A2 = 425.0      # upper arm
A3 = 395.0      # forearm
D6 = 100.0      # wrist to TCP

REACH = A2 + A3 + D6


def forward(joints: list[float]) -> list[float]:
    """Joint angles (degrees) -> TCP pose [x, y, z, rx, ry, rz]."""
    j1, j2, j3, j4, j5, j6 = (math.radians(j) for j in joints)

    r = A2 * math.cos(j2) + A3 * math.cos(j2 + j3) + D6
    z = D1 + A2 * math.sin(j2) + A3 * math.sin(j2 + j3)

    x = r * math.cos(j1)
    y = r * math.sin(j1)

    # Orientation is a deliberately simple, invertible map. It is not a real
    # spherical wrist; it exists so that IK can recover the joints exactly.
    rx = math.degrees(j4)
    ry = math.degrees(j5)
    rz = math.degrees(j6)
    return [round(v, 4) for v in (x, y, z, rx, ry, rz)]


class Unreachable(ValueError):
    """No inverse solution exists -- the controller reports error 112."""


def inverse(pose: list[float], reference: list[float] | None = None
            ) -> list[float]:
    """TCP pose -> joint angles (degrees). Raises Unreachable when outside."""
    x, y, z, rx, ry, rz = pose

    j1 = math.atan2(y, x)

    # Distance from the shoulder to the wrist, in the arm plane.
    r = math.hypot(x, y) - D6
    zz = z - D1
    planar = math.hypot(r, zz)

    # The overall envelope, checked FIRST and in Cartesian terms. The planar
    # test below subtracts the tool length before measuring, so without this a
    # pose beyond the envelope could parse as reachable. A fake more permissive
    # than the robot is the dangerous direction: a path validates here and the
    # arm refuses it there.
    envelope = math.sqrt(x * x + y * y + z * z)
    if envelope > REACH:
        raise Unreachable(
            f"target {envelope:.1f} mm from the base, beyond the "
            f"{REACH:.1f} mm envelope"
        )

    if planar > (A2 + A3) or planar < abs(A2 - A3):
        raise Unreachable(
            f"target {planar:.1f} mm from shoulder, workspace is "
            f"{abs(A2 - A3):.1f}..{A2 + A3:.1f} mm"
        )

    cos_j3 = (r * r + zz * zz - A2 * A2 - A3 * A3) / (2 * A2 * A3)
    cos_j3 = max(-1.0, min(1.0, cos_j3))
    sin_j3 = math.sqrt(max(0.0, 1.0 - cos_j3 * cos_j3))

    # Elbow branch: prefer the one nearer the reference pose, mirroring
    # GetInverseKin(config=-1), which solves from the current joint position.
    if reference is not None and reference[2] < 0:
        sin_j3 = -sin_j3
    j3 = math.atan2(sin_j3, cos_j3)
    j2 = math.atan2(zz, r) - math.atan2(A3 * math.sin(j3), A2 + A3 * math.cos(j3))

    return [round(v, 4) for v in (
        math.degrees(j1), math.degrees(j2), math.degrees(j3),
        rx, ry, rz,
    )]
