"""Shutdown cleanup must never treat a failed exchange query as a flat position.

`stop()` cancels every protective order before reading the exchange position;
if that read fails, the engine must attempt a defensive reduce-only close for
the tracked quantity instead of assuming flat and leaving live exposure
unprotected.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


class _StrictFailExecutor:
    """Executor whose strict reader always fails (exchange unreachable)."""

    def __init__(self):
        self.cancel_calls = 0
        self.market_orders: list[dict] = []

    async def cancel_all_orders(self) -> bool:
        self.cancel_calls += 1
        return True

    def get_position_snapshot(self) -> dict:
        raise RuntimeError("exchange unreachable")

    def get_position(self) -> dict:
        return {}

    async def execute_market_order(self, side, quantity, reduce_only=False):
        self.market_orders.append(
            {"side": side, "quantity": quantity, "reduce_only": reduce_only}
        )
        return SimpleNamespace(success=True, status="FILLED")


class _LivePositionExecutor(_StrictFailExecutor):
    """First strict read shows a live position; later reads confirm flat."""

    def __init__(self):
        super().__init__()
        self._snapshots = [{"positionAmt": "0.5", "markPrice": "101.0"}]

    def get_position_snapshot(self) -> dict:
        if self._snapshots:
            return self._snapshots.pop(0)
        return {"positionAmt": "0", "markPrice": "101.0"}


def _build_stop_engine(executor, tracked, *, paper_trading=False, allow_live_trading=True):
    from src.trading.engine import TradingEngine

    engine = TradingEngine.__new__(TradingEngine)
    engine.symbol = "ETHUSDC"
    engine.timeframe = "15m"
    engine.paper_trading = paper_trading
    engine.allow_live_trading = allow_live_trading
    engine._running = True
    engine._stopping = False
    engine._stopped = False
    engine._engine_cleanup_on_stop = True
    engine.executor = executor
    engine.market_data = None
    engine.trade_manager = SimpleNamespace(
        get_position=lambda symbol=None: tracked,
        close_position=MagicMock(return_value={"pnl": 0.0, "trade_id": "t1"}),
    )
    engine._synchronize_live_exit = AsyncMock(return_value=True)
    engine._close_position_record = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_stop_cleanup_never_mutates_exchange_in_paper_mode():
    executor = _LivePositionExecutor()
    tracked = SimpleNamespace(side="LONG", quantity=0.5, entry_price=100.0)
    engine = _build_stop_engine(
        executor,
        tracked,
        paper_trading=True,
        allow_live_trading=False,
    )

    await engine.stop()

    assert executor.cancel_calls == 0
    assert executor.market_orders == []
    engine.trade_manager.close_position.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_close_rejects_non_live_engine():
    executor = _LivePositionExecutor()
    engine = _build_stop_engine(
        executor,
        tracked=None,
        paper_trading=True,
        allow_live_trading=False,
    )

    with pytest.raises(PermissionError, match="forbidden"):
        await engine._execute_cleanup_close("SELL", 0.5)

    assert executor.market_orders == []


@pytest.fixture
def fast_retry_sleep(monkeypatch):
    """Collapse the shutdown retry backoff so the failure path stays fast."""
    real_sleep = asyncio.sleep

    async def instant_sleep(_delay, *args, **kwargs):
        await real_sleep(0)

    monkeypatch.setattr("src.trading.engine.asyncio.sleep", instant_sleep)


@pytest.mark.asyncio
async def test_stop_cleanup_does_not_assume_flat_when_position_query_fails(
    monkeypatch, fast_retry_sleep
):
    import src.risk.safety_alerts as safety_alerts

    alerts: list[tuple[str, dict | None]] = []

    async def record_alert(event_type, message, context=None):
        alerts.append((event_type, context))
        return True

    monkeypatch.setattr(safety_alerts, "alert_safety_event", record_alert)

    executor = _StrictFailExecutor()
    tracked = SimpleNamespace(side="LONG", quantity=0.5, entry_price=100.0)
    engine = _build_stop_engine(executor, tracked)

    await engine.stop()

    # Protections were cancelled, so the engine must close defensively with
    # the tracked quantity rather than skipping the close on a {} snapshot.
    assert executor.cancel_calls == 1
    assert executor.market_orders == [
        {"side": "SELL", "quantity": 0.5, "reduce_only": True}
    ]
    # Position state is unknown: never record a local close.
    engine.trade_manager.close_position.assert_not_called()
    assert any(event == "RECONCILIATION_FAILURE" for event, _ in alerts)


@pytest.mark.asyncio
async def test_stop_cleanup_closes_live_position_with_strict_snapshot():
    executor = _LivePositionExecutor()
    tracked = SimpleNamespace(side="LONG", quantity=0.5, entry_price=100.0)
    engine = _build_stop_engine(executor, tracked)

    await engine.stop()

    assert executor.market_orders == [
        {"side": "SELL", "quantity": 0.5, "reduce_only": True}
    ]
    engine.trade_manager.close_position.assert_called_once()
    engine._close_position_record.assert_awaited_once()


def test_log_safety_alert_channel_status_warns_without_credentials(monkeypatch, caplog):
    import src.risk.safety_alerts as safety_alerts
    from src.trading.engine import TradingEngine

    monkeypatch.setattr(safety_alerts, "_notifier", None)
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL"):
        monkeypatch.delenv(var, raising=False)

    engine = TradingEngine.__new__(TradingEngine)
    with caplog.at_level(logging.WARNING, logger="FenixTradingEngine"):
        engine._log_safety_alert_channel_status()

    assert "SAFETY ALERTS DISABLED" in caplog.text
