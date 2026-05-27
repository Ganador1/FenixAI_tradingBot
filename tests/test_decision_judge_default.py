import types

import pytest


class _FastLLM:
    async def ainvoke(self, _messages):
        # Minimal valid decision payload.
        return types.SimpleNamespace(
            content='{"final_decision":"HOLD","confidence_in_decision":"LOW","combined_reasoning":"x","key_conflicting_signals":[],"risk_assessment":{"entry_price":100.0,"stop_loss":90.0,"take_profit":110.0,"risk_reward_ratio":2.0}}'
        )


@pytest.mark.asyncio
async def test_decision_judge_disabled_by_default_on_short_tf(monkeypatch):
    # Ensure env is truly unset so we exercise the default behavior.
    monkeypatch.delenv("FENIX_ENABLE_JUDGE", raising=False)

    import src.core.orchestrator.agents.decision as decision_mod

    # If the judge is invoked, fail the test.
    class _FailJudge:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Judge should not be constructed on short TF by default")

    monkeypatch.setattr(decision_mod, "ReasoningLLMJudge", _FailJudge)
    monkeypatch.setattr(decision_mod, "REASONING_BANK_AVAILABLE", True, raising=False)

    # Force store_agent_decision to return a digest so the judge would run if enabled.
    monkeypatch.setattr(decision_mod, "store_agent_decision", lambda *a, **k: "digest")

    node = decision_mod.create_decision_agent_node(llm=_FastLLM(), reasoning_bank=object())
    out = await node(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "timestamp": "now",
            "indicators": {"rsi": 50.0},
            "current_price": 100.0,
            "current_volume": 0.0,
            "technical_report": {"signal": "HOLD", "confidence": 0.5, "rationale": "x"},
            "qabba_report": {"signal": "HOLD", "confidence": 0.5, "rationale": "x"},
            "sentiment_report": {},
            "visual_report": {},
            "messages": [],
            "errors": [],
            "execution_times": {},
        }
    )

    report = out.get("final_trade_decision") or {}
    assert report.get("final_decision") == "HOLD"


@pytest.mark.asyncio
async def test_legacy_decision_judge_disabled_by_default_on_short_tf(monkeypatch):
    monkeypatch.delenv("FENIX_ENABLE_JUDGE", raising=False)

    import src.core.langgraph_orchestrator as legacy_mod

    class _FailJudge:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Judge should not be constructed on short TF by default")

    monkeypatch.setattr(legacy_mod, "ReasoningLLMJudge", _FailJudge)
    monkeypatch.setattr(legacy_mod, "REASONING_BANK_AVAILABLE", True, raising=False)
    monkeypatch.setattr(legacy_mod, "store_agent_decision", lambda *a, **k: "digest")

    node = legacy_mod.create_decision_agent_node(llm=_FastLLM(), reasoning_bank=object())
    out = await node(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "timestamp": "now",
            "indicators": {"rsi": 50.0},
            "current_price": 100.0,
            "current_volume": 0.0,
            "technical_report": {"signal": "HOLD", "confidence": 0.5, "rationale": "x"},
            "qabba_report": {"signal": "HOLD", "confidence": 0.5, "rationale": "x"},
            "sentiment_report": {},
            "visual_report": {},
            "messages": [],
            "errors": [],
            "execution_times": {},
        }
    )

    report = out.get("final_trade_decision") or {}
    assert report.get("final_decision") == "HOLD"
