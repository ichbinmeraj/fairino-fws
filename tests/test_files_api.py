"""File management API, against the fake controller."""
from __future__ import annotations

import base64
import hashlib
import typing

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fws import config as config_mod
from fws import files_api, files_wire
from fws.control import ControlLock
from fws.events import AuditLog
from fws.lua_verdict import find_verdict, parse
from fws.testing.fake_controller import _lua_log_line


@pytest.fixture
def audit_log():
    return AuditLog()


@pytest.fixture
def control():
    return ControlLock()


@pytest.fixture
def client(fake, driver, tmp_path, control, audit_log):
    settings = config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "server.data_dir": str(tmp_path),
    })
    app = FastAPI()
    app.include_router(files_api.build(
        lambda: driver, lambda: settings, lambda: control,
        lambda action, **kw: audit_log.record(action, **kw),
    ))
    with TestClient(app) as c:
        yield c


def _lua(client, name, source, **kw):
    return client.put(f"/api/v1/files/lua/{name}",
                      json={"content": source, **kw})


# --------------------------------------------------------------- the matrix
class TestFileTypeMatrix:
    def test_names_kinds_and_never_a_raw_integer_route(self, client):
        m = client.get("/api/v1/files").json()
        assert set(m["kinds"]) == {"lua", "point_table", "open_lua",
                                   "controller_log"}
        # A route keyed on the integer would be a remote flashing endpoint.
        assert all(not seg.isdigit()
                   for r in ("lua", "point_table", "open_lua")
                   for seg in r.split("/"))

    def test_file_type_is_an_opcode_not_a_type(self, client):
        """The same fileType integer means different things per verb."""
        m = client.get("/api/v1/files").json()
        assert m["kinds"]["controller_log"]["file_type"]["download"] == 1
        assert m["refused"]["software_upgrade"]["file_type"] == 1
        assert m["refused"]["software_upgrade"]["verb"] == "upload"

    def test_point_tables_have_no_file_type_at_all(self, client):
        pt = client.get("/api/v1/files").json()["kinds"]["point_table"]
        assert pt["file_type"] == {"upload": None, "download": None,
                                   "delete": None}
        assert pt["upload_rpc"] == "PointTableUpload"
        assert pt["header_bytes"] == 44

    def test_firmware_types_are_refused_with_a_reason(self, client):
        refused = client.get("/api/v1/files").json()["refused"]
        assert {"software_upgrade", "slave_firmware", "joint_parameters",
                "os_kernel", "axle_open_lua"} <= set(refused)
        for entry in refused.values():
            assert entry["reason"] and entry["sdk_evidence"]

    def test_what_is_not_possible_is_stated(self, client):
        m = client.get("/api/v1/files").json()
        assert set(m["not_possible"]) >= {"rename", "copy", "move", "list"}
        assert m["listing"]["available"] is False
        assert "GetLuaList" in m["listing"]["why"]

    def test_a_refused_kind_is_403_not_404(self, client):
        r = client.get("/api/v1/files/os_kernel")
        assert r.status_code == 403
        assert "kernel" in r.json()["detail"].lower()

    def test_an_unknown_kind_is_404(self, client):
        assert client.get("/api/v1/files/sprockets").status_code == 404


