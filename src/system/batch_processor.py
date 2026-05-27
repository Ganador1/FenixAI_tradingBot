"""
Sistema de Procesamiento por Lotes para WebSocket y API
Optimiza llamadas a APIs y procesamiento de actualizaciones de precios
"""

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BatchItem:
    """Elemento individual en un lote"""

    data: Any
    timestamp: float
    callback: Callable | None = None
    priority: int = 0


@dataclass
class PriceBatch:
    """Lote de actualizaciones de precios"""

    symbol: str
    items: list[BatchItem] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def add_item(self, item: BatchItem) -> None:
        """Agrega un elemento al lote"""
        self.items.append(item)
        # Mantener ordenado por prioridad
        self.items.sort(key=lambda x: x.priority, reverse=True)

    def is_ready(self, max_size: int, max_wait: float) -> bool:
        """Verifica si el lote está listo para procesar"""
        return (len(self.items) >= max_size) or (time.time() - self.created_at >= max_wait)

    def size(self) -> int:
        """Tamaño actual del lote"""
        return len(self.items)


class PriceBatchProcessor:
    """Procesa actualizaciones de precios en lotes"""

    def __init__(
        self, batch_size: int = 10, max_wait_ms: int = 100, max_concurrent_batches: int = 5
    ):
        self.batch_size = batch_size
        self.max_wait_ms = max_wait_ms / 1000  # Convertir a segundos
        self.max_concurrent_batches = max_concurrent_batches

        self.batches: dict[str, PriceBatch] = defaultdict(lambda: PriceBatch(symbol=""))
        self.processing_semaphore = asyncio.Semaphore(max_concurrent_batches)
        self.is_running = False

    async def start(self) -> None:
        """Inicia el procesador de lotes"""
        if not self.is_running:
            self.is_running = True
            asyncio.create_task(self._batch_processor_loop())
            logger.info("🚀 PriceBatchProcessor iniciado")

    async def stop(self) -> None:
        """Detiene el procesador de lotes"""
        self.is_running = False
        logger.info("⏹️ PriceBatchProcessor detenido")

    def should_process(self) -> bool:
        """Verifica si hay lotes listos para procesar"""
        current_time = time.time()
        for symbol, batch in self.batches.items():
            if batch.is_ready(self.batch_size, self.max_wait_ms):
                return True
        return False

    def get_batch(self) -> list[BatchItem]:
        """Obtiene todos los items de lotes listos para procesar"""
        ready_items = []
        current_time = time.time()

        symbols_to_process = []
        for symbol, batch in list(self.batches.items()):
            if batch.is_ready(self.batch_size, self.max_wait_ms):
                symbols_to_process.append(symbol)
                ready_items.extend(batch.items)

        # Limpiar lotes procesados
        for symbol in symbols_to_process:
            if symbol in self.batches:
                del self.batches[symbol]

        return ready_items

    async def add_price_update(
        self,
        symbol: str,
        price_data: dict[str, Any],
        callback: Callable | None = None,
        priority: int = 0,
    ) -> None:
        """Agrega una actualización de precio al lote"""

        if symbol not in self.batches:
            self.batches[symbol] = PriceBatch(symbol=symbol)

        item = BatchItem(
            data=price_data, timestamp=time.time(), callback=callback, priority=priority
        )

        self.batches[symbol].add_item(item)

        # Si el lote está listo, procesar inmediatamente
        if self.batches[symbol].is_ready(self.batch_size, self.max_wait_ms):
            await self._process_batch(symbol)

    async def _batch_processor_loop(self) -> None:
        """Loop principal de procesamiento de lotes"""

        while self.is_running:
            try:
                current_time = time.time()

                # Procesar lotes vencidos
                symbols_to_process = []
                for symbol, batch in self.batches.items():
                    if batch.is_ready(self.batch_size, self.max_wait_ms):
                        symbols_to_process.append(symbol)

                # Procesar lotes
                tasks = [self._process_batch(symbol) for symbol in symbols_to_process]

                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

                await asyncio.sleep(0.01)  # Pequeña pausa

            except Exception as e:
                logger.error(f"Error en batch processor loop: {e}")
                await asyncio.sleep(1)

    async def _process_batch(self, symbol: str) -> None:
        """Procesa un lote completo de actualizaciones"""

        async with self.processing_semaphore:
            try:
                batch = self.batches[symbol]
                if not batch.items:
                    return

                # Consolidar datos del lote
                consolidated_data = self._consolidate_batch_data(batch)

                # Procesar actualizaciones
                await self._handle_batch_update(symbol, consolidated_data, batch.items)

                # Limpiar lote procesado
                del self.batches[symbol]

                logger.info(f"✅ Lote procesado para {symbol}: {batch.size()} items")

            except Exception as e:
                logger.error(f"Error procesando lote para {symbol}: {e}")

    def _consolidate_batch_data(self, batch: PriceBatch) -> dict[str, Any]:
        """Consolida datos de múltiples actualizaciones en un solo objeto"""

        if not batch.items:
            return {}

        # Tomar la última actualización como base
        latest = batch.items[-1].data

        # Calcular estadísticas
        prices = [item.data.get("price", 0) for item in batch.items if "price" in item.data]
        volumes = [item.data.get("volume", 0) for item in batch.items if "volume" in item.data]

        consolidated = {
            "symbol": batch.symbol,
            "latest_price": latest.get("price", 0),
            "latest_volume": latest.get("volume", 0),
            "price_change": latest.get("price_change", 0),
            "price_change_percent": latest.get("price_change_percent", 0),
            "batch_stats": {
                "item_count": len(batch.items),
                "price_range": {
                    "min": min(prices) if prices else 0,
                    "max": max(prices) if prices else 0,
                    "avg": sum(prices) / len(prices) if prices else 0,
                },
                "volume_range": {
                    "min": min(volumes) if volumes else 0,
                    "max": max(volumes) if volumes else 0,
                    "avg": sum(volumes) / len(volumes) if volumes else 0,
                },
                "batch_duration": time.time() - batch.created_at,
            },
        }

        return consolidated

    async def _handle_batch_update(
        self, symbol: str, consolidated_data: dict[str, Any], original_items: list[BatchItem]
    ) -> None:
        """Maneja la actualización procesada del lote"""

        # Ejecutar callbacks individuales si existen
        for item in original_items:
            if item.callback:
                try:
                    if asyncio.iscoroutinefunction(item.callback):
                        await item.callback(symbol, consolidated_data)
                    else:
                        item.callback(symbol, consolidated_data)
                except Exception as e:
                    logger.error(f"Error en callback: {e}")


