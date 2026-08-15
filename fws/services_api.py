"""Endpoints for the controller's own QNX base services (FTP, shell,
qconn, Lua validator).

Each route is dark unless its feature flag is on (all off by default);
privileged ones require configured auth (enforced by config.check_safe_to_start).
Every route adds the gateway's API-key auth, control lock, confirmation and
audit in front of the unauthenticated LAN daemons.
"""
from __future__ import annotations

import base64

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .access import full_access
from .services import (
    ServiceAuthError,
    ServiceError,
    ServiceTimeout,
    ServiceUnavailable,
)
from .services import ftp as ftp_mod
from .services import lua_validate as lv_mod
from .services import qconn as qconn_mod
from .services import shell as shell_mod

router = APIRouter(prefix="/api/v1", tags=["controller-services"])

# Shell metacharacters that chain, redirect, substitute or background a
# command. When an allowlist is active, any of these defeats the first-token
# check, so the command is refused.
_SHELL_METACHARS = frozenset(";|&$`\n\r<>(){}\\")


class ShellRequest(BaseModel):
    command: str = Field(description="the command line to run on the "
                         "controller's root shell")
    confirm: bool = Field(default=False,
                          description="required: this runs as root on the "
                                      "controller")


class RestartRequest(BaseModel):
    confirm: bool = Field(default=False)
    i_understand_the_arm_may_move_or_stop: bool = Field(
        default=False,
        description="restarting the robot application interrupts whatever it "
                    "is doing")


class RebootRequest(BaseModel):
    confirm: bool = Field(default=False)
    i_have_physical_or_switched_power: bool = Field(
        default=False,
        description="a reboot de-energises the arm and the controller must "
                    "come back on its own")


class FtpWriteRequest(BaseModel):
    path: str = Field(description="path under the controller's file root, or "
                      "an absolute path")
    content_base64: str = Field(description="file contents, base64-encoded")
    confirm: bool = Field(default=False)


def _http_from_service_error(e: ServiceError) -> HTTPException:
    """Map the service-error family to HTTP: auth 502, unreachable 503,
    timeout 504, else 502."""
    if isinstance(e, ServiceAuthError):
        return HTTPException(502, f"the controller service rejected FWS's "
                                  f"credentials: {e}")
    if isinstance(e, ServiceUnavailable):
        return HTTPException(503, str(e))
    if isinstance(e, ServiceTimeout):
        return HTTPException(504, str(e))
    return HTTPException(502, str(e))