# ----------------------------------------------------------------- routing
class TestPerTypeRouting:
    def test_lua_round_trip(self, client, fake):
        r = _lua(client, "pick.lua", "WaitMs(1)\n")
        assert r.status_code == 200, r.text
        assert fake.files["pick.lua"] == b"WaitMs(1)\n"
        got = client.get("/api/v1/files/lua/pick.lua").json()
        assert got["content"] == "WaitMs(1)\n"
        assert got["md5"] == hashlib.md5(b"WaitMs(1)\n").hexdigest()

    def test_open_lua_lands_in_a_different_store(self, client, fake):
        """fileType 11 is a different file space, not a different name."""
        body = base64.b64encode(b"-- device driver\n").decode()
        r = client.put("/api/v1/files/open_lua/CtrlDev_x.lua",
                       json={"content": "-- device driver\n"})
        assert r.status_code == 200, r.text
        assert "CtrlDev_x.lua" in fake.open_luas
        assert "CtrlDev_x.lua" not in fake.files
        assert body  # the text form is accepted for a non-binary kind

    def test_open_lua_delete_uses_type_11(self, client, fake):
        client.put("/api/v1/files/open_lua/CtrlDev_x.lua",
                   json={"content": "x"})
        assert client.delete(
            "/api/v1/files/open_lua/CtrlDev_x.lua").status_code == 200
        assert "CtrlDev_x.lua" not in fake.open_luas
        assert ("FileDelete", (11, "CtrlDev_x.lua")) in fake.calls

    def test_point_table_upload_uses_the_44_byte_header(self, client, fake):
        body = base64.b64encode(b"pretend sqlite").decode()
        r = client.put("/api/v1/files/point_table/cell.db",
                       json={"content_base64": body})
        assert r.status_code == 200, r.text
        # The fake rejects anything that is not an 8-digit zero-padded size,
        # so a successful round trip is proof of the framing.
        assert fake.point_tables["cell.db"] == b"pretend sqlite"

    def test_point_table_refuses_text_content(self, client):
        r = client.put("/api/v1/files/point_table/cell.db",
                       json={"content": "not base64"})
        assert r.status_code == 422
        assert "content_base64" in r.json()["detail"]

    def test_point_table_cannot_be_deleted_because_the_wire_cannot(self, client):
        r = client.delete("/api/v1/files/point_table/cell.db")
        assert r.status_code == 405
        assert "will not fake" in r.json()["detail"]

    @pytest.mark.parametrize("header", [46, 44])
    def test_point_table_download_survives_either_header_width(
            self, client, fake, header):
        """Point-table download accepts either the 44- or 46-byte header width."""
        fake.point_tables["live.db"] = b"original contents"
        fake.point_table_download_header = header
        got = client.get("/api/v1/files/point_table/live.db")
        assert got.status_code == 200, got.text
        assert base64.b64decode(got.json()["content_base64"]) \
            == b"original contents"

    def test_a_corrupt_transfer_is_discarded_not_returned(
            self, client, fake, monkeypatch):
        """A payload whose md5 fails is never handed back."""
        fake.files["bad.lua"] = b"body"
        real = files_wire.md5
        monkeypatch.setattr(
            files_wire, "md5",
            lambda data: real(b"tampered") if data == b"body" else real(data))
        r = client.get("/api/v1/files/lua/bad.lua")
        assert r.status_code == 404
        assert "md5" in r.json()["detail"]


# ------------------------------------------------------------- safety rails
class TestGuards:
    @pytest.mark.parametrize("name", [
        "..%2Fevil.lua", "%2E%2E%2F%2E%2E%2Fetc%2Fpasswd", "sub%2Fdir.lua",
        "no_extension", ".hidden.lua", "sp ace.lua", "sem;colon.lua",
        "caf\u00e9.lua",
    ])
    def test_unsafe_names_are_refused(self, client, name):
        r = client.put(f"/api/v1/files/lua/{name}", json={"content": "x"})
        assert r.status_code in (404, 422), (name, r.status_code)

    def test_size_is_checked_before_anything_opens_a_port(self, client, fake):
        big = "x" * (files_wire.MAX_LUA_BYTES + 1)
        r = _lua(client, "huge.lua", big)
        assert r.status_code == 413
        # The point of checking first: no FileUpload was ever sent, so the
        # controller never opened 20010 for a transfer that cannot happen.
        assert not [c for c in fake.calls if c[0] == "FileUpload"]

    def test_point_table_cap_is_2mb_not_500mb(self, client, fake):
        payload = base64.b64encode(b"x" * (2 * 1024 * 1024)).decode()
        r = client.put("/api/v1/files/point_table/big.db",
                       json={"content_base64": payload})
        assert r.status_code == 413
        assert not [c for c in fake.calls if c[0] == "PointTableUpload"]

    def test_upload_takes_the_program_lock(self, client, control):
        control.acquire("someone-else", ["program"])
        r = _lua(client, "pick.lua", "WaitMs(1)\n")
        assert r.status_code == 428

    def test_point_table_takes_the_config_lock(self, client, control):
        control.acquire("someone-else", ["config"])
        r = client.put("/api/v1/files/point_table/cell.db",
                       json={"content_base64": base64.b64encode(b"x").decode()})
        assert r.status_code == 428

    def test_download_needs_no_lock(self, client, control, fake):
        fake.files["pick.lua"] = b"WaitMs(1)\n"
        control.acquire("someone-else", ["program", "config"])
        assert client.get("/api/v1/files/lua/pick.lua").status_code == 200

    def test_overwrite_is_required_for_a_name_in_the_index(self, client):
        _lua(client, "pick.lua", "WaitMs(1)\n")
        r = _lua(client, "pick.lua", "WaitMs(1)\n")
        assert r.status_code == 409
        assert "overwrite=true" in r.json()["detail"]
        assert _lua(client, "pick.lua", "WaitMs(1)\n",
                    overwrite=True).status_code == 200


