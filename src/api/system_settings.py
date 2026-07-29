"""Validated administrative settings with separate encrypted secret storage."""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import tempfile
import threading
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

try:
    import fcntl
except ImportError:  # pragma: no cover - production deployment is POSIX.
    fcntl = None

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_SETTINGS: dict[str, dict[str, Any]] = {
    "general": {
        "site_name": "Fenix AI Trading Dashboard",
        "site_description": "Advanced trading dashboard with AI agents",
        "timezone": "UTC",
        "date_format": "YYYY-MM-DD",
        "language": "en",
    },
    "security": {
        "session_timeout": 30,
        "password_min_length": 12,
        "require_uppercase": True,
        "require_lowercase": True,
        "require_numbers": True,
        "require_special_chars": False,
        "max_login_attempts": 5,
        "lockout_duration": 30,
        "two_factor_enabled": False,
    },
    "notifications": {
        "email_enabled": False,
        "email_host": "",
        "email_port": 587,
        "email_username": "",
        "email_password": "",
        "email_from": "no-reply@fenix.ai",
        "sms_enabled": False,
        "sms_provider": "",
        "sms_api_key": "",
    },
    "trading": {
        "max_positions_per_user": 5,
        "max_daily_trades": 100,
        "risk_threshold": 2.0,
        "stop_loss_default": 1.0,
        "take_profit_default": 2.0,
        "leverage_max": 10,
        "margin_call_level": 80,
        "auto_close_on_margin_call": True,
    },
    "agents": {
        "sentiment_agent_enabled": True,
        "technical_agent_enabled": True,
        "visual_agent_enabled": True,
        "qabba_agent_enabled": True,
        "decision_agent_enabled": True,
        "risk_agent_enabled": True,
        "agent_timeout": 30,
        "max_concurrent_agents": 4,
        "reasoning_bank_retention_days": 365,
        "scorecard_retention_days": 365,
    },
    "api": {
        "rate_limit_enabled": True,
        "rate_limit_requests_per_minute": 60,
        "rate_limit_requests_per_hour": 1000,
        "cors_enabled": True,
        "cors_origins": ["http://localhost:5173"],
        "api_key_required": False,
        "jwt_expiry_hours": 24,
        "refresh_token_expiry_days": 30,
    },
    "database": {
        "backup_enabled": False,
        "backup_frequency": "daily",
        "backup_retention_days": 30,
        "maintenance_window": "03:00",
        "auto_vacuum": False,
        "connection_pool_size": 5,
        "query_timeout_seconds": 60,
    },
}

_SECRET_FIELDS = {
    ("notifications", "email_password"),
    ("notifications", "sms_api_key"),
}
_SECRET_ENV = {
    ("notifications", "email_password"): "FENIX_SMTP_PASSWORD",
    ("notifications", "sms_api_key"): "FENIX_SMS_API_KEY",
}
_SETTINGS_LOCK = threading.RLock()
_MAX_SETTINGS_BYTES = 1024 * 1024
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class SettingsValidationError(ValueError):
    """Raised when a settings update does not match the public schema."""


class _StrictSection(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def reject_control_characters(cls, value: Any) -> Any:
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str) and _CONTROL_CHARS.search(item):
                raise ValueError("control characters are not allowed")
        return value


class GeneralSettings(_StrictSection):
    site_name: str = Field(min_length=1, max_length=100)
    site_description: str = Field(max_length=500)
    timezone: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_+\-/]+$")
    date_format: str = Field(min_length=1, max_length=32)
    language: str = Field(min_length=2, max_length=16, pattern=r"^[A-Za-z-]+$")


class SecuritySettings(_StrictSection):
    session_timeout: int = Field(ge=5, le=1440)
    password_min_length: int = Field(ge=12, le=128)
    require_uppercase: bool
    require_lowercase: bool
    require_numbers: bool
    require_special_chars: bool
    max_login_attempts: int = Field(ge=1, le=20)
    lockout_duration: int = Field(ge=1, le=1440)
    two_factor_enabled: bool


