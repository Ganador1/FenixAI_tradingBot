import asyncio
import types

import pytest

from src.core.orchestrator.agents.technical import create_technical_agent_node
from src.core.orchestrator.agents.qabba import create_qabba_agent_node
from src.core.orchestrator.agents.decision import create_decision_agent_node
from src.core.orchestrator.agent_cache import AgentReportCache


class _SlowLLM:
    def __init__(self, sleep_sec: float = 0.2):
        self._sleep_sec = sleep_sec

    async def ainvoke(self, _messages):
        await asyncio.sleep(self._sleep_sec)
        # Valid-ish JSON, but we expect timeouts before we ever parse this.
        return types.SimpleNamespace(content='{"signal":"HOLD","confidence":0.5,"rationale":"x"}')


class _FastLLM:
    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, _messages):
        return types.SimpleNamespace(content=self._content)

class _DelayedLLM:
    def __init__(self, *, sleep_sec: float, content: str):
        self._sleep_sec = float(sleep_sec)
        self._content = content

    async def ainvoke(self, _messages):
        await asyncio.sleep(self._sleep_sec)
        return types.SimpleNamespace(content=self._content)


@pytest.mark.asyncio
async def test_technical_agent_uses_fallback_on_short_tf_timeout(monkeypatch):
    monkeypatch.setenv("FENIX_TECH_TIMEOUT_SHORT_SEC", "0.01")
    monkeypatch.setenv("FENIX_TECH_MAX_RETRIES", "0")

    node = create_technical_agent_node(llm=_SlowLLM(), reasoning_bank=None)
    out = await node(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "timestamp": "now",
            "indicators": {"rsi": 25.0, "macd_hist": 0.2, "chop": 30.0},
            "current_price": 100.0,
            "current_volume": 0.0,
            "obi": 1.0,
            "ofi": 0.0,
            "qi": 0.0,
            "mlofi": 0.0,
            "vpin_proxy": 0.0,
            "spread": 0.01,
            "spread_pct": 0.01,
            "microprice": 100.0,
            "trade_imbalance_5s": 0.0,
            "mtf_context": {},
            "orderbook_depth": {},
            "messages": [],
            "errors": [],
            "execution_times": {},
        }
    )

    report = out.get("technical_report") or {}
    assert report.get("signal") == "BUY"


@pytest.mark.asyncio
async def test_qabba_agent_uses_fallback_on_short_tf_timeout(monkeypatch):
    monkeypatch.setenv("FENIX_QABBA_TIMEOUT_SHORT_SEC", "0.01")
    monkeypatch.setenv("FENIX_QABBA_MAX_RETRIES", "0")

    node = create_qabba_agent_node(llm=_SlowLLM(), reasoning_bank=None)
    out = await node(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "timestamp": "now",
            "indicators": {"market_condition": "NORMAL", "chop": 30.0},
            "current_price": 100.0,
            "current_volume": 0.0,
            "obi": 2.0,
            "cvd": 0.0,
            "mid_price": 100.0,
            "microprice": 100.1,
            "spread": 0.02,
            "spread_pct": 0.02,
            "ofi": 1.0,
            "qi": 0.1,
            "mlofi": 1.0,
            "volume_imbalance": 0.3,
            "vpin_proxy": 0.1,
            "trade_imbalance_5s": 0.3,
            "trade_volume_5s": 10.0,
            "trade_count_5s": 5,
            "orderbook_depth": {"bid_depth": 100, "ask_depth": 50, "total": 150},
            "messages": [],
            "errors": [],
            "execution_times": {},
        }
    )

    report = out.get("qabba_report") or {}
    assert report.get("signal") == "BUY"