# ----------------------------------------------------------- the edit cycle
class TestEditRoundTrip:
    def test_if_match_detects_a_lost_update(self, client, fake):
        _lua(client, "pick.lua", "WaitMs(1)\n")
        mine = client.get("/api/v1/files/lua/pick.lua").json()["md5"]

        # Somebody else -- the teach pendant, the web UI, another client --
        # writes the same file. There is no controller-side locking to notice.
        fake.files["pick.lua"] = b"WaitMs(2)\n"

        r = client.put("/api/v1/files/lua/pick.lua",
                       json={"content": "WaitMs(3)\n", "if_match": mine})
        assert r.status_code == 412
        detail = r.json()["detail"]
        assert detail["if_match"] == mine
        assert detail["current_md5"] == hashlib.md5(b"WaitMs(2)\n").hexdigest()
        # And the loser's edit did not land.
        assert fake.files["pick.lua"] == b"WaitMs(2)\n"

    def test_if_match_accepts_the_version_you_edited(self, client):
        _lua(client, "pick.lua", "WaitMs(1)\n")
        current = client.get("/api/v1/files/lua/pick.lua").json()["md5"]
        r = client.put("/api/v1/files/lua/pick.lua",
                       json={"content": "WaitMs(1)\n-- edited\n",
                             "if_match": current})
        assert r.status_code == 200, r.text

    def test_if_match_on_a_missing_file_is_412(self, client):
        r = client.put("/api/v1/files/lua/ghost.lua",
                       json={"content": "WaitMs(1)\n", "if_match": "0" * 32})
        assert r.status_code == 412
        assert r.json()["detail"]["current_md5"] is None

    def test_verify_reads_back_and_compares(self, client):
        r = _lua(client, "pick.lua", "WaitMs(1)\n", verify=True)
        assert r.json()["verified_by_readback"] is True


