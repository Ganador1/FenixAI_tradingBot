"""Kline dispatch decoupling: the WS receive loop must never wait for a subscriber.

Regression for the live reconnect storms of 2026-07-09 (54-63 keepalive
disconnects/day per bot): `_process_kline` awaited every callback inline, so a
60s analysis cycle starved the socket reads until Binance dropped the stream.
"""

from __future__ import annotations

import asyncio

import pytest

from src.trading.market_data import MarketDataManager


def _kline_payload(close: str = "100.0", is_closed: bool = True, open_time: int = 1) -> dict:
    return {
        "k": {
            "s": "BTCUSDT",
            "i": "1m",
            "t": open_time,
            "T": open_time + 59_999,
            "o": "99.0",
            "h": "101.0",
            "l": "98.5",
            "c": close,
            "v": "10.0",
            "x": is_closed,
        }
    }


async def _wait_until(predicate, timeout: float = 2.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


def _manager_with_dispatcher() -> tuple[MarketDataManager, asyncio.Task]:
    manager = MarketDataManager(symbol="BTCUSDT", timeframe="1m")
    manager._running = True
    manager._kline_dispatch_queue = asyncio.Queue()
    dispatcher = asyncio.create_task(manager._dispatch_klines())
    return manager, dispatcher


async def _stop_dispatcher(manager: MarketDataManager, dispatcher: asyncio.Task) -> None:
    manager._running = False
    dispatcher.cancel()
    await asyncio.gather(dispatcher, return_exceptions=True)


@pytest.mark.asyncio
async def test_process_kline_returns_immediately_with_slow_subscriber():
    manager, dispatcher = _manager_with_dispatcher()
    release = asyncio.Event()
    received: list[dict] = []

    async def slow_callback(kline: dict) -> None:
        received.append(kline)
        await release.wait()

    manager.on_kline(slow_callback)
    try:
        # Both must return promptly even while the first delivery is stuck
        # inside the subscriber; before the fix the second call would block
        # until the analysis released the loop.
        await asyncio.wait_for(manager._process_kline(_kline_payload(open_time=1)), timeout=0.5)
        await asyncio.wait_for(manager._process_kline(_kline_payload(open_time=2)), timeout=0.5)

        assert await _wait_until(lambda: len(received) == 1)
        release.set()
        assert await _wait_until(lambda: len(received) == 2)
        # FIFO ordering is preserved across the queue.
        assert [k["open_time"] for k in received] == [1, 2]
    finally:
        release.set()
        await _stop_dispatcher(manager, dispatcher)


@pytest.mark.asyncio
async def test_process_kline_without_dispatcher_delivers_inline():
    """Direct callers (unit tests, ad-hoc consumers) keep the legacy behavior."""
    manager = MarketDataManager(symbol="BTCUSDT", timeframe="1m")
    got: list[dict] = []

    async def callback(kline: dict) -> None:
        got.append(kline)

    manager.on_kline(callback)
    await manager._process_kline(_kline_payload())
    assert len(got) == 1


@pytest.mark.asyncio
async def test_dispatcher_survives_subscriber_exception():
    manager, dispatcher = _manager_with_dispatcher()
    delivered: list[dict] = []

    async def flaky_callback(kline: dict) -> None:
        if kline["open_time"] == 1:
            raise RuntimeError("boom")
        delivered.append(kline)

    manager.on_kline(flaky_callback)
    try:
        await manager._process_kline(_kline_payload(open_time=1))
        await manager._process_kline(_kline_payload(open_time=2))
        assert await _wait_until(lambda: len(delivered) == 1)
        assert delivered[0]["open_time"] == 2
    finally:
        await _stop_dispatcher(manager, dispatcher)


@pytest.mark.asyncio
async def test_current_price_stays_fresh_while_subscriber_is_busy():
    """Price updates happen in the WS loop itself, independent of dispatch."""
    manager, dispatcher = _manager_with_dispatcher()
    release = asyncio.Event()

    async def slow_callback(kline: dict) -> None:
        await release.wait()

    manager.on_kline(slow_callback)
    try:
        await manager._process_kline(_kline_payload(close="100.0", open_time=1))
        await manager._process_kline(
            _kline_payload(close="105.5", is_closed=False, open_time=2)
        )
        assert manager.current_price == 105.5
    finally:
        release.set()
        await _stop_dispatcher(manager, dispatcher)
