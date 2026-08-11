"""The Lua catalogue, the bridge to XML-RPC, and the conflicts between them."""
from __future__ import annotations

from fws.protocol import lua_bridge as bridge
from fws.protocol.commands import COMMANDS
from fws.protocol.lua_functions import (
    ARITY_CONFLICTS,
    LUA_FUNCTIONS,
    MANUAL_ERRATA,
    by_section,
    resolve,
)


class TestCatalogue:
    def test_the_force_section_is_complete(self):
        force = {n for n, f in LUA_FUNCTIONS.items()
                 if f.section.startswith("3.6")}
        # Every force function the manual documents, by name.
        for n in ("FT_Guard", "FT_Control", "FT_SpiralSearch",
                  "FT_RotInsertion", "FT_LinInsertion", "FT_FindSurface",
                  "FT_ComplianceStart", "FT_ComplianceStop", "FT_Click",
                  "FT_CalCenterStart", "FT_CalCenterEnd",
                  "TorqueRecordStart", "TorqueRecordEnd", "TorqueRecordReset"):
            assert n in force, f"{n} missing from the force section"

    def test_movel_arity_agrees_across_three_independent_sources(self):
        """MoveL arity is 33, agreeing across three independent sources."""
        assert LUA_FUNCTIONS["MoveL"].arity == 33

    def test_point_name_and_coordinate_forms_are_distinguishable(self):
        """Lin/PTP take a point name; MoveL/MoveJ take coordinates."""
        assert LUA_FUNCTIONS["Lin"].prototype.startswith("Lin(point_name")
        assert LUA_FUNCTIONS["PTP"].prototype.startswith("PTP(point_name")
        for n in ("MoveL", "MoveJ"):
            assert "point_name" not in LUA_FUNCTIONS[n].prototype
            assert LUA_FUNCTIONS[n].prototype.startswith(f"{n}(j1")

    def test_every_prototype_names_its_own_function(self):
        for n, f in LUA_FUNCTIONS.items():
            if f.prototype:
                assert f.prototype.startswith(f"{n}("), \
                    f"{n} prototype starts with {f.prototype[:40]!r}"

    def test_arity_matches_the_captured_prototype(self):
        for n, f in LUA_FUNCTIONS.items():
            if not f.prototype:
                continue
            args = f.prototype[f.prototype.index("(") + 1:-1]
            expected = len([a for a in args.split(",") if a.strip()])
            assert f.arity == expected, n

    def test_sections_partition_the_catalogue(self):
        assert sum(len(v) for v in by_section().values()) == len(LUA_FUNCTIONS)


class TestErrata:
    """Manual heading/prototype spelling disagreements resolve to the working name."""

    def test_misspellings_resolve_to_the_working_name(self):
        assert resolve("FT_Spiralsearch") == "FT_SpiralSearch"
        assert resolve("FT_SotInsertion") == "FT_RotInsertion"
        assert resolve("ImpedanceControlStrartStop") == "ImpedanceControlStartStop"

    def test_a_correct_name_resolves_to_itself(self):
        assert resolve("MoveL") == "MoveL"

    def test_errata_never_shadow_a_real_function(self):
        for wrong in MANUAL_ERRATA:
            assert wrong not in LUA_FUNCTIONS, \
                f"{wrong} is listed as an erratum AND as a real function"

    def test_every_correction_points_somewhere_real(self):
        for wrong, e in MANUAL_ERRATA.items():
            target = e["correct"]
            if target is None:
                continue          # deliberately unresolvable, e.g. "mode"
            assert target in LUA_FUNCTIONS, \
                f"{wrong} corrects to {target}, which does not exist"

    def test_unknown_names_do_not_resolve(self):
        assert resolve("DefinitelyNotAFairinoFunction") is None


class TestArityConflicts:
    """A worked example longer than the prototype is a real arity conflict."""

    def test_the_two_force_movers_are_flagged(self):
        assert "FT_Control" in ARITY_CONFLICTS
        assert "FT_Guard" in ARITY_CONFLICTS

    def test_every_conflict_really_has_a_longer_example(self):
        for n, d in ARITY_CONFLICTS.items():
            assert max(d["example_arities"]) > d["prototype_arity"], n

    def test_both_force_movers_are_refused_for_generation(self):
        """Flagging is not enough for a command that presses on a workpiece."""
        assert set(bridge.REFUSE_TO_GENERATE) == {"FT_Control", "FT_Guard"}
        for n, why in bridge.REFUSE_TO_GENERATE.items():
            assert str(ARITY_CONFLICTS[n]["prototype_arity"]) in why


