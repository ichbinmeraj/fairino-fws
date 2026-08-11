"""Backup and restore endpoints.

Restoring a point table overwrites a cell's taught positions and is not
recoverable from FWS, so it is gated like a motion command: config lock,
explicit confirmation, and a refusal that says what will be lost.
"""
from __future__ import annotations

import base64

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .backup import (
    BACKUP_KINDS,
    BackupError,
    NotOffered,
    download_backup,
    download_point_table,
    upload_point_table,
)
from .driver import RobotError

router = APIRouter(prefix="/api/v1", tags=["backup"])


class PointTableRestore(BaseModel):
    content_base64: str = Field(description="the .db file, base64-encoded")
    confirm: bool = Field(
        default=False,
        description="required: this overwrites the controller's taught points")


def build(get_driver, get_settings, get_control, audit) -> APIRouter:

    def _lock(domain: str, token: str | None) -> None:
        control = get_control()
        if control.held_by(domain) is None:
            return
        ok, reason = control.check(domain, token)
        if not ok:
            raise HTTPException(428 if not token else 423, reason)

    def _safe_db_name(name: str) -> str:
        if "/" in name or "\\" in name or ".." in name:
            raise HTTPException(422, "name must not contain a path")
        if not name.endswith(".db"):
            raise HTTPException(422, "point table name must end in .db")
        if not name[:-3] or len(name) > 100:
            raise HTTPException(422, "implausible point table name")
        return name

    @router.get("/backup")
    def list_backups():
        """What this controller can produce, and what each contains."""
        return {
            "kinds": [
                {"kind": k, "filename": v["filename"],
                 "contains": v["describes"]}
                for k, v in sorted(BACKUP_KINDS.items())
            ],
            "point_tables": {
                "download": "GET /api/v1/points/tables/{name}",
                "restore": "PUT /api/v1/points/tables/{name}",
                "note": ("Point tables carry a cell's taught positions. "
                         "Restoring one overwrites them."),
            },
            "not_included": (
                "Lua programs are handled separately under /programs. "
                "Firmware and the OS are never transferred by FWS."),
        }

    @router.get("/backup/{kind}")
    def get_backup(kind: str):
        """Download a backup bundle (tar.gz), base64-encoded."""
        # Dedicated driver with a long timeout: building the archive takes
        # ~14 s, and sharing the main driver would hold its lock that whole
        # time.
        from .driver import RobotDriver
        d = get_driver()
        patient = RobotDriver(d.ip, timeout=180.0, port=d.port,
                              upload_port=d.upload_port,
                              download_port=d.download_port)
        try:
            out = download_backup(patient, kind)
        except (BackupError, RobotError) as e:
            raise HTTPException(502, str(e)) from e
        audit("backup.download", kind=kind, bytes=out["bytes"],
              md5=out["md5"])
        return {
            "kind": out["kind"], "filename": out["filename"],
            "bytes": out["bytes"], "md5": out["md5"],
            "content_base64": base64.b64encode(out["content"]).decode(),
        }

    @router.get("/points/tables/{name}")
    def get_point_table(name: str):
        name = _safe_db_name(name)
        try:
            out = download_point_table(get_driver(), name)
        except NotOffered as e:
            # The transfer never started -- almost always "no such table", but
            # the controller's code for that is undocumented.
            raise HTTPException(404, {
                "message": f"the controller would not send {name}",
                "error": str(e),
                "caveat": ("FWS reads this as 'not there'. The controller's "
                           "code for a missing table is not documented, so a "
                           "different refusal would look the same."),
            }) from e
        except (BackupError, RobotError) as e:
            # The transfer started and failed (timeout, short read, or md5
            # mismatch). Not evidence the table is absent; a caller must not
            # recreate over it.
            text = str(e)
            raise HTTPException(502, {
                "message": (
                    f"{name} exists but could NOT be read intact. This is "
                    f"not evidence it is absent -- the TRANSFER failed."),
                "error": text,
                "integrity": ("the controller sent this table but the bytes "
                              "did not match its own md5, so the copy is "
                              "corrupt in transit or at rest"
                              if "md5 mismatch" in text else None),
                "advice": ("do NOT treat this as an empty table and do not "
                           "restore over it; retry the read first"),
            }) from e
        return {
            "name": out["name"], "bytes": out["bytes"], "md5": out["md5"],
            "content_base64": base64.b64encode(out["content"]).decode(),
        }

    @router.put("/points/tables/{name}")
    def restore_point_table(
            name: str, req: PointTableRestore,
            x_fws_control_token: str | None = Header(default=None)):
        """Restore a point table. Overwrites taught positions; gated
        like a motion command."""
        _lock("config", x_fws_control_token)
        name = _safe_db_name(name)
        if not req.confirm:
            raise HTTPException(400, (
                "restoring a point table overwrites the positions this cell "
                "was taught. Any program that moves between named points "
                "will then move somewhere else, silently, and FWS cannot "
                "undo it. Download the current table first, then resend "
                "with confirm=true"))
        try:
            body = base64.b64decode(req.content_base64, validate=True)
        except Exception as e:
            raise HTTPException(422, f"content_base64 is not valid: {e}") from e
        try:
            out = upload_point_table(get_driver(), name, body)
        except (BackupError, RobotError) as e:
            raise HTTPException(502, str(e)) from e
        # `md5` is the digest of what was sent; read the table back and compare,
        # since a silently truncated write would leave a cell moving to the
        # wrong places with no error.
        verified: str | None = None
        try:
            back = download_point_table(get_driver(), name)
            verified = ("confirmed" if back["md5"] == out["md5"]
                        else "MISMATCH")
        except (BackupError, RobotError) as e:
            verified = f"NOT VERIFIED: {type(e).__name__}: {e}"
        audit("points.restore", name=name, bytes=out["bytes"],
              md5=out["md5"], readback=verified)
        if verified == "MISMATCH":
            raise HTTPException(502, {
                "message": (f"{name} was written but reads back with a "
                            f"different md5. The taught positions on the "
                            f"controller are NOT what you sent."),
                "sent_md5": out["md5"],
                "stored_md5": back["md5"],
            })
        return {**out, "readback": verified,
                "readback_means": (
                    "`confirmed` means FWS re-read the table and the bytes "
                    "match. Anything else means the write was accepted but "
                    "not proven, and `md5` above is only what was SENT.")}

    @router.post("/points/tables/{name}/switch")
    def switch_point_table(
            name: str, x_fws_control_token: str | None = Header(default=None)):
        """Make a point table the active one. An empty name reverts
        programs to their un-applied form."""
        _lock("config", x_fws_control_token)
        if name != "-":
            name = _safe_db_name(name)
        target = "" if name == "-" else name
        rtn = get_driver()._call("PointTableSwitch", target)
        if isinstance(rtn, list):
            rtn = rtn[0]
        if rtn != 0:
            raise HTTPException(502, f"PointTableSwitch returned {rtn}")
        audit("points.switch", name=target or "(none)")
        return {"active": target or None}

    return router
