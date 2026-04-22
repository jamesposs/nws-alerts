from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


class ConfigError(ValueError):
    """Raised when the environment configuration is invalid."""


def _parse_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default

    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean value.")


def _parse_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer.") from exc


def _parse_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number.") from exc


def _parse_required_float(name: str) -> float:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required.")
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number.") from exc


def _parse_csv(name: str) -> tuple[str, ...]:
    value = os.getenv(name, "")
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _parse_path(name: str, base_dir: Path) -> Path | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


@dataclass(frozen=True, slots=True)
class Config:
    nws_latitude: float
    nws_longitude: float
    nws_user_agent: str
    nws_accept: str
    poll_interval_seconds: int
    http_timeout_seconds: float
    http_max_retries: int
    http_backoff_seconds: float
    http_max_backoff_seconds: float
    timezone: str
    printer_ip: str | None
    printer_port: int
    printer_timeout_seconds: float
    print_width_pixels: int
    cut_paper: bool
    printer_dry_run: bool
    allowed_events: tuple[str, ...]
    blocked_events: tuple[str, ...]
    allowed_severities: tuple[str, ...]
    allowed_urgencies: tuple[str, ...]
    allowed_certainties: tuple[str, ...]
    allowed_statuses: tuple[str, ...]
    allowed_message_types: tuple[str, ...]
    print_on_new_only: bool
    print_on_updates: bool
    state_file: Path
    log_level: str
    spoof_alerts_file: Path | None
    immediate_threshold_seconds: int

    @property
    def zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def point(self) -> str:
        return f"{self.nws_latitude},{self.nws_longitude}"

    @property
    def summary(self) -> dict[str, object]:
        return {
            "point": self.point,
            "poll_interval_seconds": self.poll_interval_seconds,
            "timezone": self.timezone,
            "printer_ip": self.printer_ip or "(dry-run only)",
            "printer_port": self.printer_port,
            "printer_timeout_seconds": self.printer_timeout_seconds,
            "print_width_pixels": self.print_width_pixels,
            "cut_paper": self.cut_paper,
            "printer_dry_run": self.printer_dry_run,
            "print_on_new_only": self.print_on_new_only,
            "print_on_updates": self.print_on_updates,
            "state_file": str(self.state_file),
            "spoof_alerts_file": str(self.spoof_alerts_file) if self.spoof_alerts_file else None,
            "allowed_events": list(self.allowed_events),
            "blocked_events": list(self.blocked_events),
            "allowed_severities": list(self.allowed_severities),
            "allowed_urgencies": list(self.allowed_urgencies),
            "allowed_certainties": list(self.allowed_certainties),
            "allowed_statuses": list(self.allowed_statuses),
            "allowed_message_types": list(self.allowed_message_types),
        }