class NotificationSettings(_StrictSection):
    email_enabled: bool
    email_host: str = Field(max_length=253)
    email_port: int
    email_username: str = Field(max_length=254)
    email_password: str = Field(max_length=1024)
    email_from: str = Field(max_length=254)
    sms_enabled: bool
    sms_provider: str = Field(max_length=64)
    sms_api_key: str = Field(max_length=1024)

    @field_validator("email_port")
    @classmethod
    def require_encrypted_smtp_port(cls, value: int) -> int:
        if value not in {465, 587}:
            raise ValueError("authenticated SMTP is restricted to ports 465 or 587")
        return value

    @field_validator("email_host")
    @classmethod
    def validate_email_host(cls, value: str) -> str:
        value = value.strip().rstrip(".")
        if not value:
            return ""
        if len(value) > 253 or any(not label for label in value.split(".")):
            raise ValueError("email_host must be a valid hostname or IP address")
        if not all(re.fullmatch(r"[A-Za-z0-9_-]{1,63}", label) for label in value.split(".")):
            # IPv6 literals contain colons and are validated by the SMTP
            # destination resolver instead of this hostname branch.
            if ":" not in value:
                raise ValueError("email_host must be a valid hostname or IP address")
        return value

    @field_validator("email_from")
    @classmethod
    def validate_sender(cls, value: str) -> str:
        value = value.strip()
        if value and not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,189}", value):
            raise ValueError("email_from must be a valid email address")
        return value


class TradingSettings(_StrictSection):
    max_positions_per_user: int = Field(ge=1, le=100)
    max_daily_trades: int = Field(ge=1, le=10_000)
    risk_threshold: float = Field(gt=0, le=100)
    stop_loss_default: float = Field(gt=0, le=100)
    take_profit_default: float = Field(gt=0, le=1000)
    leverage_max: int = Field(ge=1, le=125)
    margin_call_level: int = Field(ge=1, le=100)
    auto_close_on_margin_call: bool


class AgentSettings(_StrictSection):
    sentiment_agent_enabled: bool
    technical_agent_enabled: bool
    visual_agent_enabled: bool
    qabba_agent_enabled: bool
    decision_agent_enabled: bool
    risk_agent_enabled: bool
    agent_timeout: int = Field(ge=1, le=600)
    max_concurrent_agents: int = Field(ge=1, le=32)
    reasoning_bank_retention_days: int = Field(ge=1, le=3650)
    scorecard_retention_days: int = Field(ge=1, le=3650)


class ApiSettings(_StrictSection):
    rate_limit_enabled: bool
    rate_limit_requests_per_minute: int = Field(ge=1, le=100_000)
    rate_limit_requests_per_hour: int = Field(ge=1, le=1_000_000)
    cors_enabled: bool
    cors_origins: list[str] = Field(max_length=32)
    api_key_required: bool
    jwt_expiry_hours: int = Field(ge=1, le=168)
    refresh_token_expiry_days: int = Field(ge=1, le=90)

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            if len(value) > 2048 or "*" in value:
                raise ValueError("CORS origins must be explicit and bounded")
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise ValueError("CORS origins must be bare http/https origins")
            origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
            if origin not in normalized:
                normalized.append(origin)
        return normalized


class DatabaseSettings(_StrictSection):
    backup_enabled: bool
    backup_frequency: str = Field(pattern=r"^(hourly|daily|weekly)$")
    backup_retention_days: int = Field(ge=1, le=3650)
    maintenance_window: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    auto_vacuum: bool
    connection_pool_size: int = Field(ge=1, le=100)
    query_timeout_seconds: int = Field(ge=1, le=3600)


_SECTION_MODELS: dict[str, type[_StrictSection]] = {
    "general": GeneralSettings,
    "security": SecuritySettings,
    "notifications": NotificationSettings,
    "trading": TradingSettings,
    "agents": AgentSettings,
    "api": ApiSettings,
    "database": DatabaseSettings,
}


def settings_path() -> Path:
    configured = os.getenv("FENIX_SYSTEM_SETTINGS_PATH", "").strip()
    return Path(configured) if configured else Path("data/system_settings.json")


def _secret_id(section: str, key: str) -> str:
    return f"system_settings.{section}.{key}"


