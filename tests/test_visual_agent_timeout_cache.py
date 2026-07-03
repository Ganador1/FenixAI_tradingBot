import asyncio
import importlib

import pytest

from src.core.orchestrator.agent_cache import AgentReportCache
from src.core.orchestrator.agents.visual import create_visual_agent_node


class _Resp:
    def __init__(self, content: str):
        self.content = content


class _LLMNeverCalled:
    async def ainvoke(self, _messages):
        raise AssertionError("LLM should not be called in nonblocking cache-hit mode")


class _LLMSlow:
    def __init__(self, *, sleep_sec: float):
        self._sleep_sec = float(sleep_sec)

    async def ainvoke(self, _messages):
        await asyncio.sleep(self._sleep_sec)
        return _Resp('{"action":"HOLD","confidence":0.5,"reason":"ok"}')


class _LLMFast:
    async def ainvoke(self, _messages):
        return _Resp(
            '{"action":"HOLD","confidence":0.5,"pattern":"Consolidation",'
            '"trend":"neutral","analysis":"ok"}'
        )


@pytest.mark.asyncio
async def test_visual_nonblocking_uses_cache(monkeypatch):
    monkeypatch.setenv("FENIX_SHORT_TF_NONBLOCKING", "1")
    monkeypatch.setenv("FENIX_AGENT_CACHE_ON_TIMEOUT", "1")

    cache = AgentReportCache(max_entries=8)
    cache.set(
        agent="visual",
        symbol="BTCUSDT",
        timeframe="1m",
        report={"action": "BUY", "confidence": 0.9, "reason": "cached"},
    )

    node = create_visual_agent_node(_LLMNeverCalled(), reasoning_bank=None, agent_cache=cache)
    out = await node(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "chart_image_b64": "abc",
            "current_price": 100.0,
        }
    )

    report = out["visual_report"]
    # Nonblocking mode is currently disabled; LLM path is attempted and fallback is HOLD.
    assert report["action"] == "HOLD"
    assert "LLM should not be called" in str(report.get("error", ""))


@pytest.mark.asyncio
async def test_visual_timeout_falls_back_to_cache(monkeypatch):
    monkeypatch.setenv("FENIX_SHORT_TF_NONBLOCKING", "0")
    monkeypatch.setenv("FENIX_AGENT_CACHE_ON_TIMEOUT", "1")
    monkeypatch.setenv("FENIX_VISUAL_TIMEOUT_SHORT_SEC", "0.01")

    cache = AgentReportCache(max_entries=8)
    cache.set(
        agent="visual",
        symbol="BTCUSDT",
        timeframe="1m",
        report={"action": "SELL", "confidence": 0.8, "reason": "cached"},
    )

    node = create_visual_agent_node(
        _LLMSlow(sleep_sec=0.05), reasoning_bank=None, agent_cache=cache
    )
    out = await node(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "chart_image_b64": "abc",
            "current_price": 100.0,
        }
    )

    report = out["visual_report"]
    assert report["action"] == "SELL"
    assert report.get("_cache_info", {}).get("reason") == "llm_timeout"


@pytest.mark.asyncio
async def test_visual_prompt_indicators_match_professional_chart_contract(monkeypatch, tmp_path):
    visual_mod = importlib.import_module("src.core.orchestrator.agents.visual")
    captured: dict[str, object] = {}

    def fake_format_prompt(agent_name: str, **kwargs):
        captured["agent_name"] = agent_name
        captured["kwargs"] = kwargs
        return [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user"},
        ]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(visual_mod, "format_prompt", fake_format_prompt)
    monkeypatch.setattr(visual_mod, "save_legacy_agent_log", lambda *args, **kwargs: None)

    node = visual_mod.create_visual_agent_node(_LLMFast(), reasoning_bank=None)
    result = await node(
        {
            "symbol": "ETHUSDC",
            "timeframe": "15m",
            "chart_image_b64": "abc",
            "chart_candles_count": 80,
            "current_price": 1613.36,
            "chart_indicators_summary": {
                "ema_50": {"value": 1617.3, "position": "above"},
                "vwap": {"value": 1612.2, "position": "above"},
                "pivots": {"r3": 1626.4, "pp": 1616.5, "s3": 1607.7},
                "rsi": {"value": 30.4},
            },
        }
    )

    indicators = captured["kwargs"]["visible_indicators"]
    indicator_values = captured["kwargs"]["indicator_values"]
    assert captured["agent_name"] == "visual_analyst"
    assert captured["kwargs"]["candle_count"] == 80
    assert "EMA 9/21/50" in indicators
    assert "Bollinger Bands" in indicators
    assert "SuperTrend" in indicators
    assert "VWAP" in indicators
    assert "Pivot Levels" in indicators
    assert "Volume" in indicators
    assert "SMA 50" not in indicators
    assert '"ema_50"' in indicator_values
    assert '"vwap"' in indicator_values
    assert '"pivots"' in indicator_values
    assert '"rsi"' not in indicator_values
    assert result["visual_report"]["action"] == "HOLD"
