import types
from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.trading.engine import TradingEngine
from src.trading.trade_manager import OpenPosition


class _StubExecutor:
    def __init__(self):
        self.called = False
        self.min_notional = 5.0

    def get_balance(self):
        return 1000.0

    async def execute_market_order(self, _side, quantity, stop_loss=None, take_profit=None):
        self.called = True
        return types.SimpleNamespace(
            success=True,
            status="FILLED",
            executed_qty=quantity,
            entry_price=stop_loss or 1.0,
            message="",
        )


class _StubMarketData:
    def __init__(self, price: float = 100.0):
        self.current_price = price
        self.current_volume = 0.0

    def get_microstructure_metrics(self):
        return types.SimpleNamespace(
            obi=0.0,
            cvd=0.0,
            spread=0.0,
            bid_depth=0.0,
            ask_depth=0.0,
        )


@pytest.mark.asyncio
async def test_process_decision_increments_hold_counter():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=True)
    engine.executor = _StubExecutor()
    engine.market_data = _StubMarketData()

    await engine._process_decision({"final_trade_decision": {"final_decision": "HOLD", "confidence_in_decision": "LOW", "combined_reasoning": "test"}})

    assert engine._consecutive_holds == 1


@pytest.mark.asyncio
async def test_process_decision_executes_paper_trade_without_live_flag():
    engine = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=True)
    engine.executor = _StubExecutor()
    engine.market_data = _StubMarketData(price=20000)

    await engine._process_decision({"final_trade_decision": {"final_decision": "BUY", "confidence_in_decision": "HIGH", "combined_reasoning": "test", "risk_assessment": {"entry_price": 20000}}})

    # Paper trading short-circuits before executor is called
    assert engine.executor.called is False


@pytest.mark.asyncio
async def test_process_decision_ignores_implausible_risk_manager_order_details(monkeypatch):
    monkeypatch.setenv("FENIX_MAX_NOTIONAL_USD", "20")

    engine = TradingEngine(symbol="ETHUSDT", timeframe="1m", paper_trading=False, allow_live_trading=False)
    engine.executor = _StubExecutor()
    engine.market_data = _StubMarketData(price=2231.25)
    engine._execute_trade = AsyncMock()

    await engine._process_decision(
        {
            "final_trade_decision": {
                "final_decision": "SELL",
                "confidence_in_decision": "MEDIUM",
                "combined_reasoning": "test",
                "risk_assessment": {
                    "entry_price": 2231.25,
                    "stop_loss": 2232.2,
                    "take_profit": 2226.0,
                    "risk_reward_ratio": 5.21,
                },
            },
            "risk_assessment": {
                "order_details": {
                    "approved_size": 0.02,
                    "stop_loss": 86000.0,
                    "take_profit": 84000.0,
                },
                "dynamic_risk_levels": {
                    "stop_loss": 86000.0,
                    "take_profit": 84000.0,
                    "risk_reward_ratio": 5.21,
                },
            },
        }
    )

    engine._execute_trade.assert_awaited_once()
    _, _, decision_data = engine._execute_trade.await_args.args
    assert decision_data["risk_assessment"]["stop_loss"] == pytest.approx(2232.2)
    assert decision_data["risk_assessment"]["take_profit"] == pytest.approx(2226.0)
    assert decision_data.get("position_size") != pytest.approx(0.02)


@pytest.mark.asyncio
async def test_process_decision_blocks_trade_cooldown(monkeypatch):
    monkeypatch.setenv("FENIX_MIN_TRADE_COOLDOWN_SECONDS", "120")

    engine = TradingEngine(symbol="ETHUSDT", timeframe="1m", paper_trading=False, allow_live_trading=False)
    engine.executor = _StubExecutor()
    engine.market_data = _StubMarketData(price=2231.25)
    engine._execute_trade = AsyncMock()
    engine._fast_last_trade_ts = datetime.now(timezone.utc)

    await engine._process_decision(
        {
            "final_trade_decision": {
                "final_decision": "BUY",
                "confidence_in_decision": "HIGH",
                "combined_reasoning": "test",
                "risk_assessment": {
                    "entry_price": 2231.25,
                    "stop_loss": 2227.0,
                    "take_profit": 2238.0,
                },
            },
            "risk_assessment": {
                "dynamic_risk_levels": {
                    "net_profit_potential": 0.45,
                    "fees_usd": 0.12,
                }
            },
        }
    )

    engine._execute_trade.assert_not_awaited()
    assert engine._consecutive_holds == 1


