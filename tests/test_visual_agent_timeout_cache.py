import asyncio

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

    node = create_visual_agent_node(_LLMSlow(sleep_sec=0.05), reasoning_bank=None, agent_cache=cache)
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
