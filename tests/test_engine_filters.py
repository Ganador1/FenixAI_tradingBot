import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.trading.engine import TradingEngine


class _StubMarketData:
    def __init__(
        self,
        price: float = 100.0,
        *,
        obi: float = 1.0,
        volume_imbalance: float = 0.0,
        vpin_proxy: float = 0.0,
        spread: float = 0.01,
    ):
        self.current_price = price
        self.current_volume = 0.0
        self._micro = types.SimpleNamespace(
            obi=obi,
            volume_imbalance=volume_imbalance,
            vpin_proxy=vpin_proxy,
            spread=spread,
        )

    def get_microstructure_metrics(self):
        return self._micro


@pytest.mark.asyncio
async def test_chart_analysis_uses_cached_chart_without_inline_refresh():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=True)
    engine.enable_visual = True
    engine._last_chart_b64 = "cached-chart"
    engine._last_chart_ts = None
    engine._refresh_chart_cache = AsyncMock()

    chart_b64 = await engine._get_chart_b64_for_analysis()

    assert chart_b64 == "cached-chart"
    engine._refresh_chart_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_chart_analysis_attempts_inline_refresh_when_cache_empty():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=True)
    engine.enable_visual = True

    async def _populate_chart(*, force: bool, timeout_sec: float) -> None:
        assert force is True
        assert timeout_sec > 0
        engine._last_chart_b64 = "fresh-chart"

    engine._refresh_chart_cache = AsyncMock(side_effect=_populate_chart)

    chart_b64 = await engine._get_chart_b64_for_analysis()

    assert chart_b64 == "fresh-chart"
    engine._refresh_chart_cache.assert_awaited_once()


@pytest.fixture(autouse=True)
def _stub_news_scraper(monkeypatch):
    import src.trading.engine as engine_module

    monkeypatch.setattr(
        engine_module,
        "EnhancedNewsScraper",
        lambda *args, **kwargs: types.SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_chop_transition_short_tf_allows_with_size_multiplier_when_micro_strong():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="1m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=2.0, volume_imbalance=0.3, vpin_proxy=0.2)

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "test",
        },
        "qabba_report": {"signal": "BUY", "confidence": 0.85},
        "indicators": {"chop": 50.0, "rsi": 50.0, "supports": [], "resistances": []},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 1
    args = engine._execute_trade.await_args.args
    assert args[0] == "BUY"
    decision_data = args[2]
    assert float(decision_data.get("size_multiplier", 1.0)) < 1.0
    assert engine._filter_adjust_counts.get("CHOP", 0) >= 1


@pytest.mark.asyncio
async def test_chop_transition_short_tf_allows_even_when_confidence_low_if_micro_strong(monkeypatch):
    monkeypatch.setenv("FENIX_MIN_ENTRY_CONFIDENCE", "LOW")
    engine = TradingEngine(symbol="BTCUSDT", timeframe="1m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=2.0, volume_imbalance=0.3, vpin_proxy=0.2)

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "LOW",
            "combined_reasoning": "test",
        },
        "qabba_report": {"signal": "BUY", "confidence": 0.85},
        "indicators": {"chop": 50.0, "rsi": 50.0, "supports": [], "resistances": []},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 1
    args = engine._execute_trade.await_args.args
    assert args[0] == "BUY"
    decision_data = args[2]
    # Low-confidence trades should still be size-reduced under CHOP, even when microstructure rescues them.
    assert float(decision_data.get("size_multiplier", 1.0)) < 1.0
    assert engine._filter_adjust_counts.get("CHOP", 0) >= 1


@pytest.mark.asyncio
async def test_chop_transition_short_tf_blocks_when_micro_weak(monkeypatch):
    monkeypatch.setenv("FENIX_MIN_ENTRY_CONFIDENCE", "LOW")
    engine = TradingEngine(symbol="BTCUSDT", timeframe="1m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=1.0, volume_imbalance=0.0, vpin_proxy=0.2)

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "LOW",
            "combined_reasoning": "test",
        },
        "qabba_report": {"signal": "HOLD", "confidence": 0.55},
        "indicators": {"chop": 50.0, "rsi": 50.0, "supports": [], "resistances": []},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 0
    assert engine._consecutive_holds == 1
    assert engine._filter_block_counts.get("CHOP", 0) == 1


