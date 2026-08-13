"""FWS configuration. Precedence: CLI flags > environment > config file >
defaults. Defaults are the refusing/secure ones."""
from __future__ import annotations

import ipaddress
import pathlib
import tomllib
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class RobotSettings(BaseModel):
    """Robot connection settings."""

    ip: str = Field(
        default="192.168.57.2",
        description="Controller address. 192.168.57.2 is the default on the "
                    "user LAN port; plug into the teach port instead and it "
                    "is 192.168.58.2.",
    )
    rpc_port: int = Field(default=20003, ge=1, le=65535)
    telemetry_port: int = Field(default=8083, ge=1, le=65535)
    upload_port: int = Field(default=20010, ge=1, le=65535)
    download_port: int = Field(default=20011, ge=1, le=65535)
    rpc_timeout_s: float = Field(default=5.0, gt=0, le=120)
    telemetry_reconnect_s: float = Field(
        default=2.0, gt=0, le=60,
        description="Backoff before reconnecting the telemetry stream. Keep "
                    "this LONGER than any keepalive detection window "
                    "configured on the controller, or FWS races the "
                    "controller for its own single-client slot.",
    )

    @field_validator("ip")
    @classmethod
    def _valid_ip(cls, v: str) -> str:
        try:
            ipaddress.ip_address(v)
        except ValueError as e:
            raise ValueError(f"robot.ip must be an IP address, got {v!r}") from e
        return v


class ServerSettings(BaseModel):
    bind_host: str = Field(
        default="127.0.0.1",
        description="Loopback by default. Reach FWS from elsewhere with an "
                    "SSH tunnel rather than by binding wider.",
    )
    port: int = Field(default=8000, ge=1, le=65535)
    data_dir: pathlib.Path = Field(
        default=pathlib.Path("."),
        description="Where site-specific state (taught points) is kept.",
    )
    read_only: bool = Field(
        default=False,
        description="Refuse every operation that could change controller "
                    "state: only GET, HEAD, OPTIONS and the telemetry "
                    "WebSocket are served. This includes POST /motion/stop "
                    "-- a gateway that can stop a program someone else "
                    "started is not read-only. Use the physical E-stop.",
    )

    @property
    def is_loopback(self) -> bool:
        return self.bind_host in LOOPBACK_HOSTS


class LimitSettings(BaseModel):
    """Motion caps enforced server-side, on top of the controller's own soft
    limits (never replacing them)."""

    jog_max_deg: float = Field(default=15.0, gt=0, le=90)
    jog_max_mm: float = Field(default=50.0, gt=0, le=500)
    rotation_max_deg: float = Field(default=15.0, gt=0, le=90)
    jog_max_vel_pct: float = Field(default=30.0, gt=0, le=100)
    limit_margin_deg: float = Field(
        default=0.5, ge=0, le=10,
        description="Standoff kept from every joint soft limit.",
    )
    z_floor_mm: float | None = Field(
        default=None,
        description="Refuse any commanded pose below this TCP height. Unset "
                    "means no floor, which is only sensible if nothing is "
                    "mounted below the robot.",
    )


class FeatureSettings(BaseModel):
    """Optional features, all off by default."""

    enable_movel: bool = Field(
        default=False,
        description="MoveL's argument layout produced an unintended ~300 mm "
                    "motion and a controller fault on software v3.8.5.1. Do "
                    "not enable without verifying on a simulator first.",
    )
    enable_command_passthrough: bool = Field(
        default=False,
        description="Exposes the generated registry's directly-callable "
                    "commands. Refused commands stay refused regardless.",
    )
    enable_unverified_commands: bool = Field(
        default=False,
        description="Allows passthrough of commands never exercised on "
                    "hardware. Requires enable_command_passthrough.",
    )
    enable_shutdown: bool = Field(
        default=False,
        description="Allows POST /system/shutdown. ONE-WAY on Fairino "
                    "hardware: the vendor API has ShutDownRobotOS and no "
                    "reboot, so the controller cannot be brought back "
                    "without physical access or switched power. Leave this "
                    "off unless you can power the cell on remotely.",
    )
    warn_insecure_robot_ports: bool = Field(
        default=True,
        description="Report FTP/telnet/qconn exposure on the robot LAN in "
                    "the health endpoint.",
    )


