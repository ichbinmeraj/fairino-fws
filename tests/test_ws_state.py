"""The /ws/state payload shape, pinned so WEBSOCKETS.md cannot go stale.

The live telemetry socket is what a client uses when it cares about being
current, so two properties matter and are checked here: the pushed message is
a SUPERSET of GET /api/v1/state (a field present over REST but missing here
would be a silent trap), and it carries the frame's own timestamp and age so
a consumer can tell a fresh frame from a stale repeat without diffing values
-- which it cannot always do, because a parked arm sends identical frames.
"""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from fws import app as app_mod
from fws import config as config_mod


def _client(fake):
    app_mod.create_app(config_mod.load(**{
        "robot.ip": fake.host,
        "robot.rpc_port": fake.rpc_port,
        "robot.telemetry_port": fake.stream_port,
        "robot.upload_port": fake.upload_port,
        "robot.download_port": fake.download_port,
    }))
    return TestClient(app_mod.app)


def _first_frame(fake, *, require_ts: bool = False) -> dict:
    """The first pushed frame -- or, when require_ts, the first one that
    carries a timestamp.

    At connect the socket can send one frame before the telemetry reader has
    delivered a real 8083 frame, so `ts`/`joints` are briefly None. That is
    honest (the field is nullable and documented so), but a test about the
    populated shape must wait past it -- more likely to matter under load,
    where the connect races the reader.
    """
    with _client(fake) as c, c.websocket_connect("/ws/state") as ws:
        for _ in range(30):
            frame = json.loads(ws.receive_text())
            if not require_ts or frame.get("ts") is not None:
                return frame
        return frame


class TestTheDocumentedFields:
    def test_the_frame_has_every_field_websockets_md_lists(self, fake):
        frame = _first_frame(fake)
        for field in ("connected", "joints", "tcp", "force", "joint_torque",
                      "program_state", "error_main", "error_sub", "limits",
                      "frames", "bad_checksum", "ts", "age_s"):
            assert field in frame, f"WEBSOCKETS.md lists {field}; frame lacks it"

    def test_joints_tcp_force_are_six_long(self, fake):
        frame = _first_frame(fake)
        assert len(frame["joints"]) == 6
        assert len(frame["tcp"]) == 6
        assert len(frame["force"]) == 6

    def test_age_is_small_for_a_live_frame(self, fake):
        """age_s is how old the frame is at send. A live stream's newest
        frame is fresh; a large age would mean the stream had stalled."""
        frame = _first_frame(fake, require_ts=True)
        assert frame["ts"] is not None
        assert frame["age_s"] is not None
        assert 0 <= frame["age_s"] < 2.0
        assert frame["ts"] <= time.time() + 1


class TestItIsASupersetOfRest:
    def test_no_rest_field_is_missing_from_the_socket(self, fake):
        """The trap this guards: the socket built its payload from a
        hand-written key list once, and drifted -- joint_torque was served
        over REST and silently absent from the stream, so a client watching
        the socket saw an older robot than one polling.

        REST /state renames one field: its `stream_connected` is the socket's
        `connected` (the socket spreads the raw frame, which uses `connected`;
        the REST view relabels it). That single alias is documented in
        WEBSOCKETS.md and mapped here -- everything else must match by name.
        """
        alias = {"stream_connected": "connected"}
        with _client(fake) as c:
            rest = c.get("/api/v1/state").json()
            with c.websocket_connect("/ws/state") as ws:
                frame = json.loads(ws.receive_text())
        missing = [k for k in rest
                   if k not in frame and alias.get(k) not in frame]
        assert not missing, f"the live socket omits REST fields: {missing}"