def _secret_manager():
    from src.security.secure_secrets_manager import get_secrets_manager

    return get_secrets_manager()


def _read_secret(section: str, key: str) -> str:
    env_value = os.getenv(_SECRET_ENV[(section, key)], "")
    if env_value:
        return env_value
    if not os.getenv("FENIX_MASTER_PASSWORD"):
        return ""
    try:
        return _secret_manager().get_secret(_secret_id(section, key)) or ""
    except RuntimeError:
        logger.error("Encrypted settings secret store is unavailable", exc_info=True)
        return ""


def _store_secret(section: str, key: str, value: str) -> None:
    if not os.getenv("FENIX_MASTER_PASSWORD"):
        raise SettingsValidationError(
            "FENIX_MASTER_PASSWORD is required to persist notification secrets; "
            f"use {_SECRET_ENV[(section, key)]} for environment-only configuration"
        )
    if not _secret_manager().store_secret(_secret_id(section, key), value, ttl_seconds=None):
        raise SettingsValidationError("Could not persist the secret in the encrypted vault")


def _delete_secret(section: str, key: str) -> None:
    if os.getenv("FENIX_MASTER_PASSWORD"):
        _secret_manager().delete_secret(_secret_id(section, key))


@contextmanager
def _settings_transaction():
    with _SETTINGS_LOCK:
        path = settings_path()
        lock_path = path.with_name(f".{path.name}.lock")
        if lock_path.parent.is_symlink():
            raise SettingsValidationError("settings directory must not be a symbolic link")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            fd = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise SettingsValidationError("settings lock could not be opened safely") from exc
        with os.fdopen(fd, "a+b") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise SettingsValidationError("settings lock must be a regular file")
            try:
                os.fchmod(handle.fileno(), 0o600)
            except OSError:
                logger.debug("Could not tighten settings lock permissions", exc_info=True)
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _validate_section(section: str, values: dict[str, Any]) -> dict[str, Any]:
    try:
        return _SECTION_MODELS[section].model_validate(values).model_dump()
    except ValidationError as exc:
        message = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_url=False)
        )
        raise SettingsValidationError(message) from exc


def _merge_known_values(raw: object) -> dict[str, dict[str, Any]]:
    settings = deepcopy(DEFAULT_SYSTEM_SETTINGS)
    if isinstance(raw, dict):
        for section, defaults in DEFAULT_SYSTEM_SETTINGS.items():
            saved_section = raw.get(section)
            if not isinstance(saved_section, dict):
                continue
            for key in defaults:
                if key in saved_section and (section, key) not in _SECRET_FIELDS:
                    settings[section][key] = saved_section[key]
    for section, key in _SECRET_FIELDS:
        settings[section][key] = _read_secret(section, key)
    return settings


def _read_raw_settings() -> dict[str, Any]:
    path = settings_path()
    if not path.exists():
        return {}
    if path.is_symlink():
        raise SettingsValidationError("settings path must not be a symbolic link")
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            file_stat = os.fstat(fd)
            if not stat.S_ISREG(file_stat.st_mode):
                raise SettingsValidationError("settings path must be a regular file")
            if file_stat.st_size > _MAX_SETTINGS_BYTES:
                raise SettingsValidationError("settings file exceeds the maximum supported size")
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                fd = -1
                raw = json.load(handle)
        finally:
            if fd >= 0:
                os.close(fd)
    except (OSError, json.JSONDecodeError) as exc:
        raise SettingsValidationError("settings file is unreadable or malformed") from exc
    if not isinstance(raw, dict):
        raise SettingsValidationError("settings file must contain a JSON object")
    return raw


