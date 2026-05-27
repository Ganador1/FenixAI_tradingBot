"""
Performance Optimizer for Fenix Trading Bot
Sistema de optimización de rendimiento y gestión de recursos
"""

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from functools import wraps
from typing import Any

import psutil

logger = logging.getLogger(__name__)


class PerformanceCache:
    """Cache inteligente con expiración automática"""

    def __init__(self, max_size: int = 1000, ttl: int = 300):
        self.cache = OrderedDict()
        self.max_size = max_size
        self.ttl = ttl
        self._lock = threading.RLock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key in self.cache:
                value, timestamp = self.cache[key]
                if time.time() - timestamp < self.ttl:
                    # Mover al final (LRU)
                    self.cache.move_to_end(key)
                    return value
                else:
                    del self.cache[key]
            return None

    def set(self, key: str, value: Any):
        with self._lock:
            if key in self.cache:
                del self.cache[key]
            elif len(self.cache) >= self.max_size:
                self.cache.popitem(last=False)
            self.cache[key] = (value, time.time())

    def clear(self):
        with self._lock:
            self.cache.clear()


class CircuitBreaker:
    """Circuit breaker para manejo de fallos de servicios externos"""

    def __init__(self, failure_threshold: int = 5, timeout: int = 60, reset_timeout: int = 300):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = "closed"  # closed, open, half-open
        self._lock = threading.RLock()

    def call(self, func: Callable, *args, **kwargs):
        with self._lock:
            if self.state == "open":
                if time.time() - self.last_failure_time > self.reset_timeout:
                    self.state = "half-open"
                else:
                    raise Exception("Circuit breaker is OPEN")

            try:
                result = func(*args, **kwargs)
                if self.state == "half-open":
                    self.state = "closed"
                    self.failure_count = 0
                return result
            except Exception as e:
                self.failure_count += 1
                self.last_failure_time = time.time()
                if self.failure_count >= self.failure_threshold:
                    self.state = "open"
                raise e


class MemoryManager:
    """Gestión inteligente de memoria para modelos LLM"""

    def __init__(self, max_memory_gb: float = 8.0):
        self.max_memory_gb = max_memory_gb
        self.models = {}
        self.last_used = {}
        self._lock = threading.RLock()

    def register_model(self, model_id: str, model_instance):
        """Registrar un modelo en el gestor de memoria"""
        with self._lock:
            self.models[model_id] = model_instance
            self.last_used[model_id] = time.time()

    def get_model(self, model_id: str):
        """Obtener un modelo, gestionando la memoria automáticamente"""
        with self._lock:
            if model_id not in self.models:
                return None

            self.last_used[model_id] = time.time()

            # Verificar uso de memoria
            memory_usage = self.get_memory_usage()
            if memory_usage > self.max_memory_gb * 0.9:
                self._cleanup_least_used_models()

            return self.models[model_id]

    def get_memory_usage(self) -> float:
        """Obtener uso actual de memoria en GB"""
        process = psutil.Process()
        return process.memory_info().rss / (1024**3)

    def _cleanup_least_used_models(self):
        """Limpiar modelos menos usados"""
        sorted_models = sorted(self.last_used.items(), key=lambda x: x[1])

        # Eliminar el 20% menos usado
        to_remove = int(len(sorted_models) * 0.2)
        for model_id, _ in sorted_models[:to_remove]:
            if model_id in self.models:
                del self.models[model_id]
                del self.last_used[model_id]
                logger.info(f"Modelo {model_id} eliminado por gestión de memoria")


class TimeoutManager:
    """Gestión de timeouts para operaciones asíncronas"""

    @staticmethod
    async def run_with_timeout(coro, timeout: float, default=None):
        """Ejecutar una corutina con timeout"""
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Operation timeout after {timeout}s")
            return default
        except Exception as e:
            logger.error(f"Operation failed: {e}")
            return default


class PerformanceMonitor:
    """Monitoreo de rendimiento en tiempo real"""

    def __init__(self):
        self.metrics = {
            "api_calls": 0,
            "api_errors": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "memory_usage": 0.0,
            "cpu_usage": 0.0,
            "response_times": [],
        }
        self.start_time = time.time()

    def record_api_call(self, success: bool = True):
        self.metrics["api_calls"] += 1
        if not success:
            self.metrics["api_errors"] += 1

    def record_cache_operation(self, hit: bool):
        if hit:
            self.metrics["cache_hits"] += 1
        else:
            self.metrics["cache_misses"] += 1

    def record_response_time(self, duration: float):
        self.metrics["response_times"].append(duration)
        if len(self.metrics["response_times"]) > 100:
            self.metrics["response_times"].pop(0)

    def update_system_metrics(self):
        process = psutil.Process()
        self.metrics["memory_usage"] = process.memory_info().rss / (1024**3)
        self.metrics["cpu_usage"] = psutil.cpu_percent()

    def get_report(self) -> dict[str, Any]:
        self.update_system_metrics()

        avg_response_time = 0
        if self.metrics["response_times"]:
            avg_response_time = sum(self.metrics["response_times"]) / len(
                self.metrics["response_times"]
            )

        return {
            "uptime_seconds": time.time() - self.start_time,
            "api_success_rate": (1 - self.metrics["api_errors"] / max(self.metrics["api_calls"], 1))
            * 100,
            "cache_hit_rate": (
                self.metrics["cache_hits"]
                / max(self.metrics["cache_hits"] + self.metrics["cache_misses"], 1)
            )
            * 100,
            "memory_usage_gb": self.metrics["memory_usage"],
            "cpu_usage_percent": self.metrics["cpu_usage"],
            "avg_response_time_ms": avg_response_time * 1000,
            "total_api_calls": self.metrics["api_calls"],
            "total_errors": self.metrics["api_errors"],
        }


# Instancias globales
performance_cache = PerformanceCache()
memory_manager = MemoryManager()
performance_monitor = PerformanceMonitor()

# Circuit breakers para servicios externos
circuit_binance = CircuitBreaker(failure_threshold=10, timeout=120, reset_timeout=600)
circuit_mlx = CircuitBreaker(failure_threshold=5, timeout=60, reset_timeout=300)


def cached_result(ttl: int = 300):
    """Decorador para cachear resultados de funciones"""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            cache_key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
            cached = performance_cache.get(cache_key)

            if cached is not None:
                performance_monitor.record_cache_operation(hit=True)
                return cached

            start_time = time.time()
            result = await func(*args, **kwargs)
            duration = time.time() - start_time

            performance_monitor.record_cache_operation(hit=False)
            performance_monitor.record_response_time(duration)

            performance_cache.set(cache_key, result)
            return result

        return wrapper

    return decorator


def monitor_performance(func_name: str = None):
    """Decorador para monitorear rendimiento de funciones"""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                performance_monitor.record_response_time(duration)
                logger.debug(f"{func_name or func.__name__} completed in {duration:.2f}s")
                return result
            except Exception as e:
                duration = time.time() - start_time
                logger.error(f"{func_name or func.__name__} failed after {duration:.2f}s: {e}")
                raise

        return wrapper

    return decorator
