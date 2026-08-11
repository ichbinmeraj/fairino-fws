"""Capability probing, and the difference between "no" and "no answer"."""
from __future__ import annotations

import pytest

from fws import capabilities as caps_mod
from fws.capabilities import ABSENT, AVAILABLE, UNKNOWN, Capabilities
from fws.driver import ControllerFault, RobotError, TransportError

# A real entry, so require()'s re-probe can find it in PROBES.
FEATURE, METHOD = "payload.cog", "GetTargetPayloadCog"


class StubDriver:
    """Answers however the test needs, and counts the asking."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls: list[str] = []

    def _call(self, method, *args, **kw):
        self.calls.append(method)
        b = self.behaviour
        if callable(b):
            b = b(method, len(self.calls))
        if isinstance(b, BaseException):
            raise b
        return b


def _caps(behaviour):
    return Capabilities(StubDriver(behaviour))


class TestTheThreeStates:
    def test_a_controller_fault_is_ABSENT(self):
        """A controller fault (-506) is recorded as ABSENT."""
        c = _caps(ControllerFault("GetX: fault -506: no such method", -506))
        c.probe()
        assert c.state(FEATURE) == ABSENT

    def test_a_transport_error_is_UNKNOWN_not_ABSENT(self):
        """A transport error is UNKNOWN, not ABSENT."""
        c = _caps(TransportError("GetX: transport error: timed out"))
        c.probe()
        assert c.state(FEATURE) == UNKNOWN, (
            "a dropped packet must never be recorded as a missing feature")
        assert "could not ask" in c._map[FEATURE].detail

    def test_an_error_code_in_a_normal_reply_is_ABSENT(self):
        c = _caps([-506, 0, 0])
        c.probe()
        assert c.state(FEATURE) == ABSENT
        assert "-506" in c._map[FEATURE].detail

    def test_a_zero_code_is_AVAILABLE(self):
        c = _caps([0, 1.0, 2.0])
        c.probe()
        assert c.state(FEATURE) == AVAILABLE

    def test_an_unprobed_feature_is_UNKNOWN_not_ABSENT(self):
        assert _caps([0]).state("payload.cog") == UNKNOWN


class TestUnrecognisedRepliesAreNotSuccess:
    """An unrecognised reply shape is UNKNOWN, not AVAILABLE."""

    @pytest.mark.parametrize("reply", [None, [], "unexpected", {"a": 1}])
    def test_an_unexpected_shape_is_UNKNOWN(self, reply):
        c = _caps(reply)
        c.probe()
        assert c.state(FEATURE) == UNKNOWN, (
            f"{reply!r} read as AVAILABLE under the old boolean test")
        assert "unrecognised reply" in c._map[FEATURE].detail

    def test_a_bare_zero_is_still_AVAILABLE(self):
        """Some getters answer with a bare int. That must keep working."""
        c = _caps(0)
        c.probe()
        assert c.state(FEATURE) == AVAILABLE


class TestRequireSaysWhichKindOfNo:
    def test_ABSENT_blames_the_firmware_because_that_is_true(self):
        c = _caps(ControllerFault("GetX: fault -506: nope", -506))
        c.probe()
        with pytest.raises(RobotError) as e:
            c.require(FEATURE)
        assert "does not support" in str(e.value)
        assert "later-firmware" in str(e.value)

    def test_UNKNOWN_never_blames_the_firmware(self):
        """UNKNOWN never blames the firmware."""
        c = _caps(TransportError("GetX: transport error: timed out"))
        c.probe()
        with pytest.raises(RobotError) as e:
            c.require(FEATURE)
        msg = str(e.value)
        assert "does not know whether" in msg
        assert "NOT evidence the feature is missing" in msg
        assert "later-firmware" not in msg, (
            "a network fault must never be reported as a firmware limitation")
        assert "refresh" in msg, "and it must say how to find out"

    def test_UNKNOWN_re_asks_and_succeeds_if_the_link_recovered(self):
        """UNKNOWN re-asks and succeeds if the link recovered."""
        # Key on the METHOD, not the call index: probe() walks all 32
        # entries, so counting calls does not isolate one feature.
        down = {"v": True}

        def flaky(method, n):
            if method == METHOD and down["v"]:
                return TransportError("timed out")
            return [0, 1.0]

        c = _caps(flaky)
        c.probe()
        assert c.state(FEATURE) == UNKNOWN
        down["v"] = False                       # the link comes back
        c.require(FEATURE)                      # must not raise
        assert c.state(FEATURE) == AVAILABLE, "and the answer is remembered"

    def test_ABSENT_is_not_re_asked(self):
        """ABSENT is not re-asked."""
        c = _caps(ControllerFault("GetX: fault -506: nope", -506))
        c.probe()
        before = len(c.driver.calls)
        for _ in range(3):
            with pytest.raises(RobotError):
                c.require(FEATURE)
        assert len(c.driver.calls) == before

    def test_a_feature_that_is_not_probed_at_all_says_so(self):
        c = _caps([0])
        with pytest.raises(RobotError) as e:
            c.require("not.a.real.feature")
        assert "not a probed capability" in str(e.value)


class TestReporting:
    def test_the_summary_separates_absent_from_unknown(self):
        c = _caps(lambda method, n: ([0] if n % 3 == 0
                                     else ControllerFault("f", -506)
                                     if n % 3 == 1 else TransportError("t")))
        c.probe()
        d = c.as_dict()
        assert d["absent"] > 0 and d["unknown"] > 0
        assert d["available"] + d["absent"] + d["unknown"] == d["total"]
        assert d["unavailable"] == d["absent"] + d["unknown"], (
            "the legacy field is kept, but it is the one that summed two "
            "different things and hid the bug")

    def test_every_entry_carries_its_state_not_just_a_boolean(self):
        c = _caps(TransportError("t"))
        c.probe()
        entry = c.as_dict()["groups"]["payload"]["cog"]
        assert entry["state"] == UNKNOWN
        assert entry["available"] is False, (
            "`available` stays False for UNKNOWN -- right for 'may I use "
            "this', which is why `state` exists for the other question")

    def test_the_states_are_explained_in_the_payload(self):
        d = _caps([0]).as_dict()
        assert "NOT evidence" in d["states"][UNKNOWN]
        assert "fact about this firmware" in d["states"][ABSENT]


class TestHasIsHonestlyNamed:
    def test_has_is_false_for_unknown_and_that_is_documented(self):
        c = _caps(TransportError("t"))
        c.probe()
        assert c.has(FEATURE) is False
        assert "UNKNOWN reads False" in Capabilities.has.__doc__

    def test_the_module_exposes_the_three_constants(self):
        assert {caps_mod.AVAILABLE, caps_mod.ABSENT, caps_mod.UNKNOWN} == {
            "available", "absent", "unknown"}