@pytest.mark.asyncio
async def test_chop_transition_higher_tf_allows_with_size_multiplier_when_micro_strong():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=2.0, volume_imbalance=0.3, vpin_proxy=0.2)

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "test",
        },
        "qabba_report": {"signal": "BUY", "confidence": 0.85},
        "indicators": {"chop": 50.0, "rsi": 50.0, "supports": [], "resistances": []},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 1
    args = engine._execute_trade.await_args.args
    assert args[0] == "BUY"
    decision_data = args[2]
    assert float(decision_data.get("size_multiplier", 1.0)) < 1.0
    assert engine._filter_adjust_counts.get("CHOP", 0) >= 1


@pytest.mark.asyncio
async def test_rsi_extreme_filter_blocks_chasing():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=1.0, volume_imbalance=0.0, vpin_proxy=0.2)

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "test",
        },
        "qabba_report": {"signal": "BUY", "confidence": 0.8},
        "indicators": {"chop": 20.0, "rsi": 85.0, "supports": [], "resistances": []},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 0
    assert engine._filter_block_counts.get("RSI_EXTREME", 0) == 1


@pytest.mark.asyncio
async def test_long_confluence_guard_blocks_weak_buy_without_qabba_confirmation():
    engine = TradingEngine(symbol="BTCUSDC", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=1.0, volume_imbalance=0.0, vpin_proxy=0.2)
    engine._long_confluence_guard = True
    engine._long_confluence_qabba_min_conf = 0.70

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "test",
        },
        "qabba_report": {"signal": "HOLD", "confidence": 0.45},
        "indicators": {
            "chop": 50.0,
            "rsi": 55.0,
            "supports": [],
            "resistances": [],
            "trend_conflict": True,
            "ema_9": 99.0,
            "ema_21": 100.0,
            "ema_50": 101.0,
            "supertrend": "BEARISH",
        },
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 0
    assert engine._filter_block_counts.get("LONG_CONFLUENCE", 0) == 1


@pytest.mark.asyncio
async def test_long_confluence_guard_allows_buy_when_qabba_confirms():
    engine = TradingEngine(symbol="BTCUSDC", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=1.0, volume_imbalance=0.0, vpin_proxy=0.2)
    engine._long_confluence_guard = True
    engine._long_confluence_qabba_min_conf = 0.70

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "test",
        },
        "qabba_report": {"signal": "BUY", "confidence": 0.95},
        "indicators": {
            "chop": 50.0,
            "rsi": 55.0,
            "supports": [],
            "resistances": [],
            "trend_conflict": True,
            "ema_9": 99.0,
            "ema_21": 100.0,
            "ema_50": 101.0,
            "supertrend": "BEARISH",
        },
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 1


@pytest.mark.asyncio
async def test_short_confluence_guard_blocks_conflicted_sell_without_book_confirmation():
    engine = TradingEngine(symbol="BTCUSDC", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=1.15, volume_imbalance=0.0, vpin_proxy=0.2)
    engine._short_confluence_guard = True
    engine._short_confluence_qabba_min_conf = 0.70
    engine._short_confluence_allow_high_conf = False
    engine._filter_min_sell_directional_score = 0.28

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "SELL",
            "confidence_in_decision": "HIGH",
            "combined_reasoning": "test",
            "_directional_score": -0.80,
        },
        "qabba_report": {"signal": "SELL", "confidence": 0.92},
        "indicators": {
            "chop": 20.0,
            "rsi": 45.0,
            "supports": [],
            "resistances": [],
            "trend_conflict": True,
            "ema_9": 101.0,
            "ema_21": 100.0,
            "ema_50": 99.0,
            "supertrend": "BULLISH",
        },
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 0
    assert engine._filter_block_counts.get("SHORT_CONFLUENCE", 0) == 1


