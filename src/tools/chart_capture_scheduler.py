#!/usr/bin/env python3
"""
Chart Capture Scheduler - Sistema Autónomo de Captura de Charts Multi-Timeframe.

Este servicio mantiene un caché de charts frescos para que el Visual Agent
siempre tenga gráficos actualizados disponibles sin esperas.

Características:
- Captura programada por timeframe (1m cada 30s, 15m cada 5min, etc.)
- Sistema de caché con TTL automático
- Fallback robusto entre métodos de generación
- Métricas de salud y estadísticas
- Auto-recuperación en caso de errores
- Thread-safe para acceso concurrente

Uso:
    python -m src.tools.chart_capture_scheduler
    
    O como módulo:
    from src.tools.chart_capture_scheduler import ChartCaptureScheduler
    scheduler = ChartCaptureScheduler()
    scheduler.start()
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

# Añadir path del proyecto
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

logger = logging.getLogger(__name__)


# =============================================================================
# Configuración de Timeframes y sus intervalos de actualización
# =============================================================================

TIMEFRAME_CONFIG = {
    # timeframe: (intervalo_captura_segundos, ttl_segundos, prioridad)
    "1m": (30, 60, 1),       # Capturar cada 30s, válido por 60s (alta prioridad)
    "5m": (60, 180, 2),      # Capturar cada 1min, válido por 3min
    "15m": (180, 600, 3),    # Capturar cada 3min, válido por 10min  
    "1h": (300, 1800, 4),    # Capturar cada 5min, válido por 30min
    "4h": (600, 3600, 5),    # Capturar cada 10min, válido por 1h
    "1d": (900, 7200, 6),    # Capturar cada 15min, válido por 2h
}

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


class CaptureMethod(Enum):
    """Métodos de captura disponibles."""
    PLOTLY = "plotly"
    PLAYWRIGHT = "playwright"
    MPLFINANCE = "mplfinance"


@dataclass
class ChartSnapshot:
    """Representa un snapshot de chart capturado."""
    symbol: str
    timeframe: str
    timestamp: datetime
    method: CaptureMethod
    image_b64: str
    filepath: Optional[str] = None
    file_size_bytes: int = 0
    generation_time_ms: float = 0
    indicators: list[str] = field(default_factory=list)
    error: Optional[str] = None
    
    @property
    def cache_key(self) -> str:
        """Clave única para este chart."""
        return f"{self.symbol}_{self.timeframe}"
    
    @property
    def age_seconds(self) -> float:
        """Edad del snapshot en segundos."""
        return (datetime.now(timezone.utc) - self.timestamp.replace(tzinfo=timezone.utc)).total_seconds()
    
    def is_valid(self, max_age_seconds: Optional[float] = None) -> bool:
        """Verifica si el snapshot sigue siendo válido."""
        if max_age_seconds is None:
            _, ttl, _ = TIMEFRAME_CONFIG.get(self.timeframe, (60, 120, 10))
            max_age_seconds = ttl
        return self.age_seconds < max_age_seconds and self.image_b64 is not None
    
    def to_dict(self) -> dict:
        """Convierte a diccionario para serialización."""
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp.isoformat(),
            "method": self.method.value,
            "filepath": self.filepath,
            "file_size_bytes": self.file_size_bytes,
            "generation_time_ms": self.generation_time_ms,
            "indicators": self.indicators,
            "age_seconds": self.age_seconds,
            "is_valid": self.is_valid(),
        }


class ChartCache:
    """
    Caché thread-safe para almacenar snapshots de charts.
    
    Mantiene los charts más recientes por símbolo/timeframe
    con limpieza automática de entries expirados.
    """
    
    def __init__(self, max_size: int = 100):
        self._cache: dict[str, ChartSnapshot] = {}
        self._lock = threading.RLock()
        self._max_size = max_size
        self._stats = {
            "hits": 0,
            "misses": 0,
            "updates": 0,
            "evictions": 0,
        }
    
    def get(self, symbol: str, timeframe: str, max_age_seconds: Optional[float] = None) -> Optional[ChartSnapshot]:
        """
        Obtiene un snapshot del caché si existe y es válido.
        
        Returns:
            ChartSnapshot si existe y es válido, None en caso contrario
        """
        key = f"{symbol}_{timeframe}"
        with self._lock:
            snapshot = self._cache.get(key)
            if snapshot and snapshot.is_valid(max_age_seconds):
                self._stats["hits"] += 1
                return snapshot
            self._stats["misses"] += 1
            return None
    
    def put(self, snapshot: ChartSnapshot) -> None:
        """Almacena un snapshot en el caché."""
        with self._lock:
            # Evicción si excedemos tamaño máximo
            if len(self._cache) >= self._max_size:
                self._evict_oldest()
            
            self._cache[snapshot.cache_key] = snapshot
            self._stats["updates"] += 1
    
    def get_all_valid(self) -> list[ChartSnapshot]:
        """Retorna todos los snapshots válidos."""
        with self._lock:
            return [s for s in self._cache.values() if s.is_valid()]
    
    def get_status(self) -> dict:
        """Retorna el estado actual del caché."""
        with self._lock:
            valid = [s for s in self._cache.values() if s.is_valid()]
            expired = [s for s in self._cache.values() if not s.is_valid()]
            return {
                "total_entries": len(self._cache),
                "valid_entries": len(valid),
                "expired_entries": len(expired),
                "stats": self._stats.copy(),
                "entries": [s.to_dict() for s in self._cache.values()],
            }
    
    def cleanup_expired(self) -> int:
        """Limpia entries expirados. Retorna cantidad eliminada."""
        with self._lock:
            expired_keys = [k for k, v in self._cache.items() if not v.is_valid()]
            for key in expired_keys:
                del self._cache[key]
                self._stats["evictions"] += 1
            return len(expired_keys)
    
    def _evict_oldest(self) -> None:
        """Elimina el entry más antiguo."""
        if not self._cache:
            return
        oldest_key = min(self._cache, key=lambda k: self._cache[k].timestamp)
        del self._cache[oldest_key]
        self._stats["evictions"] += 1


class ChartCaptureScheduler:
    """
    Scheduler principal para captura automática de charts.
    
    Mantiene un servicio en background que captura charts de múltiples
    símbolos y timeframes según su configuración de intervalo.
    """
    
    def __init__(
        self,
        symbols: Optional[list[str]] = None,
        timeframes: Optional[list[str]] = None,
        enable_playwright: bool = False,
        save_to_disk: bool = True,
        cache_dir: str = "cache/charts",
    ):
        self.symbols = symbols or DEFAULT_SYMBOLS
        self.timeframes = timeframes or list(TIMEFRAME_CONFIG.keys())
        self.enable_playwright = enable_playwright
        self.save_to_disk = save_to_disk
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Cache compartido
        self.cache = ChartCache(max_size=len(self.symbols) * len(self.timeframes) * 2)
        
        # Scheduler de APScheduler
        self._scheduler = BackgroundScheduler(
            timezone="UTC",
            job_defaults={
                "coalesce": True,  # Combinar ejecuciones perdidas
                "max_instances": 1,  # Solo una instancia de cada job
                "misfire_grace_time": 30,  # Tolerancia de 30s para misfires
            }
        )
        
        # Generadores (lazy loading)
        self._plotly_generator = None
        self._binance_client = None
        
        # Estado
        self._running = False
        self._jobs_executed = 0
        self._jobs_failed = 0
        self._start_time: Optional[datetime] = None
        
        # Configurar event listeners
        self._scheduler.add_listener(self._on_job_executed, EVENT_JOB_EXECUTED)
        self._scheduler.add_listener(self._on_job_error, EVENT_JOB_ERROR)
    
    @property
    def plotly_generator(self):
        """Lazy loading del generador Plotly."""
        if self._plotly_generator is None:
            try:
                from src.tools.professional_chart_generator import ProfessionalChartGenerator
                self._plotly_generator = ProfessionalChartGenerator()
                logger.info("✅ ProfessionalChartGenerator inicializado")
            except Exception as e:
                logger.error("❌ Error inicializando ProfessionalChartGenerator: %s", e)
        return self._plotly_generator
    
    def _get_binance_client(self):
        """Lazy loading del cliente de Binance."""
        if self._binance_client is None:
            try:
                from binance.client import Client
                self._binance_client = Client()
                logger.info("✅ Binance client inicializado")
            except ImportError:
                logger.warning("⚠️ python-binance no instalado, usando datos simulados")
            except Exception as e:
                logger.error("❌ Error inicializando Binance client: %s", e)
        return self._binance_client
    
    def _fetch_klines(self, symbol: str, timeframe: str, limit: int = 100) -> Optional[dict]:
        """Obtiene datos de klines de Binance."""
        client = self._get_binance_client()
        if client is None:
            return self._generate_mock_klines(limit)
        
        try:
            # Mapear timeframe a formato Binance
            tf_map = {
                "1m": "1m", "5m": "5m", "15m": "15m",
                "1h": "1h", "4h": "4h", "1d": "1d"
            }
            interval = tf_map.get(timeframe, "15m")
            
            klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
            
            return {
                "open": [float(k[1]) for k in klines],
                "high": [float(k[2]) for k in klines],
                "low": [float(k[3]) for k in klines],
                "close": [float(k[4]) for k in klines],
                "volume": [float(k[5]) for k in klines],
                "datetime": [datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc) for k in klines],
            }
        except Exception as e:
            logger.error("Error fetching klines for %s %s: %s", symbol, timeframe, e)
            return self._generate_mock_klines(limit)
    
    def _generate_mock_klines(self, n: int = 100) -> dict:
        """Genera datos de kline simulados para testing."""
        import numpy as np
        np.random.seed(int(time.time()) % 1000)
        
        base_price = 97000 + np.random.randn() * 1000
        dates = [datetime.now(timezone.utc) - timedelta(minutes=15*i) for i in range(n)][::-1]
        
        prices = [base_price]
        for _ in range(n-1):
            prices.append(prices[-1] + np.random.randn() * 200)
        
        opens, highs, lows, closes, volumes = [], [], [], [], []
        for i in range(n):
            o = prices[i] + np.random.randn() * 50
            c = prices[i] + np.random.randn() * 50
            h = max(o, c) + abs(np.random.randn() * 100)
            l = min(o, c) - abs(np.random.randn() * 100)
            v = abs(np.random.randn() * 1000 + 500)
            opens.append(o)
            highs.append(h)
            lows.append(l)
            closes.append(c)
            volumes.append(v)
        
        return {
            "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": volumes, "datetime": dates
        }
    
    def capture_chart(self, symbol: str, timeframe: str) -> ChartSnapshot:
        """
        Captura un chart para el símbolo y timeframe dados.
        
        Usa el generador Plotly profesional con fallback automático.
        """
        start_time = time.time()
        error = None
        image_b64 = None
        filepath = None
        method = CaptureMethod.PLOTLY
        indicators = ['ema_9', 'ema_21', 'bb_bands', 'vwap']
        
        try:
            # 1. Obtener datos
            kline_data = self._fetch_klines(symbol, timeframe)
            if not kline_data:
                raise ValueError("No se pudieron obtener datos de klines")
            
            # 2. Generar chart con Plotly
            generator = self.plotly_generator
            if generator is None:
                raise ValueError("Generador Plotly no disponible")
            
            result = generator.generate_chart(
                kline_data=kline_data,
                symbol=symbol,
                timeframe=timeframe,
                show_indicators=indicators,
                show_volume=True,
                show_rsi=True,
                show_macd=False,  # Reducir complejidad para velocidad
            )
            
            image_b64 = result.get("image_b64")
            filepath = result.get("filepath")
            
            if not image_b64:
                raise ValueError("Chart generado sin imagen")
                
        except Exception as e:
            error = str(e)
            logger.error("Error capturando chart %s %s: %s", symbol, timeframe, e)
        
        generation_time = (time.time() - start_time) * 1000
        
        snapshot = ChartSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=datetime.now(timezone.utc),
            method=method,
            image_b64=image_b64 or "",
            filepath=filepath,
            file_size_bytes=len(image_b64) if image_b64 else 0,
            generation_time_ms=generation_time,
            indicators=indicators if image_b64 else [],
            error=error,
        )
        
        # Guardar en caché si fue exitoso
        if image_b64:
            self.cache.put(snapshot)
            logger.info(
                "📸 Chart capturado: %s %s (%.0fms, %d bytes)",
                symbol, timeframe, generation_time, len(image_b64)
            )
        
        return snapshot
    
    def _create_capture_job(self, symbol: str, timeframe: str):
        """Crea una función de captura para el scheduler."""
        def job():
            self.capture_chart(symbol, timeframe)
        job.__name__ = f"capture_{symbol}_{timeframe}"
        return job
    
    def _on_job_executed(self, event):
        """Callback cuando un job se ejecuta exitosamente."""
        self._jobs_executed += 1
    
    def _on_job_error(self, event):
        """Callback cuando un job falla."""
        self._jobs_failed += 1
        logger.error("Job error: %s", event.exception)
    
    def start(self) -> None:
        """Inicia el scheduler de captura."""
        if self._running:
            logger.warning("Scheduler ya está corriendo")
            return
        
        logger.info("=" * 60)
        logger.info("🚀 Iniciando Chart Capture Scheduler")
        logger.info("=" * 60)
        logger.info("Símbolos: %s", self.symbols)
        logger.info("Timeframes: %s", self.timeframes)
        
        # Programar jobs para cada combinación symbol/timeframe
        for symbol in self.symbols:
            for timeframe in self.timeframes:
                if timeframe not in TIMEFRAME_CONFIG:
                    continue
                    
                interval_seconds, _, priority = TIMEFRAME_CONFIG[timeframe]
                job_id = f"capture_{symbol}_{timeframe}"
                
                # Añadir job con trigger de intervalo
                self._scheduler.add_job(
                    self._create_capture_job(symbol, timeframe),
                    trigger=IntervalTrigger(seconds=interval_seconds),
                    id=job_id,
                    name=f"Capture {symbol} {timeframe}",
                    replace_existing=True,
                )
                logger.info(
                    "  📅 Programado: %s %s cada %ds (prioridad %d)",
                    symbol, timeframe, interval_seconds, priority
                )
        
        # Job de limpieza de caché cada 5 minutos
        self._scheduler.add_job(
            self._cleanup_job,
            trigger=IntervalTrigger(minutes=5),
            id="cache_cleanup",
            name="Cache Cleanup",
            replace_existing=True,
        )
        
        # Captura inicial de todos los charts
        logger.info("\n📸 Ejecutando captura inicial...")
        self._initial_capture()
        
        # Iniciar scheduler
        self._scheduler.start()
        self._running = True
        self._start_time = datetime.now(timezone.utc)
        
        logger.info("\n✅ Scheduler iniciado. Corriendo en background...")
        logger.info("=" * 60)
    
    def _initial_capture(self) -> None:
        """
        Captura inicial COMPLETA de todos los charts al arrancar.
        
        Asegura que el caché esté lleno con datos frescos desde el inicio,
        no solo el timeframe más frecuente.
        """
        logger.info("📸 Iniciando captura inicial de TODOS los timeframes...")
        total = len(self.symbols) * len(self.timeframes)
        captured = 0
        failed = 0
        
        for symbol in self.symbols:
            for timeframe in self.timeframes:
                if timeframe not in TIMEFRAME_CONFIG:
                    continue
                try:
                    # Verificar si ya hay un chart fresco en caché
                    existing = self.cache.get(symbol, timeframe)
                    if existing and existing.is_valid():
                        logger.debug("♻️ Cache hit para %s %s, saltando captura", symbol, timeframe)
                        captured += 1
                        continue
                    
                    # Capturar chart fresco
                    snapshot = self.capture_chart(symbol, timeframe)
                    if snapshot.image_b64:
                        captured += 1
                    else:
                        failed += 1
                except Exception as e:
                    logger.error("Error en captura inicial %s %s: %s", symbol, timeframe, e)
                    failed += 1
        
        logger.info(
            "✅ Captura inicial completada: %d/%d exitosos, %d fallidos",
            captured, total, failed
        )
    
    def _cleanup_job(self) -> None:
        """Job periódico para limpiar caché."""
        removed = self.cache.cleanup_expired()
        if removed > 0:
            logger.info("🧹 Cache cleanup: %d entries expirados eliminados", removed)
    
    def stop(self) -> None:
        """Detiene el scheduler."""
        if not self._running:
            return
        
        logger.info("🛑 Deteniendo Chart Capture Scheduler...")
        self._scheduler.shutdown(wait=True)
        self._running = False
        logger.info("✅ Scheduler detenido")
    
    def get_chart(self, symbol: str, timeframe: str, max_age_seconds: Optional[float] = None) -> Optional[ChartSnapshot]:
        """
        Obtiene un chart del caché.
        
        Este es el método principal que el Visual Agent debe usar.
        Retorna el chart más reciente si es válido, o None si no hay disponible.
        """
        return self.cache.get(symbol, timeframe, max_age_seconds)
    
    def get_fresh_chart(self, symbol: str, timeframe: str) -> ChartSnapshot:
        """
        Obtiene un chart fresco (captura inmediata si es necesario).
        
        Primero intenta del caché, si no hay o está expirado, captura uno nuevo.
        """
        cached = self.cache.get(symbol, timeframe)
        if cached:
            return cached
        return self.capture_chart(symbol, timeframe)
    
    def get_status(self) -> dict:
        """Retorna el estado actual del scheduler."""
        uptime = (datetime.now(timezone.utc) - self._start_time).total_seconds() if self._start_time else 0
        
        return {
            "running": self._running,
            "uptime_seconds": uptime,
            "uptime_human": str(timedelta(seconds=int(uptime))) if uptime else "Not started",
            "symbols": self.symbols,
            "timeframes": self.timeframes,
            "jobs_executed": self._jobs_executed,
            "jobs_failed": self._jobs_failed,
            "success_rate": f"{(self._jobs_executed / max(1, self._jobs_executed + self._jobs_failed)) * 100:.1f}%",
            "scheduled_jobs": len(self._scheduler.get_jobs()) if self._running else 0,
            "cache": self.cache.get_status(),
        }
    
    def print_status(self) -> None:
        """Imprime el estado actual de forma legible."""
        status = self.get_status()
        print("\n" + "=" * 60)
        print("📊 CHART CAPTURE SCHEDULER STATUS")
        print("=" * 60)
        print(f"Running: {'✅ Yes' if status['running'] else '❌ No'}")
        print(f"Uptime: {status['uptime_human']}")
        print(f"Symbols: {', '.join(status['symbols'])}")
        print(f"Timeframes: {', '.join(status['timeframes'])}")
        print(f"Jobs executed: {status['jobs_executed']}")
        print(f"Jobs failed: {status['jobs_failed']}")
        print(f"Success rate: {status['success_rate']}")
        print(f"Scheduled jobs: {status['scheduled_jobs']}")
        print("\n📦 Cache Status:")
        cache_status = status['cache']
        print(f"  Total entries: {cache_status['total_entries']}")
        print(f"  Valid entries: {cache_status['valid_entries']}")
        print(f"  Expired: {cache_status['expired_entries']}")
        print(f"  Hits/Misses: {cache_status['stats']['hits']}/{cache_status['stats']['misses']}")
        
        if cache_status['entries']:
            print("\n📸 Current snapshots:")
            for entry in cache_status['entries']:
                valid = "✅" if entry['is_valid'] else "❌"
                print(f"  {valid} {entry['symbol']} {entry['timeframe']} - age: {entry['age_seconds']:.0f}s")
        print("=" * 60)


# =============================================================================
# CLI para ejecución standalone
# =============================================================================

def main():
    """Punto de entrada para ejecución standalone."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Chart Capture Scheduler")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS, help="Símbolos a capturar")
    parser.add_argument("--timeframes", nargs="+", default=["1m", "5m", "15m", "1h"], help="Timeframes")
    parser.add_argument("--no-save", action="store_true", help="No guardar charts a disco")
    args = parser.parse_args()
    
    # Configurar logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S"
    )
    
    # Crear y arrancar scheduler
    scheduler = ChartCaptureScheduler(
        symbols=args.symbols,
        timeframes=args.timeframes,
        save_to_disk=not args.no_save,
    )
    
    # Manejar señales para shutdown graceful
    def signal_handler(sig, frame):
        print("\n\n⚠️ Señal de interrupción recibida...")
        scheduler.stop()
        scheduler.print_status()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Iniciar
    scheduler.start()
    
    # Mantener vivo el proceso principal
    try:
        while True:
            time.sleep(60)
            scheduler.print_status()
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.stop()


if __name__ == "__main__":
    main()