# -------------------------------------------------- THE COMPILER'S VERDICT
class TestCompilerVerdict:
    """LuaUpLoadUpdate returns -1; the reason is in the controller's log."""

    def test_a_good_program_is_accepted(self, client, fake):
        r = _lua(client, "ok.lua", "WaitMs(1)\n")
        assert r.status_code == 200
        assert r.json()["compiled"] is True
        assert fake.rblog_fetches == 0, "a success must never fetch the log"

    def test_an_absent_function_is_named(self, client):
        r = _lua(client, "bad.lua", "NoSuchFunctionXYZ(1)\n")
        assert r.status_code == 422
        d = r.json()["detail"]
        assert d["verdict"]["outcome"] == "unknown_function"
        assert d["verdict"]["function"] == "NoSuchFunctionXYZ"
        assert d["verdict"]["line"] == 1
        assert d["unambiguous"] is True

    def test_a_wrong_argument_count_names_the_function_and_the_argument(
            self, client):
        r = _lua(client, "bad.lua", "-- header\nWaitMs(1,2,3,4)\n")
        d = r.json()["detail"]
        assert d["verdict"]["outcome"] == "wrong_argument_count"
        assert d["verdict"]["function"] == "WaitMs"
        assert d["verdict"]["line"] == 2
        assert d["verdict"]["arguments"] == ["#4"]

    def test_a_failed_point_lookup_is_not_reported_as_an_absent_function(
            self, client):
        """A failed point lookup is not reported as an absent function."""
        r = _lua(client, "bad.lua", "Lin(1,2,3,4,5,6,7,8,9,10,11)\n")
        d = r.json()["detail"]
        assert d["verdict"]["outcome"] == "needs_a_taught_point"
        assert "cell limitation" in d["verdict"]["explains"]

    def test_the_message_says_the_bytes_are_still_on_the_controller(self, client):
        r = _lua(client, "bad.lua", "NoSuchFunctionXYZ(1)\n")
        assert "overwritten" in r.json()["detail"]["file_state"]

    def test_the_verdict_is_read_from_the_live_log_not_the_newest_looking_one(
            self, client, fake):
        """The verdict is read from the live log, not the newest-looking one."""
        fake.stale_lua_log.append(_lua_log_line(
            "lua_name:/fruser/bad.lua---line_num:99---error_info: "
            "attempt to call global WrongAnswer (a nil value)", 43))
        fake.stale_lua_log.append(_lua_log_line("success", 44))
        r = _lua(client, "bad.lua", "NoSuchFunctionXYZ(1)\n")
        v = r.json()["detail"]["verdict"]
        assert v["function"] == "NoSuchFunctionXYZ"
        assert v["line"] == 1

    def test_an_ambiguous_archive_returns_no_verdict_rather_than_a_guess(
            self, client, fake):
        """An ambiguous archive returns no verdict rather than a guess."""
        fake.stale_lua_log.append(_lua_log_line(
            "lua_name:/fruser/bad.lua---line_num:99---error_info: "
            "attempt to call global WrongAnswer (a nil value)", 43))
        r = _lua(client, "bad.lua", "NoSuchFunctionXYZ(1)\n")
        d = r.json()["detail"]
        assert r.status_code == 422
        assert d["verdict"] is None
        assert d["unambiguous"] is False
        assert len(d["candidates"]) == 2


class TestTheLogFetchIsRationed:
    """One bad upload must never become a storm of log fetches."""

    def test_a_transfer_failure_never_fetches_the_log(self, client, fake):
        """No compile happened, so no verdict was ever written."""
        r = client.get("/api/v1/files/lua/absent.lua")
        assert r.status_code == 404
        assert fake.rblog_fetches == 0

    def test_the_second_rejection_is_refused_a_fetch_and_says_why(
            self, client, fake):
        first = _lua(client, "a.lua", "NoSuchFunctionXYZ(1)\n")
        assert first.json()["detail"]["verdict"] is not None
        assert fake.rblog_fetches == 1

        second = _lua(client, "b.lua", "NoSuchOtherThing(1)\n")
        assert second.status_code == 422
        d = second.json()["detail"]
        assert d["looked_up"] is False
        assert "wedged the controller" in d["reason"]
        assert fake.rblog_fetches == 1, "the cooldown must hold"

    def test_a_loop_of_failures_costs_one_fetch(self, client, fake):
        for i in range(12):
            r = _lua(client, f"loop{i}.lua", "NoSuchFunctionXYZ(1)\n")
            assert r.status_code == 422
        assert fake.rblog_fetches == 1

    def test_the_budget_is_observable(self, client):
        _lua(client, "a.lua", "NoSuchFunctionXYZ(1)\n")
        _lua(client, "b.lua", "NoSuchFunctionXYZ(1)\n")
        state = client.get("/api/v1/files/-/verdicts").json()
        assert state["log_fetch"]["fetches"] == 1
        assert state["log_fetch"]["suppressed"]["cooldown"] == 1
        outcomes = [e["outcome"] for e in state["recent"]]
        assert outcomes[0] == "unknown_function"

    def test_a_wedged_validator_is_diagnosed_and_costs_no_fetch(
            self, client, fake, monkeypatch):
        """A wedged validator is diagnosed and costs no log fetch."""
        monkeypatch.setattr(files_api, "WEDGE_SECONDS", 0.1)
        fake.wedge_delay_s = 0.2
        fake.lua_validator_wedged = True
        r = _lua(client, "anything.lua", "WaitMs(1)\n")
        assert r.status_code == 503
        d = r.json()["detail"]
        assert d["log_fetched"] is False
        assert "4.09" in d["diagnosis"]
        assert "restart" in d["recovery"]
        assert fake.rblog_fetches == 0

    def test_a_failing_log_fetch_does_not_replace_the_422(self, client, fake):
        """The rejected upload is the news; failing to explain it is not."""
        fake.lua_log.clear()

        def refuse():
            return -1

        fake._rpc.register_function(refuse, "RbLogDownloadPrepare")
        r = _lua(client, "bad.lua", "NoSuchFunctionXYZ(1)\n")
        assert r.status_code == 422
        d = r.json()["detail"]
        assert d["verdict"] is None
        assert "could not be fetched" in d["reason"]