@pytest.mark.asyncio
async def test_short_confluence_guard_allows_sell_when_qabba_and_book_confirm():
    engine = TradingEngine(symbol="BTCUSDC", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=0.60, volume_imbalance=-0.25, vpin_proxy=0.2)
    engine._short_confluence_guard = True
    engine._short_confluence_qabba_min_conf = 0.70
    engine._short_confluence_allow_high_conf = False
    engine._filter_min_sell_directional_score = 0.28

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "SELL",
            "confidence_in_decision": "HIGH",
            "combined_reasoning": "test",
            "_directional_score": -0.80,
        },
        "qabba_report": {"signal": "SELL", "confidence": 0.92},
        "indicators": {
            "chop": 20.0,
            "rsi": 45.0,
            "supports": [],
            "resistances": [],
            "trend_conflict": True,
            "ema_9": 101.0,
            "ema_21": 100.0,
            "ema_50": 99.0,
            "supertrend": "BULLISH",
        },
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 1


@pytest.mark.asyncio
async def test_directional_score_gate_blocks_weak_buy():
    engine = TradingEngine(symbol="BTCUSDC", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=1.0, volume_imbalance=0.0, vpin_proxy=0.2)
    engine._filter_min_buy_directional_score = 0.32

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "HIGH",
            "combined_reasoning": "test",
            "_directional_score": 0.26,
        },
        "qabba_report": {"signal": "BUY", "confidence": 0.85},
        "indicators": {"chop": 20.0, "rsi": 55.0, "supports": [], "resistances": []},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 0
    assert engine._filter_block_counts.get("DIRECTIONAL_SCORE", 0) == 1


@pytest.mark.asyncio
async def test_directional_score_gate_blocks_weak_sell():
    engine = TradingEngine(symbol="BTCUSDC", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=1.0, volume_imbalance=0.0, vpin_proxy=0.2)
    engine._filter_min_sell_directional_score = 0.28

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "SELL",
            "confidence_in_decision": "HIGH",
            "combined_reasoning": "test",
            "_directional_score": -0.24,
        },
        "qabba_report": {"signal": "SELL", "confidence": 0.90},
        "indicators": {"chop": 20.0, "rsi": 45.0, "supports": [], "resistances": []},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 0
    assert engine._filter_block_counts.get("DIRECTIONAL_SCORE", 0) == 1


@pytest.mark.asyncio
async def test_min_entry_confidence_allows_medium_sell_with_strong_edge_override(monkeypatch):
    monkeypatch.setenv("FENIX_MIN_ENTRY_CONFIDENCE", "HIGH")
    engine = TradingEngine(symbol="BTCUSDC", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=0.7, volume_imbalance=-0.2, vpin_proxy=0.2)
    engine._medium_sell_strong_edge_enabled = True
    engine._medium_sell_strong_edge_score = 0.60
    engine._execute_trade = AsyncMock()
    engine.on_agent_event = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "SELL",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "test",
            "_directional_score": -0.65,
        },
        "qabba_report": {"signal": "SELL", "confidence": 0.75},
        "indicators": {"chop": 20.0, "rsi": 45.0, "supports": [], "resistances": [], "trend_conflict": False},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 1
    decision_data = engine._execute_trade.await_args.args[2]
    assert decision_data["confidence_override_reason"] == "strong_directional_edge"
    assert any(
        call.args[0] == "filter:adjusted" and call.args[1]["filter"] == "MIN_ENTRY_CONFIDENCE"
        for call in engine.on_agent_event.await_args_list
    )


@pytest.mark.asyncio
async def test_directional_score_rebuilt_from_reports_when_llm_decision_omits_it():
    engine = TradingEngine(symbol="BTCUSDC", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=1.0, volume_imbalance=0.0, vpin_proxy=0.2)
    engine._filter_min_buy_directional_score = 0.32

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "llm decision without score",
        },
        "technical_report": {"signal": "HOLD", "confidence": 0.45},
        "qabba_report": {"signal": "BUY", "confidence": 0.85},
        "indicators": {"chop": 20.0, "rsi": 55.0, "supports": [], "resistances": []},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 1
    decision_data = engine._execute_trade.await_args.args[2]
    assert decision_data["_directional_score"] > 0.32
    assert decision_data["_directional_score_source"] == "fallback_weighted_reports"


@pytest.mark.asyncio
async def test_trend_conflict_gate_blocks_non_high_higher_tf_trade():
    engine = TradingEngine(symbol="BTCUSDC", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=1.0, volume_imbalance=0.0, vpin_proxy=0.2)
    engine._filter_block_trend_conflict_non_high = True

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "SELL",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "test",
            "_directional_score": -0.33,
        },
        "qabba_report": {"signal": "SELL", "confidence": 0.85},
        "indicators": {
            "chop": 20.0,
            "rsi": 45.0,
            "supports": [],
            "resistances": [],
            "trend_conflict": True,
        },
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 0
    assert engine._filter_block_counts.get("TREND_CONFLICT", 0) == 1


