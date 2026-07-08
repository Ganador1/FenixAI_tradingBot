"""Tests for the macro news scanner and F&G trend (sentiment macro awareness)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tools.macro_news import classify_headline, get_macro_alerts


class TestClassifyHeadline:
    def test_military_strike_is_severe(self):
        assert classify_headline("US launches strikes on Iran after tankers hit") == "severe"

    def test_sanctions_are_high(self):
        assert classify_headline("EU announces new sanctions package on Russia") == "severe" or \
            classify_headline("EU announces new sanctions package") in {"high", "severe"}

    def test_fed_rate_is_high(self):
        assert classify_headline("Federal Reserve signals possible rate hike in September") == "high"

    def test_benign_headline_ignored(self):
        assert classify_headline("Local festival draws record crowds this weekend") is None

    def test_crypto_bullish_headline_ignored(self):
        assert classify_headline("Ether climbs toward $2K as Bitmine buys ETH") is None

    def test_summary_also_scanned(self):
        assert (
            classify_headline("Markets on edge", "escalation fears after missile attack")
            == "severe"
        )


class TestGetMacroAlerts:
    def test_feed_failure_returns_empty(self):
        with patch("src.tools.macro_news.feedparser.parse", side_effect=RuntimeError("net down")):
            assert get_macro_alerts(use_cache=False) == []

    def test_filters_and_formats(self):
        entry = {
            "title": "US launches strikes on Iran",
            "summary": "Escalation in the Middle East",
            "published_parsed": None,
            "link": "https://example.com/a",
        }
        benign = {
            "title": "Sports roundup of the week",
            "summary": "",
            "published_parsed": None,
            "link": "https://example.com/b",
        }
        feed = MagicMock()
        feed.entries = [
            MagicMock(get=lambda k, d=None, e=entry: e.get(k, d)),
            MagicMock(get=lambda k, d=None, e=benign: e.get(k, d)),
        ]
        with patch("src.tools.macro_news.feedparser.parse", return_value=feed):
            alerts = get_macro_alerts(use_cache=False)
        titles = [a["title"] for a in alerts]
        assert any("Iran" in t for t in titles)
        assert not any("Sports" in t for t in titles)
        assert all(a["source"].startswith("MACRO/") for a in alerts)
        assert all(a["severity"] in {"severe", "high"} for a in alerts)


class TestFearGreedTrend:
    def test_trend_string_with_sharp_drop(self):
        from src.tools.fear_greed import FearGreedTool

        payload = {"data": [{"value": "20"}, {"value": "41"}]}
        resp = MagicMock()
        resp.json.return_value = payload
        resp.raise_for_status.return_value = None
        with patch("src.tools.fear_greed.requests.get", return_value=resp):
            out = FearGreedTool().get_value_with_trend()
        assert out == "20 (yesterday 41, change -21 — sharp drop, possible macro shock)"

    def test_trend_string_stable(self):
        from src.tools.fear_greed import FearGreedTool

        payload = {"data": [{"value": "50"}, {"value": "48"}]}
        resp = MagicMock()
        resp.json.return_value = payload
        resp.raise_for_status.return_value = None
        with patch("src.tools.fear_greed.requests.get", return_value=resp):
            out = FearGreedTool().get_value_with_trend()
        assert out == "50 (yesterday 48, change +2)"

    def test_single_entry_falls_back_to_plain(self):
        from src.tools.fear_greed import FearGreedTool

        payload = {"data": [{"value": "33"}]}
        resp = MagicMock()
        resp.json.return_value = payload
        resp.raise_for_status.return_value = None
        with patch("src.tools.fear_greed.requests.get", return_value=resp):
            out = FearGreedTool().get_value_with_trend()
        assert out == "33"


class TestPromptRules:
    def test_macro_rules_present_in_sentiment_prompt(self):
        from src.prompts.agent_prompts import SENTIMENT_ANALYST_SYSTEM

        assert "MACRO ALERT RULES" in SENTIMENT_ANALYST_SYSTEM
        assert "MACRO/" in SENTIMENT_ANALYST_SYSTEM
        assert "SHARP DROP" in SENTIMENT_ANALYST_SYSTEM


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