# ---------------------------------------------------------------- listing
class TestListing:
    def test_the_list_says_it_is_not_the_controllers_directory(self, client):
        """The listing admits it is partial and points at the authoritative source."""
        d = client.get("/api/v1/files/lua").json()
        assert d["complete"] is False
        assert d["source"] == "fws-index"
        assert "teach pendant" in d["note"]
        assert "source=controller" in d["note"]

    def test_an_upload_appears_in_the_index(self, client):
        _lua(client, "pick.lua", "WaitMs(1)\n")
        names = [f["name"] for f in client.get("/api/v1/files/lua").json()["files"]]
        assert names == ["pick.lua"]

    def test_a_file_the_gateway_never_uploaded_is_invisible_but_downloads(
            self, client, fake):
        """The precise cost of having no listing."""
        fake.files["from_the_pendant.lua"] = b"WaitMs(1)\n"
        assert client.get("/api/v1/files/lua").json()["files"] == []
        assert client.get(
            "/api/v1/files/lua/from_the_pendant.lua").status_code == 200
        # ...and once there is proof it exists, the index records it.
        names = [f["name"] for f in client.get("/api/v1/files/lua").json()["files"]]
        assert names == ["from_the_pendant.lua"]

    def test_lua_is_listed_from_the_shared_programs_index(self, client,
                                                          tmp_path):
        """Lua files are listed from the shared programs index."""
        from fws.programs_api import program_index
        program_index(tmp_path).record("legacy.lua", md5="abc")
        entries = client.get("/api/v1/files/lua").json()["files"]
        assert [e["name"] for e in entries] == ["legacy.lua"]

    def test_deleting_an_absent_file_reconciles_the_index(self, client, fake):
        _lua(client, "pick.lua", "WaitMs(1)\n")
        fake.files.pop("pick.lua")            # somebody else removed it
        r = client.delete("/api/v1/files/lua/pick.lua")
        assert r.status_code == 200
        assert r.json()["already_absent_on_controller"] is True
        assert client.get("/api/v1/files/lua").json()["files"] == []


# -------------------------------------------------------- verdict plumbing
class TestVerdictParsing:
    """Payloads quoted verbatim from a real rblog.tar.gz."""

    def test_success(self):
        assert parse("success").outcome == "success"

    def test_nil_call(self):
        v = parse("lua_name:/fruser/fws_scratch.lua---line_num:1---error_info: "
                  "attempt to call global NoSuchFunctionXYZ (a nil value)")
        assert v.outcome == "unknown_function"
        assert v.function == "NoSuchFunctionXYZ"
        assert v.lua_name == "/fruser/fws_scratch.lua"

    def test_multiple_bad_arguments(self):
        v = parse("lua_name:fws_scratch.lua---line_num:1---error_info:bad "
                  "argument #5 #6 #11 #12 to Lin (Error number of parameters)")
        assert v.outcome == "wrong_argument_count"
        assert v.function == "Lin"
        assert v.arguments == ("#5", "#6", "#11", "#12")

    def test_database_lookup_means_present(self):
        v = parse("lua_name:fws_scratch.lua---line_num:1---error_info:failed "
                  "to query the database (the data does not exist)")
        assert v.outcome == "needs_a_taught_point"

    def test_a_syntax_error_is_reported_rather_than_misclassified(self):
        v = parse('lua_name:/fruser/fws_probe0.lua---line_num:8---error_info: '
                  'unfinished string near "')
        assert v.outcome == "rejected"
        assert v.line == 8

    def test_no_verdict_for_this_program_is_said_plainly(self, fake):
        blob = fake._build_rblog()
        out = find_verdict(blob, "never_uploaded.lua")
        assert out["verdict"] is None
        assert out["unambiguous"] is False