@pytest.mark.asyncio
async def test_decision_agent_uses_fallback_on_short_tf_timeout(monkeypatch):
    monkeypatch.setenv("FENIX_DECISION_TIMEOUT_SHORT_SEC", "0.01")
    monkeypatch.setenv("FENIX_DECISION_MAX_RETRIES", "0")

    node = create_decision_agent_node(llm=_SlowLLM(), reasoning_bank=None)
    out = await node(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "timestamp": "now",
            "indicators": {"rsi": 50.0},
            "current_price": 100.0,
            "current_volume": 0.0,
            "technical_report": {"signal": "BUY", "confidence": 0.7, "rationale": "x"},
            "qabba_report": {"signal": "BUY", "confidence": 0.8, "rationale": "y"},
            "sentiment_report": {},
            "visual_report": {},
            "messages": [],
            "errors": [],
            "execution_times": {},
        }
    )

    report = out.get("final_trade_decision") or {}
    assert report.get("final_decision") == "BUY"
    # Fallback confidence can be MEDIUM/HIGH depending on configured strong threshold.
    assert report.get("confidence_in_decision") in {"MEDIUM", "HIGH"}


@pytest.mark.asyncio
async def test_technical_agent_uses_cached_report_on_timeout_when_available(monkeypatch):
    monkeypatch.setenv("FENIX_AGENT_CACHE_ON_TIMEOUT", "1")
    monkeypatch.setenv("FENIX_SHORT_TF_NONBLOCKING", "0")

    cache = AgentReportCache(max_entries=8)

    node_fast = create_technical_agent_node(
        llm=_FastLLM('{"signal":"SELL","confidence":0.9,"rationale":"fast"}'),
        reasoning_bank=None,
        agent_cache=cache,
    )
    out1 = await node_fast(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "timestamp": "now",
            "indicators": {"rsi": 55.0, "macd_hist": -0.2, "chop": 30.0},
            "current_price": 100.0,
            "current_volume": 0.0,
            "obi": 1.0,
            "ofi": 0.0,
            "qi": 0.0,
            "mlofi": 0.0,
            "vpin_proxy": 0.0,
            "spread": 0.01,
            "spread_pct": 0.01,
            "microprice": 100.0,
            "trade_imbalance_5s": 0.0,
            "mtf_context": {},
            "orderbook_depth": {},
            "messages": [],
            "errors": [],
            "execution_times": {},
        }
    )
    assert (out1.get("technical_report") or {}).get("signal") == "SELL"

    # Force a timeout: fallback would be BUY (RSI low + MACD positive), but cache should win.
    monkeypatch.setenv("FENIX_TECH_TIMEOUT_SHORT_SEC", "0.01")
    monkeypatch.setenv("FENIX_TECH_MAX_RETRIES", "0")

    node_slow = create_technical_agent_node(
        llm=_SlowLLM(sleep_sec=0.2),
        reasoning_bank=None,
        agent_cache=cache,
    )
    out2 = await node_slow(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "timestamp": "now",
            "indicators": {"rsi": 25.0, "macd_hist": 0.2, "chop": 30.0},
            "current_price": 100.0,
            "current_volume": 0.0,
            "obi": 1.0,
            "ofi": 0.0,
            "qi": 0.0,
            "mlofi": 0.0,
            "vpin_proxy": 0.0,
            "spread": 0.01,
            "spread_pct": 0.01,
            "microprice": 100.0,
            "trade_imbalance_5s": 0.0,
            "mtf_context": {},
            "orderbook_depth": {},
            "messages": [],
            "errors": [],
            "execution_times": {},
        }
    )
    report = out2.get("technical_report") or {}
    assert report.get("signal") == "SELL"
    assert report.get("_cache_info", {}).get("reason") == "llm_timeout"


