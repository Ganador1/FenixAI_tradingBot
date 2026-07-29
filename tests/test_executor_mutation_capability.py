from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.trading.executor import OrderExecutor


@pytest.mark.asyncio
async def test_executor_is_read_only_by_default():
    executor = OrderExecutor(symbol="BTCUSDT", testnet=False)
    service = MagicMock()
    executor._service = service

    result = await executor.execute_market_order("BUY", quantity=0.01)

    assert result.success is False
    assert result.status == "MUTATIONS_DISABLED"
    service.place_market_order.assert_not_called()


@pytest.mark.asyncio
async def test_read_only_executor_blocks_cancellation_and_protection():
    executor = OrderExecutor(symbol="BTCUSDT", testnet=False)
    executor._service = MagicMock()

    with pytest.raises(PermissionError, match="mutation blocked"):
        await executor.cancel_all_orders()
    with pytest.raises(PermissionError, match="mutation blocked"):
        await executor.cancel_order(123)
    with pytest.raises(PermissionError, match="mutation blocked"):
        await executor._place_protective_orders(
            entry_side="BUY",
            quantity=0.01,
            stop_loss=90.0,
            take_profit=110.0,
            entry_price=100.0,
        )

    executor._service.cancel_all_open_orders.assert_not_called()
    executor._service.cancel_order.assert_not_called()
