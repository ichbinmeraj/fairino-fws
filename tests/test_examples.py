"""The examples must actually run.

Documentation that has drifted from the code is worse than none: it teaches
a wrong shape confidently. Every example runs end to end against the
simulator here, so a route rename or a changed field breaks CI rather than
someone's first afternoon with FWS.

Three of these already caught real mistakes while being written -- a wrong
upload field name, Lua table syntax where the firmware wants flat arguments,
and a `main()` wrapper the controller's Lua has no global for.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"
SCRIPTS = sorted(p.name for p in EXAMPLES.glob("[0-9]*.py"))


def test_there_are_examples():
    """A guard on the guard: an empty glob would make every test below
    vacuously pass."""
    assert SCRIPTS, f"no example scripts found in {EXAMPLES}"


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_example_runs_against_the_simulator(script):
    repo = EXAMPLES.parent
    proc = subprocess.run(
        [sys.executable, str(EXAMPLES / script)],
        cwd=EXAMPLES, capture_output=True, text=True, timeout=180,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(repo),
             "HOME": str(repo)},
    )
    assert proc.returncode == 0, (
        f"{script} exited {proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout[-3000:]}\n"
        f"--- stderr ---\n{proc.stderr[-3000:]}")
    # A script that printed a refusal it did not intend still exits 0, so
    # check for the shapes that mean "the API moved and this example did
    # not": a validation error, or an unhandled traceback.
    assert "Traceback" not in proc.stderr, proc.stderr[-3000:]
    assert "Field required" not in proc.stdout, (
        f"{script} sent a body the API rejected:\n{proc.stdout[-2000:]}")


def test_every_example_is_listed_in_the_readme():
    """A script nobody can find is not an example."""
    readme = (EXAMPLES / "README.md").read_text()
    for script in SCRIPTS:
        assert script in readme, f"{script} is missing from examples/README.md"
