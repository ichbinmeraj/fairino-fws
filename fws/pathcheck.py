"""Validate a Lua program's motion targets before the controller runs it.

Reads the program as text and checks motion calls whose target is written as
literal numbers. Calls with named points, run-time-computed poses, arcs,
splines and servo streaming cannot be resolved and are reported as unchecked,
never silently dropped: a report is `partial` unless every motion call was
resolvable. The opening transit (from wherever the arm is to the first target)
is reported separately.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Motion calls whose target is a literal pose that can be extracted.
#   MoveL(j1..j6, x, y, z, rx, ry, rz, ...)      33 args
#   MoveJ(j1..j6, x, y, z, rx, ry, rz, ...)      29 args
# Both put the Cartesian pose at argument index 6..11.
COORDINATE_FORMS = {"MoveL": 6, "MoveJ": 6, "MoveCart": 0}

# Motion calls that are recognised but cannot be resolved, and why. Reported,
# never silently dropped.
UNRESOLVABLE = {
    "Lin": "takes a point name from the teaching database",
    "PTP": "takes a point name from the teaching database",
    "ARC": "takes point names, and the arc bulges away from its endpoints",
    "MoveC": "an arc: checking the endpoints says nothing about the middle",
    "Circle": "a full circle: endpoints prove nothing about the path",
    "NewSpiral": "a spiral; the path is not its endpoints",
    "Spiral": "a spiral; the path is not its endpoints",
    "ServoJ": "streamed servo motion, target per cycle",
    "ServoCart": "streamed servo motion, target per cycle",
    "MoveTPD": "replays a recorded trajectory FWS cannot read",
    "MoveTrajectory": "replays a trajectory file FWS cannot read",
}

_NAME = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_NUM = re.compile(r"^-?\d+(?:\.\d+)?$")


@dataclass
class Target:
    line: int
    call: str
    pose: list[float]
    joints: list[float] | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass
class Unresolved:
    line: int
    call: str
    why: str


def _strip_comments(src: str) -> str:
    """Blank out Lua line comments, preserving length and line structure
    so offsets still map to line numbers. Block comments are left as live
    text (over-reporting is the safe direction)."""
    out = []
    for ln in src.split("\n"):
        i = ln.find("--")
        out.append(ln if i < 0 else ln[:i] + " " * (len(ln) - i))
    return "\n".join(out)


def _balanced(src: str, open_idx: int) -> tuple[str | None, int]:
    """Argument text of the call whose `(` is at open_idx, and the index
    of its `)`. Depth-counted, spanning newlines and nested calls."""
    depth = 0
    for i in range(open_idx, len(src)):
        c = src[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return src[open_idx + 1:i], i
    return None, len(src)


def _split_args(text: str) -> list[str]:
    """Split on commas at depth zero. `MoveL(a, calc(1,2), b)` is three
    arguments, not four."""
    out: list[str] = []
    depth = 0
    cur: list[str] = []
    for c in text:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
    out.append("".join(cur).strip())
    return out


def parse(src: str) -> tuple[list[Target], list[Unresolved]]:
    """Motion calls found in the program text, split into checkable
    Targets and Unresolved. A recognised-but-not-understood call becomes
    an Unresolved, never nothing."""
    text = _strip_comments(src)
    targets: list[Target] = []
    unresolved: list[Unresolved] = []
    pos = 0
    while (m := _NAME.search(text, pos)):
        name = m.group(1)
        open_idx = m.end() - 1
        known = name in COORDINATE_FORMS or name in UNRESOLVABLE
        if not known:
            pos = m.end()
            continue
        line = text.count("\n", 0, m.start()) + 1
        raw, close = _balanced(text, open_idx)
        # Consume the whole call, so a motion name nested inside another
        # call's arguments is not counted twice.
        pos = close + 1
        if raw is None:
            unresolved.append(Unresolved(
                line, name, "its argument list is never closed -- the file is "
                            "truncated or malformed"))
            continue
        if name in UNRESOLVABLE:
            unresolved.append(Unresolved(line, name, UNRESOLVABLE[name]))
            continue
        args = _split_args(raw)
        start = COORDINATE_FORMS[name]
        window = args[start:start + 6]
        if len(window) < 6:
            unresolved.append(Unresolved(
                line, name, f"it has {len(args)} arguments, too few to hold a "
                            f"pose at position {start}; FWS does not recognise "
                            f"this form of the call"))
            continue
        if not all(_NUM.match(a) for a in window):
            unresolved.append(Unresolved(
                line, name,
                "its target is not literal numbers -- computed at run "
                "time, so its value is not in the program text"))
            continue
        targets.append(Target(line, name, [float(a) for a in window]))
    return targets, unresolved


def validate(src: str, *, inverse_kin, joint_limits, current_pose=None,
             limit_margin_deg: float = 2.0, z_floor: float | None = None,
             max_checks: int = 200) -> dict[str, Any]:
    """Check every resolvable target; sends no motion. inverse_kin and
    joint_limits are injected to keep this module driver-free and testable."""
    targets, unresolved = parse(src)
    limits = joint_limits()

    truncated = 0
    if len(targets) > max_checks:
        # Each check is an RPC, and the driver serialises the whole channel.
        # A thousand-move program must not hold that lock for minutes.
        truncated = len(targets) - max_checks
        targets = targets[:max_checks]

    for t in targets:
        try:
            t.joints = [round(v, 4) for v in inverse_kin(t.pose)]
        except Exception as e:
            t.problems.append(f"unreachable: no inverse-kinematics solution ({e})")
            continue
        for k, (lo, hi) in enumerate(limits):
            v = t.joints[k]
            if not (lo + limit_margin_deg <= v <= hi - limit_margin_deg):
                t.problems.append(
                    f"J{k + 1} would be {v:.2f}deg, outside its safe band "
                    f"[{lo + limit_margin_deg:.1f}, {hi - limit_margin_deg:.1f}]")
        if z_floor is not None and t.pose[2] < z_floor:
            t.problems.append(
                f"Z {t.pose[2]:.1f} is below the configured floor {z_floor:.1f}")

    failed = [t for t in targets if not t.ok]

    opening: dict[str, Any] | None = None
    if current_pose and targets:
        first = targets[0].pose
        pairs = zip(current_pose[:3], first[:3], strict=False)
        dist = sum((a - b) ** 2 for a, b in pairs) ** 0.5
        opening = {
            "from": [round(v, 2) for v in current_pose[:3]],
            "to": [round(v, 2) for v in first[:3]],
            "distance_mm": round(dist, 1),
            "note": ("when the program starts the arm travels this far to "
                     "reach its first target, as one uninterrupted move. "
                     "Nothing in the program says so -- it depends on where "
                     "the arm was left."),
        }

    complete = not unresolved and not truncated
    return {
        "motion_calls_found": len(targets) + len(unresolved),
        "checked": len(targets),
        "passed": len(targets) - len(failed),
        "failed": len(failed),
        "unchecked": len(unresolved) + truncated,
        "safe_to_run": not failed and complete,
        "complete": complete,
        "verdict": _verdict(failed, unresolved, truncated, len(targets)),
        "failures": [{"line": t.line, "call": t.call,
                      "pose": t.pose, "problems": t.problems} for t in failed],
        "unchecked_detail": (
            [{"line": u.line, "call": u.call, "why": u.why} for u in unresolved]
            + ([{"line": None, "call": None,
                 "why": f"{truncated} further targets not checked: the "
                        f"per-request limit is {max_checks}, because each "
                        f"check is an RPC on the serialised command channel"}]
               if truncated else [])),
        "opening_transit": opening,
        "limits_used": [[round(lo, 1), round(hi, 1)] for lo, hi in limits],
        "limit_margin_deg": limit_margin_deg,
        "what_this_does_not_prove": [
            "that the cell is clear",
            "that the path BETWEEN two checked points is clear -- only the "
            "endpoints were solved",
            "anything about arcs, splines or servo streaming",
            "anything about a pose the program computes at run time",
        ],
    }


def _verdict(failed, unresolved, truncated, checked) -> str:
    if failed:
        return (f"{len(failed)} of {checked} checked targets cannot be reached "
                f"or would violate a joint limit")
    if unresolved or truncated:
        n = len(unresolved) + truncated
        return (f"every checked target is reachable, but {n} motion call(s) "
                f"could not be checked -- this is NOT a clean bill of health")
    if not checked:
        return "no motion calls were found in this program"
    return f"all {checked} motion targets are reachable and inside the limits"
