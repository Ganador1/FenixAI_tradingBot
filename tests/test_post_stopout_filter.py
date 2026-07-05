"""Tests for the post-stopout re-entry filter and CAUTION cooldown defaults.

Motivated by the 2026-07-05 losing streak: after a LONG stop-loss was swept,
the bot opened a SHORT 30 seconds later chasing the sweep (which reversed).
Literature: never re-enter in the direction of the move that hit your stop.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.trading.engine import TradingEngine


def _make_engine(bars: int = 2, timeframe: str = "15m"):
    engine = TradingEngine.__new__(TradingEngine)
    engine._post_stopout_block_bars = bars
    engine._post_stopout_block = None
    engine.timeframe = timeframe
    return engine


class TestRegisterPostStopoutBlock:
    def test_losing_long_blocks_sell(self):
        engine = _make_engine()
        engine._register_post_stopout_block({"side": "LONG"}, None, realized_pnl=-3.2)
        assert engine._post_stopout_block is not None
        assert engine._post_stopout_block["direction"] == "SELL"

    def test_losing_short_blocks_buy(self):
        engine = _make_engine()
        engine._register_post_stopout_block({"side": "SHORT"}, None, realized_pnl=-1.0)
        assert engine._post_stopout_block["direction"] == "BUY"

    def test_winning_close_does_not_arm(self):
        engine = _make_engine()
        engine._register_post_stopout_block({"side": "LONG"}, None, realized_pnl=2.5)
        assert engine._post_stopout_block is None

    def test_disabled_via_zero_bars(self):
        engine = _make_engine(bars=0)
        engine._register_post_stopout_block({"side": "LONG"}, None, realized_pnl=-5.0)
        assert engine._post_stopout_block is None

    def test_side_from_tracked_position(self):
        engine = _make_engine()

        class Pos:
            side = "SHORT"

        engine._register_post_stopout_block({}, Pos(), realized_pnl=-1.0)
        assert engine._post_stopout_block["direction"] == "BUY"

    def test_unknown_side_ignored(self):
        engine = _make_engine()
        engine._register_post_stopout_block({}, None, realized_pnl=-1.0)
        assert engine._post_stopout_block is None

    def test_block_duration_matches_bars_times_timeframe(self):
        engine = _make_engine(bars=2, timeframe="15m")
        before = datetime.now(timezone.utc)
        engine._register_post_stopout_block({"side": "LONG"}, None, realized_pnl=-1.0)
        until = engine._post_stopout_block["until"]
        delta = (until - before).total_seconds()
        assert 1790 <= delta <= 1810  # 2 * 900s


class TestPostStopoutBlocksEntry:
    def test_blocks_matching_direction(self):
        engine = _make_engine()
        engine._post_stopout_block = {
            "direction": "SELL",
            "until": datetime.now(timezone.utc) + timedelta(minutes=10),
        }
        reason = engine._post_stopout_blocks_entry("SELL")
        assert reason is not None and "post_stopout_reentry_block" in reason

    def test_allows_opposite_direction(self):
        engine = _make_engine()
        engine._post_stopout_block = {
            "direction": "SELL",
            "until": datetime.now(timezone.utc) + timedelta(minutes=10),
        }
        assert engine._post_stopout_blocks_entry("BUY") is None

    def test_expired_block_is_cleared(self):
        engine = _make_engine()
        engine._post_stopout_block = {
            "direction": "SELL",
            "until": datetime.now(timezone.utc) - timedelta(seconds=1),
        }
        assert engine._post_stopout_blocks_entry("SELL") is None
        assert engine._post_stopout_block is None

    def test_no_block_returns_none(self):
        engine = _make_engine()
        assert engine._post_stopout_blocks_entry("BUY") is None


class TestCautionCooldownDefaults:
    def test_caution_default_is_two_bars_of_15m(self, monkeypatch):
        monkeypatch.delenv("FENIX_CAUTION_COOLDOWN_SECONDS", raising=False)
        from src.risk.runtime_feedback import RiskFeedbackLoopConfig

        cfg = RiskFeedbackLoopConfig()
        assert cfg.caution_cooldown_seconds == 1800
        assert cfg.severe_cooldown_seconds == 3600

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FENIX_CAUTION_COOLDOWN_SECONDS", "600")
        monkeypatch.setenv("FENIX_SEVERE_COOLDOWN_SECONDS", "1200")
        from src.risk.runtime_feedback import RiskFeedbackLoopConfig

        cfg = RiskFeedbackLoopConfig()
        assert cfg.caution_cooldown_seconds == 600
        assert cfg.severe_cooldown_seconds == 1200

    def test_invalid_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("FENIX_CAUTION_COOLDOWN_SECONDS", "not-a-number")
        from src.risk.runtime_feedback import RiskFeedbackLoopConfig

        cfg = RiskFeedbackLoopConfig()
        assert cfg.caution_cooldown_seconds == 1800


class TestQabbaCapitulationPrompt:
    def test_prompt_contains_capitulation_rule(self):
        from src.prompts.agent_prompts import QABBA_ANALYST_SYSTEM

        assert "CAPITULATION" in QABBA_ANALYST_SYSTEM
        assert "chasing the sweep" in QABBA_ANALYST_SYSTEM


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
