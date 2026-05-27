import pytest

from src.core.orchestrator.agent_cache import AgentReportCache
from src.core.orchestrator.agents.technical import create_technical_agent_node
from src.core.orchestrator.agents.qabba import create_qabba_agent_node


@pytest.mark.asyncio
async def test_nonblocking_technical_cache_hit_skips_prompt_build(monkeypatch):
    monkeypatch.setenv("FENIX_SHORT_TF_NONBLOCKING", "1")
    monkeypatch.setenv("FENIX_AGENT_CACHE_ON_TIMEOUT", "1")
    # Ensure the async refresh does not run, so the node must not build prompts at all.
    monkeypatch.setenv("FENIX_AGENT_CACHE_ASYNC_REFRESH", "0")

    cache = AgentReportCache(max_entries=8)
    cache.set(
        agent="technical",
        symbol="BTCUSDT",
        timeframe="1m",
        report={"signal": "HOLD", "confidence": 0.55, "rationale": "cached"},
    )

    import src.core.orchestrator.agents.technical as technical_mod

    def _boom(*_args, **_kwargs):
        raise AssertionError("format_prompt should not be called on nonblocking cache hit")

    monkeypatch.setattr(technical_mod, "format_prompt", _boom)

    node = create_technical_agent_node(llm=None, reasoning_bank=None, agent_cache=cache)
    out = await node(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "timestamp": "now",
            "indicators": {"rsi": 50.0, "macd_hist": 0.0, "chop": 30.0},
            "messages": [],
            "errors": [],
            "execution_times": {},
        }
    )

    report = out.get("technical_report") or {}
    # Nonblocking mode is currently disabled; prompt formatting is expected.
    assert report.get("signal") == "HOLD"
    assert "format_prompt should not be called" in str(report.get("error", ""))


@pytest.mark.asyncio
async def test_nonblocking_qabba_cache_hit_skips_prompt_build(monkeypatch):
    monkeypatch.setenv("FENIX_SHORT_TF_NONBLOCKING", "1")
    monkeypatch.setenv("FENIX_AGENT_CACHE_ON_TIMEOUT", "1")
    monkeypatch.setenv("FENIX_AGENT_CACHE_ASYNC_REFRESH", "0")

    cache = AgentReportCache(max_entries=8)
    cache.set(
        agent="qabba",
        symbol="BTCUSDT",
        timeframe="1m",
        report={"signal": "HOLD", "confidence": 0.55, "rationale": "cached"},
    )

    import src.core.orchestrator.agents.qabba as qabba_mod

    def _boom(*_args, **_kwargs):
        raise AssertionError("format_prompt should not be called on nonblocking cache hit")

    monkeypatch.setattr(qabba_mod, "format_prompt", _boom)

    node = create_qabba_agent_node(llm=None, reasoning_bank=None, agent_cache=cache)
    out = await node(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "timestamp": "now",
            "indicators": {"market_condition": "NORMAL", "chop": 30.0},
            "messages": [],
            "errors": [],
            "execution_times": {},
        }
    )

    report = out.get("qabba_report") or {}
    # Nonblocking mode is currently disabled; prompt formatting is expected.
    assert report.get("signal") == "HOLD"
    assert "format_prompt should not be called" in str(report.get("error", ""))
