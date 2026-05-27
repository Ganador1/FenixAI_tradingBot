import pytest

from src.core.orchestrator.agents.risk import create_risk_agent_node


@pytest.mark.asyncio
async def test_risk_agent_deterministic_outputs_approved_size_as_notional(monkeypatch):
    monkeypatch.setenv("FENIX_RISK_DETERMINISTIC", "1")

    risk_node = create_risk_agent_node(llm=object(), reasoning_bank=None)
    out = await risk_node(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "timestamp": "now",
            "account_balance_usdt": 10000.0,
            "indicators": {"atr": 1.0},
            "current_price": 100.0,
            "current_volume": 0.0,
            "final_trade_decision": {"final_decision": "BUY", "confidence_in_decision": "MEDIUM"},
            "messages": [],
            "errors": [],
            "execution_times": {},
        }
    )

    assessment = out.get("risk_assessment") or {}
    order = assessment.get("order_details") or {}
    approved = float(order.get("approved_size") or 0.0)

    # With entry=100, ATR=1, default config yields qty~90.909, notional~9090.91
    assert approved == pytest.approx(9090.91, rel=1e-3)


@pytest.mark.asyncio
async def test_risk_agent_replaces_zero_llm_order_details_with_dynamic_levels(monkeypatch):
    from src.core.orchestrator.agents import risk as risk_mod

    monkeypatch.delenv("FENIX_RISK_DETERMINISTIC", raising=False)
    monkeypatch.setattr(risk_mod, "save_legacy_agent_log", lambda *args, **kwargs: None)

    async def fake_invoke_with_retry_and_validation(**kwargs):
        return (
            {
                "verdict": "APPROVE",
                "risk_score": 3.0,
                "reasoning": "approve but malformed order details",
                "order_details": {
                    "approved_size": 0.0,
                    "stop_loss": 0.0,
                    "take_profit": 0.0,
                },
            },
            1,
            [],
        )

    monkeypatch.setattr(
        risk_mod,
        "invoke_with_retry_and_validation",
        fake_invoke_with_retry_and_validation,
    )

    risk_node = risk_mod.create_risk_agent_node(llm=object(), reasoning_bank=None)
    out = await risk_node(
        {
            "symbol": "SOLUSDT",
            "timeframe": "5m",
            "timestamp": "now",
            "account_balance_usdt": 50.0,
            "indicators": {"atr": 0.18},
            "current_price": 84.0,
            "current_volume": 0.0,
            "final_trade_decision": {"final_decision": "SELL", "confidence_in_decision": "MEDIUM"},
            "messages": [],
            "errors": [],
            "execution_times": {},
        }
    )

    assessment = out.get("risk_assessment") or {}
    order = assessment.get("order_details") or {}

    assert assessment["_order_details_fallback"] is True
    assert float(order["approved_size"]) > 0
    assert float(order["take_profit"]) < 84.0 < float(order["stop_loss"])