def build(get_settings, get_control, audit) -> APIRouter:

    def _svc(get_settings):
        return get_settings().services

    def _require_flag(flag: str, human: str) -> None:
        if not getattr(_svc(get_settings), flag):
            raise HTTPException(403, (
                f"{human} is disabled. Enable it with services.{flag}=true "
                f"(see the configuration reference for what it exposes and "
                f"why it is off by default)."))

    def _lock(domain: str, token: str | None) -> None:
        control = get_control()
        if control.held_by(domain) is None:
            return
        ok, reason = control.check(domain, token)
        if not ok:
            raise HTTPException(428 if not token else 423, reason)

    def _ftp_client() -> ftp_mod.FtpClient:
        s = _svc(get_settings)
        return ftp_mod.FtpClient(
            get_settings().robot.ip, s.ftp_port, user=s.ftp_user,
            password=s.ftp_password, timeout_s=s.connect_timeout_s)

    def _run_shell(command: str) -> shell_mod.ShellResult:
        s = _svc(get_settings)
        return shell_mod.run_command(
            get_settings().robot.ip, command, port=s.shell_port,
            user=s.shell_user, password=s.shell_password, prompt=s.shell_prompt,
            connect_timeout_s=s.connect_timeout_s,
            command_timeout_s=s.command_timeout_s)

    # ------------------------------------------------------------- status
    @router.get("/controller/services")
    def services_status():
        """Enabled services and a connect-and-handshake liveness probe of
        each. Reports no credentials."""
        s = _svc(get_settings)
        ip = get_settings().robot.ip
        out: dict = {
            "enabled": {
                "ftp": s.ftp_enabled, "shell": s.shell_enabled,
                "qconn": s.qconn_enabled,
                "lua_validate": s.lua_validate_enabled,
            },
            "liveness": {},
            "note": ("these are the controller's own QNX services. FWS gates "
                     "them with its auth, control lock and audit; it cannot "
                     "authenticate the daemons themselves."),
        }
        # Liveness probes get their own short timeout, not connect_timeout_s:
        # the status page must stay responsive even when connect_timeout_s is
        # tuned high for slow FTP transfers. A probe verdict is decided at
        # connect time (refused/accepted are instant); the timeout only bounds
        # the "unreachable" answer, so a few seconds is enough.
        live_t = s.liveness_timeout_s
        if s.qconn_enabled:
            try:
                out["liveness"]["qconn"] = qconn_mod.liveness(
                    ip, s.qconn_port, timeout_s=live_t)
            except ServiceError as e:
                out["liveness"]["qconn"] = {"reachable": False, "error": str(e)}
        if s.lua_validate_enabled:
            try:
                out["liveness"]["lua_validator"] = lv_mod.LuaValidateClient(
                    ip, s.lua_validate_port,
                    timeout_s=live_t).probe_health()
            except ServiceError as e:
                out["liveness"]["lua_validator"] = {
                    "healthy": None, "error": str(e)}
        return out

    # --------------------------------------------------------------- FTP
    @router.get("/controller/files")
    def ftp_list(path: str | None = None):
        _require_flag("ftp_enabled", "FTP file access")
        try:
            with _ftp_client() as c:
                entries = c.list(path)
        except ServiceError as e:
            raise _http_from_service_error(e) from e
        return {
            "path": path or ftp_mod.DEFAULT_ROOT,
            "source": "ftp",
            "entries": [
                {"name": e.name, "size": e.size,
                 "kind": "dir" if e.is_dir else "file"} for e in entries],
            "note": ("a true, current listing over FTP -- not the backup-"
                     "archive snapshot GET /files/{kind} serves."),
        }

    @router.get("/controller/files/download")
    def ftp_download(path: str):
        _require_flag("ftp_enabled", "FTP file access")
        try:
            with _ftp_client() as c:
                blob = c.download(path)
        except ServiceError as e:
            raise _http_from_service_error(e) from e
        audit("controller.ftp_download", path=path, bytes=len(blob))
        return {"path": path, "bytes": len(blob),
                "content_base64": base64.b64encode(blob).decode()}

    @router.put("/controller/files")
    def ftp_upload(req: FtpWriteRequest,
                   x_fws_control_token: str | None = Header(default=None)):
        _require_flag("ftp_enabled", "FTP file access")
        if not (req.confirm or full_access()):
            raise HTTPException(400, (
                "writing a file over FTP bypasses the controller's compile-"
                "and-register step: a Lua program put this way lands on disk "
                "with NO compiler verdict and the controller may not know it "
                "exists until a rescan. Use PUT /files/lua/{name} for Lua you "
                "want validated. Resend with confirm=true to write it raw."))
        _lock("config", x_fws_control_token)
        try:
            body = base64.b64decode(req.content_base64, validate=True)
        except (ValueError, base64.binascii.Error) as e:
            raise HTTPException(422, f"content_base64 is not valid: {e}") from e
        try:
            with _ftp_client() as c:
                n = c.upload(req.path, body)
        except ServiceError as e:
            raise _http_from_service_error(e) from e
        audit("controller.ftp_upload", path=req.path, bytes=n)
        return {"path": req.path, "bytes": n, "source": "ftp",
                "warning": ("written raw over FTP; no compiler verdict exists "
                            "for this file")}

    @router.delete("/controller/files")
    def ftp_delete(path: str, confirm: bool = False,
                   x_fws_control_token: str | None = Header(default=None)):
        _require_flag("ftp_enabled", "FTP file access")
        if not confirm:
            raise HTTPException(400, "resend with confirm=true to delete "
                                     f"{path} over FTP")
        _lock("config", x_fws_control_token)
        try:
            with _ftp_client() as c:
                c.delete(path)
        except ServiceError as e:
            raise _http_from_service_error(e) from e
        audit("controller.ftp_delete", path=path)
        return {"deleted": path, "source": "ftp"}

    # ------------------------------------------------------------- shell
    @router.get("/controller/processes")
    def processes():
        """`pidin` over the shell: processes running on the controller."""
        _require_flag("shell_enabled", "shell access")
        try:
            r = _run_shell("pidin")
        except ServiceError as e:
            raise _http_from_service_error(e) from e
        audit("controller.processes")
        return {"command": "pidin", "output": r.output,
                "duration_s": round(r.duration_s, 3)}

    @router.post("/controller/shell")
    def shell(req: ShellRequest,
              x_fws_control_token: str | None = Header(default=None)):
        _require_flag("shell_enabled", "shell access")
        s = _svc(get_settings)
        if s.shell_allowlist:
            head = req.command.split()[0] if req.command.split() else ""
            if head not in s.shell_allowlist:
                raise HTTPException(403, (
                    f"command '{head}' is not in services.shell_allowlist "
                    f"{list(s.shell_allowlist)}. Clear the allowlist to permit "
                    f"any command, or add this one."))
            # A first-token check is defeated by shell metacharacters
            # ("echo x; reboot"), so reject them when an allowlist is active.
            if set(req.command) & _SHELL_METACHARS:
                raise HTTPException(403, (
                    "the command contains shell metacharacters "
                    "(one of ; | & $ ` newline < > ( ) { } \\) and an "
                    "allowlist is active. The allowlist constrains the command "
                    "to its first token, which a shell metacharacter defeats. "
                    "Remove them, or clear services.shell_allowlist to accept "
                    "an unconstrained root shell."))
        if not (req.confirm or full_access()):
            raise HTTPException(400, "resend with confirm=true: this runs as "
                                     "root on the controller")
        _lock("config", x_fws_control_token)
        # Audit before running, so a command that wedges the shell still leaves
        # a record of what was attempted.
        audit("controller.shell", command=req.command)
        try:
            r = _run_shell(req.command)
        except ServiceError as e:
            raise _http_from_service_error(e) from e
        return r.as_dict()

    @router.post("/controller/restart")
    def restart(req: RestartRequest,
                x_fws_control_token: str | None = Header(default=None)):
        """Restart the robot application (not the OS).

        Command is operator-supplied (services.shell_restart_command); FWS does
        not guess a process to signal.
        """
        _require_flag("shell_enabled", "shell access")
        s = _svc(get_settings)
        if not s.shell_restart_command:
            raise HTTPException(400, (
                "services.shell_restart_command is not set. FWS will not "
                "guess how to restart your controller's robot application -- "
                "the process to signal is firmware-specific. Set it to the "
                "verified command for your controller (test it once over "
                "POST /controller/shell first)."))
        if not ((req.confirm and req.i_understand_the_arm_may_move_or_stop)
                or full_access()):
            raise HTTPException(422, (
                "both confirm and i_understand_the_arm_may_move_or_stop are "
                "required: restarting the application interrupts whatever the "
                "arm is doing"))
        _lock("config", x_fws_control_token)
        audit("controller.restart", command=s.shell_restart_command)
        try:
            r = _run_shell(s.shell_restart_command)
        except ServiceError as e:
            raise _http_from_service_error(e) from e
        return {"restarted": True, "command": s.shell_restart_command,
                "output": r.output,
                "next": ("give the application ~45 s to rebind its ports, "
                         "then poll GET /system/health -- the RPC and "
                         "telemetry ports come back last")}

    @router.post("/controller/reboot")
    def reboot(req: RebootRequest,
               x_fws_control_token: str | None = Header(default=None)):
        """Reboot the whole controller OS. De-energises the arm."""
        _require_flag("shell_enabled", "shell access")
        s = _svc(get_settings)
        if not s.shell_reboot_command:
            raise HTTPException(400, (
                "services.shell_reboot_command is not set. A reboot cuts "
                "power to the arm; set this only if the cell is safe to "
                "de-energise remotely."))
        if not ((req.confirm and req.i_have_physical_or_switched_power)
                or full_access()):
            raise HTTPException(422, (
                "both confirm and i_have_physical_or_switched_power are "
                "required: a reboot de-energises the arm and the controller "
                "must come back on its own"))
        _lock("config", x_fws_control_token)
        audit("controller.reboot", command=s.shell_reboot_command)
        try:
            # A dropped connection mid-command is success, not failure: it may
            # arrive as timeout, clean FIN, or RST. Only auth failure or an
            # unreachable port before the command lands is a real error.
            r = _run_shell(s.shell_reboot_command)
            output = r.output
        except (ServiceTimeout, ServiceUnavailable):
            output = "(no clean reply; expected -- the controller went down)"
        except ServiceError as e:
            raise _http_from_service_error(e) from e
        return {"reboot_requested": True, "command": s.shell_reboot_command,
                "output": output,
                "next": "the controller will be unreachable for ~60-90 s"}

    # ------------------------------------------------------------- qconn
    @router.get("/controller/qconn")
    def qconn_liveness():
        _require_flag("qconn_enabled", "qconn access")
        s = _svc(get_settings)
        try:
            return qconn_mod.liveness(get_settings().robot.ip, s.qconn_port,
                                      timeout_s=s.liveness_timeout_s)
        except ServiceError as e:
            raise _http_from_service_error(e) from e

    # ------------------------------------------------- 8060 lua validator
    @router.get("/controller/lua-validator")
    def lua_validator_health():
        """Health of the controller's Lua validator; a refused connection
        means healthy. See LuaValidateClient.probe_health."""
        _require_flag("lua_validate_enabled", "Lua validator access")
        s = _svc(get_settings)
        return lv_mod.LuaValidateClient(
            get_settings().robot.ip, s.lua_validate_port,
            timeout_s=s.liveness_timeout_s).probe_health()

    @router.post("/controller/lua-validate")
    def lua_validate(req: ShellRequest):
        """Compile-check without upload. Currently refuses with 501; see
        LuaValidateClient.validate."""
        _require_flag("lua_validate_enabled", "Lua validator access")
        s = _svc(get_settings)
        client = lv_mod.LuaValidateClient(
            get_settings().robot.ip, s.lua_validate_port,
            timeout_s=s.connect_timeout_s)
        try:
            return client.validate(req.command)
        except ServiceError as e:
            raise HTTPException(501, str(e)) from e

    return router


__all__ = ["build"]
