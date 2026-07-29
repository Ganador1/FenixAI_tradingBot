"""Validation for credential-bearing outbound notification endpoints."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_TELEGRAM_TOKEN = re.compile(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,128}")
_TELEGRAM_CHAT = re.compile(r"(?:-?[0-9]{1,20}|@[A-Za-z0-9_]{5,32})")
_DISCORD_HOST = re.compile(r"(?:(?:canary|ptb)\.)?discord(?:app)?\.com")
_DISCORD_PATH = re.compile(r"/api/webhooks/[0-9]{5,30}/[A-Za-z0-9._-]{20,256}/?")


def validated_telegram_token(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized if _TELEGRAM_TOKEN.fullmatch(normalized) else None


def validated_telegram_chat(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized if _TELEGRAM_CHAT.fullmatch(normalized) else None


def validated_discord_webhook(value: str | None) -> str | None:
    """Allow only canonical HTTPS Discord webhook endpoints, never arbitrary URLs."""
    if value is None:
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or not _DISCORD_HOST.fullmatch(host)
        or not _DISCORD_PATH.fullmatch(parsed.path)
        or parsed.fragment
    ):
        return None
    return urlunsplit(("https", host, parsed.path.rstrip("/"), "", ""))
