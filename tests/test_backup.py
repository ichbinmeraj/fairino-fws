"""Backup and restore."""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod
from fws.backup import BackupError, upload_point_table


@pytest.fixture
def client(fake, tmp_path):
    app_mod.create_app(config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
        "server.data_dir": str(tmp_path),
    }))
    with TestClient(app_mod.app) as c:
        app_mod.control._leases.clear()
        yield c
        app_mod.control._leases.clear()


class TestDiscovery:
    def test_lists_what_can_be_backed_up(self, client):
        d = client.get("/api/v1/backup").json()
        kinds = {k["kind"] for k in d["kinds"]}
        assert kinds == {"datasource", "userdata"}
        assert "Firmware" in d["not_included"]


class TestBundleDownload:
    @pytest.mark.parametrize("kind,filename", [
        ("datasource", "alldatasource.tar.gz"),
        ("userdata", "fr_user_data.tar.gz"),
    ])
    def test_download(self, client, fake, kind, filename):
        fake.backups[filename] = b"payload-" + kind.encode()
        r = client.get(f"/api/v1/backup/{kind}")
        assert r.status_code == 200
        d = r.json()
        assert d["filename"] == filename
        assert base64.b64decode(d["content_base64"]) == b"payload-" + kind.encode()

    def test_unknown_kind(self, client):
        assert client.get("/api/v1/backup/nonsense").status_code == 502

    def test_download_is_audited(self, client, fake):
        fake.backups["alldatasource.tar.gz"] = b"x"
        client.get("/api/v1/backup/datasource")
        actions = [e["action"] for e in
                   client.get("/api/v1/events").json()["events"]]
        assert "backup.download" in actions


class TestPointTableFraming:
    """The header width is the whole reason this is separate from files.py."""

    def test_upload_uses_a_44_byte_header(self, driver, fake):
        body = b"pretend sqlite point table"
        upload_point_table(driver, "cell.db", body)
        assert fake.point_tables["cell.db"] == body, (
            "the fake rejects anything that is not a 44-byte header, so a "
            "round trip proves the framing")

    def test_lua_framing_would_be_rejected(self, driver, fake):
        """The fake rejects Lua framing for a point-table upload."""
        from fws import backup as bk
        driver._call("PointTableUpload", "wrong.db")
        s = bk._connect(driver.ip, driver.upload_port, 5.0)
        try:
            body = b"data"
            total = len(body) + 46 + 4
            # Deliberately the LUA header: 10-digit size, 46 bytes.
            s.sendall(b"/f/b" + f"{total:10d}".encode()
                      + bk._md5(body).encode())
            s.sendall(body)
            s.sendall(b"/b/f")
            reply = s.recv(64)
        finally:
            s.close()
        assert not reply.startswith(b"SUCCESS")

    def test_size_cap_is_2mb_not_500mb(self, driver):
        with pytest.raises(BackupError, match=r"2 MB|accepts"):
            upload_point_table(driver, "big.db", b"x" * (3 * 1024 * 1024))

    def test_name_must_be_db(self, driver):
        with pytest.raises(BackupError, match=r"\.db"):
            upload_point_table(driver, "table.lua", b"x")


class TestRestoreIsGuarded:
    PAYLOAD = base64.b64encode(b"restored table").decode()

    def test_requires_confirmation_and_says_what_is_lost(self, client, fake):
        r = client.put("/api/v1/points/tables/cell.db",
                       json={"content_base64": self.PAYLOAD})
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert "overwrites the positions" in detail
        assert "cannot undo" in detail
        assert "cell.db" not in fake.point_tables

    def test_confirmed_restore_applies(self, client, fake):
        r = client.put("/api/v1/points/tables/cell.db",
                       json={"content_base64": self.PAYLOAD, "confirm": True})
        assert r.status_code == 200
        assert fake.point_tables["cell.db"] == b"restored table"

    def test_takes_the_config_lock(self, client):
        client.post("/api/v1/control",
                    json={"client_id": "other", "domains": ["config"]})
        r = client.put("/api/v1/points/tables/cell.db",
                       json={"content_base64": self.PAYLOAD, "confirm": True})
        assert r.status_code == 428

    def test_rejects_path_traversal(self, client):
        r = client.put("/api/v1/points/tables/..%2Fevil.db",
                       json={"content_base64": self.PAYLOAD, "confirm": True})
        assert r.status_code in (404, 422)

    def test_rejects_invalid_base64(self, client):
        r = client.put("/api/v1/points/tables/cell.db",
                       json={"content_base64": "not!base64!", "confirm": True})
        assert r.status_code == 422


