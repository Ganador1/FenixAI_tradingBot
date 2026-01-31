#!/usr/bin/env python3
"""
Chart Provider - Interfaz simple para obtener charts del scheduler.

Este módulo provee una interfaz sencilla para que el Visual Agent
y el TradingEngine obtengan charts frescos sin preocuparse por
la captura subyacente.

Uso:
    from src.tools.chart_provider import get_chart, ensure_fresh_charts

    # Obtener un chart específico
    chart = get_chart("BTCUSDT", "15m")
    if chart:
        image_b64 = chart.image_b64
    
    # Asegurar charts frescos antes de un loop de trading
    await ensure_fresh_charts(["BTCUSDT", "ETHUSDT"], ["15m", "1h"])
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from src.tools.chart_capture_scheduler import (
    ChartCaptureScheduler,
    ChartSnapshot,
    TIMEFRAME_CONFIG,
)

logger = logging.getLogger(__name__)

# Singleton del scheduler
_scheduler: Optional[ChartCaptureScheduler] = None
_scheduler_started = False


def get_scheduler() -> ChartCaptureScheduler:
    """Obtiene o crea el singleton del scheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = ChartCaptureScheduler()
    return _scheduler


def start_scheduler(
    symbols: Optional[list[str]] = None,
    timeframes: Optional[list[str]] = None,
) -> ChartCaptureScheduler:
    """
    Inicia el scheduler de captura de charts.
    
    Llamar al inicio de la aplicación para tener charts
    pre-capturados disponibles.
    """
    global _scheduler, _scheduler_started
    
    if _scheduler_started:
        logger.info("Scheduler ya está corriendo")
        return get_scheduler()
    
    _scheduler = ChartCaptureScheduler(
        symbols=symbols,
        timeframes=timeframes,
    )
    _scheduler.start()
    _scheduler_started = True
    
    return _scheduler


def stop_scheduler() -> None:
    """Detiene el scheduler de captura."""
    global _scheduler, _scheduler_started
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler_started = False


def get_chart(
    symbol: str,
    timeframe: str,
    max_age_seconds: Optional[float] = None,
) -> Optional[ChartSnapshot]:
    """
    Obtiene un chart del caché.
    
    Args:
        symbol: Símbolo (ej: "BTCUSDT")
        timeframe: Timeframe (ej: "15m", "1h")
        max_age_seconds: Edad máxima aceptable (usa TTL por defecto)
    
    Returns:
        ChartSnapshot si hay uno válido, None si no
    """
    scheduler = get_scheduler()
    return scheduler.get_chart(symbol, timeframe, max_age_seconds)


def get_fresh_chart(symbol: str, timeframe: str) -> ChartSnapshot:
    """
    Obtiene un chart fresco (captura si es necesario).
    
    Primero intenta del caché. Si no hay válido,
    captura uno nuevo sincrónicamente.
    """
    scheduler = get_scheduler()
    return scheduler.get_fresh_chart(symbol, timeframe)