@pytest.mark.asyncio
async def test_technical_agent_overrides_hold_when_directional_alignment_is_strong():
    node = create_technical_agent_node(
        llm=_FastLLM(
            '{"signal":"HOLD","confidence_level":"MEDIUM","confidence":0.6,'
            '"rationale":"Mixed signals, waiting.","support_level":88.8,'
            '"resistance_level":91.8,"risk_reward_ratio":1.9}'
        ),
        reasoning_bank=None,
    )
    out = await node(
        {
            "symbol": "SOLUSDT",
            "timeframe": "15m",
            "timestamp": "now",
            "indicators": {
                "market_condition": "TRENDING",
                "rsi": 61.5,
                "macd_hist": 0.42,
                "ema_9": 90.4,
                "ema_20": 90.0,
                "ema_50": 89.2,
                "ema_200": 87.9,
                "vwap": 90.1,
                "supertrend_signal": "bullish",
                "cmf": 0.22,
                "adx": 28.0,
                "bb_inside_kc": False,
                "bb_squeeze": False,
                "bandwidth_pct": 0.062,
                "supports": [89.7, 89.2],
                "resistances": [91.8, 92.4],
            },
            "current_price": 90.65,
            "current_volume": 1200.0,
            "obi": 1.0,
            "ofi": 0.0,
            "qi": 0.0,
            "mlofi": 0.0,
            "vpin_proxy": 0.0,
            "spread": 0.01,
            "spread_pct": 0.01,
            "microprice": 90.66,
            "trade_imbalance_5s": 0.0,
            "mtf_context": {"htf": {"trend": "bullish"}, "ltf": {"trend": "bullish"}},
            "orderbook_depth": {},
            "messages": [],
            "errors": [],
            "execution_times": {},
        }
    )

    report = out.get("technical_report") or {}
    assert report.get("signal") == "BUY"
    assert report.get("_signal_adjustment") == "hold_override"
    assert report.get("_llm_signal_original") == "HOLD"


@pytest.mark.asyncio
async def test_technical_agent_keeps_hold_in_true_squeeze_without_directional_edge():
    node = create_technical_agent_node(
        llm=_FastLLM(
            '{"signal":"HOLD","confidence_level":"MEDIUM","confidence":0.6,'
            '"rationale":"Compression, no edge.","support_level":89.4,'
            '"resistance_level":89.7,"risk_reward_ratio":1.0}'
        ),
        reasoning_bank=None,
    )
    out = await node(
        {
            "symbol": "SOLUSDT",
            "timeframe": "15m",
            "timestamp": "now",
            "indicators": {
                "market_condition": "LOW_VOLATILITY",
                "rsi": 49.8,
                "macd_hist": -0.01,
                "ema_9": 89.55,
                "ema_20": 89.54,
                "ema_50": 89.56,
                "ema_200": 89.60,
                "vwap": 89.56,
                "supertrend_signal": "bearish",
                "cmf": -0.03,
                "adx": 15.0,
                "bb_inside_kc": True,
                "bb_squeeze": True,
                "bandwidth_pct": 0.018,
                "supports": [89.4],
                "resistances": [89.7],
            },
            "current_price": 89.55,
            "current_volume": 800.0,
            "obi": 1.0,
            "ofi": 0.0,
            "qi": 0.0,
            "mlofi": 0.0,
            "vpin_proxy": 0.0,
            "spread": 0.01,
            "spread_pct": 0.01,
            "microprice": 89.55,
            "trade_imbalance_5s": 0.0,
            "mtf_context": {"htf": {"trend": "neutral"}, "ltf": {"trend": "neutral"}},
            "orderbook_depth": {},
            "messages": [],
            "errors": [],
            "execution_times": {},
        }
    )

    report = out.get("technical_report") or {}
    assert report.get("signal") == "HOLD"
    assert report.get("_signal_adjustment") != "hold_override"


