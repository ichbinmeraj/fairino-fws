"""8083 status stream reader (receive-only).

Frame layout is v3.8.5.1-specific (433-byte frame) and does NOT match the
SDK's RobotStatePkg (2673 bytes, firmware V3.9.x); parsing with the SDK struct
misreads every field. Offsets are defined as constants below. The frame carries
positions, joint torque and reference-frame force/torque, but no velocity or
acceleration; unidentified bytes are not parsed.
"""
from __future__ import annotations

import contextlib
import socket
import struct
import threading
import time
from typing import Any

HEADER = b"\x5a\x5a"
FRAME_LEN = 433
JOINTS_OFF = 8
TCP_OFF = 56
TORQUE_OFF = 108
PROGRAM_OFF = 156        # 20-byte fixed field; see PROGRAM_LEN
PROGRAM_LEN = 20
FT_OFF = 184

# The frame reports joint torque in milli-N·m; GetJointTorques reports N·m.
TORQUE_SCALE = 1000.0


class Telemetry:
    """Background reader; receive-only (socket shut down for writing after connect)."""

    def __init__(self, ip: str = "192.168.57.2", port: int = 8083):
        self.ip, self.port = ip, port
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "connected": False, "frames": 0, "bad_checksum": 0}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    # -- internals --------------------------------------------------------
    def _set(self, **kw: Any) -> None:
        with self._lock:
            self._state.update(kw)

    def _run(self) -> None:
        buf = b""
        sock = None
        while not self._stop.is_set():
            try:
                if sock is None:
                    sock = socket.socket()
                    sock.settimeout(5)
                    sock.connect((self.ip, self.port))
                    sock.shutdown(socket.SHUT_WR)   # receive-only, enforced
                    buf = b""
                    self._set(connected=True, error=None)

                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("controller closed the stream")
                buf += chunk

                while True:
                    i = buf.find(HEADER)
                    if i < 0 or len(buf) - i < FRAME_LEN:
                        break
                    frame = buf[i:i + FRAME_LEN]
                    buf = buf[i + FRAME_LEN:]
                    self._parse(frame)

                if len(buf) > 4 * FRAME_LEN:
                    buf = buf[-FRAME_LEN:]

            except Exception as e:
                self._set(connected=False, error=f"{type(e).__name__}: {e}")
                if sock:
                    with contextlib.suppress(Exception):
                        sock.close()
                sock = None
                time.sleep(2.0)   # back off; do not hammer a wedged controller

        if sock:
            sock.close()

    def _parse(self, frame: bytes) -> None:
        declared = struct.unpack_from("<H", frame, 3)[0]
        chk = struct.unpack_from("<H", frame, 5 + declared)[0]
        calc = sum(frame[:5 + declared]) & 0xFFFF
        if chk != calc:
            # Drop the frame and count it; never zero-fill and pass it on.
            with self._lock:
                self._state["bad_checksum"] = self._state.get("bad_checksum", 0) + 1
            return

        joints = list(struct.unpack_from("<6d", frame, JOINTS_OFF))
        tcp = list(struct.unpack_from("<6d", frame, TCP_OFF))
        torque = [v / TORQUE_SCALE
                  for v in struct.unpack_from("<6d", frame, TORQUE_OFF)]
        # Loaded program path, 20-byte fixed field: truncates, so it only
        # signals that the loaded program changed, not the full name.
        raw = bytes(frame[PROGRAM_OFF:PROGRAM_OFF + PROGRAM_LEN])
        program = raw.split(b"\x00")[0].decode("ascii", "replace") or None
        ft = list(struct.unpack_from("<6d", frame, FT_OFF))
        with self._lock:
            self._state.update(
                connected=True,
                error=None,
                frames=self._state.get("frames", 0) + 1,
                counter=frame[2],
                program_state=frame[5],
                joints=[round(j, 4) for j in joints],
                tcp=[round(t, 3) for t in tcp],
                joint_torque=[round(v, 5) for v in torque],
                loaded_program_truncated=program,
                ft=[round(v, 4) for v in ft],
                ts=time.time(),
            )