class OptimizedBinanceClient:
    """Cliente Binance optimizado con procesamiento por lotes"""

    def __init__(self, binance_client=None):
        self.binance_client = binance_client
        self.batch_processor = PriceBatchProcessor(
            batch_size=5, max_wait_ms=50, max_concurrent_batches=3
        )
        self.symbol_cache: dict[str, dict[str, Any]] = {}

    def __getattr__(self, name):
        """Delega todos los métodos al cliente binance original"""
        if self.binance_client:
            return getattr(self.binance_client, name)
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    async def start(self) -> None:
        """Inicia el cliente optimizado"""
        await self.batch_processor.start()

    async def stop(self) -> None:
        """Detiene el cliente optimizado"""
        await self.batch_processor.stop()

    async def get_multiple_symbols(self, symbols: list[str]) -> dict[str, Any]:
        """Obtiene datos de múltiples símbolos en un solo llamado"""

        try:
            # Implementar llamada batch real a Binance
            # Por ahora, simularemos con datos del cache
            results = {}

            for symbol in symbols:
                if symbol in self.symbol_cache:
                    results[symbol] = self.symbol_cache[symbol]
                else:
                    # Aquí iría la llamada real a la API
                    # Por ahora, datos simulados
                    results[symbol] = {
                        "symbol": symbol,
                        "price": 50000.0,
                        "volume": 1000000.0,
                        "change_24h": 2.5,
                    }
                    self.symbol_cache[symbol] = results[symbol]

            return results

        except Exception as e:
            logger.error(f"Error obteniendo símbolos múltiples: {e}")
            return {}

    async def subscribe_price_updates(self, symbols: list[str], callback: Callable) -> None:
        """Suscribe a actualizaciones de precios en lotes"""

        for symbol in symbols:
            await self.batch_processor.add_price_update(
                symbol,
                {"symbol": symbol, "price": 50000.0, "volume": 1000000.0},
                callback=callback,
                priority=10,
            )


# Instancias globales
price_batch_processor = PriceBatchProcessor()
optimized_binance = OptimizedBinanceClient()
