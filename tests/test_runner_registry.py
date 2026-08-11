"""The abort registry: how the gateway stops what it does not know about."""
from __future__ import annotations

import gc

from fws.runners import AbortRegistry


class Spy:
    def __init__(self):
        self.aborted = False

    def request_abort(self):
        self.aborted = True


class Exploding:
    def request_abort(self):
        raise RuntimeError("badly behaved runner")


def test_registered_runners_are_aborted():
    reg, a, b = AbortRegistry(), Spy(), Spy()
    reg.register(a)
    reg.register(b)
    assert reg.request_abort_all() == 2
    assert a.aborted and b.aborted


def test_a_throwing_runner_does_not_block_the_others():
    """A stop path that can throw is a stop path that can be skipped."""
    reg, good = AbortRegistry(), Spy()
    reg.register(Exploding())
    reg.register(good)
    reg.request_abort_all()
    assert good.aborted


def test_collected_runners_are_dropped_not_resurrected():
    reg = AbortRegistry()
    reg.register(Spy())          # no reference kept
    gc.collect()
    assert reg.request_abort_all() == 0
    assert len(reg) == 0


def test_gateway_does_not_import_an_example():
    """fws must never import the examples package (checks imports, not the word)."""
    import ast
    import pathlib

    for f in pathlib.Path("fws").rglob("*.py"):
        tree = ast.parse(f.read_text(), filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for n in names:
                assert n.split(".")[0] != "examples", \
                    f"{f}:{node.lineno} imports the example package"
