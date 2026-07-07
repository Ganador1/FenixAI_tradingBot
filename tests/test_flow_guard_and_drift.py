"""Tests for the OBI flow-coherence guard, the pre-order drift guard and the
scorecard-weighted consensus (2026-07-07 improvements)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.trading.engine import TradingEngine


def _engine(obi=None):
    e = TradingEngine.__new__(TradingEngine)
    e.market_data = MagicMock()
    micro = SimpleNamespace(obi=obi)
    e.market_data.get_microstructure_metrics.return_value = micro
    return e


class TestFlowCoherenceGuard:
    def test_short_into_extreme_bid_blocked(self, monkeypatch):
        monkeypatch.delenv("FENIX_FLOW_GUARD_ENABLE", raising=False)
        e = _engine(obi=237.0)  # the ETH 2026-07-07 09:30 case
        reason = e._flow_coherence_blocks_entry("SELL")
        assert reason is not None and "extreme bid dominance" in reason

    def test_long_into_extreme_ask_blocked(self, monkeypatch):
        monkeypatch.delenv("FENIX_FLOW_GUARD_ENABLE", raising=False)
        e = _engine(obi=0.12)  # the ETH 2026-07-05 05:48 capitulation case
        reason = e._flow_coherence_blocks_entry("BUY")
        assert reason is not None and "extreme ask dominance" in reason

    def test_normal_obi_allows_both(self, monkeypatch):
        monkeypatch.delenv("FENIX_FLOW_GUARD_ENABLE", raising=False)
        e = _engine(obi=1.1)
        assert e._flow_coherence_blocks_entry("SELL") is None
        assert e._flow_coherence_blocks_entry("BUY") is None

    def test_short_with_extreme_ask_allowed(self, monkeypatch):
        # OBI 0.1 = sellers dominate -> SELL is WITH the flow, allowed
        monkeypatch.delenv("FENIX_FLOW_GUARD_ENABLE", raising=False)
        e = _engine(obi=0.1)
        assert e._flow_coherence_blocks_entry("SELL") is None

    def test_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("FENIX_FLOW_GUARD_ENABLE", "0")
        e = _engine(obi=999.0)
        assert e._flow_coherence_blocks_entry("SELL") is None

    def test_missing_obi_is_noop(self, monkeypatch):
        monkeypatch.delenv("FENIX_FLOW_GUARD_ENABLE", raising=False)
        e = _engine(obi=None)
        assert e._flow_coherence_blocks_entry("SELL") is None

    def test_hold_is_noop(self):
        e = _engine(obi=999.0)
        assert e._flow_coherence_blocks_entry("HOLD") is None


class TestWeightedConsensus:
    def _data(self, tech="HOLD", qabba="HOLD", visual="HOLD"):
        return {
            "_execution_technical_signal": tech,
            "_execution_qabba_signal": qabba,
            "_execution_visual_signal": visual,
        }

    def test_weights_from_scorecards(self):
        e = TradingEngine.__new__(TradingEngine)
        scores = {
            "tech": SimpleNamespace(multiplier=0.88),
            "qabba": SimpleNamespace(multiplier=0.54),
            "visual": SimpleNamespace(multiplier=0.50),
        }
        with patch("src.analysis.agent_scorecards.get_agent_scorecards") as gs:
            gs.return_value.get_scores.return_value = scores
            w = e._weighted_consensus("SELL", self._data("SELL", "SELL_QABBA", "BUY"))
        assert w == pytest.approx(0.88 + 0.54)

    def test_fallback_to_count_when_scorecards_unavailable(self):
        e = TradingEngine.__new__(TradingEngine)
        with patch(
            "src.analysis.agent_scorecards.get_agent_scorecards",
            side_effect=RuntimeError("no data"),
        ):
            w = e._weighted_consensus("BUY", self._data("BUY", "BUY_QABBA", "HOLD"))
        assert w == pytest.approx(2.0)

    def test_two_weak_agents_below_two_strong(self):
        e = TradingEngine.__new__(TradingEngine)
        scores = {
            "tech": SimpleNamespace(multiplier=0.88),
            "qabba": SimpleNamespace(multiplier=0.54),
            "visual": SimpleNamespace(multiplier=0.50),
        }
        with patch("src.analysis.agent_scorecards.get_agent_scorecards") as gs:
            gs.return_value.get_scores.return_value = scores
            weak = e._weighted_consensus("SELL", self._data("HOLD", "SELL_QABBA", "SELL"))
            strong = e._weighted_consensus("SELL", self._data("SELL", "SELL_QABBA", "HOLD"))
        assert weak < strong


class TestEntryDriftGuard:
    def _engine_for_drift(self, ref, live):
        e = TradingEngine.__new__(TradingEngine)
        e.market_data = MagicMock()
        e.market_data.current_price = live
        e._emit_filter_blocked = AsyncMock()
        e._consecutive_holds = 0
        e._last_decision_time = None
        return e, {"_decision_reference_price": ref, "risk_assessment": {}}

    @pytest.mark.asyncio
    async def test_adverse_buy_drift_aborts(self, monkeypatch):
        monkeypatch.delenv("FENIX_MAX_ENTRY_DRIFT_BPS", raising=False)
        # BUY decided at 1769.43, price now 1772.45 -> +17 bps adverse (>15)
        e, data = self._engine_for_drift(ref=1769.43, live=1772.45)
        await e._execute_trade("BUY", "MEDIUM", data)
        e._emit_filter_blocked.assert_awaited_once()
        assert e._emit_filter_blocked.await_args.args[0] == "ENTRY_DRIFT"

    @pytest.mark.asyncio
    async def test_favorable_drift_proceeds_past_guard(self, monkeypatch):
        monkeypatch.delenv("FENIX_MAX_ENTRY_DRIFT_BPS", raising=False)
        # SELL decided at 1769.43, price now 1772.45 -> favorable for SELL
        e, data = self._engine_for_drift(ref=1769.43, live=1772.45)
        # Let it crash after the guard (no more attrs mocked) — reaching past
        # the guard is what we assert.
        with pytest.raises(Exception):
            await e._execute_trade("SELL", "MEDIUM", data)
        e._emit_filter_blocked.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("FENIX_MAX_ENTRY_DRIFT_BPS", "0")
        e, data = self._engine_for_drift(ref=1769.43, live=1790.0)
        with pytest.raises(Exception):
            await e._execute_trade("BUY", "MEDIUM", data)
        e._emit_filter_blocked.assert_not_awaited()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
