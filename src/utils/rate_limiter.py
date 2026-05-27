"""
Rate Limiter Inteligente para HuggingFace API
Maneja límites de requests por minuto con backoff exponencial y colas por modelo
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Configuración de rate limiting"""

    requests_per_minute: int = 25  # HF Free tier limit
    max_queue_size: int = 100
    backoff_base: float = 2.0
    max_backoff: float = 60.0
    cleanup_interval: int = 300  # 5 minutos


class RequestToken:
    """Token para trackear requests individuales"""

    def __init__(self, model_id: str, priority: int = 1):
        self.model_id = model_id
        self.priority = priority
        self.timestamp = time.time()
        self.future: asyncio.Future | None = None


class ModelRateLimiter:
    """Rate limiter para un modelo específico"""

    def __init__(self, model_id: str, config: RateLimitConfig):
        self.model_id = model_id
        self.config = config

        # Tracking de requests
        self.request_times: deque = deque()
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=config.max_queue_size)

        # Backoff state
        self.consecutive_failures = 0
        self.last_failure_time = 0.0
        self.is_backing_off = False

        # Stats
        self.stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "rate_limited": 0,
            "queue_full_rejections": 0,
            "avg_wait_time": 0.0,
        }

        logger.info(f"🚦 Rate limiter creado para modelo: {model_id}")

    def _cleanup_old_requests(self) -> None:
        """Limpia requests antiguos (> 1 minuto)"""
        cutoff_time = time.time() - 60
        while self.request_times and self.request_times[0] < cutoff_time:
            self.request_times.popleft()

    def _can_make_request(self) -> bool:
        """Verifica si podemos hacer un request ahora"""
        self._cleanup_old_requests()

        # Check backoff
        if self.is_backing_off:
            backoff_duration = min(
                self.config.backoff_base**self.consecutive_failures, self.config.max_backoff
            )
            if time.time() - self.last_failure_time < backoff_duration:
                return False
            else:
                self.is_backing_off = False
                logger.info(f"🟢 [{self.model_id}] Backoff terminado, reiniciando requests")

        # Check rate limit
        return len(self.request_times) < self.config.requests_per_minute

    def _calculate_wait_time(self) -> float:
        """Calcula tiempo de espera hasta el próximo slot disponible"""
        if not self.request_times:
            return 0.0

        oldest_request = self.request_times[0]
        time_until_slot_free = 60 - (time.time() - oldest_request)
        return max(0.0, time_until_slot_free)

    async def acquire(self, priority: int = 1) -> bool:
        """
        Adquiere un slot para hacer request

        Args:
            priority: Prioridad del request (mayor = más prioritario)

        Returns:
            True si se puede proceder, False si se rechaza
        """
        request_start = time.time()

        try:
            # Check if queue is full
            if self.queue.full():
                self.stats["queue_full_rejections"] += 1
                logger.warning(f"🔴 [{self.model_id}] Cola llena, rechazando request")
                return False

            # Check immediate availability
            if self._can_make_request():
                self._record_request()
                return True

            # Calculate wait time
            wait_time = self._calculate_wait_time()

            if wait_time > 30:  # Don't wait more than 30 seconds
                logger.warning(
                    f"🟡 [{self.model_id}] Tiempo de espera muy largo ({wait_time:.1f}s), rechazando"
                )
                return False

            # Wait for slot
            logger.info(f"🟡 [{self.model_id}] Esperando slot disponible: {wait_time:.1f}s")

            await asyncio.sleep(wait_time)

            # Re-check availability after wait
            if self._can_make_request():
                self._record_request()
                wait_duration = time.time() - request_start
                self._update_wait_stats(wait_duration)
                return True
            else:
                logger.warning(f"🔴 [{self.model_id}] Slot no disponible después de esperar")
                return False

        except Exception as e:
            logger.error(f"🔴 [{self.model_id}] Error en rate limiter: {e}")
            return False

    def _record_request(self) -> None:
        """Registra un request exitoso"""
        current_time = time.time()
        self.request_times.append(current_time)
        self.stats["total_requests"] += 1

    def record_success(self) -> None:
        """Registra un request exitoso"""
        self.stats["successful_requests"] += 1
        self.consecutive_failures = 0

        if self.is_backing_off:
            self.is_backing_off = False
            logger.info(f"🟢 [{self.model_id}] Request exitoso, terminando backoff")

    def record_failure(self, is_rate_limit: bool = False) -> None:
        """Registra un request fallido"""
        self.stats["failed_requests"] += 1

        if is_rate_limit:
            self.stats["rate_limited"] += 1
            self.consecutive_failures += 1
            self.last_failure_time = time.time()
            self.is_backing_off = True

            backoff_duration = min(
                self.config.backoff_base**self.consecutive_failures, self.config.max_backoff
            )

            logger.warning(
                f"🟡 [{self.model_id}] Rate limited, iniciando backoff: {backoff_duration:.1f}s"
            )

    def _update_wait_stats(self, wait_time: float) -> None:
        """Actualiza estadísticas de tiempo de espera"""
        current_avg = self.stats["avg_wait_time"]
        total_requests = self.stats["total_requests"]

        if total_requests == 1:
            self.stats["avg_wait_time"] = wait_time
        else:
            self.stats["avg_wait_time"] = (
                current_avg * (total_requests - 1) + wait_time
            ) / total_requests

    def get_stats(self) -> dict[str, Any]:
        """Obtiene estadísticas del rate limiter"""
        self._cleanup_old_requests()

        return {
            "model_id": self.model_id,
            "current_requests_in_window": len(self.request_times),
            "requests_remaining": self.config.requests_per_minute - len(self.request_times),
            "is_backing_off": self.is_backing_off,
            "consecutive_failures": self.consecutive_failures,
            "queue_size": self.queue.qsize(),
            **self.stats,
        }