@pytest.mark.asyncio
async def test_sr_filter_short_tf_skipped_when_micro_strong():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="1m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=2.0, volume_imbalance=0.3, vpin_proxy=0.2)

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "test",
        },
        "qabba_report": {"signal": "BUY", "confidence": 0.85},
        "indicators": {"chop": 20.0, "rsi": 50.0, "supports": [], "resistances": [100.0]},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 1


@pytest.mark.asyncio
async def test_sr_filter_short_tf_blocks_when_micro_weak():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="1m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=1.0, volume_imbalance=0.0, vpin_proxy=0.2)

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "test",
        },
        "qabba_report": {"signal": "HOLD", "confidence": 0.55},
        "indicators": {"chop": 20.0, "rsi": 50.0, "supports": [], "resistances": [100.0]},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 0
    assert engine._filter_block_counts.get("RESISTANCE", 0) == 1


@pytest.mark.asyncio
async def test_sr_filter_blocks_buy_near_resistance_when_nanofenix_disallows():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=2.0, volume_imbalance=0.3, vpin_proxy=0.2)
    engine._nanofenix_companion_enabled = True
    engine._build_nanofenix_policy_payload = MagicMock(
        return_value={
            "allow_execute": False,
            "reason": "low_pred_bps",
            "signal": "BUY",
            "confidence": 0.62,
            "pred_bps": 0.9,
            "direction_accuracy": 0.61,
        }
    )

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "test",
        },
        "qabba_report": {"signal": "BUY", "confidence": 0.85},
        "indicators": {"chop": 20.0, "rsi": 50.0, "supports": [], "resistances": [100.4]},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 0
    assert engine._filter_block_counts.get("NANOFENIX", 0) == 1


@pytest.mark.asyncio
async def test_sr_filter_allows_buy_near_resistance_with_relaxed_technical_context_even_when_nanofenix_disallows():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=1.05, volume_imbalance=0.03, vpin_proxy=0.35)
    engine._nanofenix_companion_enabled = True
    engine._build_nanofenix_policy_payload = MagicMock(
        return_value={
            "allow_execute": False,
            "reason": "low_pred_bps",
            "signal": "BUY",
            "confidence": 0.58,
            "pred_bps": 1.0,
            "direction_accuracy": 0.60,
        }
    )

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "test",
        },
        "technical_report": {
            "signal": "BUY",
            "confidence_level": "MEDIUM",
            "confidence": 0.65,
            "resistance_level": 100.4,
            "risk_reward_ratio": 1.20,
        },
        "qabba_report": {"signal": "BUY", "confidence": 0.90},
        "indicators": {"chop": 20.0, "rsi": 50.0, "supports": [], "resistances": []},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 1


@pytest.mark.asyncio
async def test_sr_filter_blocks_buy_near_resistance_when_technical_extension_is_weak():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=2.0, volume_imbalance=0.3, vpin_proxy=0.2)
    engine._nanofenix_companion_enabled = True
    engine._build_nanofenix_policy_payload = MagicMock(
        return_value={
            "allow_execute": True,
            "reason": "ok",
            "signal": "BUY",
            "confidence": 0.74,
            "pred_bps": 2.5,
            "direction_accuracy": 0.60,
        }
    )

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "HIGH",
            "combined_reasoning": "test",
        },
        "technical_report": {
            "signal": "BUY",
            "confidence_level": "MEDIUM",
            "confidence": 0.62,
            "resistance_level": 100.4,
            "risk_reward_ratio": 1.2,
        },
        "qabba_report": {"signal": "BUY", "confidence": 0.90},
        "indicators": {"chop": 20.0, "rsi": 50.0, "supports": [], "resistances": []},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 0
    assert engine._filter_block_counts.get("TECHNICAL_EXTENSION", 0) == 1


