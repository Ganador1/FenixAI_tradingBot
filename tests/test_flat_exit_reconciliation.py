"""Regression test: the "exchange already flat" exit path must reconcile the
close with the REAL protective-order fills (via _synchronize_live_exit) before
recording, instead of booking the candle-close price.

2026-07-07: an ETH SHORT whose SL filled at ~1782.95 was booked at the candle
close 1787.29 (-4.38 vs ~-3.1 real), inflating DB losses, poisoning
ReasoningBank rewards and delaying the post-stopout filter.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.trading.engine import TradingEngine


def _build_engine_for_flat_exit():
    engine = TradingEngine.__new__(TradingEngine)
    engine.symbol = "ETHUSDC"
    engine.timeframe = "15m"
    engine.paper_trading = False
    engine.allow_live_trading = True
    engine.on_agent_event = None
    engine.reasoning_bank = MagicMock()
    engine.risk_manager = None
    engine._fast_last_trade_ts = None
    engine._post_stopout_block_bars = 0
    engine._post_stopout_block = None

    tracked = MagicMock()
    tracked.side = "SHORT"
    tracked.quantity = 0.3
    tracked.entry_price = 1769.43
    tracked.entry_time = datetime.now(timezone.utc)
    engine._get_tracked_position = lambda: tracked
    engine._tracked = tracked

    engine.market_data = MagicMock()
    engine.market_data.current_price = 1787.29

    close_result = {
        "trade_id": "t-1",
        "pnl": -4.38,
        "pnl_pct": -1.0,
        "exit_price": 1787.29,
        "exit_time": datetime.now(timezone.utc).isoformat(),
    }
    engine.trade_manager = MagicMock()
    engine.trade_manager.check_exit_conditions.return_value = close_result

    engine.executor = MagicMock()
    # Exchange is already flat: the protective order filled mid-candle.
    engine._confirm_exchange_flat_snapshot = AsyncMock(
        return_value=({"positionAmt": "0.000"}, 0.0, True)
    )
    engine._synchronize_live_exit = AsyncMock(return_value=True)
    engine._close_position_record = AsyncMock()
    engine._is_opposite_side = TradingEngine._is_opposite_side.__get__(engine)
    return engine, close_result


@pytest.mark.asyncio
async def test_already_flat_exit_reconciles_real_fills_before_recording():
    engine, close_result = _build_engine_for_flat_exit()

    result = await engine._manage_open_position(new_signal="HOLD")

    assert result is close_result
    engine._synchronize_live_exit.assert_awaited_once()
    engine._close_position_record.assert_awaited_once()
    # Reconciliation must happen BEFORE the record call.
    sync_call_order = engine._synchronize_live_exit.await_args_list
    assert sync_call_order  # called at least once with the close_result
    kwargs = engine._synchronize_live_exit.await_args.kwargs
    assert kwargs.get("close_result") is close_result


@pytest.mark.asyncio
async def test_already_flat_exit_still_records_when_reconciliation_fails():
    engine, close_result = _build_engine_for_flat_exit()
    engine._synchronize_live_exit = AsyncMock(side_effect=RuntimeError("api down"))

    result = await engine._manage_open_position(new_signal="HOLD")

    assert result is close_result
    engine._close_position_record.assert_awaited_once()


@pytest.mark.asyncio
async def test_watchdog_reconciles_protective_fill_even_after_price_returns_inside_stop():
    """A filled intrabar stop must close local state before local exit checks."""
    engine, _ = _build_engine_for_flat_exit()
    engine._live_position_reconciliation_enabled = True
    engine._live_position_reconciliation_lock = asyncio.Lock()
    engine.market_data.current_price = 1768.00  # Back inside the local short SL range.
    engine.trade_manager.check_exit_conditions.return_value = None
    engine._get_tracked_position = MagicMock(
        side_effect=[engine._tracked, engine._tracked, None]
    )
    engine.executor.get_position.return_value = {"positionAmt": "0.000", "markPrice": "1768.00"}
    engine.executor.cancel_position_protection = AsyncMock(return_value=True)
    engine.executor.cancel_all_orders = AsyncMock(return_value=True)
    engine.trade_manager.close_position.return_value = {
        "trade_id": "t-protective-fill",
        "exit_price": 1768.00,
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "pnl": -1.0,
        "pnl_pct": -0.1,
    }

    result = await engine._manage_open_position(new_signal="HOLD")

    assert result is None
    engine.trade_manager.close_position.assert_called_once()
    engine._synchronize_live_exit.assert_awaited_once()
    engine._close_position_record.assert_awaited_once()
    engine.trade_manager.check_exit_conditions.assert_not_called()


@pytest.mark.asyncio
async def test_reconciliation_preserves_untracked_orders_for_the_symbol():
    engine, _ = _build_engine_for_flat_exit()
    engine._tracked.protection_position_id = None
    engine.executor.cancel_all_orders = AsyncMock(return_value=True)

    cleaned = await engine._cleanup_flat_symbol_orders(
        tracked_position=engine._tracked,
        source="exchange_reconciliation",
    )

    assert cleaned is False
    engine.executor.cancel_all_orders.assert_not_awaited()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
