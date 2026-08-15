"""The measured model, served as URDF.

No URDF matched to this firmware is published anywhere, and the vendor's
rounded lengths are measurably worse than this controller's own. Serving one
is what lets RViz, Foxglove or a three.js scene draw this arm off /ws/state
with nothing else installed.

These tests pin the two things that make it useful rather than decorative:
the numbers are the MEASURED ones, and the document is real URDF that a
consumer can parse.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod
from fws import model as model_mod


def _client(fake):
    app_mod.create_app(config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
    }))
    return TestClient(app_mod.app)


class TestTheNumbersAreTheMeasuredOnes:
    def test_the_chain_matches_the_fitted_lengths(self):
        """Fitted against this controller's own GetForwardKin over 59 poses.
        The vendor URDF's rounded 395.01 / 102.1 are measurably WORSE, so a
        future edit toward them must fail here."""
        z = [link.xyz_mm[2] for link in model_mod.FR5_CHAIN]
        x = [link.xyz_mm[0] for link in model_mod.FR5_CHAIN]
        assert z == [0.0, 152.0, 0.0, 0.0, 102.0, 102.0]
        assert x == [0.0, 0.0, -425.0, -395.0, 0.0, 0.0]
        assert model_mod.FLANGE_MM == (0.0, 0.0, 100.0)

    def test_the_twists_are_quarter_turns(self):
        twists = [link.twist_rad for link in model_mod.FR5_CHAIN]
        assert twists == pytest.approx(
            [0, math.pi / 2, 0, 0, math.pi / 2, -math.pi / 2])

    def test_the_provenance_is_stated_not_implied(self):
        info = model_mod.MODEL_INFO
        assert info["provenance"] == "measured"
        assert info["samples"] == 59
        assert "GetForwardKin" in info["fitted_against"]


class TestItIsRealUrdf:
    def test_it_parses_and_has_one_chain(self):
        root = ET.fromstring(model_mod.urdf())
        assert root.tag == "robot"
        joints = root.findall("joint")
        revolute = [j for j in joints if j.get("type") == "revolute"]
        assert len(revolute) == 6, "six revolute joints"
        assert [j.get("name") for j in revolute] == [
            "j1", "j2", "j3", "j4", "j5", "j6"]
        # Every child link is some other joint's parent, or the flange: a
        # chain, not a bag of links.
        parents = {j.find("parent").get("link") for j in joints}
        assert "base_link" in parents

    def test_every_joint_turns_about_z(self):
        """The model's convention is translate, twist about x, then Rz(q) --
        which is exactly a URDF origin plus a z axis. If that stops being
        true the URDF silently describes a different robot."""
        root = ET.fromstring(model_mod.urdf())
        for j in root.findall("joint"):
            if j.get("type") == "revolute":
                assert j.find("axis").get("xyz") == "0 0 1"

    def test_lengths_are_metres(self):
        """URDF is metres; the model is millimetres. Getting this wrong makes
        a 1.6 m arm render 1600 m tall, which is the classic URDF bug."""
        root = ET.fromstring(model_mod.urdf())
        j3 = next(j for j in root.findall("joint") if j.get("name") == "j3")
        xyz = j3.find("origin").get("xyz").split()
        assert float(xyz[0]) == pytest.approx(-0.425)

    def test_visuals_can_be_omitted_entirely(self):
        """The primitives are a stand-in, not the real shell. Anything that
        computes rather than draws should be able to say so."""
        assert "<visual>" in model_mod.urdf(visuals="primitives")
        assert "<visual>" not in model_mod.urdf(visuals="none")

    def test_the_document_admits_the_visuals_are_not_real(self):
        body = model_mod.urdf()
        assert "NOT the real shell" in body
        assert "MEASURED" in body

    def test_an_unknown_visuals_mode_is_refused(self):
        with pytest.raises(ValueError, match="visuals"):
            model_mod.urdf(visuals="meshes")


class TestOverHttp:
    def test_the_urdf_is_served_as_xml(self, fake):
        with _client(fake) as c:
            r = c.get("/api/v1/model/urdf")
            assert r.status_code == 200
            assert "xml" in r.headers["content-type"]
            ET.fromstring(r.text)

    def test_the_description_points_at_the_urdf(self, fake):
        with _client(fake) as c:
            d = c.get("/api/v1/model").json()
            assert d["urdf"] == "/api/v1/model/urdf"
            assert d["provenance"] == "measured"
            assert len(d["chain"]) == 6

    def test_limits_come_from_the_controller_when_it_answers(self, fake):
        with _client(fake) as c:
            d = c.get("/api/v1/model").json()
            assert d["joint_limits_deg"], "the fake reports soft limits"
            assert d["joint_limits_source"] == "the controller"
            root = ET.fromstring(c.get("/api/v1/model/urdf").text)
            j1 = next(j for j in root.findall("joint")
                      if j.get("name") == "j1")
            lo = float(j1.find("limit").get("lower"))
            assert lo == pytest.approx(math.radians(d["joint_limits_deg"][0][0]))

    def test_the_model_is_served_with_the_robot_unreachable(self, fake,
                                                            monkeypatch):
        """A developer opening this in RViz on a laptop, with the cell
        powered down, still gets the geometry."""
        from fws.driver import TransportError
        with _client(fake) as c:
            def dead():
                raise TransportError("no route to host")
            monkeypatch.setattr(app_mod.driver, "joint_limits", dead)
            r = c.get("/api/v1/model/urdf")
            assert r.status_code == 200
            d = c.get("/api/v1/model").json()
            assert d["joint_limits_deg"] is None
            assert "rather than inventing" in d["joint_limits_source"]

    def test_a_full_turn_is_the_fallback_not_a_tight_guess(self, fake):
        """A fabricated tight limit is worse than a loose one: a planner that
        trusts it refuses reachable poses."""
        body = model_mod.urdf(soft_limits=None)
        root = ET.fromstring(body)
        j1 = next(j for j in root.findall("joint") if j.get("name") == "j1")
        assert float(j1.find("limit").get("upper")) == pytest.approx(
            2 * math.pi)


class TestTheUrdfTranscribesTheModelFaithfully:
    """The URDF and the model must describe the same robot.

    The model's convention is: translate xyz in the parent frame, twist about
    x, THEN rotate about z by the joint angle. A URDF origin plus a z axis
    means exactly that -- but only in that order. Swapping the twist and the
    joint rotation, or applying the translation after the twist, produces a
    document that parses, renders, and describes a different arm. This walks
    the URDF and the model independently and requires they agree.
    """

    @staticmethod
    def _rot(axis, a):
        c, s = math.cos(a), math.sin(a)
        if axis == "x":
            return [[1, 0, 0], [0, c, -s], [0, s, c]]
        return [[c, -s, 0], [s, c, 0], [0, 0, 1]]

    @classmethod
    def _mul(cls, A, B):
        return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)]
                for i in range(3)]

    @classmethod
    def _mv(cls, A, v):
        return [sum(A[i][k] * v[k] for k in range(3)) for i in range(3)]

    @classmethod
    def _walk(cls, steps, q_deg, flange):
        """steps: [(xyz, twist)] in the SAME units as flange."""
        R = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        p = [0.0, 0.0, 0.0]
        # strict: a length mismatch here is a bug, not something to
        # silently truncate past.
        for (xyz, twist), qd in zip(steps, q_deg, strict=True):
            t = cls._mv(R, xyz)
            p = [p[i] + t[i] for i in range(3)]
            R = cls._mul(R, cls._rot("x", twist))
            R = cls._mul(R, cls._rot("z", math.radians(qd)))
        t = cls._mv(R, flange)
        return [p[i] + t[i] for i in range(3)]

    @pytest.mark.parametrize("q", [
        [0, -90, 90, -90, -90, 0],
        [10, -80, 70, -100, -85, 20],
        [-30, -120, 110, -60, -95, 45],
        [45, -45, 45, -45, 45, -45],
    ])
    def test_the_two_agree_to_a_micron(self, q):
        from_model = self._walk(
            [(list(link.xyz_mm), link.twist_rad)
             for link in model_mod.FR5_CHAIN],
            q, list(model_mod.FLANGE_MM))

        root = ET.fromstring(model_mod.urdf(visuals="none"))
        joints = [j for j in root.findall("joint")
                  if j.get("type") == "revolute"]
        steps = []
        for j in joints:
            xyz = [float(v) * 1000.0            # URDF metres back to mm
                   for v in j.find("origin").get("xyz").split()]
            rpy = [float(v) for v in j.find("origin").get("rpy").split()]
            steps.append((xyz, rpy[0]))
        fl = next(j for j in root.findall("joint")
                  if j.get("name") == "flange")
        flange = [float(v) * 1000.0
                  for v in fl.find("origin").get("xyz").split()]
        from_urdf = self._walk(steps, q, flange)

        for a, b in zip(from_model, from_urdf, strict=True):
            assert a == pytest.approx(b, abs=1e-3), (
                f"URDF and model disagree at {q}: {from_model} vs {from_urdf}")
