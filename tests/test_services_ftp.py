"""The FTP client, against an in-process fake FTP server on loopback."""
from __future__ import annotations

import pytest

from fws.services import ServiceAuthError, ServiceError, ServiceUnavailable
from fws.services.ftp import FtpClient, _parse_list_line
from fws.testing.fake_ftp import FakeFtpServer

ROOT = "/fruser"
FILES = {
    "/fruser/force_test.lua": b"-- force test\nprint(1)\n",
    "/fruser/paint.lua": b"-- paint\n",
    "/fruser/history/2026-02-20.lua": b"-- old\n",
}


@pytest.fixture
def server():
    with FakeFtpServer(files=dict(FILES)) as srv:
        yield srv


def _client(server, **kw):
    kw.setdefault("timeout_s", 30)
    return FtpClient(server.host, server.port, root=ROOT, **kw)


class TestListing:
    def test_the_true_listing_is_returned_instantly(self, server):
        with _client(server) as c:
            entries = c.list()
        names = {e.name for e in entries}
        assert "force_test.lua" in names
        assert "paint.lua" in names
        # The subdirectory is surfaced as a dir entry, not its contents.
        dirs = {e.name for e in entries if e.is_dir}
        assert "history" in dirs

    def test_sizes_are_parsed(self, server):
        with _client(server) as c:
            entries = {e.name: e for e in c.list()}
        assert entries["force_test.lua"].size == len(
            FILES["/fruser/force_test.lua"])

    def test_directories_sort_before_files(self, server):
        with _client(server) as c:
            entries = c.list()
        assert entries[0].is_dir


class TestTransfer:
    def test_download_round_trips_bytes(self, server):
        with _client(server) as c:
            blob = c.download("force_test.lua")
        assert blob == FILES["/fruser/force_test.lua"]

    def test_a_missing_file_is_a_service_error_not_a_crash(self, server):
        with _client(server) as c, pytest.raises(ServiceError):
            c.download("does_not_exist.lua")

    def test_upload_lands_on_the_server(self, server):
        payload = b"-- newly uploaded\n"
        with _client(server) as c:
            n = c.upload("uploaded.lua", payload)
        assert n == len(payload)
        assert server.uploaded["/fruser/uploaded.lua"] == payload

    def test_upload_then_download_is_identical(self, server):
        payload = bytes(range(256)) * 4
        with _client(server) as c:
            c.upload("blob.bin", payload)
            assert c.download("blob.bin") == payload

    def test_delete_removes_the_file(self, server):
        with _client(server) as c:
            c.delete("paint.lua")
        assert "/fruser/paint.lua" in server.deleted


class TestAuth:
    def test_a_rejected_login_raises_auth_error(self):
        with FakeFtpServer(user="root", password="right") as srv:
            c = FtpClient(srv.host, srv.port, user="root", password="wrong",
                          timeout_s=30, root=ROOT)
            with pytest.raises(ServiceAuthError):
                c.connect()

    def test_a_correct_login_succeeds(self):
        srv = FakeFtpServer(user="root", password="right", files=dict(FILES))
        with srv:
            c = FtpClient(srv.host, srv.port, user="root", password="right",
                          timeout_s=30, root=ROOT)
            with c:
                assert c.list()


class TestUnreachable:
    def test_a_dead_port_is_service_unavailable(self):
        c = FtpClient("127.0.0.1", 1, timeout_s=2, root=ROOT)
        with pytest.raises(ServiceUnavailable):
            c.connect()


class TestListParsing:
    """An unparseable line is skipped, never turned into a wrong entry."""

    def test_a_file_line_parses(self):
        e = _parse_list_line(
            "-rw-r--r-- 1 root root 42 Feb 20 01:34 force_test.lua")
        assert e and e.name == "force_test.lua" and e.size == 42
        assert not e.is_dir

    def test_a_dir_line_parses(self):
        e = _parse_list_line("drwxr-xr-x 2 root root 4096 Feb 20 01:34 history")
        assert e and e.is_dir

    @pytest.mark.parametrize("line", ["", "garbage", "total 8",
                                      "drwx too few fields"])
    def test_an_unparseable_line_is_skipped(self, line):
        assert _parse_list_line(line) is None

    def test_dot_entries_are_dropped(self):
        assert _parse_list_line(
            "drwxr-xr-x 2 root root 4096 Feb 20 01:34 .") is None


class TestListingFailuresAreDistinguishable:
    """An empty list must not conflate 'empty directory' with 'unparseable
    format'; symlink lines must parse."""

    def test_unparseable_listing_is_an_error_not_empty(self):
        # A server whose LIST lines are non-empty but not Unix `ls -l`.
        class WeirdListing(FakeFtpServer):
            def _render_list(self, target):
                return "DIR  FORCE_TEST.LUA  1234\r\nDIR  PAINT.LUA  99\r\n"

        with WeirdListing(files=dict(FILES)) as srv:
            c = FtpClient(srv.host, srv.port, root=ROOT, timeout_s=30)
            with c, pytest.raises(ServiceError) as e:
                c.list()
            assert "NOT an empty directory" in str(e.value)

    def test_a_genuinely_empty_directory_is_still_empty(self):
        with FakeFtpServer(files={}) as srv:
            c = FtpClient(srv.host, srv.port, root=ROOT, timeout_s=30)
            with c:
                assert c.list() == []

    def test_a_symlink_line_keeps_the_link_name_not_the_target(self):
        e = _parse_list_line(
            "lrwxrwxrwx 1 root root 10 Feb 20 01:34 curprog -> paint.lua")
        assert e is not None
        assert e.name == "curprog", "the link's own name, not the arrow/target"
        assert e.is_dir is False