def load_config(dotenv_path: str | Path | None = None) -> Config:
    env_path = Path(dotenv_path).expanduser() if dotenv_path else Path.cwd() / ".env"
    load_dotenv(dotenv_path=env_path, override=False)
    base_dir = env_path.parent

    nws_user_agent = os.getenv("NWS_USER_AGENT", "").strip()
    if not nws_user_agent:
        raise ConfigError("NWS_USER_AGENT is required.")

    spoof_alerts_file = _parse_path("SPOOF_ALERTS_FILE", base_dir)

    poll_interval_seconds = _parse_int("POLL_INTERVAL_SECONDS", 30)
    minimum_interval = 1 if spoof_alerts_file else 30
    if poll_interval_seconds < minimum_interval:
        if spoof_alerts_file:
            raise ConfigError("POLL_INTERVAL_SECONDS must be at least 1 second in spoof mode.")
        raise ConfigError(
            "POLL_INTERVAL_SECONDS must be at least 30 seconds to respect NWS alerts polling guidance."
        )

    timezone_name = os.getenv("TIMEZONE", "America/New_York").strip() or "America/New_York"
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"Unknown TIMEZONE value: {timezone_name}") from exc

    printer_dry_run = _parse_bool("PRINTER_DRY_RUN", False)
    printer_ip = os.getenv("PRINTER_IP", "").strip() or None
    if not printer_dry_run and not printer_ip:
        raise ConfigError("PRINTER_IP is required unless PRINTER_DRY_RUN=true.")

    state_file = _parse_path("STATE_FILE", base_dir)
    if state_file is None:
        raise ConfigError("STATE_FILE is required.")

    log_level = (os.getenv("LOG_LEVEL", "INFO").strip() or "INFO").upper()
    valid_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
    if log_level not in valid_levels:
        raise ConfigError(f"LOG_LEVEL must be one of: {', '.join(sorted(valid_levels))}")

    config = Config(
        nws_latitude=_parse_required_float("NWS_LATITUDE"),
        nws_longitude=_parse_required_float("NWS_LONGITUDE"),
        nws_user_agent=nws_user_agent,
        nws_accept=os.getenv("NWS_ACCEPT", "application/geo+json").strip() or "application/geo+json",
        poll_interval_seconds=poll_interval_seconds,
        http_timeout_seconds=_parse_float("HTTP_TIMEOUT_SECONDS", 15.0),
        http_max_retries=_parse_int("HTTP_MAX_RETRIES", 5),
        http_backoff_seconds=_parse_float("HTTP_BACKOFF_SECONDS", 2.0),
        http_max_backoff_seconds=_parse_float("HTTP_MAX_BACKOFF_SECONDS", 120.0),
        timezone=timezone_name,
        printer_ip=printer_ip,
        printer_port=_parse_int("PRINTER_PORT", 9100),
        printer_timeout_seconds=_parse_float("PRINTER_TIMEOUT_SECONDS", 10.0),
        print_width_pixels=_parse_int("PRINT_WIDTH_PIXELS", 576),
        cut_paper=_parse_bool("CUT_PAPER", True),
        printer_dry_run=printer_dry_run,
        allowed_events=_parse_csv("ALLOWED_EVENTS"),
        blocked_events=_parse_csv("BLOCKED_EVENTS"),
        allowed_severities=_parse_csv("ALLOWED_SEVERITIES"),
        allowed_urgencies=_parse_csv("ALLOWED_URGENCIES"),
        allowed_certainties=_parse_csv("ALLOWED_CERTAINTIES"),
        allowed_statuses=_parse_csv("ALLOWED_STATUSES"),
        allowed_message_types=_parse_csv("ALLOWED_MESSAGE_TYPES"),
        print_on_new_only=_parse_bool("PRINT_ON_NEW_ONLY", True),
        print_on_updates=_parse_bool("PRINT_ON_UPDATES", False),
        state_file=state_file,
        log_level=log_level,
        spoof_alerts_file=spoof_alerts_file,
        immediate_threshold_seconds=_parse_int("IMMEDIATE_THRESHOLD_SECONDS", 120),
    )

    if not -90 <= config.nws_latitude <= 90:
        raise ConfigError("NWS_LATITUDE must be between -90 and 90.")
    if not -180 <= config.nws_longitude <= 180:
        raise ConfigError("NWS_LONGITUDE must be between -180 and 180.")
    if config.http_max_retries < 1:
        raise ConfigError("HTTP_MAX_RETRIES must be at least 1.")
    if config.http_timeout_seconds <= 0:
        raise ConfigError("HTTP_TIMEOUT_SECONDS must be greater than 0.")
    if config.http_backoff_seconds <= 0:
        raise ConfigError("HTTP_BACKOFF_SECONDS must be greater than 0.")
    if config.http_max_backoff_seconds < config.http_backoff_seconds:
        raise ConfigError("HTTP_MAX_BACKOFF_SECONDS must be >= HTTP_BACKOFF_SECONDS.")
    if config.printer_timeout_seconds <= 0:
        raise ConfigError("PRINTER_TIMEOUT_SECONDS must be greater than 0.")
    if config.print_width_pixels < 200:
        raise ConfigError("PRINT_WIDTH_PIXELS is too small for a typical thermal receipt.")
    if config.immediate_threshold_seconds < 0:
        raise ConfigError("IMMEDIATE_THRESHOLD_SECONDS must be zero or greater.")

    return config
