"""External alert delivery for live-safety events.

Extends the existing CircuitBreakerNotifier (which only handles risk-mode
changes) to cover the operational safety events that the live-safety
hardening can emit:

- ORDER_OUTCOME_UNCERTAIN   — ambiguous submission, possible duplicate exposure
- LIMIT_CANCEL_UNCONFIRMED  — limit fallback blocked, position state unknown
- ACCOUNT_MARGIN_CAP        — global portfolio guard blocked an entry
- PROTECTION_NOT_VERIFIED   — SL/TP not visible on exchange after entry
- RECONCILIATION_FAILURE    — local/exchange position mismatch
- STALE_HEARTBEAT            — instance heartbeat not seen within freshness window

The notifier is async, deduplicates within a cooldown window, and degrades
gracefully when credentials are absent (same pattern as circuit_breaker_alerts).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Event severity levels for routing and filtering.
_SEVERITY_ORDER = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}
_DEFAULT_SEVERITY = {
    "ORDER_OUTCOME_UNCERTAIN": "CRITICAL",
    "LIMIT_CANCEL_UNCONFIRMED": "CRITICAL",
    "ACCOUNT_MARGIN_CAP": "WARNING",
    "PROTECTION_NOT_VERIFIED": "CRITICAL",
    "RECONCILIATION_FAILURE": "CRITICAL",
    "STALE_HEARTBEAT": "WARNING",
}


@dataclass
class SafetyAlertConfig:
    """Configuration for the safety alert notifier."""

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    discord_webhook_url: str | None = None
    # Minimum severity to send: INFO, WARNING, or CRITICAL.
    min_severity: str = "WARNING"
    # Per-event-type cooldown in seconds to prevent alert storms.
    cooldown_seconds: int = 300
    # Global rate limit: max alerts per minute.
    max_alerts_per_minute: int = 10


@dataclass
class _CooldownEntry:
    last_sent: float = 0.0
    count: int = 0


class SafetyAlertNotifier:
    """Send external alerts for live-safety events via Telegram and Discord."""

    def __init__(self, config: SafetyAlertConfig | None = None) -> None:
        self.config = config or self._load_config_from_env()
        self._cooldowns: dict[str, _CooldownEntry] = {}
        self._recent_alerts: list[float] = []
        self._lock = asyncio.Lock()

    @staticmethod
    def _clean_credential(value: str | None, name: str) -> str | None:
        if not value:
            return None
        lowered = value.strip().lower()
        if not lowered or any(marker in lowered for marker in ("your_", "changeme", "example", "xxx", "placeholder")):
            return None
        return value.strip()

    @classmethod
    def _load_config_from_env(cls) -> SafetyAlertConfig:
        return SafetyAlertConfig(
            telegram_bot_token=cls._clean_credential(
                os.getenv("TELEGRAM_BOT_TOKEN"), "TELEGRAM_BOT_TOKEN"
            ),
            telegram_chat_id=cls._clean_credential(
                os.getenv("TELEGRAM_CHAT_ID"), "TELEGRAM_CHAT_ID"
            ),
            discord_webhook_url=cls._clean_credential(
                os.getenv("DISCORD_WEBHOOK_URL"), "DISCORD_WEBHOOK_URL"
            ),
            min_severity=os.getenv("FENIX_SAFETY_ALERT_MIN_SEVERITY", "WARNING"),
            cooldown_seconds=max(30, int(os.getenv("FENIX_SAFETY_ALERT_COOLDOWN_SEC", "300"))),
            max_alerts_per_minute=max(1, int(os.getenv("FENIX_SAFETY_ALERT_MAX_PER_MIN", "10"))),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.config.telegram_bot_token or self.config.discord_webhook_url)

    def _severity_level(self, event_type: str) -> str:
        return _DEFAULT_SEVERITY.get(event_type, "WARNING")

    def _should_send(self, event_type: str) -> bool:
        severity = self._severity_level(event_type)
        min_level = _SEVERITY_ORDER.get(self.config.min_severity, 1)
        if _SEVERITY_ORDER.get(severity, 1) < min_level:
            return False

        entry = self._cooldowns.get(event_type)
        if entry and (time.monotonic() - entry.last_sent) < self.config.cooldown_seconds:
            return False

        # Global rate limit.
        now = time.monotonic()
        self._recent_alerts = [t for t in self._recent_alerts if now - t < 60.0]
        if len(self._recent_alerts) >= self.config.max_alerts_per_minute:
            logger.warning("Safety alert rate limit reached, dropping %s", event_type)
            return False

        return True

    async def send_alert(
        self,
        event_type: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Send an alert for a safety event. Returns True if any channel succeeded."""
        if not self.enabled:
            return False

        async with self._lock:
            if not self._should_send(event_type):
                return False

            self._recent_alerts.append(time.monotonic())
            entry = self._cooldowns.setdefault(event_type, _CooldownEntry())
            entry.last_sent = time.monotonic()
            entry.count += 1

        severity = self._severity_level(event_type)
        context_str = "\n".join(f"  • {k}: {v}" for k, v in (context or {}).items()) or "  (none)"
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        success = False

        if self.config.telegram_bot_token and self.config.telegram_chat_id:
            try:
                await self._send_telegram(event_type, severity, message, context_str, timestamp)
                success = True
            except Exception as exc:
                logger.error("Failed to send Telegram safety alert: %s", exc)

        if self.config.discord_webhook_url:
            try:
                await self._send_discord(event_type, severity, message, context_str, timestamp)
                success = True
            except Exception as exc:
                logger.error("Failed to send Discord safety alert: %s", exc)

        return success

    async def _send_telegram(
        self,
        event_type: str,
        severity: str,
        message: str,
        context_str: str,
        timestamp: str,
    ) -> None:
        import aiohttp

        emoji = {"CRITICAL": "🚨", "WARNING": "⚠️", "INFO": "ℹ️"}.get(severity, "ℹ️")
        text = (
            f"{emoji} *FENIX SAFETY ALERT*\n\n"
            f"*Event:* `{event_type}`\n"
            f"*Severity:* {severity}\n"
            f"*Message:* {message}\n\n"
            f"*Context:*\n{context_str}\n\n"
            f"_Time: {timestamp}_"
        )
        url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self.config.telegram_chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_notification": severity != "CRITICAL",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    logger.info("Telegram safety alert sent: %s", event_type)
                elif 400 <= resp.status < 500 and resp.status != 429:
                    body = await resp.text()
                    logger.error(
                        "Telegram API error %d (invalid credentials?); "
                        "disabling Telegram for this session: %s",
                        resp.status, body,
                    )
                    self.config.telegram_bot_token = None
                else:
                    body = await resp.text()
                    logger.error("Telegram API error: %d - %s", resp.status, body)

    async def _send_discord(
        self,
        event_type: str,
        severity: str,
        message: str,
        context_str: str,
        timestamp: str,
    ) -> None:
        import aiohttp

        colors = {"CRITICAL": 0xFF0000, "WARNING": 0xFFA500, "INFO": 0x0099FF}
        embed = {
            "title": f"🛡️ Fenix Safety: {event_type}",
            "color": colors.get(severity, 0x808080),
            "description": message,
            "fields": [
                {"name": "Severity", "value": severity, "inline": True},
                {"name": "Context", "value": context_str[:1024], "inline": False},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "FenixAI Live Safety Monitor"},
        }
        payload = {
            "embeds": [embed],
            "content": "@everyone" if severity == "CRITICAL" else None,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.config.discord_webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status in (200, 204):
                    logger.info("Discord safety alert sent: %s", event_type)
                elif 400 <= resp.status < 500 and resp.status != 429:
                    body = await resp.text()
                    logger.error(
                        "Discord webhook error %d (invalid URL?); "
                        "disabling Discord for this session: %s",
                        resp.status, body,
                    )
                    self.config.discord_webhook_url = None
                else:
                    body = await resp.text()
                    logger.error("Discord webhook error: %d - %s", resp.status, body)


# Singleton.
_notifier: SafetyAlertNotifier | None = None


def get_safety_alert_notifier() -> SafetyAlertNotifier:
    """Return the global safety alert notifier singleton."""
    global _notifier
    if _notifier is None:
        _notifier = SafetyAlertNotifier()
    return _notifier


async def alert_safety_event(
    event_type: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> bool:
    """Convenience function to send a safety alert."""
    return await get_safety_alert_notifier().send_alert(event_type, message, context)