@pytest.mark.asyncio
async def test_sr_filter_allows_buy_breakout_near_resistance_when_qabba_and_nanofenix_confirm():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=1.05, volume_imbalance=0.02, vpin_proxy=0.35)
    engine._nanofenix_companion_enabled = True
    engine._build_nanofenix_policy_payload = MagicMock(
        return_value={
            "companion_ready": True,
            "allow_execute": True,
            "reason": "ok",
            "signal": "BUY",
            "confidence": 0.78,
            "pred_bps": 3.1,
            "direction_accuracy": 0.66,
        }
    )

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "test",
        },
        "technical_report": {
            "signal": "HOLD",
            "confidence_level": "MEDIUM",
            "confidence": 0.65,
            "resistance_level": 100.4,
            "risk_reward_ratio": 1.2,
        },
        "qabba_report": {"signal": "BUY", "confidence": 0.90},
        "indicators": {"chop": 20.0, "rsi": 50.0, "supports": [], "resistances": []},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 1


@pytest.mark.asyncio
async def test_sr_filter_allows_buy_breakout_near_resistance_when_nanofenix_is_holding():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0, obi=1.08, volume_imbalance=0.05, vpin_proxy=0.30)
    engine._nanofenix_companion_enabled = True
    engine._build_nanofenix_policy_payload = MagicMock(
        return_value={
            "companion_ready": False,
            "allow_execute": False,
            "reason": "low_confidence,low_pred_bps,no_directional_signal",
            "signal": "HOLD",
            "action": "HOLD",
            "confidence": 0.0,
            "pred_bps": 0.0,
            "direction_accuracy": 0.65,
        }
    )

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "HIGH",
            "combined_reasoning": "test",
        },
        "technical_report": {
            "signal": "HOLD",
            "confidence_level": "MEDIUM",
            "confidence": 0.72,
            "resistance_level": 100.4,
            "risk_reward_ratio": 1.8,
        },
        "qabba_report": {"signal": "BUY", "confidence": 0.86},
        "indicators": {"chop": 20.0, "rsi": 52.0, "supports": [], "resistances": []},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 1


@pytest.mark.asyncio
async def test_buy_hold_consolidation_guard_blocks_flip_without_nanofenix_buy_confirmation():
    engine = TradingEngine(symbol="SOLUSDT", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=85.28, obi=1.35, volume_imbalance=0.2, vpin_proxy=0.18)
    engine._nanofenix_companion_enabled = True
    engine._buy_hold_consolidation_guard = True
    engine._build_nanofenix_policy_payload = MagicMock(
        return_value={
            "companion_ready": True,
            "allow_execute": True,
            "reason": "ok",
            "signal": "SHORT",
            "confidence": 0.635,
            "pred_bps": -3.3,
            "direction_accuracy": 0.548,
        }
    )

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "test",
        },
        "technical_report": {"signal": "HOLD", "confidence": 0.45},
        "qabba_report": {"signal": "BUY_QABBA", "confidence": 0.75},
        "indicators": {
            "bb_squeeze": True,
            "bandwidth_pct": 0.0167,
            "market_condition": "EXTREME_CONSOLIDATION",
            "chop": 20.0,
            "rsi": 50.0,
            "supports": [],
            "resistances": [85.92],
        },
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 0
    assert engine._filter_block_counts.get("NANOFENIX", 0) == 1


@pytest.mark.asyncio
async def test_buy_hold_consolidation_guard_allows_when_nanofenix_confirms_buy():
    engine = TradingEngine(symbol="SOLUSDT", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=85.28, obi=1.35, volume_imbalance=0.2, vpin_proxy=0.18)
    engine._nanofenix_companion_enabled = True
    engine._buy_hold_consolidation_guard = True
    engine._build_nanofenix_policy_payload = MagicMock(
        return_value={
            "companion_ready": True,
            "allow_execute": True,
            "reason": "ok",
            "signal": "BUY",
            "confidence": 0.635,
            "pred_bps": 3.3,
            "direction_accuracy": 0.61,
        }
    )

    engine._execute_trade = AsyncMock()

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "test",
        },
        "technical_report": {"signal": "HOLD", "confidence": 0.45},
        "qabba_report": {"signal": "BUY_QABBA", "confidence": 0.75},
        "indicators": {
            "bb_squeeze": True,
            "bandwidth_pct": 0.0167,
            "market_condition": "EXTREME_CONSOLIDATION",
            "chop": 20.0,
            "rsi": 50.0,
            "supports": [],
            "resistances": [90.0],
        },
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 1
