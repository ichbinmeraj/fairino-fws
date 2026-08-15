"""`configure_app`: the seam a separately installed package mounts through.

The gateway ships no user interface. `fairino-fws-console` adds one by
installing alongside and mounting routes through this hook, which is why the
contract below matters more than it looks: the hook has to run *after* the
startup checks, so a package cannot mount itself onto a gateway that refused
to start.
"""
from __future__ import annotations

from fws import cli


class TestConfigureAppHook:
    def test_default_is_no_hook(self):
        """Nothing changes for callers that do not pass one."""
        rc = cli.main(["--simulator", "--check"])
        assert rc == 0

    def test_hook_receives_the_app_and_the_resolved_settings(self, monkeypatch):
        seen = {}

        def spy(app, settings):
            seen["app"] = app
            seen["settings"] = settings

        # --check returns before the hook; stub the server instead so main()
        # runs the whole startup path without blocking on a real socket.
        import uvicorn
        monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: None)

        rc = cli.main(["--simulator"], configure_app=spy)
        assert rc == 0

        assert seen["app"] is not None, "hook was never called"
        assert hasattr(seen["app"], "mount"), "hook did not receive an ASGI app"
        # The settings must be the resolved ones, pointed at the simulator.
        assert seen["settings"].robot.ip == "127.0.0.1"

    def test_hook_can_mount_routes_that_the_server_then_serves(self, monkeypatch):
        """What the console actually does."""
        from fastapi.responses import PlainTextResponse
        from fastapi.testclient import TestClient

        started = {}

        def mount_something(app, settings):
            @app.get("/console/probe", response_class=PlainTextResponse)
            def probe():
                return "mounted"

        def fake_run(app, **kw):
            started["app"] = app

        import uvicorn
        monkeypatch.setattr(uvicorn, "run", fake_run)

        # This mounts onto the module-level app -- exactly as the console
        # does -- so the added route must be removed afterwards, or it leaks
        # into every later test that reads the app's surface (the contract
        # snapshot, the served-paths checks). A test that mutates a global
        # restores it.
        from fws.app import app as shared_app
        before = list(shared_app.router.routes)
        try:
            rc = cli.main(["--simulator"], configure_app=mount_something)
            assert rc == 0

            client = TestClient(started["app"])
            assert client.get("/console/probe").text == "mounted"
            # The API must be untouched by whatever was mounted.
            assert client.get("/").json()["service"] == "fws"
        finally:
            shared_app.router.routes[:] = before
            shared_app.openapi_schema = None

    def test_hook_does_not_run_when_startup_checks_refuse(self, monkeypatch):
        """A refused gateway must not let a package mount onto it anyway."""
        called = []

        monkeypatch.setattr(
            "fws.config.Settings.check_safe_to_start",
            lambda self: ["a deliberate problem"],
        )
        rc = cli.main(["--simulator"], configure_app=lambda a, s: called.append(1))
        assert rc == 3
        assert called == [], "hook ran despite the gateway refusing to start"

    def test_hook_does_not_run_in_check_mode(self):
        """--check validates configuration; it starts nothing."""
        called = []
        rc = cli.main(["--simulator", "--check"],
                      configure_app=lambda a, s: called.append(1))
        assert rc == 0
        assert called == []
