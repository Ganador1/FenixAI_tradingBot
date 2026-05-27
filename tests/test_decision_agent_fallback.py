import pytest

from src.core.orchestrator.agents.decision import (
    _decision_market_metrics_for_prompt,
    _decision_prompt_report,
    create_decision_agent_node,
)


class _Resp:
    def __init__(self, content: str):
        self.content = content


class _StubLLM:
    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, _messages):
        return _Resp(self._content)


@pytest.mark.asyncio
async def test_decision_agent_falls_back_when_llm_returns_non_json(monkeypatch):
    monkeypatch.setenv("FENIX_DECISION_MAX_RETRIES", "0")

    node = create_decision_agent_node(_StubLLM("not-json"))

    state = {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "technical_report": {"signal": "BUY", "confidence": 0.90},
        "qabba_report": {"signal": "HOLD", "confidence": 0.55},
        "indicators_filtered": {},
        "indicators": {},
        "messages": [],
        "execution_times": {},
    }

    out = await node(state)
    report = out["final_trade_decision"]

    assert report["final_decision"] == "BUY"
    assert report["confidence_in_decision"] in {"LOW", "MEDIUM", "HIGH"}
    assert report.get("_llm_error") is not None
    assert report.get("_llm_errors") is not None


@pytest.mark.asyncio
async def test_decision_agent_falls_back_when_llm_missing_required_fields(monkeypatch):
    monkeypatch.setenv("FENIX_DECISION_MAX_RETRIES", "0")

    # Missing combined_reasoning should fail validation.
    node = create_decision_agent_node(
        _StubLLM('{"final_decision":"BUY","confidence_in_decision":"MEDIUM"}')
    )

    state = {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "technical_report": {"signal": "BUY", "confidence": 0.90},
        "qabba_report": {"signal": "HOLD", "confidence": 0.55},
        "indicators_filtered": {},
        "indicators": {},
        "messages": [],
        "execution_times": {},
    }

    out = await node(state)
    report = out["final_trade_decision"]

    assert report["final_decision"] == "BUY"
    assert report.get("_llm_error") is not None


@pytest.mark.asyncio
async def test_decision_agent_attaches_directional_score_when_llm_omits_it(monkeypatch):
    monkeypatch.setenv("FENIX_DECISION_MAX_RETRIES", "0")
    monkeypatch.setenv("FENIX_W_TECHNICAL", "0.5")
    monkeypatch.setenv("FENIX_W_QABBA", "0.5")

    node = create_decision_agent_node(
        _StubLLM(
            '{"final_decision":"SELL","confidence_in_decision":"MEDIUM","combined_reasoning":"ok","risk_assessment":{}}'
        )
    )

    state = {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "technical_report": {"signal": "SELL", "confidence": 0.75},
        "qabba_report": {"signal": "HOLD", "confidence": 0.40},
        "sentiment_report": {},
        "visual_report": {},
        "obi": 0.60,
        "wdi": -0.25,
        "trade_imbalance_5s": -0.20,
        "indicators_filtered": {},
        "indicators": {},
        "messages": [],
        "execution_times": {},
    }

    out = await node(state)
    report = out["final_trade_decision"]

    assert report["final_decision"] == "SELL"
    assert report["_directional_score"] < 0
    assert report["_directional_score_source"] == "decision_agent_weighted_reports"
    assert report["_directional_agent_votes"]


def test_decision_prompt_helpers_trim_report_and_market_metrics():
    report = _decision_prompt_report(
        {
            "signal": "BUY",
            "confidence": 0.85,
            "rationale": "x" * 400,
            "unused_blob": {"foo": "bar"},
        },
        rationale_max_chars=120,
    )
    assert report["signal"] == "BUY"
    assert report["confidence"] == 0.85
    assert len(report["rationale"]) == 120
    assert "unused_blob" not in report

    metrics = _decision_market_metrics_for_prompt(
        {"timeframe": "15m", "obi": 1.4, "wdi": 0.2, "trade_imbalance_5s": 0.15},
        {
            "last_price": 67000,
            "rsi": 52.0,
            "market_condition": "NORMAL",
            "trend_conflict": False,
            "bollinger_upper": 67500,
            "unused_field": "drop-me",
        },
    )
    assert metrics["last_price"] == 67000
    assert metrics["rsi"] == 52.0
    assert metrics["obi"] == 1.4
    assert "unused_field" not in metrics
    assert "bollinger_upper" not in metrics
