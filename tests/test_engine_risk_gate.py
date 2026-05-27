import types
from unittest.mock import AsyncMock

import pytest

from src.trading.engine import TradingEngine


class _StubMarketData:
    def __init__(
        self,
        price: float = 100.0,
        *,
        obi: float = 2.0,
        volume_imbalance: float = 0.3,
        vpin_proxy: float = 0.2,
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
async def test_process_decision_blocks_when_llm_risk_missing_verdict_and_enforced():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0)
    engine._execute_trade = AsyncMock()
    engine._engine_enforce_llm_risk = True

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "x",
            "risk_assessment": {"entry_price": 100.0},
        },
        "risk_assessment": {"parse_error": True},
        "qabba_report": {"signal": "BUY", "confidence": 0.85},
        "indicators": {"chop": 20.0, "rsi": 50.0, "supports": [], "resistances": []},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 0
    assert engine._consecutive_holds == 1


@pytest.mark.asyncio
async def test_process_decision_allows_when_llm_risk_missing_verdict_and_not_enforced():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0)
    engine._execute_trade = AsyncMock()
    engine._engine_enforce_llm_risk = False

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "x",
            "risk_assessment": {"entry_price": 100.0},
        },
        "risk_assessment": {"parse_error": True},
        "qabba_report": {"signal": "BUY", "confidence": 0.85},
        "indicators": {"chop": 20.0, "rsi": 50.0, "supports": [], "resistances": []},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 1


@pytest.mark.asyncio
async def test_process_decision_normalizes_sltp_and_size_from_risk_report():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=True)
    engine.market_data = _StubMarketData(price=100.0)
    engine._execute_trade = AsyncMock()
    engine._engine_enforce_llm_risk = True

    result = {
        "final_trade_decision": {
            "final_decision": "BUY",
            "confidence_in_decision": "MEDIUM",
            "combined_reasoning": "x",
            "risk_assessment": {"entry_price": 100.0, "stop_loss": 90.0, "take_profit": 120.0},
        },
        "risk_assessment": {
            "verdict": "APPROVE_REDUCED",
            "order_details": {"approved_size": 123.0, "stop_loss": 95.0, "take_profit": 110.0},
            "dynamic_risk_levels": {"risk_reward_ratio": 2.0},
        },
        "qabba_report": {"signal": "BUY", "confidence": 0.85},
        "indicators": {"chop": 20.0, "rsi": 50.0, "supports": [], "resistances": []},
    }

    await engine._process_decision(result)

    assert engine._execute_trade.await_count == 1
    decision_data = engine._execute_trade.await_args.args[2]
    assert decision_data.get("position_size") == pytest.approx(123.0)
    ra = decision_data.get("risk_assessment") or {}
    assert ra.get("entry_price") == pytest.approx(100.0)
    assert ra.get("stop_loss") == pytest.approx(95.0)
    assert ra.get("take_profit") == pytest.approx(110.0)
    assert ra.get("risk_reward_ratio") == pytest.approx(2.0)