class TestVerdictAttribution:
    """A verdict is attributed to a program only by exact name, not substring."""

    def _archive(self, records):
        """A log archive shaped like the controller's, one file."""
        import io
        import tarfile

        from fws.testing.fake_controller import _lua_log_line
        lines = "\n".join(_lua_log_line(r, i) for i, r in enumerate(records))
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            data = lines.encode()
            info = tarfile.TarInfo("rblog/rblog_2015-01-06_09-52-00.863.log")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def test_a_longer_name_is_not_mistaken_for_ours(self):
        from fws.lua_verdict import find_verdict
        blob = self._archive([
            "lua_name:/fruser/mytest.lua---line_num:1---error_info: "
            "attempt to call global Nope (a nil value)",
            "lua_name:/fruser/other.lua---line_num:2---error_info: boom",
        ])
        got = find_verdict(blob, "test.lua")
        assert got["verdict"] is None, (
            "mytest.lua's verdict was attributed to test.lua")
        assert not got["unambiguous"]

    def test_a_name_quoted_inside_another_error_is_not_ours(self):
        from fws.lua_verdict import find_verdict
        blob = self._archive([
            "lua_name:/fruser/loader.lua---line_num:9---error_info: "
            "could not open test.lua",
        ])
        got = find_verdict(blob, "test.lua")
        assert got["verdict"] is None
        assert not got["unambiguous"]

    def test_our_own_verdict_is_still_found(self):
        from fws.lua_verdict import find_verdict
        blob = self._archive([
            "lua_name:/fruser/other.lua---line_num:1---error_info: boom",
            "lua_name:/fruser/test.lua---line_num:4---error_info: "
            "attempt to call global PrintMsg (a nil value)",
        ])
        got = find_verdict(blob, "test.lua")
        assert got["unambiguous"] is True
        assert got["verdict"].lua_name.endswith("test.lua")
        assert "PrintMsg" in got["verdict"].raw

    def test_a_bare_name_matches_the_controllers_absolute_path(self):
        """The controller reports /fruser/x.lua; callers say x.lua."""
        from fws.lua_verdict import find_verdict
        blob = self._archive([
            "lua_name:/fruser/prog.lua---line_num:1---error_info: nope",
        ])
        assert find_verdict(blob, "prog.lua")["verdict"] is not None


class TestDeleteRespectsTheLoadedProgram:
    """Deleting the loaded program is refused on the files route too."""

    def test_deleting_the_loaded_program_is_refused(self, client, fake):
        client.put("/api/v1/files/lua/loaded.lua",
                   json={"content": "-- x\n", "overwrite": True})
        fake.state.loaded_program = "/fruser/loaded.lua"
        r = client.delete("/api/v1/files/lua/loaded.lua")
        assert r.status_code == 409
        assert "currently loaded" in r.json()["detail"]
        assert "loaded.lua" in fake.files, "the file must still be there"

    def test_an_upload_here_is_visible_to_the_programs_route(self, client,
                                                             tmp_path):
        """An upload via the files route is visible to the programs route."""
        from fws.programs_api import program_index
        client.put("/api/v1/files/lua/shared.lua",
                   json={"content": "-- x\n", "overwrite": True})
        assert [e["name"] for e in program_index(tmp_path).all()] == [
            "shared.lua"]

    def test_a_delete_here_is_visible_to_the_programs_route(self, client,
                                                            tmp_path, fake):
        from fws.programs_api import program_index
        client.put("/api/v1/files/lua/gone.lua",
                   json={"content": "-- x\n", "overwrite": True})
        fake.state.loaded_program = ""
        assert client.delete("/api/v1/files/lua/gone.lua").status_code == 200
        assert program_index(tmp_path).all() == []

    def test_an_unloaded_program_still_deletes(self, client, fake):
        client.put("/api/v1/files/lua/spare.lua",
                   json={"content": "-- x\n", "overwrite": True})
        fake.state.loaded_program = "/fruser/other.lua"
        assert client.delete("/api/v1/files/lua/spare.lua").status_code == 200


