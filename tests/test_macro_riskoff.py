"""Tests for the macro risk-off window (fresh severe geopolitical event ->
block new BUYs, cap SELL sizing, never force a SELL)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.trading.engine import TradingEngine


def _engine():
    return TradingEngine.__new__(TradingEngine)


SEVERE_FRESH = [{"title": "US launches strikes on Iran", "severity": "severe", "age_hours": 2.0}]
SEVERE_OLD = [{"title": "Old conflict update", "severity": "severe", "age_hours": 20.0}]
HIGH_ONLY = [{"title": "Fed signals rate path", "severity": "high", "age_hours": 1.0}]


class TestMacroRiskoffEvent:
    @pytest.mark.asyncio
    async def test_fresh_severe_event_detected(self, monkeypatch):
        monkeypatch.delenv("FENIX_MACRO_RISKOFF_ENABLE", raising=False)
        with patch("src.tools.macro_news.get_macro_alerts", return_value=SEVERE_FRESH):
            event = await _engine()._macro_riskoff_event()
        assert event is not None and "Iran" in event["title"]

    @pytest.mark.asyncio
    async def test_old_severe_event_expired(self, monkeypatch):
        monkeypatch.delenv("FENIX_MACRO_RISKOFF_MAX_AGE_H", raising=False)
        with patch("src.tools.macro_news.get_macro_alerts", return_value=SEVERE_OLD):
            assert await _engine()._macro_riskoff_event() is None

    @pytest.mark.asyncio
    async def test_high_but_not_severe_ignored(self):
        with patch("src.tools.macro_news.get_macro_alerts", return_value=HIGH_ONLY):
            assert await _engine()._macro_riskoff_event() is None

    @pytest.mark.asyncio
    async def test_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("FENIX_MACRO_RISKOFF_ENABLE", "0")
        with patch("src.tools.macro_news.get_macro_alerts", return_value=SEVERE_FRESH):
            assert await _engine()._macro_riskoff_event() is None

    @pytest.mark.asyncio
    async def test_feed_failure_is_noop(self):
        with patch(
            "src.tools.macro_news.get_macro_alerts", side_effect=RuntimeError("net down")
        ):
            assert await _engine()._macro_riskoff_event() is None

    @pytest.mark.asyncio
    async def test_undated_severe_alert_does_not_arm_gate(self):
        # Codex review PR #12: a severe headline without timestamp must not
        # block BUYs indefinitely.
        undated = [{"title": "War escalation feared", "severity": "severe", "age_hours": None}]
        with patch("src.tools.macro_news.get_macro_alerts", return_value=undated):
            assert await _engine()._macro_riskoff_event() is None

    @pytest.mark.asyncio
    async def test_custom_window_via_env(self, monkeypatch):
        monkeypatch.setenv("FENIX_MACRO_RISKOFF_MAX_AGE_H", "24")
        with patch("src.tools.macro_news.get_macro_alerts", return_value=SEVERE_OLD):
            event = await _engine()._macro_riskoff_event()
        assert event is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
