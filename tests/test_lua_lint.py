"""The Lua linter: does it pass real programs and catch real mistakes?

The regression that matters most is the first class below. A linter that cries
wolf on working code gets switched off within a week, so the four programs that
have actually painted parts on a real FR5 must come back clean. If a rule is
added that flags them, either the rule is wrong or the claim "these programs
run" is wrong -- and both are worth stopping for.
"""
from __future__ import annotations

import pathlib

import pytest

from fws.lua_lint import ERROR, WARNING, fatal, lint_file, lint_text

PROVEN_DIR = pathlib.Path("/home/user/paint")
PROVEN = ["RAW_row8_production.lua", "RAW_row4_production.lua",
          "RAW_paint.lua", "RAW_paint_H.lua"]


def _rules(findings):
    return {f.rule for f in findings}


class TestProgramsThatHaveRunMustLintClean:
    @pytest.mark.parametrize("name", PROVEN)
    def test_no_fatal_findings(self, name):
        path = PROVEN_DIR / name
        if not path.exists():
            pytest.skip(f"{name} is not on this machine")
        problems = fatal(lint_file(path))
        assert not problems, (
            f"{name} has painted parts on a real arm, so a fatal finding here "
            f"means the rule is wrong: " + "; ".join(str(p) for p in problems))

    def test_the_guarded_print_idiom_passes(self):
        """PrintMsg is absent on this firmware, so the proven programs call it
        only behind a type check. That idiom must not be flagged."""
        source = ('local function log(a)\n'
                  '  if type(PrintMsg) == "function" then PrintMsg(a) end\n'
                  'end\n')
        assert not fatal(lint_text(source))

    def test_math_is_allowed_because_it_has_run(self):
        """math.sqrt executed for 260 s in the production program."""
        assert not fatal(lint_text("local d = math.sqrt(4.0)\n"))


class TestItCatchesWhatTheControllerRejects:
    @pytest.mark.parametrize("source,rule", [
        ("local n = 5 % 2\n", "modulo/format '%'"),
        ("local c = #t\n", "length operator '#'"),
        ('require("x")\n', "require()"),
        ('local f = io.open("x")\n', "standard library use"),
        ('PrintMsg("hello")\n', "unguarded PrintMsg"),
        ("SetSysVarvalue(1, 2)\n", "banned call SetSysVarvalue"),
    ])
    def test_fatal_rule_fires(self, source, rule):
        problems = fatal(lint_text(source))
        assert rule in _rules(problems), (
            f"expected {rule!r}, got {_rules(problems)}")

    def test_wrong_movel_arity_is_fatal(self):
        problems = fatal(lint_text("MoveL(1, 2, 3)\n"))
        assert "MoveL arity" in _rules(problems)
        assert "33" in " ".join(p.detail for p in problems)

    def test_wrong_movej_arity_is_fatal(self):
        assert "MoveJ arity" in _rules(fatal(lint_text("MoveJ(1, 2)\n")))

    def test_non_ascii_is_fatal(self):
        assert "non-ASCII" in _rules(fatal(lint_text('local s = "café"\n')))


class TestSeveritiesAreEvidenceBased:
    def test_string_library_is_a_warning_not_an_error(self):
        """string.* appears in the proven programs only inside error paths
        that never fired: unproven, not known-broken."""
        findings = lint_text('local s = string.format("%d", 1)\n')
        assert not fatal(findings)
        assert any(f.rule == "string library" and f.severity == WARNING
                   for f in findings)

    def test_error_is_a_warning_because_it_is_legal_before_motion(self):
        findings = lint_text('error("bad config")\n')
        assert not fatal(findings)
        assert any(f.rule == "error()" and f.severity == WARNING
                   for f in findings)

    def test_severity_is_only_ever_error_or_warning(self):
        for f in lint_text("local n = 5 % 2\nerror('x')\n"):
            assert f.severity in (ERROR, WARNING)


class TestItDoesNotMisreadItsOwnInput:
    def test_commas_in_trailing_comments_do_not_inflate_arity(self):
        """The proven programs annotate each MoveL argument. Counting comment
        commas turned a correct 33-argument call into 39."""
        # The comma comes BEFORE the comment, as it does in real code --
        # putting it after would make the comment swallow the separator.
        args = "\n".join(f"  {i},  -- arg {i}, with a comma" for i in range(32))
        # 32 comma-terminated arguments plus a final one makes 33.
        assert not fatal(lint_text(f"MoveL(\n{args}\n  0)\n"))

    def test_a_percent_inside_a_comment_is_not_a_modulo(self):
        assert not fatal(lint_text("local x = 1  -- 50% of the way\n"))

    def test_a_percent_inside_a_string_is_not_a_modulo(self):
        assert not fatal(lint_text('local s = "50% done"\n'))


class TestTheEndpoint:
    def _client(self, fake):
        from fastapi.testclient import TestClient

        from fws import app as app_mod
        from fws import config as config_mod
        app_mod.create_app(config_mod.load(**{
            "robot.ip": fake.host, "robot.rpc_port": fake.rpc_port,
            "robot.telemetry_port": fake.stream_port,
            "robot.upload_port": fake.upload_port,
            "robot.download_port": fake.download_port}))
        return TestClient(app_mod.app)

    def test_it_needs_no_robot(self, fake):
        """Nothing is uploaded, compiled or run: the check is static.

        The gateway polls the controller on its own, so the total call count
        moves for reasons that have nothing to do with linting. What must
        never appear is a transfer, a compile or a program command.
        """
        with self._client(fake) as c:
            before = len(fake.calls)
            body = c.post("/api/v1/lua/lint",
                          json={"source": "local n = 5 % 2\n"}).json()
            during = [name for name, _ in fake.calls[before:]]
        assert body["ok"] is False
        assert body["errors"] >= 1
        forbidden = {"FileUpload", "LuaUpLoadUpdate", "ProgramLoad",
                     "ProgramRun", "MoveL", "MoveJ", "StartJOG"}
        assert not forbidden & set(during), (
            f"linting reached the robot: {sorted(forbidden & set(during))}")

    def test_a_proven_program_answers_ok(self, fake):
        path = PROVEN_DIR / "RAW_row8_production.lua"
        if not path.exists():
            pytest.skip("the proven program is not on this machine")
        with self._client(fake) as c:
            body = c.post("/api/v1/lua/lint",
                          json={"source": path.read_text()}).json()
        assert body["ok"] is True, body["findings"]

    def test_findings_name_the_line_and_the_reason(self, fake):
        with self._client(fake) as c:
            body = c.post("/api/v1/lua/lint",
                          json={"source": "local a = 1\nPrintMsg('x')\n"}).json()
        bad = [f for f in body["findings"] if f["severity"] == "error"]
        assert bad and bad[0]["line"] == 2
        assert "absent" in bad[0]["detail"]

    def test_unknown_function_check_can_be_turned_off(self, fake):
        source = "SomeVendorThingNobodyProbed(1)\n"
        with self._client(fake) as c:
            on = c.post("/api/v1/lua/lint",
                        json={"source": source}).json()
            off = c.post("/api/v1/lua/lint",
                         json={"source": source,
                               "check_known_functions": False}).json()
        assert on["warnings"] > off["warnings"]
