"""Firmware-legality linter for controller Lua.

Checks a Lua program against what THIS controller family will actually accept,
before it is uploaded. Static, offline, and a few milliseconds.

The controller's Lua is not the Lua on your laptop. It has no standard
library to speak of, its parser predates operators you will reach for out of
habit, and when it rejects a program it does so by writing a line into a log
you have to fetch over a slow channel -- or, worse, by accepting the program
and then silently refusing to start it.

Every rule below exists because someone was caught by it on real hardware.
The controller answers a bad program either by writing one line into a log you
have to fetch over a slow channel, or -- worse -- by accepting it and then
silently refusing to start it. Neither tells you which line was wrong.

  ASCII only            the compiler rejects anything else outright
  no '%'                this parser predates the modulo operator
  no '#'                the length operator is not reliable here either
  stdlib, by evidence   math.* is PROVEN (math.sqrt ran for 260 s in the
                        production program). string.* is unproven -- it appears
                        in the proven programs only inside error paths that
                        never fired. os/io/coroutine/debug/require are refused.
  PrintMsg only guarded absent on v3.8.5.1 (measured), so a bare call is fatal;
                        the guarded idiom `if type(PrintMsg) == "function"` is
                        how the proven programs log and is allowed
  error() is a warning  the script races ahead of motion, so an error thrown
                        while a move executes kills the program mid-move. Legal
                        before any motion, dangerous after -- flagged, not banned
  no Get/SetSysVarvalue the lower-case-v spellings are absent (measured); the
                        correct Lua spelling is verified separately
  MoveL takes 33 args   flat prototype on this firmware
  MoveJ takes 29 args
  known functions only  every global call is checked against the measured
                        firmware table, at the arity it is called with

Pass `known_functions` to also check that every global call resolves to a name
this firmware really has, at the arity it is called with; fws.protocol.
lua_firmware has the measured table for that.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

ERROR = "error"        # will not run, or is known to break the controller
WARNING = "warning"    # runs, but nobody has proven it on this firmware

BANNED_TOKENS: dict[str, tuple[str, str]] = {
    "GetSysVarvalue": (ERROR, "the lower-case-v spelling is absent on this firmware"),
    "SetSysVarvalue": (ERROR, "the lower-case-v spelling is absent on this firmware"),
}

BANNED_PATTERNS: list[tuple[str, str, str, str]] = [
    (r"%", ERROR, "modulo/format '%'",
     "this Lua parser predates the '%' operator; use arithmetic instead"),
    (r"#\s*[A-Za-z_{]", ERROR, "length operator '#'",
     "the '#' operator is not dependable here; carry explicit counts"),
    (r"\b(os|io|coroutine|debug)\s*\.", ERROR, "standard library use",
     "os/io/coroutine/debug are not available in the controller interpreter"),
    (r"\brequire\s*\(", ERROR, "require()",
     "there is no module loader on the controller"),
    (r"\bgoto\b", ERROR, "goto", "not supported by this parser"),
    (r"\bstring\s*\.", WARNING, "string library",
     "string.* is unproven on this firmware: it appears in the programs that "
     "have run only inside error paths that never fired. Avoid it on any path "
     "that executes"),
    (r"\berror\s*\(", WARNING, "error()",
     "the script races ahead of motion, so an error raised while a move is "
     "executing kills that move. Safe before the first motion command, a "
     "hazard after it: prefer returning a status"),
]

ARITY_RULES: dict[str, int] = {"MoveL": 33, "MoveJ": 29}


@dataclass
class Finding:
    line: int
    rule: str
    detail: str
    text: str
    severity: str = ERROR

    def __str__(self) -> str:
        return (f"{self.severity.upper():7} line {self.line}: {self.rule} -- "
                f"{self.detail}\n    {self.text.strip()}")


def fatal(findings: list[Finding]) -> list[Finding]:
    """Only the findings that mean 'do not upload this'."""
    return [f for f in findings if f.severity == ERROR]


def _strip_strings_and_comments(line: str) -> str:
    """Blank out string literals and comments so their contents do not trip
    the token rules. A '%' inside a comment is harmless."""
    out = re.sub(r'"[^"]*"', '""', line)
    out = re.sub(r"'[^']*'", "''", out)
    return out.split("--", 1)[0]


def _strip_comments_keeping_lines(source: str) -> str:
    """Remove comments while preserving line numbering.

    Arity counting must run on code only: the proven programs annotate each
    MoveL argument with a trailing comment, and those comments contain commas
    and brackets. Counting them turned a correct 33-argument call into 39.
    """
    out = []
    for line in source.split("\n"):
        blanked = re.sub(r'"[^"]*"',
                         lambda m: '"' + "x" * (len(m.group(0)) - 2) + '"', line)
        blanked = re.sub(r"'[^']*'",
                         lambda m: "'" + "x" * (len(m.group(0)) - 2) + "'", blanked)
        index = blanked.find("--")
        out.append(line if index < 0 else line[:index])
    return "\n".join(out)


def _call_arity(text: str, name: str, start: int) -> int | None:
    """Count top-level arguments of a call to ``name`` beginning at ``start``."""
    i = text.find("(", start)
    if i < 0:
        return None
    depth, args, seen = 0, 1, False
    for ch in text[i:]:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return args if seen else 0
        elif ch == "," and depth == 1:
            args += 1
        elif depth >= 1 and not ch.isspace():
            seen = True
    return None


def lint_text(source: str,
              known_functions: dict[str, int] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    lines = source.splitlines()

    try:
        source.encode("ascii")
    except UnicodeEncodeError as e:
        bad = source[e.start:e.end]
        line_no = source[: e.start].count("\n") + 1
        findings.append(Finding(line_no, "non-ASCII", f"byte {bad!r} is not ASCII",
                                lines[line_no - 1] if line_no <= len(lines) else "",
                                ERROR))

    for number, raw in enumerate(lines, start=1):
        code = _strip_strings_and_comments(raw)
        for token, (severity, why) in BANNED_TOKENS.items():
            if token in code:
                findings.append(
                    Finding(number, f"banned call {token}", why, raw, severity))
        # PrintMsg is absent, so calling it unguarded is fatal -- but the
        # guarded idiom is exactly how the proven programs log and must pass.
        # Checked against the RAW line: stripping string literals would erase
        # the "function" in the guard and make every guarded call look bare.
        if "PrintMsg" in code and not re.search(
            r'type\s*\(\s*PrintMsg\s*\)\s*==\s*"function"', raw
        ):
            findings.append(Finding(
                number, "unguarded PrintMsg",
                "PrintMsg is absent on this firmware (measured). Call it only "
                'behind `if type(PrintMsg) == "function" then ... end`',
                raw, ERROR))
        for pattern, severity, rule, why in BANNED_PATTERNS:
            if re.search(pattern, code):
                findings.append(Finding(number, rule, why, raw, severity))

    # Arity checks run on the whole text (these calls span many lines) but on
    # code with comments removed -- see _strip_comments_keeping_lines.
    code_only = _strip_comments_keeping_lines(source)
    for name, expected in ARITY_RULES.items():
        for match in re.finditer(rf"\b{name}\s*\(", code_only):
            arity = _call_arity(code_only, name, match.start())
            if arity is not None and arity != expected:
                line_no = code_only[: match.start()].count("\n") + 1
                findings.append(Finding(
                    line_no, f"{name} arity",
                    f"this firmware takes exactly {expected} arguments, found {arity}",
                    lines[line_no - 1] if line_no <= len(lines) else "", ERROR))

    if known_functions:
        declared = set(re.findall(r"\blocal\s+function\s+([A-Za-z_][\w]*)", code_only))
        declared |= set(re.findall(r"\bfunction\s+([A-Za-z_][\w]*)", code_only))
        declared |= set(re.findall(r"\blocal\s+([A-Za-z_][\w]*)\s*=", code_only))
        for match in re.finditer(r"\b([A-Z][A-Za-z_0-9]*)\s*\(", code_only):
            name = match.group(1)
            if name in declared or name in ARITY_RULES:
                continue
            if name not in known_functions:
                line_no = code_only[: match.start()].count("\n") + 1
                findings.append(Finding(
                    line_no, f"unknown function {name}",
                    "not present in the measured firmware function table",
                    lines[line_no - 1] if line_no <= len(lines) else "", WARNING))
    return findings


def lint_syntax(path: Path) -> list[Finding]:
    """Run luac -p if it is available. Catches plain syntax errors offline."""
    luac = shutil.which("luac5.1") or shutil.which("luac")
    if not luac:
        return []
    result = subprocess.run(
        [luac, "-p", str(path)], capture_output=True, text=True, timeout=20
    )
    if result.returncode == 0:
        return []
    message = (result.stderr or result.stdout).strip()
    line_no = 0
    match = re.search(r":(\d+):", message)
    if match:
        line_no = int(match.group(1))
    return [Finding(line_no, "syntax error", message, "", ERROR)]


def lint_file(path: Path,
              known_functions: dict[str, int] | None = None) -> list[Finding]:
    source = Path(path).read_text("utf-8", errors="replace")
    return lint_text(source, known_functions) + lint_syntax(Path(path))
