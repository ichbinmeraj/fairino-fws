# Contributing

Thanks for your interest in FWS. It talks to a machine that can move, so this
guide is mostly about how to change it safely.

## Before you open a pull request

```bash
pip install -e ".[dev]"
pytest -q                       # runs entirely against the simulator, no robot
ruff check fws tools tests      # must be clean
```

CI runs both on Python 3.11, 3.12 and 3.13. 3.11 is the documented deployment
target (Raspberry Pi OS Bookworm's system Python) and must stay green.

## Tests

The whole suite runs against an in-process simulator, so you can develop the
gateway with no hardware:

```bash
fws --simulator
```

If a change fixes a bug, add the test that would have caught it. If a change
makes the simulator diverge from the real controller, say so in its docstring —
a test double is only worth having if it is honest about where it is wrong.

## Safety review

A change touching any of these needs a note in the PR describing what you
considered:

- The driver's refusal list and forbidden calls. These are the floor, enforced
  below the API; a rule enforced only at the API boundary is not enough.
- The invoker's gating matrix and typed-route ownership. For some commands the
  typed wrapper *is* the safety property.
- Anything named `stop`. The stop path is never gated, never authenticated, and
  always returns 200.
- `SAFETY.md`. If you change what FWS refuses, change this too, and check the
  claim is true.

## Style

Match the surrounding code. Comments explain *why*, not *what*; a comment that
repeats the code earns nothing. Keep them terse and technical.

Generated registry modules under `fws/protocol/` are machine output — do not
hand-edit them.

## Releasing

Releases publish to PyPI automatically via `.github/workflows/release.yml`,
using PyPI Trusted Publishing (no stored token). To cut a release:

1. Bump `version` in `pyproject.toml`.
2. Update `CHANGELOG.md`.
3. Tag and push:

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

The tag triggers the workflow, which builds the wheel + sdist and publishes
`fairino-fws` to PyPI. The one-time PyPI trusted-publisher setup is documented
at the top of the release workflow file.

## Reporting a security issue

See `SECURITY.md`. Please do not open a public issue for anything that could be
used to move a robot.