class TestDualWidthHeaderAmbiguity:
    """A 46-byte transfer header is not truncated by a 44-byte parse."""

    def _serve(self, payload: bytes):
        """A one-shot server that speaks the controller's download framing."""
        import socket
        import threading
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)

        def run():
            conn, _ = srv.accept()
            with conn:
                # Send in small chunks so the reader must decide on a partial
                # buffer, as it does against the real controller.
                for i in range(0, len(payload), 512):
                    conn.sendall(payload[i:i + 512])
            srv.close()

        threading.Thread(target=run, daemon=True).start()
        return srv.getsockname()[1]

    def _stub(self, port):
        return type("D", (), {"ip": "127.0.0.1", "download_port": port})()

    def test_a_46_byte_header_is_not_truncated_by_the_44_byte_parse(self):
        import hashlib

        from fws.files_wire import GENERIC_HEADER, TRAILER, resolve
        from fws.files_wire import _receive as receive

        body = b"POINT-TABLE-CONTENT " * 400          # >> one 44-parse length
        total = len(body) + GENERIC_HEADER + TRAILER
        frame = (b"/f/b" + f"{total:10d}".encode()
                 + hashlib.md5(body).hexdigest().encode() + body + b"/b/f")

        # point_table's EXPECTED width is 44, so the wrong parse is tried
        # first -- exactly the ambiguous regime.
        kind = resolve("point_table")
        assert kind.header == 44
        port = self._serve(frame)
        got = receive(self._stub(port), kind, timeout=5.0)
        assert got == body, "the 44-byte parse truncated a 46-byte transfer"

    def test_the_expected_width_still_works(self):
        import hashlib

        from fws.files_wire import POINT_TABLE_HEADER, resolve
        from fws.files_wire import _receive as receive

        body = b"db-bytes " * 300
        total = len(body) + 16 + 32
        frame = (b"/f/b" + f"{total:08d}".encode()
                 + hashlib.md5(body).hexdigest().encode() + body + b"/b/f")
        port = self._serve(frame)
        got = receive(self._stub(port), resolve("point_table"), timeout=5.0)
        assert got == body
        assert POINT_TABLE_HEADER == 44


class TestLogFetchDoesNotParkWorkers:
    """The log-fetch lock is not held across the download."""

    def test_a_second_caller_returns_instead_of_waiting(self):
        import threading
        import time

        from fws.lua_verdict import LogFetcher

        release = threading.Event()
        entered = threading.Event()

        class SlowDriver:
            ip, port, upload_port, download_port = "127.0.0.1", 1, 2, 3

        fetcher = LogFetcher(lambda: SlowDriver(), min_interval_s=0)

        def slow_download(*_a, **_kw):
            entered.set()
            release.wait(5)
            return {"content": b"archive"}

        import fws.lua_verdict as mod
        original, mod.download = mod.download, slow_download
        try:
            t = threading.Thread(
                target=lambda: fetcher.fetch(covering_since=0.0), daemon=True)
            t.start()
            assert entered.wait(3), "first fetch never started"

            began = time.monotonic()
            blob, why = fetcher.fetch(covering_since=0.0)
            elapsed = time.monotonic() - began

            assert elapsed < 1.0, (
                f"the second caller waited {elapsed:.1f}s on the first's "
                f"download instead of returning")
            assert blob is None
            assert "already in progress" in why
        finally:
            release.set()
            mod.download = original
            t.join(timeout=5)

    def test_only_one_fetch_happens_for_a_burst(self):
        """Single-flight survives releasing the lock."""
        import threading

        from fws.lua_verdict import LogFetcher

        class D:
            ip, port, upload_port, download_port = "127.0.0.1", 1, 2, 3

        calls = []
        gate = threading.Event()
        fetcher = LogFetcher(lambda: D(), min_interval_s=0)

        def counting(*_a, **_kw):
            calls.append(1)
            gate.wait(5)
            return {"content": b"archive"}

        import fws.lua_verdict as mod
        original, mod.download = mod.download, counting
        try:
            threads = [threading.Thread(
                target=lambda: fetcher.fetch(covering_since=0.0), daemon=True)
                for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=0.5)
            assert len(calls) == 1, f"{len(calls)} concurrent downloads"
        finally:
            gate.set()
            mod.download = original
            for t in threads:
                t.join(timeout=5)


