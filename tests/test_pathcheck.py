"""Whole-path validation before a program runs."""
from __future__ import annotations

import pytest

from fws.pathcheck import parse, validate

LIMITS = [(-175.0, 175.0), (-265.0, 85.0), (-160.0, 160.0),
          (-265.0, 85.0), (-175.0, 175.0), (-360.0, 360.0)]


def movel(x, y, z, rx=180.0, ry=0.0, rz=90.0):
    args = ["j1", "j2", "j3", "j4", "j5", "j6",
            str(x), str(y), str(z), str(rx), str(ry), str(rz)]
    return "MoveL(" + ",".join(args + ["0"] * 21) + ")"


def ok_ik(pose):
    return [0.0, -30.0, 30.0, -90.0, -80.0, 70.0]


def limits():
    return LIMITS


class TestParsing:
    def test_a_literal_target_is_extracted(self):
        targets, unresolved = parse(movel(100, 200, 300))
        assert len(targets) == 1 and not unresolved
        assert targets[0].pose == [100.0, 200.0, 300.0, 180.0, 0.0, 90.0]

    def test_a_point_name_call_is_reported_not_skipped(self):
        """Silently dropping an unreadable call would turn unknown into passed."""
        targets, unresolved = parse("Lin(mypoint,100,-1,0,0,0,0,0,0,0,0)\n")
        assert not targets
        assert len(unresolved) == 1
        assert "teaching database" in unresolved[0].why

    def test_a_computed_target_is_reported_not_skipped(self):
        src = "MoveL(j1,j2,j3,j4,j5,j6,x,y,z,rx,ry,rz" + ",0" * 21 + ")\n"
        targets, unresolved = parse(src)
        assert not targets
        assert "computed at run time" in unresolved[0].why

    def test_arcs_are_reported_because_endpoints_prove_nothing(self):
        _t, unresolved = parse("MoveC(1,2,3)\nCircle(1,2)\n")
        assert {u.call for u in unresolved} == {"MoveC", "Circle"}
        assert all("endpoints" in u.why for u in unresolved)

    def test_commented_out_motion_is_not_counted(self):
        targets, unresolved = parse("-- " + movel(1, 2, 3) + "\n")
        assert not targets and not unresolved

    def test_line_numbers_are_reported(self):
        targets, _u = parse("-- header\n\n" + movel(1, 2, 3) + "\n")
        assert targets[0].line == 3


class TestValidation:
    def test_a_clean_program_passes(self):
        rep = validate(movel(100, 200, 300), inverse_kin=ok_ik,
                       joint_limits=limits)
        assert rep["safe_to_run"] is True
        assert rep["complete"] is True
        assert rep["failed"] == 0

    def test_an_unreachable_target_fails(self):
        """No IK solution for the approach pose fails the target."""
        def no_solution(pose):
            raise RuntimeError("GetInverseKin returned [112, ...]")

        rep = validate(movel(-802, -195, 505), inverse_kin=no_solution,
                       joint_limits=limits)
        assert rep["safe_to_run"] is False
        assert rep["failed"] == 1
        assert "unreachable" in rep["failures"][0]["problems"][0]

    def test_a_joint_limit_violation_names_the_joint_and_the_band(self):
        def near_limit(pose):
            return [0.0, -264.5, 30.0, -90.0, -80.0, 70.0]

        rep = validate(movel(1, 2, 3), inverse_kin=near_limit,
                       joint_limits=limits)
        p = rep["failures"][0]["problems"][0]
        assert "J2" in p and "-264.50" in p and "outside its safe band" in p

    def test_a_z_floor_is_enforced_when_configured(self):
        rep = validate(movel(0, 0, 50), inverse_kin=ok_ik,
                       joint_limits=limits, z_floor=100.0)
        assert rep["failed"] == 1
        assert "below the configured floor" in rep["failures"][0]["problems"][0]


