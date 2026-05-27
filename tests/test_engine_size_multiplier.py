import types

import pytest

from src.trading.engine import TradingEngine


class _StubMarketData:
    def __init__(self, price: float = 100.0):
        self.current_price = price
        self.current_volume = 0.0


class _StubExecutor:
    def __init__(self, balance: float = 1000.0):
        self._balance = balance
        self.min_notional = 5.0
        self.last_quantity: float | None = None

    def get_balance(self):
        return self._balance

    def get_position(self):
        return {"positionAmt": 0}

    async def execute_market_order(self, side, quantity, stop_loss=None, take_profit=None, reduce_only=False):
        self.last_quantity = float(quantity)
        return types.SimpleNamespace(
            success=True,
            status="FILLED",
            executed_qty=quantity,
            entry_price=100.0,
            order_id="order1",
            message="",
        )

    async def cancel_all_orders(self):
        return True


class _StubRiskStatus:
    def describe(self):
        return "OK"

    def dict(self):
        return {"status": "OK"}


class _StubRiskManager:
    def __init__(self):
        self.checked_base_size: float | None = None
        self.current_status = _StubRiskStatus()

    def update_balance(self, _balance):
        return None

    def check_trade_allowed(self, _symbol, base_size):
        self.checked_base_size = float(base_size)
        return True, _StubRiskStatus()

    def get_adjusted_size(self, base_size):
        return float(base_size)


@pytest.mark.asyncio
async def test_execute_trade_applies_size_multiplier_in_fallback_sizing(monkeypatch):
    monkeypatch.setenv("FENIX_MAX_RISK_PER_TRADE", "0.02")
    monkeypatch.setenv("FENIX_LEVERAGE", "10.0")

    engine = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=False, allow_live_trading=True)
    engine.executor = _StubExecutor(balance=1000.0)
    engine.market_data = _StubMarketData(price=100.0)
    engine.risk_manager = None  # Force fallback sizing branch

    decision_data = {
        "risk_assessment": {"entry_price": 100.0, "stop_loss": 95.0, "take_profit": 110.0},
        "size_multiplier": 0.5,
    }

    await engine._execute_trade("BUY", "MEDIUM", decision_data)

    # baseline = 1000 * 0.02 * 10 = $200; with multiplier 0.5 => $100; qty = 100/100 = 1
    assert engine.executor.last_quantity == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_execute_trade_passes_size_multiplier_adjusted_base_to_risk_manager(monkeypatch):
    import src.trading.engine as engine_mod

    monkeypatch.setenv("FENIX_MAX_RISK_PER_TRADE", "0.02")
    monkeypatch.setenv("FENIX_LEVERAGE", "10.0")
    monkeypatch.setattr(engine_mod, "RISK_MANAGER_AVAILABLE", True, raising=False)

    engine = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=False, allow_live_trading=False)
    engine.executor = _StubExecutor(balance=1000.0)
    engine.market_data = _StubMarketData(price=100.0)
    engine.risk_manager = _StubRiskManager()

    decision_data = {"risk_assessment": {"entry_price": 100.0}, "size_multiplier": 0.5}

    await engine._execute_trade("BUY", "MEDIUM", decision_data)

    # baseline base_size = 1000 * 0.02 * 10 = $200; multiplier 0.5 applied pre-risk => $100
    assert engine.risk_manager.checked_base_size == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_execute_trade_caps_requested_notional_to_available_margin(monkeypatch):
    import src.trading.engine as engine_mod

    monkeypatch.setenv("FENIX_LEVERAGE", "1.0")
    monkeypatch.setenv("FENIX_MAX_ENTRY_MARGIN_PCT", "0.90")
    monkeypatch.setattr(engine_mod, "RISK_MANAGER_AVAILABLE", True, raising=False)

    engine = TradingEngine(symbol="SOLUSDT", timeframe="15m", paper_trading=False, allow_live_trading=True)
    engine.executor = _StubExecutor(balance=50.0)
    engine.market_data = _StubMarketData(price=100.0)
    engine.risk_manager = _StubRiskManager()

    decision_data = {
        "risk_assessment": {
            "entry_price": 100.0,
            "stop_loss": 99.0,
            "take_profit": 102.0,
        },
        "position_size": 400.0,
    }

    await engine._execute_trade("BUY", "MEDIUM", decision_data)

    assert engine.risk_manager.checked_base_size == pytest.approx(45.0)
    assert engine.executor.last_quantity == pytest.approx(0.45)
