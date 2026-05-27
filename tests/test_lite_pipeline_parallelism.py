import asyncio
import time
from types import SimpleNamespace

import pytest


def _micro_snapshot() -> SimpleNamespace:
    return SimpleNamespace(
        obi=1.5,
        cvd=0.0,
        spread=0.01,
        spread_pct=0.01,
        ofi=0.0,
        qi=0.0,
        mlofi=0.0,
        volume_imbalance=0.0,
        wdi=0.2,
        liquidity_gap_pct=0.01,
        vpin_proxy=0.1,
        trade_imbalance_5s=0.2,
        trade_volume_5s=0.0,
        trade_count_5s=0,
        trade_buy_vol_5s=0.0,
        trade_sell_vol_5s=0.0,
        cvd_delta_5s=0.0,
        recent_trades_5s=[],
        trade_intensity_5s=0.0,
        avg_trade_size_5s=0.0,
        bid_depth=0.0,
        ask_depth=0.0,
        liquidity=0.0,
        mid_price=100.0,
        microprice=100.0,
        microprice_bps=0.0,
    )


@pytest.fixture(autouse=True)
def _clear_lite_pipeline_env(monkeypatch):
    for key in (
        "FENIX_LITE_CONSENSUS_MODE",
        "FENIX_LITE_NODE_TIMEOUT_SEC",
        "FENIX_LITE_ALLOW_MTF_QABBA_WHEN_TECH_HOLD",
        "FENIX_LITE_MTF_CONFIRM_CONF",
        "FENIX_LITE_MTF_QABBA_MIN_CONF",
        "FENIX_LITE_QABBA_OPPOSE_CONF",
        "FENIX_STRICT_MTF_BIAS_TIMEFRAME",
        "FENIX_STRICT_MTF_OPPOSING_VETO_CONF",
        "FENIX_STRICT_MTF_BIAS_CACHE_SEC",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.asyncio
async def test_lite_pipeline_runs_technical_and_qabba_in_parallel(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_LITE_PIPELINE", "1")

    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="15m",
        use_testnet=True,
        paper_trading=True,
        enable_visual_agent=False,
        enable_sentiment_agent=False,
        allow_live_trading=False,
    )

    # Minimal runtime state for the method.
    engine.market_data = SimpleNamespace(current_price=100.0, current_volume=0.0)

    async def tech_node(_state):
        await asyncio.sleep(0.25)
        return {
            "technical_report": {"signal": "BUY", "confidence": 0.9, "rationale": "t"},
            "execution_times": {"technical": 0.25},
        }

    async def qabba_node(_state):
        await asyncio.sleep(0.25)
        return {
            "qabba_report": {"signal": "BUY", "confidence": 0.9, "rationale": "q"},
            "execution_times": {"qabba": 0.25},
        }

    engine._lite_nodes = {"technical": tech_node, "qabba": qabba_node}

    t0 = time.perf_counter()
    result = await engine._execute_lite_analysis(indicators={"atr": 0.5}, micro=_micro_snapshot())
    elapsed = time.perf_counter() - t0

    # If nodes were serial, we'd be closer to 0.50s. Parallel should be ~0.25s (+ small overhead).
    assert elapsed < 0.40

    assert result["technical_report"]["signal"] == "BUY"
    assert result["qabba_report"]["signal"] == "BUY"
    assert result["final_trade_decision"]["final_decision"] == "BUY"
    assert result["final_trade_decision"].get("_scripted") is True
    assert "risk_assessment" in result


@pytest.mark.asyncio
async def test_lite_pipeline_passes_trade_tape_and_volume_fallback(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_LITE_PIPELINE", "1")

    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="15m",
        use_testnet=True,
        paper_trading=True,
        enable_visual_agent=False,
        enable_sentiment_agent=False,
        allow_live_trading=False,
    )
    engine.market_data = SimpleNamespace(current_price=100.0, current_volume=0.0)

    captured = {}
    micro = _micro_snapshot()
    micro.trade_count_5s = 2
    micro.trade_volume_5s = 3.5
    micro.trade_buy_vol_5s = 2.0
    micro.trade_sell_vol_5s = 1.5
    micro.cvd_delta_5s = 0.5
    micro.trade_intensity_5s = 0.4
    micro.avg_trade_size_5s = 1.75
    micro.recent_trades_5s = [
        {"side": "buy", "qty": 2.0, "price": 100.01, "age_sec": 0.5},
        {"side": "sell", "qty": 1.5, "price": 100.0, "age_sec": 1.0},
    ]

    async def tech_node(state):
        captured["technical"] = state
        return {"technical_report": {"signal": "HOLD", "confidence": 0.5}}

    async def qabba_node(state):
        captured["qabba"] = state
        return {"qabba_report": {"signal": "HOLD", "confidence": 0.5}}

    engine._lite_nodes = {"technical": tech_node, "qabba": qabba_node}

    await engine._execute_lite_analysis(indicators={"atr": 0.5, "curr_vol": 12345.0}, micro=micro)

    assert captured["technical"]["current_volume"] == 12345.0
    assert captured["qabba"]["trade_count_5s"] == 2
    assert captured["qabba"]["trade_volume_5s"] == 3.5
    assert captured["qabba"]["cvd_delta_5s"] == 0.5
    assert captured["qabba"]["recent_trades_5s"] == micro.recent_trades_5s


@pytest.mark.asyncio
async def test_lite_pipeline_strict_consensus_buys_only_when_technical_and_qabba_align(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_LITE_PIPELINE", "1")
    monkeypatch.setenv("FENIX_LITE_CONSENSUS_MODE", "strict_tech_qabba")

    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="15m",
        use_testnet=True,
        paper_trading=True,
        enable_visual_agent=False,
        enable_sentiment_agent=False,
        allow_live_trading=False,
    )
    engine.market_data = SimpleNamespace(current_price=100.0, current_volume=0.0)

    async def tech_node(_state):
        return {
            "technical_report": {"signal": "BUY", "confidence": 0.82, "rationale": "t"},
            "execution_times": {"technical": 0.01},
        }

    async def qabba_node(_state):
        return {
            "qabba_report": {"signal": "BUY", "confidence": 0.78, "rationale": "q"},
            "execution_times": {"qabba": 0.01},
        }

    engine._lite_nodes = {"technical": tech_node, "qabba": qabba_node}

    result = await engine._execute_lite_analysis(indicators={"atr": 0.5}, micro=_micro_snapshot())

    decision = result["final_trade_decision"]
    assert decision["final_decision"] == "BUY"
    assert decision["confidence_in_decision"] == "MEDIUM"
    assert decision["_scripted"] is True
    assert decision["_scripted_mode"] == "strict_tech_qabba"
    assert decision["_directional_score"] == pytest.approx(0.80 * 1.15)
    assert decision["key_conflicting_signals"] == []


@pytest.mark.asyncio
async def test_lite_pipeline_strict_consensus_holds_when_technical_and_qabba_disagree(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_LITE_PIPELINE", "1")
    monkeypatch.setenv("FENIX_LITE_CONSENSUS_MODE", "strict_tech_qabba")

    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="15m",
        use_testnet=True,
        paper_trading=True,
        enable_visual_agent=False,
        enable_sentiment_agent=False,
        allow_live_trading=False,
    )
    engine.market_data = SimpleNamespace(current_price=100.0, current_volume=0.0)

    async def tech_node(_state):
        return {
            "technical_report": {"signal": "BUY", "confidence": 0.90, "rationale": "t"},
            "execution_times": {"technical": 0.01},
        }

    async def qabba_node(_state):
        return {
            "qabba_report": {"signal": "SELL", "confidence": 0.88, "rationale": "q"},
            "execution_times": {"qabba": 0.01},
        }

    engine._lite_nodes = {"technical": tech_node, "qabba": qabba_node}

    result = await engine._execute_lite_analysis(indicators={"atr": 0.5}, micro=_micro_snapshot())

    decision = result["final_trade_decision"]
    assert decision["final_decision"] == "HOLD"
    assert decision["_scripted_mode"] == "strict_tech_qabba"
    assert decision["_directional_score"] == 0.0
    assert decision["key_conflicting_signals"] == [
        "Strict consensus requires agreement: tech=BUY, qabba=SELL"
    ]


@pytest.mark.asyncio
async def test_lite_pipeline_strict_consensus_respects_higher_tf_bias_veto(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_LITE_PIPELINE", "1")
    monkeypatch.setenv("FENIX_LITE_CONSENSUS_MODE", "strict_tech_qabba")
    monkeypatch.setenv("FENIX_STRICT_MTF_BIAS_TIMEFRAME", "30m")
    monkeypatch.setenv("FENIX_STRICT_MTF_OPPOSING_VETO_CONF", "0.75")

    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="15m",
        use_testnet=True,
        paper_trading=True,
        enable_visual_agent=False,
        enable_sentiment_agent=False,
        allow_live_trading=False,
    )
    engine.market_data = SimpleNamespace(current_price=100.0, current_volume=0.0)

    async def tech_node(_state):
        return {
            "technical_report": {"signal": "BUY", "confidence": 0.82, "rationale": "t"},
            "execution_times": {"technical": 0.01},
        }

    async def qabba_node(_state):
        return {
            "qabba_report": {"signal": "BUY", "confidence": 0.78, "rationale": "q"},
            "execution_times": {"qabba": 0.01},
        }

    async def bias_ctx():
        return {"timeframe": "30m", "signal": "SELL", "confidence": 0.8}

    engine._lite_nodes = {"technical": tech_node, "qabba": qabba_node}
    engine._get_strict_mtf_bias_context = bias_ctx

    result = await engine._execute_lite_analysis(indicators={"atr": 0.5}, micro=_micro_snapshot())

    decision = result["final_trade_decision"]
    assert decision["final_decision"] == "HOLD"
    assert decision["confidence_in_decision"] == "LOW"
    assert decision["_mtf_bias"] == {"timeframe": "30m", "signal": "SELL", "confidence": 0.8}
    assert (
        "Strict MTF bias veto: 30m bias=SELL(0.80) opposes entry=BUY"
        in decision["key_conflicting_signals"]
    )


@pytest.mark.asyncio
async def test_lite_pipeline_strict_consensus_allows_entry_when_opposing_higher_tf_bias_is_soft(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_LITE_PIPELINE", "1")
    monkeypatch.setenv("FENIX_LITE_CONSENSUS_MODE", "strict_tech_qabba")
    monkeypatch.setenv("FENIX_STRICT_MTF_BIAS_TIMEFRAME", "30m")
    monkeypatch.setenv("FENIX_STRICT_MTF_OPPOSING_VETO_CONF", "0.90")

    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="15m",
        use_testnet=True,
        paper_trading=True,
        enable_visual_agent=False,
        enable_sentiment_agent=False,
        allow_live_trading=False,
    )
    engine.market_data = SimpleNamespace(current_price=100.0, current_volume=0.0)

    async def tech_node(_state):
        return {
            "technical_report": {"signal": "SELL", "confidence": 0.74, "rationale": "t"},
            "execution_times": {"technical": 0.01},
        }

    async def qabba_node(_state):
        return {
            "qabba_report": {"signal": "SELL", "confidence": 0.71, "rationale": "q"},
            "execution_times": {"qabba": 0.01},
        }

    async def bias_ctx():
        return {"timeframe": "30m", "signal": "BUY", "confidence": 0.8}

    engine._lite_nodes = {"technical": tech_node, "qabba": qabba_node}
    engine._get_strict_mtf_bias_context = bias_ctx

    result = await engine._execute_lite_analysis(indicators={"atr": 0.5}, micro=_micro_snapshot())

    decision = result["final_trade_decision"]
    assert decision["final_decision"] == "SELL"
    assert decision["confidence_in_decision"] == "MEDIUM"
    assert decision["_mtf_bias"] == {"timeframe": "30m", "signal": "BUY", "confidence": 0.8}
    assert decision["key_conflicting_signals"] == []


@pytest.mark.asyncio
async def test_lite_pipeline_technical_mtf_guard_allows_qabba_hold(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_LITE_PIPELINE", "1")
    monkeypatch.setenv("FENIX_LITE_CONSENSUS_MODE", "technical_mtf_qabba_guard")
    monkeypatch.setenv("FENIX_LITE_MTF_CONFIRM_CONF", "0.55")

    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="15m",
        use_testnet=True,
        paper_trading=True,
        enable_visual_agent=False,
        enable_sentiment_agent=False,
        allow_live_trading=False,
    )
    engine.market_data = SimpleNamespace(current_price=100.0, current_volume=0.0)

    async def tech_node(_state):
        return {
            "technical_report": {"signal": "BUY", "confidence": 0.82, "rationale": "t"},
            "execution_times": {"technical": 0.01},
        }

    async def qabba_node(_state):
        return {
            "qabba_report": {"signal": "HOLD", "confidence": 0.64, "rationale": "q"},
            "execution_times": {"qabba": 0.01},
        }

    async def bias_ctx():
        return {"timeframe": "30m", "signal": "BUY", "confidence": 0.61}

    engine._lite_nodes = {"technical": tech_node, "qabba": qabba_node}
    engine._get_strict_mtf_bias_context = bias_ctx

    result = await engine._execute_lite_analysis(indicators={"atr": 0.5}, micro=_micro_snapshot())

    decision = result["final_trade_decision"]
    assert decision["final_decision"] == "BUY"
    assert decision["confidence_in_decision"] == "MEDIUM"
    assert decision["_scripted_mode"] == "technical_mtf_qabba_guard"
    assert decision["_directional_score"] == pytest.approx(((0.82 * 0.65) + (0.61 * 0.35)) * 1.10)
    assert decision["key_conflicting_signals"] == []


@pytest.mark.asyncio
async def test_lite_pipeline_technical_mtf_guard_blocks_strong_opposing_qabba(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_LITE_PIPELINE", "1")
    monkeypatch.setenv("FENIX_LITE_CONSENSUS_MODE", "technical_mtf_qabba_guard")
    monkeypatch.setenv("FENIX_LITE_QABBA_OPPOSE_CONF", "0.72")

    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="15m",
        use_testnet=True,
        paper_trading=True,
        enable_visual_agent=False,
        enable_sentiment_agent=False,
        allow_live_trading=False,
    )
    engine.market_data = SimpleNamespace(current_price=100.0, current_volume=0.0)

    async def tech_node(_state):
        return {
            "technical_report": {"signal": "BUY", "confidence": 0.86, "rationale": "t"},
            "execution_times": {"technical": 0.01},
        }

    async def qabba_node(_state):
        return {
            "qabba_report": {"signal": "SELL", "confidence": 0.80, "rationale": "q"},
            "execution_times": {"qabba": 0.01},
        }

    async def bias_ctx():
        return {"timeframe": "30m", "signal": "BUY", "confidence": 0.62}

    engine._lite_nodes = {"technical": tech_node, "qabba": qabba_node}
    engine._get_strict_mtf_bias_context = bias_ctx

    result = await engine._execute_lite_analysis(indicators={"atr": 0.5}, micro=_micro_snapshot())

    decision = result["final_trade_decision"]
    assert decision["final_decision"] == "HOLD"
    assert decision["_scripted_mode"] == "technical_mtf_qabba_guard"
    assert decision["_directional_score"] == 0.0
    assert decision["key_conflicting_signals"] == [
        "QABBA guard blocks opposing strong signal: tech=BUY, mtf=BUY(0.62), qabba=SELL(0.80)"
    ]


@pytest.mark.asyncio
async def test_lite_pipeline_mtf_qabba_can_enter_when_technical_holds(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_LITE_PIPELINE", "1")
    monkeypatch.setenv("FENIX_LITE_CONSENSUS_MODE", "technical_mtf_qabba_guard")
    monkeypatch.setenv("FENIX_LITE_ALLOW_MTF_QABBA_WHEN_TECH_HOLD", "1")
    monkeypatch.setenv("FENIX_LITE_MTF_CONFIRM_CONF", "0.55")
    monkeypatch.setenv("FENIX_LITE_MTF_QABBA_MIN_CONF", "0.70")

    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="15m",
        use_testnet=True,
        paper_trading=True,
        enable_visual_agent=False,
        enable_sentiment_agent=False,
        allow_live_trading=False,
    )
    engine.market_data = SimpleNamespace(current_price=100.0, current_volume=0.0)

    async def tech_node(_state):
        return {
            "technical_report": {"signal": "HOLD", "confidence": 0.55, "rationale": "neutral"},
            "execution_times": {"technical": 0.01},
        }

    async def qabba_node(_state):
        return {
            "qabba_report": {"signal": "BUY", "confidence": 0.95, "rationale": "q"},
            "execution_times": {"qabba": 0.01},
        }

    async def bias_ctx():
        return {"timeframe": "30m", "signal": "BUY", "confidence": 0.80}

    engine._lite_nodes = {"technical": tech_node, "qabba": qabba_node}
    engine._get_strict_mtf_bias_context = bias_ctx

    result = await engine._execute_lite_analysis(indicators={"atr": 0.5}, micro=_micro_snapshot())

    decision = result["final_trade_decision"]
    assert decision["final_decision"] == "BUY"
    assert decision["confidence_in_decision"] == "MEDIUM"
    assert decision["_directional_score"] == pytest.approx(((0.95 * 0.55) + (0.80 * 0.45)) * 1.10)
    assert decision["key_conflicting_signals"] == []


@pytest.mark.asyncio
async def test_lite_pipeline_technical_mtf_guard_reports_missing_mtf_configuration(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_LITE_PIPELINE", "1")
    monkeypatch.setenv("FENIX_LITE_CONSENSUS_MODE", "technical_mtf_qabba_guard")
    monkeypatch.delenv("FENIX_STRICT_MTF_BIAS_TIMEFRAME", raising=False)

    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="15m",
        use_testnet=True,
        paper_trading=True,
        enable_visual_agent=False,
        enable_sentiment_agent=False,
        allow_live_trading=False,
    )
    engine.market_data = SimpleNamespace(current_price=100.0, current_volume=0.0)

    async def tech_node(_state):
        return {
            "technical_report": {"signal": "BUY", "confidence": 0.84, "rationale": "t"},
            "execution_times": {"technical": 0.01},
        }

    async def qabba_node(_state):
        return {
            "qabba_report": {"signal": "HOLD", "confidence": 0.60, "rationale": "q"},
            "execution_times": {"qabba": 0.01},
        }

    engine._lite_nodes = {"technical": tech_node, "qabba": qabba_node}

    result = await engine._execute_lite_analysis(indicators={"atr": 0.5}, micro=_micro_snapshot())

    decision = result["final_trade_decision"]
    assert decision["final_decision"] == "HOLD"
    assert decision["_mtf_bias"]["reason"] == "strict_mtf_bias_timeframe_unset"
    assert "Strict MTF bias unavailable: strict_mtf_bias_timeframe_unset" in decision[
        "key_conflicting_signals"
    ]


@pytest.mark.asyncio
async def test_lite_pipeline_times_out_slow_node_and_continues(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_LITE_PIPELINE", "1")
    monkeypatch.setenv("FENIX_LITE_CONSENSUS_MODE", "technical_mtf_qabba_guard")
    monkeypatch.setenv("FENIX_LITE_NODE_TIMEOUT_SEC", "0.05")
    monkeypatch.setenv("FENIX_LITE_ALLOW_MTF_QABBA_WHEN_TECH_HOLD", "1")

    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="15m",
        use_testnet=True,
        paper_trading=True,
        enable_visual_agent=False,
        enable_sentiment_agent=False,
        allow_live_trading=False,
    )
    engine.market_data = SimpleNamespace(current_price=100.0, current_volume=0.0)

    async def tech_node(_state):
        await asyncio.sleep(1.0)
        return {
            "technical_report": {"signal": "BUY", "confidence": 0.9, "rationale": "late"},
            "execution_times": {"technical": 1.0},
        }

    async def qabba_node(_state):
        return {
            "qabba_report": {"signal": "BUY", "confidence": 0.80, "rationale": "q"},
            "execution_times": {"qabba": 0.01},
        }

    async def bias_ctx():
        return {"timeframe": "30m", "signal": "BUY", "confidence": 0.61}

    engine._lite_nodes = {"technical": tech_node, "qabba": qabba_node}
    engine._get_strict_mtf_bias_context = bias_ctx

    result = await engine._execute_lite_analysis(indicators={"atr": 0.5}, micro=_micro_snapshot())

    assert result["technical_report"]["signal"] == "HOLD"
    assert result["technical_report"]["error"] == "technical_lite_node_timeout"
    assert "technical_lite_node_timeout" in result["errors"]
    assert result["qabba_report"]["signal"] == "BUY"
    assert result["final_trade_decision"]["final_decision"] == "HOLD"


@pytest.mark.asyncio
async def test_engine_builds_strict_mtf_bias_context_from_higher_timeframe_klines(monkeypatch):
    from src.trading.engine import TradingEngine

    monkeypatch.setenv("FENIX_STRICT_MTF_BIAS_TIMEFRAME", "30m")

    class FakeBinanceClient:
        def __init__(self, testnet):
            self.testnet = testnet
            self.closed = False

        async def connect(self):
            return True

        async def get_klines(self, symbol, interval, limit=120):
            assert symbol == "BTCUSDT"
            assert interval == "30m"
            assert limit >= 80
            rows = []
            for i in range(120):
                close = 100.0 + i * 0.2
                rows.append(
                    {
                        "timestamp": i,
                        "open": close - 0.05,
                        "high": close + 0.1,
                        "low": close - 0.1,
                        "close": close,
                        "volume": 1000.0,
                    }
                )
            return rows

        async def close(self):
            self.closed = True

    monkeypatch.setattr("src.trading.engine.BinanceClient", FakeBinanceClient)

    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="15m",
        use_testnet=False,
        paper_trading=False,
        enable_visual_agent=False,
        enable_sentiment_agent=False,
        allow_live_trading=True,
    )

    bias = await engine._get_strict_mtf_bias_context()

    assert bias["timeframe"] == "30m"
    assert bias["signal"] == "BUY"
    assert bias["confidence"] >= 0.75
    assert bias["source"] == "deterministic_ema_momentum"