def _sanitized_for_disk(settings: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    sanitized = deepcopy(settings)
    for section, key in _SECRET_FIELDS:
        sanitized[section].pop(key, None)
    return sanitized


def _write_system_settings(settings: dict[str, dict[str, Any]]) -> None:
    path = settings_path()
    if path.exists() and path.is_symlink():
        raise SettingsValidationError("settings path must not be a symbolic link")
    if path.parent.is_symlink():
        raise SettingsValidationError("settings directory must not be a symbolic link")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(_sanitized_for_disk(settings), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            logger.debug("Could not tighten settings file permissions", exc_info=True)
    finally:
        temporary_path.unlink(missing_ok=True)


def _load_system_settings_unlocked() -> dict[str, dict[str, Any]]:
    raw = _read_raw_settings()
    legacy_secrets: list[tuple[str, str, str]] = []
    for section, key in _SECRET_FIELDS:
        value = raw.get(section, {}).get(key) if isinstance(raw.get(section), dict) else None
        if isinstance(value, str) and value:
            legacy_secrets.append((section, key, value))

    if legacy_secrets:
        settings = _merge_known_values(raw)
        for section, key, value in legacy_secrets:
            settings[section][key] = value
        for section, values in settings.items():
            settings[section] = _validate_section(section, values)
        for section, key, value in legacy_secrets:
            _store_secret(section, key, value)
        _write_system_settings(settings)
        logger.warning("Migrated plaintext system-setting secrets into the encrypted vault")
        return settings

    settings = _merge_known_values(raw)
    for section, values in settings.items():
        settings[section] = _validate_section(section, values)
    return settings


def load_system_settings() -> dict[str, dict[str, Any]]:
    with _settings_transaction():
        return _load_system_settings_unlocked()


def update_system_settings(section: str, payload: object) -> dict[str, Any]:
    if section not in DEFAULT_SYSTEM_SETTINGS:
        raise KeyError(section)
    if not isinstance(payload, dict):
        raise SettingsValidationError("Settings payload must be an object")

    unknown = sorted(set(payload) - set(DEFAULT_SYSTEM_SETTINGS[section]))
    if unknown:
        raise SettingsValidationError(f"Unknown setting(s): {', '.join(unknown)}")

    with _settings_transaction():
        settings = _load_system_settings_unlocked()
        current = deepcopy(settings[section])

        if section == "notifications":
            identity_fields = {"email_host", "email_port", "email_username"}
            identity_changed = any(
                key in payload and payload[key] != current[key] for key in identity_fields
            )
            submitted_password = payload.get("email_password", "")
            if identity_changed and current["email_password"] and not submitted_password:
                raise SettingsValidationError(
                    "email_password must be re-entered when the SMTP destination or username changes"
                )

        merged = deepcopy(current)
        for key, value in payload.items():
            if (section, key) in _SECRET_FIELDS:
                if value == "":
                    continue
                if value is None:
                    merged[key] = ""
                    continue
            merged[key] = value

        validated = _validate_section(section, merged)

        for secret_section, secret_key in _SECRET_FIELDS:
            if secret_section != section or secret_key not in payload:
                continue
            secret_value = payload[secret_key]
            if secret_value is None:
                _delete_secret(section, secret_key)
                validated[secret_key] = ""
            elif secret_value:
                _store_secret(section, secret_key, str(secret_value))

        settings[section] = validated
        _write_system_settings(settings)
        return deepcopy(validated)


def reset_system_settings(section: str) -> dict[str, Any]:
    if section not in DEFAULT_SYSTEM_SETTINGS:
        raise KeyError(section)
    with _settings_transaction():
        settings = _load_system_settings_unlocked()
        for secret_section, secret_key in _SECRET_FIELDS:
            if secret_section == section:
                _delete_secret(secret_section, secret_key)
        settings[section] = deepcopy(DEFAULT_SYSTEM_SETTINGS[section])
        _write_system_settings(settings)
        return deepcopy(settings[section])


def public_system_settings(
    settings: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    raw = deepcopy(settings or load_system_settings())
    configured_secrets: dict[str, bool] = {}
    for section, key in _SECRET_FIELDS:
        configured_secrets[f"{section}.{key}"] = bool(raw[section].get(key))
        raw[section][key] = ""
    raw["_meta"] = {
        "persistence": "file_with_encrypted_secret_vault",
        "configured_secrets": configured_secrets,
        "runtime_application": "administrative_only",
        "runtime_notice": (
            "These settings are persisted for the dashboard and administrative policy. "
            "They do not hot-reconfigure the active trading engine; use Engine controls "
            "or deployment configuration for execution changes."
        ),
    }
    return raw