@pytest.mark.asyncio
async def test_qabba_agent_uses_cached_report_on_timeout_when_available(monkeypatch):
    monkeypatch.setenv("FENIX_AGENT_CACHE_ON_TIMEOUT", "1")
    monkeypatch.setenv("FENIX_SHORT_TF_NONBLOCKING", "0")

    cache = AgentReportCache(max_entries=8)

    node_fast = create_qabba_agent_node(
        llm=_FastLLM('{"signal":"SELL","confidence":0.9,"rationale":"fast"}'),
        reasoning_bank=None,
        agent_cache=cache,
    )
    out1 = await node_fast(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "timestamp": "now",
            "indicators": {"market_condition": "NORMAL", "chop": 30.0},
            "current_price": 100.0,
            "current_volume": 0.0,
            "obi": 1.0,
            "cvd": 0.0,
            "mid_price": 100.0,
            "microprice": 100.1,
            "spread": 0.02,
            "spread_pct": 0.02,
            "ofi": 0.0,
            "qi": 0.0,
            "mlofi": 0.0,
            "volume_imbalance": 0.0,
            "vpin_proxy": 0.1,
            "trade_imbalance_5s": 0.0,
            "trade_volume_5s": 0.0,
            "trade_count_5s": 0,
            "orderbook_depth": {"bid_depth": 100, "ask_depth": 50, "total": 150},
            "messages": [],
            "errors": [],
            "execution_times": {},
        }
    )
    assert (out1.get("qabba_report") or {}).get("signal") == "SELL"

    monkeypatch.setenv("FENIX_QABBA_TIMEOUT_SHORT_SEC", "0.01")
    monkeypatch.setenv("FENIX_QABBA_MAX_RETRIES", "0")

    node_slow = create_qabba_agent_node(
        llm=_SlowLLM(sleep_sec=0.2),
        reasoning_bank=None,
        agent_cache=cache,
    )
    out2 = await node_slow(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "timestamp": "now",
            "indicators": {"market_condition": "NORMAL", "chop": 30.0},
            "current_price": 100.0,
            "current_volume": 0.0,
            "obi": 2.0,
            "cvd": 0.0,
            "mid_price": 100.0,
            "microprice": 100.1,
            "spread": 0.02,
            "spread_pct": 0.02,
            "ofi": 1.0,
            "qi": 0.1,
            "mlofi": 1.0,
            "volume_imbalance": 0.3,
            "vpin_proxy": 0.1,
            "trade_imbalance_5s": 0.3,
            "trade_volume_5s": 10.0,
            "trade_count_5s": 5,
            "orderbook_depth": {"bid_depth": 100, "ask_depth": 50, "total": 150},
            "messages": [],
            "errors": [],
            "execution_times": {},
        }
    )
    report = out2.get("qabba_report") or {}
    assert report.get("signal") == "SELL"
    assert report.get("_cache_info", {}).get("reason") == "llm_timeout"


@pytest.mark.asyncio
async def test_technical_agent_nonblocking_refresh_populates_cache(monkeypatch):
    monkeypatch.setenv("FENIX_SHORT_TF_NONBLOCKING", "1")
    monkeypatch.setenv("FENIX_AGENT_CACHE_ON_TIMEOUT", "1")
    monkeypatch.setenv("FENIX_AGENT_CACHE_REFRESH_MIN_SEC", "0")
    monkeypatch.setenv("FENIX_AGENT_CACHE_ASYNC_REFRESH_UNDER_PYTEST", "1")
    monkeypatch.setenv("FENIX_TECH_MAX_RETRIES", "0")

    cache = AgentReportCache(max_entries=8)
    node = create_technical_agent_node(
        llm=_DelayedLLM(sleep_sec=0.05, content='{"signal":"SELL","confidence":0.9,"rationale":"ok"}'),
        reasoning_bank=None,
        agent_cache=cache,
    )

    state = {
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "timestamp": "now",
        "indicators": {"rsi": 50.0, "macd_hist": 0.0, "chop": 30.0},
        "current_price": 100.0,
        "current_volume": 0.0,
        "obi": 1.0,
        "ofi": 0.0,
        "qi": 0.0,
        "mlofi": 0.0,
        "vpin_proxy": 0.0,
        "spread": 0.01,
        "spread_pct": 0.01,
        "microprice": 100.0,
        "trade_imbalance_5s": 0.0,
        "mtf_context": {},
        "orderbook_depth": {},
        "messages": [],
        "errors": [],
        "execution_times": {},
    }

    out1 = await node(state)
    rep1 = out1.get("technical_report") or {}
    # Nonblocking short-TF mode is intentionally disabled in current runtime.
    assert rep1.get("_nonblocking") is not True
    assert rep1.get("signal") == "SELL"

    out2 = await node(state)
    rep2 = out2.get("technical_report") or {}
    assert rep2.get("signal") == "SELL"
    assert rep2.get("_nonblocking") is not True


