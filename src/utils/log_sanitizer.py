# src/utils/log_sanitizer.py
"""
Log sanitization utilities for Fenix Trading Bot.

Removes sensitive information from logs to prevent exposure
of API keys, secrets, and other sensitive data.
"""

import re
from typing import Any

# Fields that should never be logged
SENSITIVE_FIELDS = {
    "signature",
    "apikey",
    "api_key",
    "apiKey",
    "secretkey",
    "secret_key",
    "secretKey",
    "api_secret",
    "apisecret",
    "apiSecret",
    "password",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
}

# Patterns to redact in string values
SENSITIVE_PATTERNS = [
    (re.compile(r'api[_-]?key["\']?\s*[:=]\s*["\']?[\w-]+', re.IGNORECASE), "[API_KEY_REDACTED]"),
    (re.compile(r'secret["\']?\s*[:=]\s*["\']?[\w-]+', re.IGNORECASE), "[SECRET_REDACTED]"),
    (re.compile(r'password["\']?\s*[:=]\s*["\']?[\w-]+', re.IGNORECASE), "[PASSWORD_REDACTED]"),
]


def sanitize_for_log(data: dict[str, Any]) -> dict[str, Any]:
    """
    Remove sensitive fields from dict before logging.

    Args:
        data: Dictionary that may contain sensitive information

    Returns:
        Sanitized dictionary safe for logging
    """
    if not isinstance(data, dict):
        return data

    result = {}
    for key, value in data.items():
        # Check if key is sensitive
        if key.lower() in {s.lower() for s in SENSITIVE_FIELDS}:
            result[key] = "[REDACTED]"
        elif isinstance(value, dict):
            # Recursively sanitize nested dicts
            result[key] = sanitize_for_log(value)
        elif isinstance(value, list):
            # Sanitize list items
            result[key] = [
                sanitize_for_log(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            result[key] = value

    return result


def sanitize_string(text: str) -> str:
    """
    Redact sensitive patterns from a string.

    Args:
        text: String that may contain sensitive information

    Returns:
        String with sensitive patterns redacted
    """
    result = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def safe_order_log(order: dict[str, Any]) -> dict[str, Any]:
    """
    Create a safe version of an order dict for logging.

    Keeps useful trading information but removes sensitive data.

    Args:
        order: Order dictionary from Binance API

    Returns:
        Safe order dict for logging
    """
    # Fields that are safe and useful to log
    SAFE_FIELDS = {
        "symbol",
        "orderId",
        "clientOrderId",
        "status",
        "type",
        "side",
        "price",
        "origQty",
        "executedQty",
        "avgPrice",
        "stopPrice",
        "time",
        "updateTime",
        "reduceOnly",
        "closePosition",
        "positionSide",
        "priceProtect",
        "workingType",
    }

    return {k: v for k, v in order.items() if k in SAFE_FIELDS}
