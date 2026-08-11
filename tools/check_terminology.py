#!/usr/bin/env python3
"""Enforce the emergency-stop terminology rule.

Nothing in FWS may present itself as an emergency stop. The only emergency
stop is the physical button wired per ISO 13850; everything this software can
do is a non-safety-rated functional stop that depends on a network, a host, a
Python process and controller firmware.

The rule outlives anyone's memory only if a machine checks it. But it has to
be checked properly:

  * Disclaimers must be ALLOWED -- "this is not an emergency stop" is the
    sentence we most want people to write. A naive grep bans it.
  * Negations often wrap across lines, so line-based matching gets it wrong.
  * The controller genuinely has commands called GetRobotEmergencyStopState
    and AuxServoSetEmergencyStopAcc. Those are Fairino's names, and reporting
    a fact is not making a claim. The generated registry is exempt.

Usage:  python tools/check_terminology.py [paths...]
Exit 0 clean, 1 on violation.
"""
from __future__ import annotations

import pathlib
import re
import sys

PHRASE = re.compile(r"emergency[\s_-]*stop", re.IGNORECASE)

# Text within this many characters of a hit that makes it a disclaimer.
CONTEXT = 240

ALLOWED = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bnot\s+(an?\s+)?emergency[\s_-]*stop",
    r"\bno\s+endpoint\s+is\s+an?\s+emergency",
    r"\bnothing\s+.{0,40}is\s+an?\s+emergency",
    r"\bthe\s+only\s+emergency[\s_-]*stop",
    r"emergency[\s_-]*stop\s+is\s+(the\s+)?(physical|hardware)",
    r"physical\s+e-?stop",
    r"\bis\s+hardware[\s-]*only",
    r"must\s+never\s+(appear|say)",
    r"terminology",
))

EXEMPT_FILES = {
    "SAFETY.md",             # where the disclaimer lives
    "commands.py",           # generated: vendor command names are facts
    "check_terminology.py",  # this file describes the rule
    # Generated from the vendor's fault table. Code 186 is "Motion stopped by
    # emergency stop signal" -- the controller reporting that the PHYSICAL
    # E-stop fired. Reporting that is precisely what we want; the rule exists
    # to stop FWS claiming to BE an emergency stop, not to stop it relaying
    # the real one.
    "error_codes.py",
}

SUFFIXES = {".py", ".html", ".js", ".toml", ".yml", ".yaml"}


def violations(path: pathlib.Path) -> list[tuple[int, str]]:
    if path.name in EXEMPT_FILES:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []

    out: list[tuple[int, str]] = []
    for m in PHRASE.finditer(text):
        window = text[max(0, m.start() - CONTEXT): m.end() + CONTEXT]
        # A vendor identifier such as GetRobotEmergencyStopState is a name,
        # not a claim about what this software does.
        ident = re.match(r"[A-Za-z_]*", text[max(0, m.start() - 30):m.start()][::-1])
        if ident and ident.group(0) and text[m.start() - 1:m.start()].isalpha():
            continue
        if any(p.search(window) for p in ALLOWED):
            continue
        line = text.count("\n", 0, m.start()) + 1
        snippet = text[max(0, m.start() - 60): m.end() + 60].replace("\n", " ")
        out.append((line, snippet.strip()))
    return out


def main(argv: list[str]) -> int:
    roots = [pathlib.Path(a) for a in argv[1:]] or [
        pathlib.Path("fws"), pathlib.Path("tools"), pathlib.Path("tests")]

    failures = 0
    scanned = 0
    for root in roots:
        paths = ([root] if root.is_file()
                 else [p for p in root.rglob("*") if p.suffix in SUFFIXES])
        for path in paths:
            scanned += 1
            for line, snippet in violations(path):
                failures += 1
                print(f"{path}:{line}: claims to be an emergency stop")
                print(f"    ...{snippet}...")

    if failures:
        print(f"\n{failures} violation(s). Nothing in FWS is an emergency "
              f"stop; say 'functional stop' or state the disclaimer.")
        return 1
    print(f"terminology rule holds ({scanned} files scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