class TestBridge:
    def test_the_three_sets_partition_both_apis(self):
        assert (set(bridge.both()) | set(bridge.lua_only())) == set(LUA_FUNCTIONS)
        assert (set(bridge.both()) | set(bridge.rpc_only())) == set(COMMANDS)
        assert not set(bridge.lua_only()) & set(bridge.rpc_only())

    def test_spiralsearch_argument_order_is_recorded_both_ways(self):
        """The one conflict that runs and does the wrong thing silently."""
        c = bridge.ARGUMENT_ORDER_CONFLICTS["FT_SpiralSearch"]
        assert c["rpc_order"] != c["lua_order"]
        assert sorted(c["rpc_order"]) == sorted(c["lua_order"]), \
            "same arguments, different order -- that is the whole point"
        # And the recorded RPC order must match the generated registry, or the
        # note describes a signature that is not actually sent.
        assert list(COMMANDS["FT_SpiralSearch"].wire_args) == c["rpc_order"]

    def test_sensor_setup_is_rpc_only_and_strategy_is_shared(self):
        """The architectural split, asserted rather than just documented."""
        rpc_only = set(bridge.rpc_only())
        for n in ("FT_Activate", "FT_SetZero", "FT_GetConfig",
                  "SetForceSensorPayload", "GetForceSensorPayload"):
            assert n in rpc_only, f"{n} should be gateway-side setup"
        both_sides = set(bridge.both())
        for n in ("FT_Control", "FT_Guard", "FT_SpiralSearch",
                  "FT_FindSurface"):
            assert n in both_sides

    def test_tap_and_torque_recording_are_lua_only(self):
        lua_only = set(bridge.lua_only())
        for n in bridge.FORCE_LUA_ONLY:
            assert n in lua_only, f"{n} is not actually Lua-only"

    def test_divergence_reports_both_sides(self):
        d = bridge.divergence("FT_SpiralSearch")
        assert d["in_lua"] and d["in_rpc"]
        assert "argument_order_conflict" in d

    def test_divergence_is_none_for_an_unknown_name(self):
        assert bridge.divergence("NotAFunctionAnywhere") is None

    def test_summary_counts_agree_with_the_sets(self):
        s = bridge.summary()
        assert s["lua_functions"] == len(LUA_FUNCTIONS)
        assert s["in_both"] == len(bridge.both())
        assert s["lua_only"] == len(bridge.lua_only())


class TestMeasuredFirmwareCapability:
    """Firmware capability: absence is claimed only for a nil-value call."""

    def test_the_ten_corrected_names_are_present(self):
        """These functions exist; they merely need a taught point."""
        from fws.protocol.lua_firmware import LUA_FIRMWARE
        d = LUA_FIRMWARE["v3.8.5.1"]
        for n in ("Lin", "ARC", "Circle", "DMP", "EXT_AXIS_PTP",
                  "ModbusMasterReadAI", "ModbusMasterReadDI"):
            assert d[n].present, f"{n} was wrongly reported absent"

    def test_point_name_functions_are_separated_from_absent_ones(self):
        from fws.protocol.lua_firmware import absent_on, needs_a_taught_point
        needs = set(needs_a_taught_point("v3.8.5.1"))
        assert "Lin" in needs and "PTP" in needs
        assert not needs & set(absent_on("v3.8.5.1")), \
            "a name cannot be both absent and merely missing its point"

    def test_absence_is_only_claimed_for_a_nil_call(self):
        from fws.protocol.lua_firmware import LUA_FIRMWARE
        for n, a in LUA_FIRMWARE["v3.8.5.1"].items():
            if not a.present:
                assert a.status == "absent", n

    def test_printmsg_is_absent_though_the_manual_documents_it(self):
        """A program written from the manual that prints will not compile."""
        from fws.protocol.lua_firmware import LUA_FIRMWARE
        from fws.protocol.lua_functions import LUA_FUNCTIONS
        assert "PrintMsg" in LUA_FUNCTIONS
        assert not LUA_FIRMWARE["v3.8.5.1"]["PrintMsg"].present

    def test_the_motion_route_we_actually_use_is_present_and_clean(self):
        from fws.protocol.lua_firmware import LUA_FIRMWARE
        d = LUA_FIRMWARE["v3.8.5.1"]
        for n in ("MoveL", "MoveJ"):
            assert d[n].present and d[n].status == "ok", n
            assert d[n].manual_arity_accepted is True

    def test_coverage_is_complete(self):
        from fws.protocol.lua_firmware import LUA_FIRMWARE
        from fws.protocol.lua_functions import LUA_FUNCTIONS
        assert set(LUA_FIRMWARE["v3.8.5.1"]) == set(LUA_FUNCTIONS)

    def test_an_unprobed_firmware_reports_unknown_not_absent(self):
        from fws.protocol.lua_firmware import availability
        assert availability("MoveL", "v9.9.9.9") is None