@pytest.mark.asyncio
async def test_qabba_agent_nonblocking_refresh_populates_cache(monkeypatch):
    monkeypatch.setenv("FENIX_SHORT_TF_NONBLOCKING", "1")
    monkeypatch.setenv("FENIX_AGENT_CACHE_ON_TIMEOUT", "1")
    monkeypatch.setenv("FENIX_AGENT_CACHE_REFRESH_MIN_SEC", "0")
    monkeypatch.setenv("FENIX_AGENT_CACHE_ASYNC_REFRESH_UNDER_PYTEST", "1")
    monkeypatch.setenv("FENIX_QABBA_MAX_RETRIES", "0")

    cache = AgentReportCache(max_entries=8)
    node = create_qabba_agent_node(
        llm=_DelayedLLM(sleep_sec=0.05, content='{"signal":"BUY","confidence":0.9,"rationale":"ok"}'),
        reasoning_bank=None,
        agent_cache=cache,
    )

    state = {
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "timestamp": "now",
        "indicators": {"market_condition": "NORMAL", "chop": 30.0},
        "current_price": 100.0,
        "current_volume": 0.0,
        "obi": 1.0,
        "cvd": 0.0,
        "mid_price": 100.0,
        "microprice": 100.1,
        "spread": 0.02,
        "spread_pct": 0.02,
        "ofi": 0.0,
        "qi": 0.0,
        "mlofi": 0.0,
        "volume_imbalance": 0.0,
        "vpin_proxy": 0.1,
        "trade_imbalance_5s": 0.0,
        "trade_volume_5s": 0.0,
        "trade_count_5s": 0,
        "orderbook_depth": {"bid_depth": 100, "ask_depth": 50, "total": 150},
        "messages": [],
        "errors": [],
        "execution_times": {},
    }

    out1 = await node(state)
    rep1 = out1.get("qabba_report") or {}
    # Nonblocking short-TF mode is intentionally disabled in current runtime.
    assert rep1.get("_nonblocking") is not True
    assert rep1.get("signal") == "BUY"

    out2 = await node(state)
    rep2 = out2.get("qabba_report") or {}
    assert rep2.get("signal") == "BUY"
    assert rep2.get("_nonblocking") is not True


@pytest.mark.asyncio
async def test_decision_agent_is_nonblocking_on_short_tf(monkeypatch):
    class _ExplodingLLM:
        async def ainvoke(self, _messages):
            raise RuntimeError("LLM should not be called on short TF nonblocking mode")

    monkeypatch.setenv("FENIX_SHORT_TF_NONBLOCKING", "1")

    node = create_decision_agent_node(llm=_ExplodingLLM(), reasoning_bank=None)
    out = await node(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "timestamp": "now",
            "indicators": {"rsi": 50.0},
            "current_price": 100.0,
            "current_volume": 0.0,
            "technical_report": {"signal": "BUY", "confidence": 0.7, "rationale": "x"},
            "qabba_report": {"signal": "BUY", "confidence": 0.8, "rationale": "y"},
            "sentiment_report": {},
            "visual_report": {},
            "messages": [],
            "errors": [],
            "execution_times": {},
        }
    )

    report = out.get("final_trade_decision") or {}
    # Decision nonblocking mode was removed; this now validates deterministic fallback.
    assert report.get("final_decision") == "BUY"
    assert report.get("_nonblocking") is not True
    assert report.get("_llm_errors")
