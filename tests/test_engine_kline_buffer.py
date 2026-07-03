"""Tests del buffer de indicadores frente a velas parciales y backfill.

Contexto (2026-07-01, sesión live 19): el engine ingería cada snapshot de la
vela EN FORMACIÓN al buffer de indicadores, convirtiendo la serie "15m" en
pseudo-ticks. Un RSI de 88.9 (el real de 15m era ~31) disparó un SELL que
cerró en falso una posición ganadora. Estos tests fijan el comportamiento
correcto: parciales solo refrescan precio; el buffer solo recibe velas
cerradas; y el arranque siembra históricas por REST.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.tools import technical_tools
from src.trading.engine import TradingEngine


@pytest.fixture()
def engine():
    technical_tools.clear_all_buffers()
    eng = TradingEngine(symbol="BTCUSDT", timeframe="15m", paper_trading=True)
    yield eng
    technical_tools.clear_all_buffers()


def _kline(close: float, *, is_closed: bool, open_time: int = 1_700_000_000_000) -> dict:
    return {
        "open_time": open_time,
        "close_time": open_time + 899_999,
        "open": close - 1.0,
        "high": close + 1.0,
        "low": close - 2.0,
        "close": close,
        "volume": 10.0,
        "is_closed": is_closed,
    }


def test_partial_kline_does_not_pollute_indicator_buffer(engine):
    before = len(technical_tools.close_buf)
    kline_count_before = engine._kline_count

    asyncio.run(engine._on_kline_received(_kline(50_000.0, is_closed=False)))

    assert len(technical_tools.close_buf) == before, (
        "una vela en formación no debe entrar al buffer de indicadores"
    )
    assert engine._kline_count == kline_count_before, (
        "una vela en formación no debe avanzar el warmup"
    )


def test_partial_kline_still_refreshes_market_price(engine):
    class _MarketData:
        current_price = 0.0

    engine.market_data = _MarketData()
    asyncio.run(engine._on_kline_received(_kline(50_123.5, is_closed=False)))
    assert engine.market_data.current_price == 50_123.5


def test_closed_kline_enters_buffer(engine):
    before = len(technical_tools.close_buf)
    asyncio.run(engine._on_kline_received(_kline(50_000.0, is_closed=True)))
    assert len(technical_tools.close_buf) == before + 1
    assert engine._kline_count == 1


class _FakeBinanceClient:
    """Cliente REST falso para el backfill: 3 cerradas + 1 en formación."""

    def __init__(self, testnet: bool = False):
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        step = 900_000  # 15m
        self._klines = []
        for i in range(3):
            open_time = now_ms - step * (4 - i)
            self._klines.append(
                {
                    "timestamp": open_time,
                    "close_time": open_time + step - 1,
                    "open": 100.0 + i,
                    "high": 101.0 + i,
                    "low": 99.0 + i,
                    "close": 100.5 + i,
                    "volume": 5.0,
                }
            )
        # Vela aún abierta (close_time en el futuro): debe descartarse.
        open_now = now_ms - 60_000
        self._klines.append(
            {
                "timestamp": open_now,
                "close_time": open_now + step - 1 + 10_000_000,
                "open": 103.0,
                "high": 104.0,
                "low": 102.0,
                "close": 103.5,
                "volume": 2.0,
            }
        )

    async def connect(self):
        return True

    async def get_klines(self, symbol, timeframe, limit=60):
        return self._klines[-limit:]

    async def close(self):
        return None


def test_backfill_seeds_buffer_with_closed_candles_only(engine, monkeypatch):
    monkeypatch.setattr("src.trading.engine.BinanceClient", _FakeBinanceClient)
    before = len(technical_tools.close_buf)

    injected = asyncio.run(engine._backfill_closed_klines(limit=10))

    assert injected == 3, "solo las 3 velas cerradas deben sembrarse"
    assert len(technical_tools.close_buf) == before + 3
    # La última vista queda marcada para que el WS no duplique.
    assert engine._last_closed_kline_open_time is not None
    # Y el warmup avanza con velas reales.
    assert engine._kline_count == 3


def test_backfill_disabled_with_zero_limit(engine, monkeypatch):
    monkeypatch.setattr("src.trading.engine.BinanceClient", _FakeBinanceClient)
    injected = asyncio.run(engine._backfill_closed_klines(limit=0))
    assert injected == 0