class IntelligentRateLimiter:
    """
    Rate Limiter Inteligente para múltiples modelos HuggingFace

    Características:
    - Rate limiting por modelo individual
    - Backoff exponencial en caso de rate limits
    - Colas de prioridad por modelo
    - Estadísticas detalladas
    - Auto-limpieza de datos antiguos
    """

    def __init__(self, config: RateLimitConfig | None = None):
        self.config = config or RateLimitConfig()
        self.limiters: dict[str, ModelRateLimiter] = {}
        self.global_stats = {
            "total_models": 0,
            "total_requests": 0,
            "total_successes": 0,
            "total_failures": 0,
            "created_at": datetime.utcnow().isoformat(),
        }

        # Start cleanup task
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

        logger.info("🚦 IntelligentRateLimiter inicializado")

    def _get_or_create_limiter(self, model_id: str) -> ModelRateLimiter:
        """Obtiene o crea un rate limiter para un modelo"""
        if model_id not in self.limiters:
            self.limiters[model_id] = ModelRateLimiter(model_id, self.config)
            self.global_stats["total_models"] += 1
            logger.info(f"🆕 Rate limiter creado para nuevo modelo: {model_id}")

        return self.limiters[model_id]

    async def acquire_for_model(self, model_id: str, priority: int = 1) -> bool:
        """
        Adquiere permiso para hacer request a un modelo específico

        Args:
            model_id: ID del modelo HuggingFace
            priority: Prioridad del request (1-10, mayor = más prioritario)

        Returns:
            True si se puede proceder con el request
        """
        limiter = self._get_or_create_limiter(model_id)
        success = await limiter.acquire(priority)

        if success:
            self.global_stats["total_requests"] += 1

        return success

    async def acquire(self, model_id: str = "default") -> bool:
        """
        Método de compatibilidad simple para acquire
        """
        return await self.acquire_for_model(model_id)

    def record_success(self, model_id: str) -> None:
        """Registra un request exitoso para un modelo"""
        if model_id in self.limiters:
            self.limiters[model_id].record_success()
            self.global_stats["total_successes"] += 1

    def record_failure(self, model_id: str, is_rate_limit: bool = False) -> None:
        """Registra un request fallido para un modelo"""
        if model_id in self.limiters:
            self.limiters[model_id].record_failure(is_rate_limit)
            self.global_stats["total_failures"] += 1

    def get_model_stats(self, model_id: str) -> dict[str, Any] | None:
        """Obtiene estadísticas para un modelo específico"""
        if model_id in self.limiters:
            return self.limiters[model_id].get_stats()
        return None

    def get_global_stats(self) -> dict[str, Any]:
        """Obtiene estadísticas globales"""
        model_stats = {}
        for model_id, limiter in self.limiters.items():
            model_stats[model_id] = limiter.get_stats()

        return {
            "global": self.global_stats,
            "models": model_stats,
            "summary": {
                "active_models": len(self.limiters),
                "total_requests_per_minute": sum(
                    stats["current_requests_in_window"] for stats in model_stats.values()
                ),
                "models_backing_off": sum(
                    1 for stats in model_stats.values() if stats["is_backing_off"]
                ),
            },
        }

    async def _periodic_cleanup(self) -> None:
        """Tarea periódica de limpieza"""
        while True:
            try:
                await asyncio.sleep(self.config.cleanup_interval)

                # Cleanup old data in all limiters
                for limiter in self.limiters.values():
                    limiter._cleanup_old_requests()

                logger.debug(f"🧹 Limpieza periódica completada para {len(self.limiters)} modelos")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error en limpieza periódica: {e}")

    async def shutdown(self) -> None:
        """Cierra el rate limiter limpiamente"""
        self._cleanup_task.cancel()
        try:
            await self._cleanup_task
        except asyncio.CancelledError:
            pass

        logger.info("🚦 IntelligentRateLimiter cerrado")


# Instancia global singleton
_global_rate_limiter: IntelligentRateLimiter | None = None


def get_rate_limiter() -> IntelligentRateLimiter:
    """Obtiene la instancia global del rate limiter"""
    global _global_rate_limiter

    if _global_rate_limiter is None:
        _global_rate_limiter = IntelligentRateLimiter()

    return _global_rate_limiter


async def shutdown_rate_limiter() -> None:
    """Cierra el rate limiter global"""
    global _global_rate_limiter

    if _global_rate_limiter is not None:
        await _global_rate_limiter.shutdown()
        _global_rate_limiter = None