class TestHonesty:
    """Report honesty: unresolved motion must not read as safe."""

    def test_unresolvable_calls_make_the_report_incomplete(self):
        src = movel(1, 2, 3) + "\nLin(pt,100,-1,0,0,0,0,0,0,0,0)\n"
        rep = validate(src, inverse_kin=ok_ik, joint_limits=limits)
        assert rep["failed"] == 0, "the one checkable target is fine"
        assert rep["complete"] is False
        assert rep["safe_to_run"] is False, (
            "a program with unreadable motion must NOT be called safe just "
            "because everything readable passed")
        assert rep["unchecked"] == 1

    def test_the_verdict_says_it_is_not_a_clean_bill_of_health(self):
        src = movel(1, 2, 3) + "\nPTP(pt,100,0,0,0,0,0,0,0,0)\n"
        rep = validate(src, inverse_kin=ok_ik, joint_limits=limits)
        assert "NOT a clean bill of health" in rep["verdict"]

    def test_what_it_does_not_prove_is_stated_in_every_report(self):
        rep = validate(movel(1, 2, 3), inverse_kin=ok_ik, joint_limits=limits)
        text = " ".join(rep["what_this_does_not_prove"])
        assert "cell is clear" in text
        assert "BETWEEN two checked points" in text

    def test_a_program_with_no_motion_is_not_called_safe_by_default(self):
        rep = validate("WaitMs(10)\n", inverse_kin=ok_ik, joint_limits=limits)
        assert rep["checked"] == 0
        assert "no motion calls" in rep["verdict"]

    def test_truncation_is_reported_rather_than_silent(self):
        """A per-request cap must report what it dropped, not read as full coverage."""
        src = "\n".join(movel(i, 0, 300) for i in range(20))
        rep = validate(src, inverse_kin=ok_ik, joint_limits=limits,
                       max_checks=5)
        assert rep["checked"] == 5
        assert rep["unchecked"] == 15
        assert rep["complete"] is False
        assert any("per-request limit" in u["why"]
                   for u in rep["unchecked_detail"])


class TestOpeningTransit:
    """The unwritten opening move: from the current pose to the first target."""

    def test_it_is_measured_and_explained(self):
        rep = validate(movel(-802.4, -195.1, 505.0), inverse_kin=ok_ik,
                       joint_limits=limits,
                       current_pose=[-872.4, 24.8, 214.6, 0, 0, 0])
        o = rep["opening_transit"]
        assert o["distance_mm"] == pytest.approx(370.9, abs=1.0)
        assert "where the arm was left" in o["note"]

    def test_absent_when_the_current_pose_is_unknown(self):
        rep = validate(movel(1, 2, 3), inverse_kin=ok_ik, joint_limits=limits)
        assert rep["opening_transit"] is None


class TestARecognisedCallIsNeverInvisible:
    """A recognised motion call must never be silently invisible; unseen
    is worse than unchecked."""

    LIMITS = staticmethod(lambda: [(-175.0, 175.0)] * 6)
    POSE = "750.0, 0.0, 550.0, 180.0, 0.0, 0.0"

    def _v(self, src):
        from fws import pathcheck
        return pathcheck.validate(src, inverse_kin=lambda p: [0.0] * 6,
                                  joint_limits=self.LIMITS)

    def test_a_call_split_across_lines_is_seen(self):
        r = self._v(f"MoveL(j1,j2,j3,j4,j5,j6,\n      {self.POSE}, 0,0,0)")
        assert r["motion_calls_found"] == 1
        assert r["checked"] == 1, "and it is fully checked, not just flagged"

    def test_a_nested_call_in_the_arguments_is_seen(self):
        """A nested call where a number belongs must not lose the MoveL."""
        r = self._v("MoveL(j1,j2,j3,j4,j5,j6, 750.0, 0.0, 550.0, calc(1),"
                    " 0.0, 0.0, 0,0,0)")
        assert r["motion_calls_found"] == 1
        assert r["unchecked"] == 1
        assert r["safe_to_run"] is False

    def test_an_unclosed_argument_list_is_reported_not_dropped(self):
        r = self._v("MoveL(j1,j2,j3,j4,j5,j6, 750.0, 0.0")
        assert r["unchecked"] == 1
        assert "never closed" in r["unchecked_detail"][0]["why"]
        assert r["safe_to_run"] is False

    def test_an_unrecognised_form_is_reported_not_dropped(self):
        """A MoveL too short to hold a pose is reported as unrecognised, not guessed."""
        r = self._v("MoveL(1,2,3)")
        assert r["unchecked"] == 1
        assert "does not recognise this form" in r["unchecked_detail"][0]["why"]

    def test_a_nested_motion_name_is_not_counted_twice(self):
        r = self._v(f"MoveL(j1,j2,j3,j4,j5,j6, {self.POSE}, 0,0,0)\n"
                    f"MoveL(j1,j2,j3,j4,j5,j6, {self.POSE}, 0,0,0)")
        assert r["motion_calls_found"] == 2

    def test_comment_stripping_does_not_shift_line_numbers(self):
        """The comment stripper must preserve length so line numbers stay correct."""
        r = self._v("-- a long comment that would shift an offset\n"
                    "local x = 1  -- another\n"
                    "Lin(P1, 0, 0, 0)\n")
        assert r["unchecked_detail"][0]["line"] == 3
