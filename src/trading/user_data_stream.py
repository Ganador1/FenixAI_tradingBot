"""Authenticated Binance Futures user-data stream with reconnect handling."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from binance import AsyncClient, BinanceSocketManager

logger = logging.getLogger("FenixUserDataStream")


EventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class FuturesUserDataStream:
    """Consume private Futures account/order events without exposing credentials."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        testnet: bool,
        on_event: EventCallback,
        reconnect_delay_sec: float = 2.0,
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError("Futures user-data stream requires API credentials")
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = bool(testnet)
        self.on_event = on_event
        self.reconnect_delay_sec = max(0.25, float(reconnect_delay_sec))
        self._running = False
        self._task: asyncio.Task | None = None
        self._client: AsyncClient | None = None
        self._ready = asyncio.Event()
        self.event_count = 0
        self.reconnect_count = 0
        self.last_event_type: str | None = None
        self.last_error: str | None = None

    async def start(self, timeout_sec: float = 15.0) -> None:
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._ready.clear()
        self._task = asyncio.create_task(self._run(), name="fenix-futures-user-data")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=max(1.0, timeout_sec))
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        self._running = False
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._close_client()

    async def _close_client(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.close_connection()
            except Exception:
                logger.debug("Failed to close Binance user-data client", exc_info=True)

    async def _dispatch(self, event: dict[str, Any]) -> None:
        self.event_count += 1
        self.last_event_type = str(event.get("e") or event.get("eventType") or "UNKNOWN")
        result = self.on_event(event)
        if inspect.isawaitable(result):
            await result

    async def _run(self) -> None:
        while self._running:
            try:
                self._client = await AsyncClient.create(
                    self.api_key,
                    self.api_secret,
                    testnet=self.testnet,
                )
                manager = BinanceSocketManager(self._client)
                socket = manager.futures_user_socket()
                async with socket as stream:
                    self.last_error = None
                    self._ready.set()
                    logger.info(
                        "Futures user-data stream connected (%s)",
                        "testnet" if self.testnet else "mainnet",
                    )
                    while self._running:
                        event = await stream.recv()
                        if not isinstance(event, dict):
                            continue
                        if event.get("e") == "error" or event.get("type") == "error":
                            raise RuntimeError(str(event.get("m") or event))
                        await self._dispatch(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.reconnect_count += 1
                logger.warning("Futures user-data stream disconnected: %s", self.last_error)
            finally:
                self._ready.clear()
                await self._close_client()
            if self._running:
                await asyncio.sleep(self.reconnect_delay_sec)

    def get_status(self) -> dict[str, Any]:
        return {
            "running": bool(self._task is not None and not self._task.done()),
            "ready": self._ready.is_set(),
            "testnet": self.testnet,
            "event_count": self.event_count,
            "reconnect_count": self.reconnect_count,
            "last_event_type": self.last_event_type,
            "last_error": self.last_error,
        }