async def get_fresh_chart_async(symbol: str, timeframe: str) -> ChartSnapshot:
    """
    Versión async de get_fresh_chart.
    
    Ejecuta la captura en un executor para no bloquear el event loop.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        get_fresh_chart,
        symbol,
        timeframe,
    )


async def ensure_fresh_charts(
    symbols: list[str],
    timeframes: list[str],
    max_age_seconds: Optional[float] = None,
) -> dict[str, ChartSnapshot]:
    """
    Asegura que hay charts frescos disponibles.
    
    Para cada combinación symbol/timeframe, verifica el caché
    y captura uno nuevo si es necesario. Ejecuta capturas en paralelo.
    
    Args:
        symbols: Lista de símbolos
        timeframes: Lista de timeframes
        max_age_seconds: Edad máxima aceptable
        
    Returns:
        Dict con key "{symbol}_{timeframe}" y valor ChartSnapshot
    """
    scheduler = get_scheduler()
    results = {}
    tasks = []
    
    for symbol in symbols:
        for timeframe in timeframes:
            key = f"{symbol}_{timeframe}"
            cached = scheduler.get_chart(symbol, timeframe, max_age_seconds)
            
            if cached:
                results[key] = cached
            else:
                # Necesitamos capturar
                async def capture(s=symbol, tf=timeframe, k=key):
                    loop = asyncio.get_event_loop()
                    snapshot = await loop.run_in_executor(
                        None,
                        scheduler.capture_chart,
                        s,
                        tf,
                    )
                    return k, snapshot
                tasks.append(capture())
    
    # Ejecutar capturas pendientes en paralelo
    if tasks:
        logger.info("📸 Capturando %d charts faltantes...", len(tasks))
        captured = await asyncio.gather(*tasks, return_exceptions=True)
        for result in captured:
            if isinstance(result, Exception):
                logger.error("Error en captura: %s", result)
            else:
                key, snapshot = result
                results[key] = snapshot
    
    return results


def get_all_charts_for_symbol(
    symbol: str,
    timeframes: Optional[list[str]] = None,
) -> dict[str, ChartSnapshot]:
    """
    Obtiene todos los charts disponibles para un símbolo.
    
    Returns:
        Dict con key timeframe y valor ChartSnapshot
    """
    scheduler = get_scheduler()
    timeframes = timeframes or list(TIMEFRAME_CONFIG.keys())
    
    results = {}
    for tf in timeframes:
        chart = scheduler.get_chart(symbol, tf)
        if chart:
            results[tf] = chart
    
    return results


def get_scheduler_status() -> dict:
    """Obtiene el estado del scheduler."""
    scheduler = get_scheduler()
    return scheduler.get_status()


def is_scheduler_running() -> bool:
    """Verifica si el scheduler está corriendo."""
    global _scheduler_started
    return _scheduler_started


# =============================================================================
# Funciones de conveniencia para el Visual Agent
# =============================================================================

def get_chart_image_b64(symbol: str, timeframe: str) -> Optional[str]:
    """
    Shortcut para obtener solo la imagen base64.
    
    Ideal para el Visual Agent que solo necesita la imagen.
    """
    chart = get_chart(symbol, timeframe)
    return chart.image_b64 if chart else None


def get_chart_filepath(symbol: str, timeframe: str) -> Optional[str]:
    """
    Shortcut para obtener solo el filepath.
    """
    chart = get_chart(symbol, timeframe)
    return chart.filepath if chart else None


async def get_multi_timeframe_analysis_charts(
    symbol: str,
    timeframes: Optional[list[str]] = None,
) -> dict[str, str]:
    """
    Obtiene charts para análisis multi-timeframe.
    
    Retorna un dict con timeframe -> image_b64 para análisis visual.
    """
    timeframes = timeframes or ["15m", "1h", "4h"]
    await ensure_fresh_charts([symbol], timeframes)
    
    result = {}
    for tf in timeframes:
        chart = get_chart(symbol, tf)
        if chart and chart.image_b64:
            result[tf] = chart.image_b64
    
    return result


# =============================================================================
# Contexto para uso como context manager
# =============================================================================

class ChartSchedulerContext:
    """
    Context manager para el scheduler.
    
    Uso:
        async with ChartSchedulerContext(symbols=["BTCUSDT"]) as scheduler:
            chart = get_chart("BTCUSDT", "15m")
    """
    
    def __init__(
        self,
        symbols: Optional[list[str]] = None,
        timeframes: Optional[list[str]] = None,
    ):
        self.symbols = symbols
        self.timeframes = timeframes
    
    async def __aenter__(self) -> ChartCaptureScheduler:
        start_scheduler(self.symbols, self.timeframes)
        # Esperar a que se capturen los charts iniciales
        await asyncio.sleep(2)
        return get_scheduler()
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        stop_scheduler()
        return False


# =============================================================================
# Testing
# =============================================================================

async def _test():
    """Test del chart provider."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
    )
    
    print("🧪 Testing Chart Provider\n")
    
    # Test 1: Captura directa sin scheduler
    print("1️⃣ Test: Captura directa")
    chart = get_fresh_chart("BTCUSDT", "15m")
    print(f"   ✅ Chart capturado: {chart.symbol} {chart.timeframe}")
    print(f"   📏 Tamaño: {len(chart.image_b64)} bytes")
    print(f"   ⏱️ Tiempo: {chart.generation_time_ms:.0f}ms")
    
    # Test 2: Caché
    print("\n2️⃣ Test: Caché")
    cached = get_chart("BTCUSDT", "15m")
    if cached:
        print(f"   ✅ Chart del caché: edad {cached.age_seconds:.1f}s")
    else:
        print("   ❌ No hay cache hit")
    
    # Test 3: Multi-timeframe
    print("\n3️⃣ Test: Multi-timeframe async")
    charts = await ensure_fresh_charts(
        ["BTCUSDT"],
        ["1m", "5m", "15m", "1h"],
    )
    print(f"   ✅ Charts obtenidos: {list(charts.keys())}")
    
    # Test 4: Scheduler
    print("\n4️⃣ Test: Scheduler")
    start_scheduler(
        symbols=["BTCUSDT"],
        timeframes=["1m", "5m"],
    )
    await asyncio.sleep(5)
    
    status = get_scheduler_status()
    print(f"   Running: {status['running']}")
    print(f"   Cache entries: {status['cache']['valid_entries']}")
    
    stop_scheduler()
    print("\n✅ Tests completados!")


if __name__ == "__main__":
    asyncio.run(_test())
