#!/usr/bin/env python3
# src/utils/retry.py
"""
Módulo de Retry y Recuperación para Fenix Trading Bot.

Implementa patrones de resiliencia: exponential backoff, circuit breaker,
y recuperación automática.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """Estados del circuit breaker."""

    CLOSED = "closed"  # Normal, permitiendo requests
    OPEN = "open"  # Fallando, bloqueando requests
    HALF_OPEN = "half_open"  # Probando recuperación


@dataclass
class RetryConfig:
    """Configuración de retry."""

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True
    retryable_exceptions: tuple = (Exception,)


@dataclass
class CircuitBreakerConfig:
    """Configuración de circuit breaker."""

    failure_threshold: int = 5
    success_threshold: int = 2
    timeout: float = 30.0
    half_open_timeout: float = 10.0


@dataclass
class CircuitBreakerState:
    """Estado del circuit breaker."""

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: datetime | None = None


def calculate_delay(
    attempt: int,
    config: RetryConfig,
) -> float:
    """
    Calcula el delay para el próximo retry con exponential backoff.

    delay = min(base_delay * (exponential_base ^ attempt), max_delay)
    + jitter aleatorio opcional
    """
    delay = config.base_delay * (config.exponential_base**attempt)
    delay = min(delay, config.max_delay)

    if config.jitter:
        # Jitter de ±25%
        jitter_factor = 1 + random.uniform(-0.25, 0.25)
        delay *= jitter_factor

    return delay


def retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retryable_exceptions: tuple = (Exception,),
) -> Callable:
    """
    Decorator para retry con exponential backoff (funciones síncronas).

    Uso:
        @retry(max_retries=3)
        def fetch_data():
            ...
    """
    config = RetryConfig(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        retryable_exceptions=retryable_exceptions,
    )

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None

            for attempt in range(config.max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except config.retryable_exceptions as e:
                    last_exception = e

                    if attempt < config.max_retries:
                        delay = calculate_delay(attempt, config)
                        logger.warning(
                            f"Retry {attempt + 1}/{config.max_retries} for "
                            f"{func.__name__} after {delay:.2f}s: {e}"
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"All {config.max_retries} retries failed for {func.__name__}: {e}"
                        )

            raise last_exception

        return wrapper

    return decorator


def async_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retryable_exceptions: tuple = (Exception,),
) -> Callable:
    """
    Decorator para retry con exponential backoff (funciones async).

    Uso:
        @async_retry(max_retries=3)
        async def fetch_data():
            ...
    """
    config = RetryConfig(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        retryable_exceptions=retryable_exceptions,
    )

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception = None

            for attempt in range(config.max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except config.retryable_exceptions as e:
                    last_exception = e

                    if attempt < config.max_retries:
                        delay = calculate_delay(attempt, config)
                        logger.warning(
                            f"Async retry {attempt + 1}/{config.max_retries} for "
                            f"{func.__name__} after {delay:.2f}s: {e}"
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"All {config.max_retries} async retries failed for "
                            f"{func.__name__}: {e}"
                        )

            raise last_exception

        return wrapper

    return decorator


class CircuitBreaker:
    """
    Implementación de Circuit Breaker para proteger servicios.

    Estados:
    - CLOSED: Normal, requests permitidos
    - OPEN: Servicio fallando, requests bloqueados
    - HALF_OPEN: Probando si el servicio se recuperó

    Uso:
        breaker = CircuitBreaker(name="binance")

        @breaker
        async def call_api():
            ...
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        success_threshold: int = 2,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure: datetime | None = None

    @property
    def state(self) -> CircuitState:
        """Retorna el estado actual, actualizando si es necesario."""
        if self._state == CircuitState.OPEN:
            if self._should_try_recovery():
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
        return self._state

    def _should_try_recovery(self) -> bool:
        """Verifica si es momento de intentar recuperación."""
        if self._last_failure is None:
            return True

        elapsed = (datetime.now() - self._last_failure).total_seconds()
        return elapsed >= self.recovery_timeout

    def record_success(self) -> None:
        """Registra una llamada exitosa."""
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                logger.info(f"Circuit breaker '{self.name}' recovered, closing")
                self._state = CircuitState.CLOSED
                self._failure_count = 0
        elif self._state == CircuitState.CLOSED:
            self._failure_count = 0

    def record_failure(self, exception: Exception) -> None:
        """Registra una falla."""
        self._failure_count += 1
        self._last_failure = datetime.now()

        if self._state == CircuitState.HALF_OPEN:
            logger.warning(f"Circuit breaker '{self.name}' failed in half-open, reopening")
            self._state = CircuitState.OPEN
        elif self._failure_count >= self.failure_threshold:
            logger.error(
                f"Circuit breaker '{self.name}' opened after {self._failure_count} failures"
            )
            self._state = CircuitState.OPEN

    def __call__(self, func: Callable) -> Callable:
        """Permite usar el circuit breaker como decorator."""
        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                if self.state == CircuitState.OPEN:
                    raise CircuitBreakerOpenError(f"Circuit breaker '{self.name}' is open")

                try:
                    result = await func(*args, **kwargs)
                    self.record_success()
                    return result
                except Exception as e:
                    self.record_failure(e)
                    raise

            return async_wrapper
        else:

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                if self.state == CircuitState.OPEN:
                    raise CircuitBreakerOpenError(f"Circuit breaker '{self.name}' is open")

                try:
                    result = func(*args, **kwargs)
                    self.record_success()
                    return result
                except Exception as e:
                    self.record_failure(e)
                    raise

            return sync_wrapper

    def get_status(self) -> dict[str, Any]:
        """Retorna el estado actual del circuit breaker."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "last_failure": self._last_failure.isoformat() if self._last_failure else None,
        }


class CircuitBreakerOpenError(Exception):
    """Excepción cuando el circuit breaker está abierto."""

    pass


# ============================================================================
# CIRCUIT BREAKERS GLOBALES
# ============================================================================

_circuit_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 30.0,
) -> CircuitBreaker:
    """Obtiene o crea un circuit breaker por nombre."""
    if name not in _circuit_breakers:
        _circuit_breakers[name] = CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
        )
    return _circuit_breakers[name]


def get_all_circuit_breakers_status() -> list[dict[str, Any]]:
    """Retorna el estado de todos los circuit breakers."""
    return [cb.get_status() for cb in _circuit_breakers.values()]


# ============================================================================
# EJEMPLO DE USO
# ============================================================================

if __name__ == "__main__":
    import httpx

    # Ejemplo con retry
    @retry(max_retries=3, base_delay=0.5)
    def fetch_price_sync():
        """Simula fetch que puede fallar."""
        if random.random() < 0.7:  # 70% de falla
            raise ConnectionError("Simulated failure")
        return {"price": 67000}

    # Ejemplo con async retry y circuit breaker
    binance_breaker = get_circuit_breaker("binance", failure_threshold=3)

    @binance_breaker
    @async_retry(max_retries=2, base_delay=1.0)
    async def fetch_price_async():
        """Fetch con protección completa."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
            )
            return response.json()

    # Test
    print("=== Test de Retry ===")
    try:
        result = fetch_price_sync()
        print(f"Éxito: {result}")
    except ConnectionError as e:
        print(f"Falló después de todos los retries: {e}")

    print("\n=== Test de Circuit Breaker ===")
    print(f"Estado: {binance_breaker.get_status()}")