@pytest.mark.asyncio
async def test_process_decision_blocks_trade_for_low_net_edge(monkeypatch):
    monkeypatch.setenv("FENIX_MIN_EXPECTED_NET_EDGE_USD", "0.50")
    monkeypatch.setenv("FENIX_MIN_EXPECTED_NET_EDGE_MULTIPLE_OF_FEES", "1.0")

    engine = TradingEngine(symbol="ETHUSDT", timeframe="1m", paper_trading=False, allow_live_trading=False)
    engine.executor = _StubExecutor()
    engine.market_data = _StubMarketData(price=2231.25)
    engine._execute_trade = AsyncMock()

    await engine._process_decision(
        {
            "final_trade_decision": {
                "final_decision": "SELL",
                "confidence_in_decision": "HIGH",
                "combined_reasoning": "test",
                "risk_assessment": {
                    "entry_price": 2231.25,
                    "stop_loss": 2235.0,
                    "take_profit": 2227.0,
                },
            },
            "risk_assessment": {
                "dynamic_risk_levels": {
                    "net_profit_potential": 0.20,
                    "fees_usd": 0.15,
                }
            },
        }
    )

    engine._execute_trade.assert_not_awaited()
    assert engine._consecutive_holds == 1


@pytest.mark.asyncio
async def test_process_decision_blocks_reversal_when_position_remains_open():
    engine = TradingEngine(symbol="ETHUSDT", timeframe="5m", paper_trading=False, allow_live_trading=False)
    engine.executor = _StubExecutor()
    engine.market_data = _StubMarketData(price=85.52)
    engine.trade_manager = MagicMock()
    tracked = OpenPosition(
        symbol="ETHUSDT",
        side="LONG",
        entry_price=85.69,
        quantity=0.09,
        entry_time=datetime.now(timezone.utc),
        entry_signal_ts=datetime.now(timezone.utc).isoformat(),
        trade_id="trade-long-open",
        decision_agent_name="Decision Agent",
    )
    engine.trade_manager.get_position.return_value = tracked
    engine._manage_open_position = AsyncMock(return_value=None)
    engine._execute_trade = AsyncMock()

    await engine._process_decision(
        {
            "final_trade_decision": {
                "final_decision": "SELL",
                "confidence_in_decision": "HIGH",
                "combined_reasoning": "reversal",
                "risk_assessment": {
                    "entry_price": 85.52,
                    "stop_loss": 86.10,
                    "take_profit": 84.80,
                },
            },
            "technical_report": {"signal": "SELL", "confidence": 0.82},
            "qabba_report": {"signal": "SELL", "confidence": 0.86},
        }
    )

    assert any(
        call.kwargs == {"new_signal": "SELL"} for call in engine._manage_open_position.await_args_list
    )
    engine._execute_trade.assert_not_awaited()
    assert engine._consecutive_holds == 1


@pytest.mark.asyncio
async def test_process_decision_allows_reversal_after_position_is_cleared():
    engine = TradingEngine(symbol="ETHUSDT", timeframe="5m", paper_trading=False, allow_live_trading=False)
    engine.executor = _StubExecutor()
    engine.market_data = _StubMarketData(price=85.52)
    tracked = OpenPosition(
        symbol="ETHUSDT",
        side="LONG",
        entry_price=85.69,
        quantity=0.09,
        entry_time=datetime.now(timezone.utc),
        entry_signal_ts=datetime.now(timezone.utc).isoformat(),
        trade_id="trade-long-open",
        decision_agent_name="Decision Agent",
    )
    state = {"position": tracked}

    trade_manager = MagicMock()
    trade_manager.get_position.side_effect = lambda *_args, **_kwargs: state["position"]
    engine.trade_manager = trade_manager

    async def _clear_position(*_args, **_kwargs):
        state["position"] = None
        return {"exit_reason": "exchange_reconciliation"}

    engine._manage_open_position = AsyncMock(side_effect=_clear_position)
    engine._execute_trade = AsyncMock()

    await engine._process_decision(
        {
            "final_trade_decision": {
                "final_decision": "SELL",
                "confidence_in_decision": "HIGH",
                "combined_reasoning": "reversal",
                "risk_assessment": {
                    "entry_price": 85.52,
                    "stop_loss": 86.10,
                    "take_profit": 84.80,
                },
            },
            "technical_report": {"signal": "SELL", "confidence": 0.82},
            "qabba_report": {"signal": "SELL", "confidence": 0.86},
        }
    )

    assert any(
        call.kwargs == {"new_signal": "SELL"} for call in engine._manage_open_position.await_args_list
    )
    engine._execute_trade.assert_awaited_once()
