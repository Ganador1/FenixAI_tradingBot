import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.trading_constants import SymbolConfig
from src.trading.executor import OrderExecutor


@pytest.mark.unit
def test_symbol_config_reads_futures_min_notional_notional_key():
    config = SymbolConfig.from_filters(
        "SOLUSDC",
        [
            {"filterType": "PRICE_FILTER", "tickSize": "0.0100"},
            {"filterType": "LOT_SIZE", "stepSize": "0.01"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ],
    )

    assert config.min_notional == pytest.approx(5.0)
    assert config.step_size == pytest.approx(0.01)
    assert config.tick_size == pytest.approx(0.01)


@pytest.mark.unit
def test_sl_tp_clamping(monkeypatch):
    """OrderExecutor clamps SL/TP values if they are too close to entry."""
    # Avoid depending on global indicator buffers
    try:
        import src.tools.technical_tools as technical_tools
        monkeypatch.setattr(technical_tools, "get_current_indicators", lambda: {})
    except Exception:
        pass

    async def run():
        with monkeypatch.context() as m:
            m.setenv("FENIX_MIN_SL_PCT", "0.003")  # 0.3%
            m.setenv("FENIX_MIN_TP_PCT", "0.005")  # 0.5%
            m.setenv("FENIX_ESTIMATED_FEE_PCT", "0.0004")

            executor = OrderExecutor(symbol="BTCUSDT", testnet=True, timeframe="15m")

            svc = MagicMock()
            svc.get_symbol_config.return_value = SymbolConfig(
                symbol="BTCUSDT",
                tick_size=0.1,
                step_size=0.001,
                min_notional=5.0,
                price_precision=1,
                quantity_precision=3,
            )
            svc.place_stop_loss_market.return_value = {"orderId": 101}
            svc.place_take_profit_market.return_value = {"orderId": 102}
            executor._service = svc

            entry_price = 50000.0
            quantity = 0.1

            # BUY: SL too close (should clamp down to 50000 * (1 - 0.003) = 49850)
            bad_sl = 49990.0
            await executor._place_protective_orders(
                entry_side="BUY",
                quantity=quantity,
                stop_loss=bad_sl,
                take_profit=None,
                entry_price=entry_price,
            )
            _, kwargs = svc.place_stop_loss_market.call_args
            actual_sl = float(kwargs["stop_price"])
            assert actual_sl == pytest.approx(49850.0, abs=0.11)  # tick_size=0.1

            # SELL: SL too close (should clamp up to 50000 * (1 + 0.003) = 50150)
            svc.reset_mock()
            bad_sl_short = 50010.0
            await executor._place_protective_orders(
                entry_side="SELL",
                quantity=quantity,
                stop_loss=bad_sl_short,
                take_profit=None,
                entry_price=entry_price,
            )
            _, kwargs = svc.place_stop_loss_market.call_args
            actual_sl_short = float(kwargs["stop_price"])
            assert actual_sl_short == pytest.approx(50150.0, abs=0.11)

            # BUY: TP too close (should clamp up to 50000 * (1 + 0.005) = 50250)
            svc.reset_mock()
            bad_tp = 50050.0
            await executor._place_protective_orders(
                entry_side="BUY",
                quantity=quantity,
                stop_loss=None,
                take_profit=bad_tp,
                entry_price=entry_price,
            )
            _, kwargs = svc.place_take_profit_market.call_args
            actual_tp = float(kwargs["stop_price"])
            assert actual_tp == pytest.approx(50250.0, abs=0.11)

    asyncio.run(run())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_all_orders_cleans_monitor_and_exchange(monkeypatch):
    executor = OrderExecutor(symbol="ETHUSDT", testnet=True, timeframe="3m")

    monitor = MagicMock()
    monitor.cancel_all_for_symbol = AsyncMock(return_value=True)
    monkeypatch.setattr("src.trading.executor.get_order_monitor", lambda: monitor)

    svc = MagicMock()
    svc.cancel_all_open_orders.return_value = {"code": 200}
    executor._service = svc

    result = await executor.cancel_all_orders()

    assert result is True
    monitor.cancel_all_for_symbol.assert_awaited_once_with("ETHUSDT")
    svc.cancel_all_open_orders.assert_called_once_with("ETHUSDT")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_market_entry_closes_fail_safe_when_protection_not_visible():
    executor = OrderExecutor(symbol="SOLUSDT", testnet=False, timeframe="15m")
    svc = MagicMock()
    svc.place_market_order.side_effect = [
        {"orderId": 1001},
        {"orderId": 1002},
    ]
    svc.get_order.side_effect = [
        {"orderId": 1001, "status": "FILLED", "avgPrice": "86.40", "executedQty": "0.06"},
        {"orderId": 1002, "status": "FILLED", "avgPrice": "86.35", "executedQty": "0.06"},
    ]
    svc.get_position.return_value = {"positionAmt": "-0.06"}
    svc.get_symbol_config.return_value = SymbolConfig(
        symbol="SOLUSDT",
        tick_size=0.01,
        step_size=0.01,
        min_notional=5.0,
        price_precision=2,
        quantity_precision=2,
    )
    svc.cancel_all_open_orders.return_value = []
    svc.place_stop_loss_market.return_value = {"orderId": 2001}
    svc.place_take_profit_market.return_value = {"orderId": 2002}
    svc.get_open_orders.return_value = []
    executor._service = svc

    result = await executor.execute_market_order(
        "SELL",
        quantity=0.06,
        stop_loss=87.16,
        take_profit=84.75,
    )

    assert result.success is False
    assert result.status == "PROTECTION_NOT_VERIFIED"
    assert result.sl_order_id == 2001
    assert result.tp_order_id == 2002
    assert "CLOSED" in result.message
    assert svc.place_market_order.call_count == 2
    _, close_kwargs = svc.place_market_order.call_args
    assert close_kwargs["side"] == "BUY"
    assert close_kwargs["reduce_only"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_protection_verification_accepts_visible_algo_orders():
    executor = OrderExecutor(symbol="SOLUSDT", testnet=False, timeframe="15m")
    svc = MagicMock()
    svc.get_open_orders.return_value = []
    svc.get_open_algo_orders.return_value = [
        {"algoId": 3000001349109503, "type": "STOP_MARKET"},
        {"algoId": 3000001349109551, "type": "TAKE_PROFIT_MARKET"},
    ]
    executor._service = svc

    verified = await executor._verify_protective_orders(
        sl_order_id=3000001349109503,
        tp_order_id=3000001349109551,
        require_sl=True,
        require_tp=True,
        retries=1,
        delay=0.0,
    )

    assert verified is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_market_entry_succeeds_when_protection_is_visible():
    executor = OrderExecutor(symbol="SOLUSDT", testnet=False, timeframe="15m")
    svc = MagicMock()
    svc.place_market_order.return_value = {"orderId": 1001}
    svc.get_order.return_value = {
        "orderId": 1001,
        "status": "FILLED",
        "avgPrice": "86.40",
        "executedQty": "0.06",
    }
    svc.get_position.return_value = {"positionAmt": "-0.06"}
    svc.get_symbol_config.return_value = SymbolConfig(
        symbol="SOLUSDT",
        tick_size=0.01,
        step_size=0.01,
        min_notional=5.0,
        price_precision=2,
        quantity_precision=2,
    )
    svc.cancel_all_open_orders.return_value = []
    svc.place_stop_loss_market.return_value = {"orderId": 2001}
    svc.place_take_profit_market.return_value = {"orderId": 2002}
    svc.get_open_orders.return_value = [
        {"orderId": 2001, "type": "STOP_MARKET"},
        {"orderId": 2002, "type": "TAKE_PROFIT_MARKET"},
    ]
    executor._service = svc

    result = await executor.execute_market_order(
        "SELL",
        quantity=0.06,
        stop_loss=87.16,
        take_profit=84.75,
    )

    assert result.success is True
    assert result.status == "FILLED_WITH_PROTECTION"
    assert result.sl_order_id == 2001
    assert result.tp_order_id == 2002
    svc.place_market_order.assert_called_once()


@pytest.mark.unit
def test_get_protection_status_filters_monitored_positions(monkeypatch):
    executor = OrderExecutor(symbol="ETHUSDT", testnet=True, timeframe="3m")

    monkeypatch.setattr(
        executor,
        "list_monitored_positions",
        lambda: [
            {"symbol": "ETHUSDT", "position_id": "eth-1"},
            {"symbol": "BTCUSDT", "position_id": "btc-1"},
        ],
    )
    monkeypatch.setattr(
        executor,
        "get_monitor_stats",
        lambda: {"monitoring_active": True},
    )

    status = executor.get_protection_status()

    assert status["symbol"] == "ETHUSDT"
    assert status["active_protections"] == 1
    assert status["monitoring_active"] is True
    assert status["positions"] == [{"symbol": "ETHUSDT", "position_id": "eth-1"}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refresh_position_protection_replaces_monitored_orders(monkeypatch):
    executor = OrderExecutor(symbol="ETHUSDT", testnet=True, timeframe="3m")

    monitor = MagicMock()
    monitor.get_position.return_value = MagicMock(
        entry_order_id=321,
        sl_order_id="sl-old",
        tp_order_id="tp-old",
    )
    monitor.cancel_all_for_position = AsyncMock(return_value=True)
    monitor.unregister_position = MagicMock()
    monkeypatch.setattr("src.trading.executor.get_order_monitor", lambda: monitor)

    executor._place_protective_orders = AsyncMock(return_value=("sl-new", "tp-new"))

    result = await executor.refresh_position_protection(
        position_id="pos-1",
        entry_order_id=321,
        entry_side="SELL",
        quantity=0.25,
        entry_price=2000.0,
        stop_loss=2010.0,
        take_profit=1980.0,
    )

    assert result.success is True
    assert result.position_id == "pos-1"
    assert result.sl_order_id == "sl-new"
    assert result.tp_order_id == "tp-new"
    monitor.cancel_all_for_position.assert_awaited_once_with("pos-1")
    monitor.unregister_position.assert_called_once_with("pos-1")
    monitor.register_position.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refresh_position_protection_aborts_when_cancel_fails(monkeypatch):
    executor = OrderExecutor(symbol="ETHUSDT", testnet=True, timeframe="3m")

    monitor = MagicMock()
    monitor.get_position.return_value = MagicMock(
        entry_order_id=321,
        sl_order_id="sl-old",
        tp_order_id="tp-old",
    )
    monitor.cancel_all_for_position = AsyncMock(return_value=False)
    monitor.unregister_position = MagicMock()
    monkeypatch.setattr("src.trading.executor.get_order_monitor", lambda: monitor)

    executor._place_protective_orders = AsyncMock()

    result = await executor.refresh_position_protection(
        position_id="pos-1",
        entry_order_id=321,
        entry_side="BUY",
        quantity=0.25,
        entry_price=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
    )

    assert result.success is False
    assert "failed to cancel" in result.message
    executor._place_protective_orders.assert_not_awaited()
