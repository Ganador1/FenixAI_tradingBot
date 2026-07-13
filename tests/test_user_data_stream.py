from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.trading.user_data_stream import FuturesUserDataStream


@pytest.mark.asyncio
async def test_user_data_stream_dispatches_private_event(monkeypatch):
    events = []
    socket = MagicMock()
    socket.__aenter__ = AsyncMock(return_value=socket)
    socket.__aexit__ = AsyncMock(return_value=None)
    socket.recv = AsyncMock(
        side_effect=[
            {"e": "ORDER_TRADE_UPDATE", "o": {"s": "SOLUSDT", "X": "FILLED"}},
            asyncio.CancelledError(),
        ]
    )
    client = MagicMock()
    client.close_connection = AsyncMock()
    manager = MagicMock()
    manager.futures_user_socket.return_value = socket

    async def fake_create(*_args, **_kwargs):
        return client

    monkeypatch.setattr("src.trading.user_data_stream.AsyncClient.create", fake_create)
    monkeypatch.setattr(
        "src.trading.user_data_stream.BinanceSocketManager",
        lambda _client: manager,
    )

    stream = FuturesUserDataStream(
        api_key="test-key",
        api_secret="test-secret",
        testnet=True,
        on_event=events.append,
    )
    await stream.start()
    for _ in range(20):
        if events:
            break
        await asyncio.sleep(0)
    await stream.stop()

    assert events[0]["e"] == "ORDER_TRADE_UPDATE"
    assert stream.event_count == 1
    assert stream.last_event_type == "ORDER_TRADE_UPDATE"


@pytest.mark.asyncio
async def test_engine_user_event_wakes_reconciliation():
    from src.trading.engine import TradingEngine

    engine = TradingEngine.__new__(TradingEngine)
    engine.symbol = "SOLUSDT"
    engine._live_position_reconciliation_wakeup = asyncio.Event()
    engine.on_agent_event = AsyncMock()

    await engine._on_user_data_event(
        {
            "e": "ORDER_TRADE_UPDATE",
            "o": {"s": "SOLUSDT", "X": "FILLED", "x": "TRADE"},
        }
    )

    assert engine._live_position_reconciliation_wakeup.is_set()
    engine.on_agent_event.assert_awaited_once()

    engine._live_position_reconciliation_wakeup.clear()
    await engine._on_user_data_event(
        {"e": "ALGO_UPDATE", "o": {"s": "SOLUSDT", "X": "FILLED"}}
    )
    assert engine._live_position_reconciliation_wakeup.is_set()


def test_engine_status_retains_stopped_user_stream_diagnostics():
    from src.trading.engine import TradingEngine

    engine = TradingEngine.__new__(TradingEngine)
    engine._running = False
    engine.symbol = "SOLUSDT"
    engine.timeframe = "1m"
    engine.paper_trading = False
    engine._kline_count = 61
    engine._consecutive_holds = 1
    engine._last_decision_time = None
    engine.market_data = MagicMock(current_price=77.84)
    engine._trading_graph = MagicMock()
    engine._user_data_stream = None
    engine._last_user_data_stream_status = {
        "running": False,
        "ready": False,
        "testnet": True,
        "event_count": 12,
        "reconnect_count": 0,
        "last_event_type": "ALGO_UPDATE",
        "last_error": None,
    }

    status = engine.get_status()

    assert status["user_data_stream"]["event_count"] == 12
    assert status["user_data_stream"]["reconnect_count"] == 0