class ControllerServicesSettings(BaseModel):
    """QNX base services the controller exposes on the robot LAN (FTP, telnet,
    qconn, Lua validator). All off by default; FWS gates them behind its own
    auth and audit."""

    ftp_enabled: bool = Field(
        default=False,
        description="Enable FTP-backed file operations (port 21): a true, "
                    "instant controller directory listing and raw file "
                    "get/put. Reads are low-risk; writes bypass the "
                    "controller's compile-and-register step, so a Lua file "
                    "put this way lands on disk without the verdict that "
                    "tells you it compiled.",
    )
    ftp_port: int = Field(default=21, ge=1, le=65535)
    ftp_user: str = Field(default="", description="FTP username, if the "
                          "controller requires one. Blank tries anonymous.")
    ftp_password: str = Field(default="", description="FTP password. Held in "
                              "memory only; never written to the audit log.")

    shell_enabled: bool = Field(
        default=False,
        description="Enable telnet-backed shell command execution (port 23). "
                    "This is a ROOT SHELL on the controller reached over the "
                    "network: it powers remote restart, process control and "
                    "recovery. The blast radius is the whole machine, so it "
                    "requires authentication configured (FWS refuses to start "
                    "with shell on and no api_keys_file) and every call is "
                    "audited.",
    )
    shell_port: int = Field(default=23, ge=1, le=65535)
    shell_user: str = Field(default="root")
    shell_password: str = Field(default="")
    shell_prompt: str = Field(
        default="#",
        description="The shell prompt to read command output up to. QNX root "
                    "is '#'. Calibrate against the real controller if it "
                    "differs; a wrong prompt means output is read until "
                    "timeout rather than framed.",
    )
    shell_allowlist: tuple[str, ...] = Field(
        default=(),
        description="If non-empty, only commands whose first token is in this "
                    "set may run. Empty means any command -- appropriate only "
                    "for an operator who accepts a full root shell over HTTP.",
    )
    shell_restart_command: str = Field(
        default="",
        description="The command that restarts the controller's ROBOT "
                    "APPLICATION (not the OS). FWS does not guess it: the "
                    "process name to signal is firmware-specific and killing "
                    "the wrong thing on a live controller is how this project "
                    "is risky to get wrong. POST /controller/restart refuses "
                    "until you set this to the verified command for your "
                    "controller.",
    )
    shell_reboot_command: str = Field(
        default="",
        description="The command that reboots the whole controller OS. Empty "
                    "means POST /controller/reboot refuses. A reboot drops "
                    "the arm's power; only set this if the cell is safe to "
                    "de-energise remotely.",
    )

    qconn_enabled: bool = Field(
        default=False,
        description="Enable qconn process inspection (port 8000, QNX target "
                    "agent, unauthenticated root by design). Used to see and "
                    "signal controller processes without a shell. Gated like "
                    "the shell.",
    )
    qconn_port: int = Field(default=8000, ge=1, le=65535)

    lua_validate_enabled: bool = Field(
        default=False,
        description="Enable Lua validation via the controller's internal "
                    "validator (port 8060) -- compile-check a program WITHOUT "
                    "uploading it. Off by default: a dead client on 8060 can "
                    "wedge the upload service, so this path is the least "
                    "proven of the controller services.",
    )
    lua_validate_port: int = Field(default=8060, ge=1, le=65535)

    connect_timeout_s: float = Field(default=8.0, gt=0, le=60)
    command_timeout_s: float = Field(default=20.0, gt=0, le=300)

    @property
    def any_privileged_enabled(self) -> bool:
        """Shell and qconn reach a root-equivalent path; must not run without
        auth even on loopback. FTP is excluded: like jog, it follows the
        ordinary loopback/auth rule, not the root-equivalent one."""
        return self.shell_enabled or self.qconn_enabled

    @property
    def any_enabled(self) -> bool:
        return (self.ftp_enabled or self.shell_enabled or self.qconn_enabled
                or self.lua_validate_enabled)


class AuthSettings(BaseModel):
    api_keys_file: pathlib.Path | None = Field(
        default=None,
        description="One API key per line. Required for any non-loopback "
                    "bind; FWS refuses to start otherwise.",
    )

    @property
    def enabled(self) -> bool:
        return self.api_keys_file is not None


class _FileSource(PydanticBaseSettingsSource):
    """Supplies config-file data as a settings source ranked BELOW env vars,
    so precedence is the documented CLI > env > file > defaults. The file
    dict is handed in per call via load()'s `_file_data` init argument, not a
    class global, so concurrent loads don't clobber each other."""

    def get_field_value(self, field, field_name):  # pragma: no cover - unused
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return getattr(self.settings_cls, "_pending_file_data", {}) or {}


