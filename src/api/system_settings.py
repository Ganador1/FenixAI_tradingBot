"""Persistent, validated storage for dashboard system settings.

These values configure the dashboard and administrative policies. They do not
hot-reconfigure a running trading engine; execution controls remain behind the
dedicated ``/api/engine`` endpoints.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

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


class SettingsValidationError(ValueError):
    """Raised when a settings update does not match the public schema."""


def settings_path() -> Path:
    configured = os.getenv("FENIX_SYSTEM_SETTINGS_PATH", "").strip()
    return Path(configured) if configured else Path("data/system_settings.json")


def _merge_known_values(raw: object) -> dict[str, dict[str, Any]]:
    settings = deepcopy(DEFAULT_SYSTEM_SETTINGS)
    if not isinstance(raw, dict):
        return settings
    for section, defaults in DEFAULT_SYSTEM_SETTINGS.items():
        saved_section = raw.get(section)
        if not isinstance(saved_section, dict):
            continue
        for key in defaults:
            if key in saved_section:
                settings[section][key] = saved_section[key]
    return settings


def load_system_settings() -> dict[str, dict[str, Any]]:
    path = settings_path()
    if not path.exists():
        return deepcopy(DEFAULT_SYSTEM_SETTINGS)
    try:
        return _merge_known_values(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load system settings from %s: %s", path, exc)
        return deepcopy(DEFAULT_SYSTEM_SETTINGS)


def _write_system_settings(settings: dict[str, dict[str, Any]]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _value_matches_default(value: Any, default: Any) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, list):
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return isinstance(value, type(default))


def update_system_settings(section: str, payload: object) -> dict[str, Any]:
    if section not in DEFAULT_SYSTEM_SETTINGS:
        raise KeyError(section)
    if not isinstance(payload, dict):
        raise SettingsValidationError("Settings payload must be an object")

    defaults = DEFAULT_SYSTEM_SETTINGS[section]
    unknown = sorted(set(payload) - set(defaults))
    if unknown:
        raise SettingsValidationError(f"Unknown setting(s): {', '.join(unknown)}")

    settings = load_system_settings()
    for key, value in payload.items():
        if (section, key) in _SECRET_FIELDS and value == "":
            # The API masks saved secrets as an empty string. Sending that
            # representation back must not erase the stored credential.
            continue
        if (section, key) in _SECRET_FIELDS and value is None:
            settings[section][key] = ""
            continue
        if not _value_matches_default(value, defaults[key]):
            raise SettingsValidationError(
                f"Invalid type for {section}.{key}: expected {type(defaults[key]).__name__}"
            )
        settings[section][key] = value

    email_port = settings["notifications"]["email_port"]
    if not 1 <= email_port <= 65535:
        raise SettingsValidationError("notifications.email_port must be between 1 and 65535")

    _write_system_settings(settings)
    return deepcopy(settings[section])


def reset_system_settings(section: str) -> dict[str, Any]:
    if section not in DEFAULT_SYSTEM_SETTINGS:
        raise KeyError(section)
    settings = load_system_settings()
    settings[section] = deepcopy(DEFAULT_SYSTEM_SETTINGS[section])
    _write_system_settings(settings)
    return deepcopy(settings[section])


def public_system_settings(settings: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    raw = deepcopy(settings or load_system_settings())
    configured_secrets: dict[str, bool] = {}
    for section, key in _SECRET_FIELDS:
        configured_secrets[f"{section}.{key}"] = bool(raw[section].get(key))
        raw[section][key] = ""
    raw["_meta"] = {
        "persistence": "file",
        "configured_secrets": configured_secrets,
        "runtime_application": "administrative_only",
        "runtime_notice": (
            "These settings are persisted for the dashboard and administrative policy. "
            "They do not hot-reconfigure the active trading engine; use Engine controls "
            "or deployment configuration for execution changes."
        ),
    }
    return raw
