import types

import pytest

from src.trading.engine import TradingEngine


class _DummyExecutor:
    def __init__(self):
        self.called = False
        self.min_notional = 5.0

    def get_balance(self):
        return 1000.0

    async def execute_market_order(self, side, quantity, stop_loss=None, take_profit=None):
        self.called = True
        return types.SimpleNamespace(
            success=True,
            status="FILLED",
            order_id=123,
            position_id=None,
            executed_qty=quantity,
            entry_price=stop_loss or 1.0,
            sl_order_id=None,
            tp_order_id=None,
            message="",
        )


class _DummyMarketData:
    def __init__(self, price: float = 100.0):
        self.current_price = price


@pytest.mark.asyncio
async def test_live_trade_blocked_without_flag():
    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="15m",
        use_testnet=False,
        paper_trading=False,
        allow_live_trading=False,
    )
    engine.executor = _DummyExecutor()
    engine.market_data = _DummyMarketData(price=50000)
    engine._get_tracked_position = lambda: None

    await engine._execute_trade(
        decision="BUY",
        confidence="HIGH",
        decision_data={"risk_assessment": {"entry_price": 50000}},
    )

    assert engine.executor.called is False, "Trade should not execute without allow_live_trading"


@pytest.mark.asyncio
async def test_live_trade_allows_execution_with_flag():
    engine = TradingEngine(
        symbol="BTCUSDT",
        timeframe="15m",
        use_testnet=False,
        paper_trading=False,
        allow_live_trading=True,
    )
    engine.executor = _DummyExecutor()
    engine.market_data = _DummyMarketData(price=40000)
    engine._get_tracked_position = lambda: None

    await engine._execute_trade(
        decision="SELL",
        confidence="HIGH",
        decision_data={"risk_assessment": {"entry_price": 40000}},
    )

    assert engine.executor.called is True, "Trade should execute when allow_live_trading is True"