class TestRoundTrip:
    def test_download_then_restore(self, client, fake):
        fake.point_tables["live.db"] = b"original contents"
        got = client.get("/api/v1/points/tables/live.db").json()
        assert base64.b64decode(got["content_base64"]) == b"original contents"

        fake.point_tables.clear()
        r = client.put("/api/v1/points/tables/live.db",
                       json={"content_base64": got["content_base64"],
                             "confirm": True})
        assert r.status_code == 200
        assert fake.point_tables["live.db"] == b"original contents"

    def test_missing_table_is_404(self, client):
        assert client.get("/api/v1/points/tables/nope.db").status_code == 404


class TestSwitch:
    def test_switch_and_revert(self, client, fake):
        client.post("/api/v1/points/tables/cell.db/switch")
        assert fake.active_point_table == "cell.db"
        r = client.post("/api/v1/points/tables/-/switch")
        assert r.json()["active"] is None


class TestACorruptReadIsNotAMissingTable:
    """A corrupt or failed read is distinguished from a missing table."""

    def test_a_declined_transfer_is_404_but_says_what_it_assumed(self, client):
        r = client.get("/api/v1/points/tables/nope.db")
        assert r.status_code == 404
        d = r.json()["detail"]
        assert "would not send" in d["message"]
        assert "not documented" in d["caveat"], (
            "FWS must not assert absence it cannot establish")

    def test_a_corrupt_transfer_is_502_not_404(self, client, fake,
                                               monkeypatch):
        """The table is there; the bytes did not survive."""
        from fws import backup
        fake.point_tables["real.db"] = b"a" * 64

        def corrupt(driver, timeout=30.0):
            raise backup.BackupError("md5 mismatch: declared abc, computed def")

        monkeypatch.setattr(backup, "_receive", corrupt)
        r = client.get("/api/v1/points/tables/real.db")
        assert r.status_code == 502, (
            "a corrupt download must never read as 'not found'")
        d = r.json()["detail"]
        assert "not evidence it is absent" in d["message"]
        assert d["integrity"] is not None
        assert "do NOT treat this as an empty table" in d["advice"]

    def test_a_transport_failure_is_also_502(self, client, fake, monkeypatch):
        from fws import backup
        fake.point_tables["real2.db"] = b"b" * 64

        def timeout(driver, timeout=30.0):
            raise backup.BackupError("timed out waiting for the transfer")

        monkeypatch.setattr(backup, "_receive", timeout)
        r = client.get("/api/v1/points/tables/real2.db")
        assert r.status_code == 502
        assert r.json()["detail"]["integrity"] is None, (
            "only an md5 mismatch is an integrity failure")


class TestARestoreIsProvenNotAssumed:
    """A restore is proven by read-back, not assumed from what was sent."""

    def test_a_good_restore_reports_confirmed_readback(self, client, fake):
        import base64
        body = base64.b64encode(b"x" * 128).decode()
        r = client.put("/api/v1/points/tables/cell.db",
                       json={"content_base64": body, "confirm": True})
        assert r.status_code == 200
        d = r.json()
        assert d["readback"] == "confirmed"
        assert "only what was SENT" in d["readback_means"]

    def test_a_readback_mismatch_is_502_not_a_success(self, client, fake,
                                                      monkeypatch):
        import base64

        from fws import backup_api
        real = backup_api.download_point_table

        def wrong(driver, name, timeout=30.0):
            out = real(driver, name, timeout)
            return {**out, "md5": "0" * 32}      # pretend it stored otherwise

        monkeypatch.setattr(backup_api, "download_point_table", wrong)
        body = base64.b64encode(b"y" * 128).decode()
        r = client.put("/api/v1/points/tables/cell2.db",
                       json={"content_base64": body, "confirm": True})
        assert r.status_code == 502
        assert "NOT what you sent" in r.json()["detail"]["message"]

    def test_an_unverifiable_restore_says_so_rather_than_claiming_success(
            self, client, fake, monkeypatch):
        """An unverifiable restore says so rather than claiming success."""
        import base64

        from fws import backup_api
        from fws.backup import BackupError

        def broken(driver, name, timeout=30.0):
            raise BackupError("cannot read it back")

        monkeypatch.setattr(backup_api, "download_point_table", broken)
        body = base64.b64encode(b"z" * 128).decode()
        r = client.put("/api/v1/points/tables/cell3.db",
                       json={"content_base64": body, "confirm": True})
        assert r.status_code == 200, "the write did happen"
        assert r.json()["readback"].startswith("NOT VERIFIED")