class Settings(BaseSettings):
    """Complete FWS configuration."""

    model_config = SettingsConfigDict(
        env_prefix="FWS_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings,
        dotenv_settings, file_secret_settings,
    ):
        # Highest first: CLI overrides (init) > env > config file > defaults.
        # The stock order puts init above env, which silently discarded an
        # env var whenever the file also set that key.
        return (init_settings, env_settings, _FileSource(settings_cls),
                file_secret_settings)

    robot: RobotSettings = Field(default_factory=RobotSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    limits: LimitSettings = Field(default_factory=LimitSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    services: ControllerServicesSettings = Field(
        default_factory=ControllerServicesSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)

    def check_safe_to_start(self) -> list[str]:
        """Refusal reasons; empty list means safe to start."""
        problems: list[str] = []
        if not self.server.is_loopback and not self.auth.enabled:
            problems.append(
                f"refusing to bind {self.server.bind_host} with no "
                f"authentication configured. Either bind 127.0.0.1 and use an "
                f"SSH tunnel, or set auth.api_keys_file."
            )
        if self.auth.api_keys_file and not self.auth.api_keys_file.exists():
            problems.append(
                f"auth.api_keys_file does not exist: {self.auth.api_keys_file}"
            )
        elif self.auth.api_keys_file:
            # Existence is not enough: a file that parses to zero usable keys
            # (emptied, truncated, all lines commented) must be refused, not
            # treated as "no auth configured", which on a non-loopback bind
            # would be an authentication bypass.
            from .auth import parse_key_file
            try:
                n = len(parse_key_file(self.auth.api_keys_file))
            except OSError as e:
                problems.append(
                    f"auth.api_keys_file cannot be read: {e}")
                n = -1
            if n == 0:
                problems.append(
                    f"auth.api_keys_file has no usable keys: "
                    f"{self.auth.api_keys_file}. Every line is blank or "
                    f"commented. FWS will refuse every request rather than "
                    f"serve them unauthenticated, so this is a refusal, not "
                    f"a warning.")
        if (self.features.enable_unverified_commands
                and not self.features.enable_command_passthrough):
            problems.append(
                "features.enable_unverified_commands requires "
                "features.enable_command_passthrough"
            )
        # Root shell or process control over the network without auth is
        # refused, same as a non-loopback bind with no auth. FTP and the Lua
        # validator are excluded: they cannot execute code or kill a process,
        # so they follow the ordinary loopback/auth rule via the bind check
        # above.
        if self.services.any_privileged_enabled and not self.auth.enabled:
            enabled = [n for n, on in (("shell", self.services.shell_enabled),
                                       ("qconn", self.services.qconn_enabled))
                       if on]
            problems.append(
                f"controller service(s) {', '.join(enabled)} are enabled with "
                f"no authentication configured. These reach a root-equivalent "
                f"path on the controller; FWS will not expose them without an "
                f"api_keys_file, on loopback or otherwise. Set auth.api_keys_"
                f"file, or turn them off.")
        return problems

    def summary(self) -> dict[str, Any]:
        """Startup banner content. Never includes secrets."""
        return {
            "robot": f"{self.robot.ip}:{self.robot.rpc_port}",
            "telemetry": f"{self.robot.ip}:{self.robot.telemetry_port}",
            "bind": f"{self.server.bind_host}:{self.server.port}",
            "loopback_only": self.server.is_loopback,
            "read_only": self.server.read_only,
            "auth": "enabled" if self.auth.enabled else "DISABLED (loopback)",
            "movel": self.features.enable_movel,
            "passthrough": self.features.enable_command_passthrough,
            "controller_services": [
                n for n, on in (
                    ("ftp", self.services.ftp_enabled),
                    ("shell", self.services.shell_enabled),
                    ("qconn", self.services.qconn_enabled),
                    ("lua_validate", self.services.lua_validate_enabled),
                ) if on] or "none",
        }


def _read_toml(path: pathlib.Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load(config_path: pathlib.Path | None = None,
         **overrides: Any) -> Settings:
    """Build Settings from file, environment and explicit overrides (dotted
    keys, e.g. "robot.ip")."""
    file_data: dict[str, Any] = {}
    if config_path is not None:
        if not config_path.exists():
            raise FileNotFoundError(f"config file not found: {config_path}")
        file_data = _read_toml(config_path)

    # CLI overrides (dotted keys) become init kwargs -- the highest source.
    # The config file goes through _FileSource, ranked below env, so the
    # precedence is CLI > env > file > defaults (see settings_customise_sources).
    cli_data: dict[str, Any] = {}
    for dotted, value in overrides.items():
        if value is None:
            continue
        section, _, key = dotted.partition(".")
        if key:
            cli_data.setdefault(section, {})[key] = value
        else:
            cli_data[section] = value

    Settings._pending_file_data = file_data
    try:
        return Settings(**cli_data)
    finally:
        Settings._pending_file_data = {}
