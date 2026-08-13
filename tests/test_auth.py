"""API-key authentication, and the paths that must never require it."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod
from fws.auth import KeyStore


@pytest.fixture
def keyfile(tmp_path):
    p = tmp_path / "keys"
    p.write_text("# comment line\n\nsecret-key-one  ci-runner\nsecret-key-two\n")
    return p


@pytest.fixture
def secured(fake, keyfile):
    app_mod.create_app(config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
        "auth.api_keys_file": str(keyfile),
    }))
    with TestClient(app_mod.app) as c:
        yield c


class TestKeyStore:
    def test_parses_keys_and_labels_ignoring_comments(self, keyfile):
        ks = KeyStore(keyfile)
        assert len(ks) == 2
        assert ks.identify("secret-key-one") == "ci-runner"
        assert ks.identify("secret-key-two") == "unlabelled"
        assert ks.identify("wrong") is None
        assert ks.identify(None) is None

    def test_keys_are_not_held_in_plaintext(self, keyfile):
        """A heap dump should not hand over working credentials."""
        ks = KeyStore(keyfile)
        assert "secret-key-one" not in repr(ks.__dict__)
        assert all(len(d) == 64 for d in ks._digests)

    def test_disabled_when_no_file(self):
        assert not KeyStore(None).enabled


class TestEnforcement:
    def test_protected_route_requires_a_key(self, secured):
        assert secured.get("/api/v1/state").status_code == 401

    def test_valid_key_is_accepted(self, secured):
        r = secured.get("/api/v1/state", headers={"X-API-Key": "secret-key-one"})
        assert r.status_code == 200

    def test_wrong_key_is_rejected(self, secured):
        r = secured.get("/api/v1/state", headers={"X-API-Key": "nope"})
        assert r.status_code == 401


class TestStopIsNeverAuthenticated:
    """A client that cannot authenticate must still be able to stop the arm."""

    def test_stop_works_with_no_key(self, secured):
        r = secured.post("/api/v1/motion/stop")
        assert r.status_code == 200
        assert r.json()["results"]["ImmStopJOG"] == "ok"

    def test_stop_works_with_a_wrong_key(self, secured):
        r = secured.post("/api/v1/motion/stop", headers={"X-API-Key": "wrong"})
        assert r.status_code == 200

    def test_health_is_probeable_without_a_key(self, secured):
        assert secured.get("/api/v1/system/health").status_code == 200


class TestStartupRefusal:
    def test_wide_bind_without_auth_is_refused(self):
        s = config_mod.load(**{"server.bind_host": "0.0.0.0"})
        problems = s.check_safe_to_start()
        assert problems and "authentication" in problems[0]

    def test_wide_bind_with_auth_is_allowed(self, keyfile):
        s = config_mod.load(**{"server.bind_host": "0.0.0.0",
                               "auth.api_keys_file": str(keyfile)})
        assert s.check_safe_to_start() == []


class TestAZeroKeyFileFailsClosed:
    """A key file that parses to zero usable keys fails closed, not open."""

    EMPTIED = ("", "\n\n\n", "# key-one  ci\n# key-two  ops\n")

    @pytest.mark.parametrize("text", EMPTIED)
    def test_startup_refuses_a_keyfile_with_no_usable_keys(self, tmp_path,
                                                           text):
        kf = tmp_path / "keys"
        kf.write_text(text)
        s = config_mod.load(**{"server.bind_host": "0.0.0.0",
                               "auth.api_keys_file": str(kf)})
        problems = s.check_safe_to_start()
        assert problems, "this configuration used to start happily"
        assert any("no usable keys" in p for p in problems)

    def test_the_refusal_says_it_would_401_rather_than_serve_openly(
            self, tmp_path):
        kf = tmp_path / "keys"
        kf.write_text("# all rotated out\n")
        s = config_mod.load(**{"server.bind_host": "0.0.0.0",
                               "auth.api_keys_file": str(kf)})
        msg = " ".join(s.check_safe_to_start())
        assert "refuse every request" in msg

    def test_an_unreadable_keyfile_is_refused_not_ignored(self, tmp_path):
        kf = tmp_path / "keys"
        kf.write_text("secret\n")
        kf.chmod(0o000)
        try:
            s = config_mod.load(**{"server.bind_host": "0.0.0.0",
                                   "auth.api_keys_file": str(kf)})
            problems = s.check_safe_to_start()
        finally:
            kf.chmod(0o600)
        if problems:                      # skipped when running as root
            assert any("cannot be read" in p for p in problems)

    @pytest.mark.parametrize("text", EMPTIED)
    def test_enforcement_keys_off_configured_not_on_keys_loaded(self, tmp_path,
                                                                text):
        kf = tmp_path / "keys"
        kf.write_text(text)
        ks = KeyStore(kf)
        assert len(ks) == 0
        assert ks.enabled is False, "no key can authenticate -- that is true"
        assert ks.configured is True, (
            "but a file WAS named, and that is what must gate enforcement")

    def test_no_keyfile_at_all_leaves_auth_off(self, tmp_path):
        """The documented loopback default must be untouched by this."""
        ks = KeyStore(None)
        assert ks.configured is False

    def test_a_zero_key_deployment_401s_instead_of_serving(self, fake,
                                                           tmp_path,
                                                           monkeypatch):
        """If the startup refusal is bypassed, the runtime still 401s
        every gated route."""
        kf = tmp_path / "keys"
        kf.write_text("# every key rotated out\n")
        app_mod.create_app(config_mod.load(**{
            "robot.ip": fake.host, "robot.rpc_port": fake.rpc_port,
            "robot.telemetry_port": fake.stream_port,
            "robot.upload_port": fake.upload_port,
            "robot.download_port": fake.download_port,
            "auth.api_keys_file": str(kf)}))
        # Pretend the startup guard was skipped (packaged config, direct
        # uvicorn import in an old build, etc.). Patched on the class -- the
        # Settings model blocks instance attribute assignment.
        monkeypatch.setattr(config_mod.Settings, "check_safe_to_start",
                            lambda self: [])
        with TestClient(app_mod.app) as c:
            assert c.get("/api/v1/capabilities").status_code == 401
            assert c.get("/api/v1/robot/state").status_code == 401
            # And the two that must never be gated, still are not.
            assert c.post("/api/v1/motion/stop").status_code == 200
            assert c.get("/api/v1/system/health").status_code == 200


class TestTheKeyParsingRuleHasOneImplementation:
    """The startup check and the enforcement path share one key parser."""

    def test_the_keystore_and_the_startup_check_share_a_parser(self, tmp_path):
        from fws.auth import parse_key_file
        kf = tmp_path / "keys"
        kf.write_text("# c\n\nk1  one\nk2\n   \nk3  three\n")
        assert len(parse_key_file(kf)) == 3
        assert len(KeyStore(kf)) == 3

    def test_labels_survive_the_shared_parser(self, tmp_path):
        kf = tmp_path / "keys"
        kf.write_text("k1  ci-runner\nk2\n")
        ks = KeyStore(kf)
        assert ks.identify("k1") == "ci-runner"
        assert ks.identify("k2") == "unlabelled"
        assert ks.identify("nope") is None


class TestOpenPathRegistration:
    """A package that mounts a UI must be able to serve the page that ASKS
    for the key; the data behind it stays protected."""

    def test_registering_opens_only_that_prefix(self, secured, monkeypatch):
        from fws import auth as auth_mod
        original = list(auth_mod.ALWAYS_OPEN)
        try:
            auth_mod.register_open_path("/console")
            assert auth_mod.is_open_path("/console/js/main.js")
            assert not auth_mod.is_open_path("/api/v1/state")
        finally:
            auth_mod.ALWAYS_OPEN[:] = original

    def test_registering_is_idempotent(self):
        from fws import auth as auth_mod
        original = list(auth_mod.ALWAYS_OPEN)
        try:
            auth_mod.register_open_path("/console")
            auth_mod.register_open_path("/console")
            assert auth_mod.ALWAYS_OPEN.count("/console") == 1
        finally:
            auth_mod.ALWAYS_OPEN[:] = original

    def test_the_api_surface_cannot_be_opened(self):
        """The whole point is that data stays behind the key."""
        from fws import auth as auth_mod
        for bad in ("/", "/api", "/api/v1", "/api/v1/"):
            with pytest.raises(ValueError, match=r"refusing|must start"):
                auth_mod.register_open_path(bad)

    def test_a_relative_prefix_is_refused(self):
        from fws import auth as auth_mod
        with pytest.raises(ValueError, match="must start"):
            auth_mod.register_open_path("console")