class TestControllerListing:
    """The controller file listing is read from the user-data backup, not GetLuaList."""

    ARCHIVE: typing.ClassVar = [
        ("root/web/file/user/prog.lua", 100),
        ("root/web/file/user/prog/2026-02-20-01-34.lua", 90),
        ("root/web/file/user/prog/2026-02-20-01-50.lua", 95),
        ("root/web/file/user/prog/web_point.db", 4096),
        ("root/web/file/user/other.lua", 50),
        ("root/web/file/points/point_table/Camera.db", 40960),
        ("root/web/file/template/empty.lua", 0),
    ]

    def _blob(self, entries=None):
        import io
        import tarfile
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            for path, size in (entries or self.ARCHIVE):
                info = tarfile.TarInfo(path)
                info.size = size
                t.addfile(info, io.BytesIO(b"\0" * size))
        return buf.getvalue()

    def test_top_level_programs_are_listed_and_history_is_not(self):
        from fws.files_listing import parse_archive
        lua = [e for e in parse_archive(self._blob()) if e.kind == "lua"]
        assert sorted(e.name for e in lua) == ["other.lua", "prog.lua"], (
            "a saved revision inside a program directory is history, not a "
            "program")

    def test_history_is_attached_to_its_program(self):
        from fws.files_listing import parse_archive
        prog = next(e for e in parse_archive(self._blob()) if e.name == "prog.lua")
        assert [v.saved_at for v in prog.versions] == [
            "2026-02-20-01-34", "2026-02-20-01-50"]
        assert prog.has_taught_points is True

    def test_a_program_without_history_reports_none(self):
        from fws.files_listing import parse_archive
        other = next(e for e in parse_archive(self._blob())
                     if e.name == "other.lua")
        assert other.versions == ()
        assert other.has_taught_points is False

    def test_point_tables_and_templates_are_classified(self):
        from fws.files_listing import parse_archive
        es = parse_archive(self._blob())
        assert any(e.kind == "point_table" and e.name == "Camera.db" for e in es)
        assert any(e.kind == "template" for e in es)

    def test_a_failed_fetch_serves_the_last_good_listing(self):
        """A failed fetch serves the last good listing."""
        from fws.files_listing import ControllerListing
        blobs = [self._blob()]

        def download():
            if blobs:
                return blobs.pop()
            raise OSError("transfer port refused")

        li = ControllerListing(download, ttl_s=0.0)
        first, meta = li.get()
        assert first and meta["authoritative"]
        again, meta = li.get()
        assert again == first, "the last good listing must survive a failure"
        assert "fetch failed" in meta["source"]

    def test_the_cache_reports_its_own_age(self):
        from fws.files_listing import ControllerListing
        li = ControllerListing(lambda: self._blob(), ttl_s=300.0)
        li.get()
        _entries, meta = li.get()
        assert meta["source"] == "cache"
        assert meta["age_s"] is not None
        assert "snapshot, not a live view" in meta["caveat"]


class TestAGuardThatDidNotRunSaysSo:
    """When the loaded-program guard cannot run, the response says so."""

    def test_a_normal_delete_reports_the_guard_ran(self, client, fake):
        client.put("/api/v1/files/lua/guard_ok.lua",
                   json={"content": "-- x\n", "confirm": True})
        d = client.delete("/api/v1/files/lua/guard_ok.lua",
                          params={"confirm": "true"}).json()
        assert d["loaded_program_guard"] == "checked"

    def test_a_failed_guard_is_named_in_the_response(self, client, driver,
                                                     monkeypatch):
        from fws.driver import TransportError
        client.put("/api/v1/files/lua/guard_down.lua",
                   json={"content": "-- x\n", "confirm": True})

        real = driver._call

        def flaky(method, *a, **kw):
            if method == "GetLoadedProgram":
                raise TransportError("GetLoadedProgram: transport error")
            return real(method, *a, **kw)

        # The router is built over the `driver` FIXTURE, not app_mod.driver.
        monkeypatch.setattr(driver, "_call", flaky)
        d = client.delete("/api/v1/files/lua/guard_down.lua",
                          params={"confirm": "true"}).json()
        assert d["deleted"] == "guard_down.lua", (
            "the delete must still succeed -- that is the documented tradeoff")
        assert d["loaded_program_guard"].startswith("NOT CHECKED")
        assert "without confirming" in d["loaded_program_guard"]
