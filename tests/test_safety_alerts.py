"""Tests for the external safety alert notifier."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.risk.safety_alerts import SafetyAlertConfig, SafetyAlertNotifier

TELEGRAM_TOKEN = "123456:abcdefghijklmnopqrstuvwxyzABCDEFGH"
TELEGRAM_CHAT = "-123456789"
DISCORD_WEBHOOK = (
    "https://discord.com/api/webhooks/1234567890/"
    "abcdefghijklmnopqrstuvwxyzABCDEFGH"
)


@pytest.fixture
def notifier() -> SafetyAlertNotifier:
    return SafetyAlertNotifier(
        SafetyAlertConfig(
            telegram_bot_token=TELEGRAM_TOKEN,
            telegram_chat_id=TELEGRAM_CHAT,
            discord_webhook_url=DISCORD_WEBHOOK,
            min_severity="WARNING",
            cooldown_seconds=1,
            max_alerts_per_minute=100,
        )
    )


class TestSafetyAlertConfig:
    def test_loads_from_env(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TELEGRAM_TOKEN)
        monkeypatch.setenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT)
        monkeypatch.setenv("FENIX_SAFETY_ALERT_MIN_SEVERITY", "CRITICAL")
        configured = SafetyAlertNotifier()
        assert configured.config.telegram_bot_token == TELEGRAM_TOKEN
        assert configured.config.telegram_chat_id == TELEGRAM_CHAT
        assert configured.config.min_severity == "CRITICAL"

    def test_rejects_placeholder_credentials(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "your_bot_token_here")
        config = SafetyAlertNotifier._load_config_from_env()
        assert config.telegram_bot_token is None

    def test_disabled_when_no_credentials(self, monkeypatch):
        for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL"):
            monkeypatch.delenv(key, raising=False)
        notifier = SafetyAlertNotifier()
        assert not notifier.enabled

    @pytest.mark.parametrize(
        "webhook",
        [
            "http://discord.com/api/webhooks/123456/abcdefghijklmnopqrstuvwxyz",
            "https://127.0.0.1/api/webhooks/123456/abcdefghijklmnopqrstuvwxyz",
            "https://discord.com.evil.example/api/webhooks/123456/abcdefghijklmnopqrstuvwxyz",
            "https://discord.com@127.0.0.1/api/webhooks/123456/abcdefghijklmnopqrstuvwxyz",
        ],
    )
    def test_rejects_ssrf_discord_webhooks(self, webhook):
        notifier = SafetyAlertNotifier(
            SafetyAlertConfig(discord_webhook_url=webhook)
        )
        assert notifier.config.discord_webhook_url is None
        assert not notifier.enabled


class TestSafetyAlertNotifier:
    @pytest.mark.asyncio
    async def test_send_alert_calls_both_channels(self, notifier: SafetyAlertNotifier):
        notifier._send_telegram = AsyncMock()
        notifier._send_discord = AsyncMock()

        result = await notifier.send_alert("ORDER_OUTCOME_UNCERTAIN", "test message")

        assert result is True
        notifier._send_telegram.assert_awaited_once()
        notifier._send_discord.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cooldown_prevents_duplicate_alerts(self, notifier: SafetyAlertNotifier):
        notifier._send_telegram = AsyncMock()
        notifier._send_discord = AsyncMock()

        await notifier.send_alert("RECONCILIATION_FAILURE", "first")
        await notifier.send_alert("RECONCILIATION_FAILURE", "second (should be blocked)")

        # Only the first call should have been sent.
        assert notifier._send_telegram.await_count == 1
        assert notifier._send_discord.await_count == 1

    @pytest.mark.asyncio
    async def test_different_event_types_bypass_cooldown(self, notifier: SafetyAlertNotifier):
        notifier._send_telegram = AsyncMock()
        notifier._send_discord = AsyncMock()

        await notifier.send_alert("ORDER_OUTCOME_UNCERTAIN", "first event")
        await notifier.send_alert("ACCOUNT_MARGIN_CAP", "different event")

        assert notifier._send_telegram.await_count == 2
        assert notifier._send_discord.await_count == 2

    @pytest.mark.asyncio
    async def test_info_severity_filtered_by_default(self, notifier: SafetyAlertNotifier):
        notifier._send_telegram = AsyncMock()
        notifier._send_discord = AsyncMock()

        await notifier.send_alert("UNKNOWN_EVENT", "info level event")

        # Unknown events default to WARNING, which passes the WARNING filter.
        assert notifier._send_telegram.await_count == 1

    @pytest.mark.asyncio
    async def test_critical_severity_passes_critical_filter(self):
        notifier = SafetyAlertNotifier(
            SafetyAlertConfig(
                telegram_bot_token=TELEGRAM_TOKEN,
                telegram_chat_id=TELEGRAM_CHAT,
                discord_webhook_url=DISCORD_WEBHOOK,
                min_severity="CRITICAL",
                cooldown_seconds=1,
                max_alerts_per_minute=100,
            )
        )
        notifier._send_telegram = AsyncMock()
        notifier._send_discord = AsyncMock()

        # ACCOUNT_MARGIN_CAP is WARNING, should be filtered.
        await notifier.send_alert("ACCOUNT_MARGIN_CAP", "warning event")
        assert notifier._send_telegram.await_count == 0

        # ORDER_OUTCOME_UNCERTAIN is CRITICAL, should pass.
        await notifier.send_alert("ORDER_OUTCOME_UNCERTAIN", "critical event")
        assert notifier._send_telegram.await_count == 1

    @pytest.mark.asyncio
    async def test_rate_limit_drops_excess_alerts(self):
        notifier = SafetyAlertNotifier(
            SafetyAlertConfig(
                telegram_bot_token=TELEGRAM_TOKEN,
                telegram_chat_id=TELEGRAM_CHAT,
                discord_webhook_url=DISCORD_WEBHOOK,
                min_severity="WARNING",
                cooldown_seconds=0,
                max_alerts_per_minute=2,
            )
        )
        notifier._send_telegram = AsyncMock()
        notifier._send_discord = AsyncMock()

        # Send 3 different event types to avoid cooldown.
        await notifier.send_alert("ORDER_OUTCOME_UNCERTAIN", "1")
        await notifier.send_alert("LIMIT_CANCEL_UNCONFIRMED", "2")
        await notifier.send_alert("PROTECTION_NOT_VERIFIED", "3 (rate limited)")

        # Only 2 should have been sent.
        assert notifier._send_telegram.await_count == 2

    @pytest.mark.asyncio
    async def test_disabled_notifier_returns_false(self, monkeypatch):
        for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL"):
            monkeypatch.delenv(key, raising=False)
        notifier = SafetyAlertNotifier()
        result = await notifier.send_alert("ORDER_OUTCOME_UNCERTAIN", "test")
        assert result is False

    @pytest.mark.asyncio
    async def test_telegram_error_disables_channel(self, notifier: SafetyAlertNotifier):
        """A 400-level Telegram error should disable the channel for the session."""
        # Patch _send_telegram to simulate a 400 error that disables the token.
        original_telegram = notifier.config.telegram_bot_token

        async def mock_send_telegram(event_type, severity, message, context_str, timestamp):
            notifier.config.telegram_bot_token = None

        notifier._send_telegram = mock_send_telegram  # type: ignore
        notifier._send_discord = AsyncMock()

        await notifier.send_alert("ORDER_OUTCOME_UNCERTAIN", "test")

        assert notifier.config.telegram_bot_token is None
        # Restore for other tests.
        notifier.config.telegram_bot_token = original_telegram
