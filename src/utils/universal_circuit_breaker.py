# utils/universal_circuit_breaker.py
import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Circuit is open, calls fail fast
    HALF_OPEN = "half_open"  # Testing if service is back


class CircuitOpenException(Exception):
    """Excepción lanzada cuando el circuit breaker está abierto"""
    pass


class ServiceUnavailableException(Exception):
    """Excepción lanzada cuando el servicio no está disponible"""
    pass


@dataclass
class CircuitBreakerConfig:
    """Configuración del Circuit Breaker"""
    failure_threshold: int = 5
    recovery_timeout_seconds: int = 60  # segundos
    half_open_max_calls: int = 3
    success_threshold: int = 2

    # Mantener compatibilidad con recovery_timeout
    @property
    def recovery_timeout(self) -> int:
        return self.recovery_timeout_seconds
    timeout: float = 30.0
    expected_exception: tuple = (Exception,)


@dataclass
class CallResult:
    """Resultado de una llamada"""
    success: bool
    timestamp: float
    duration: float
    exception: Exception | None = None


class UniversalCircuitBreaker:
    """Circuit breaker universal con estrategias de fallback"""

    def __init__(self, service_name: str, config: CircuitBreakerConfig | None = None):
        self.service_name = service_name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0
        self.half_open_calls = 0
        self.call_history = deque(maxlen=100)
        self.fallback_strategies = {}
        self.metrics = {
            'total_calls': 0,
            'successful_calls': 0,
            'failed_calls': 0,
            'circuit_opens': 0,
            'fallback_calls': 0
        }

    def add_fallback_strategy(self, strategy_name: str, fallback_func: Callable):
        """Agregar estrategia de fallback"""
        self.fallback_strategies[strategy_name] = fallback_func
        logger.info(f"Added fallback strategy '{strategy_name}' for {self.service_name}")

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Ejecutar función con circuit breaker"""
        self.metrics['total_calls'] += 1

        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self._move_to_half_open()
            else:
                raise CircuitOpenException(f"Circuit breaker is OPEN for {self.service_name}")

        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_calls >= self.config.half_open_max_calls:
                raise CircuitOpenException(f"Half-open call limit reached for {self.service_name}")

        return await self._execute_call(func, *args, **kwargs)

    async def execute_with_fallback(self, primary_func: Callable, fallback_strategy: str = None, *args, **kwargs) -> Any:
        """Ejecutar con estrategia de fallback automática"""
        try:
            return await self.call(primary_func, *args, **kwargs)
        except CircuitOpenException:
            if fallback_strategy and fallback_strategy in self.fallback_strategies:
                self.metrics['fallback_calls'] += 1
                logger.warning(f"Using fallback strategy '{fallback_strategy}' for {self.service_name}")
                return await self.fallback_strategies[fallback_strategy](*args, **kwargs)
            else:
                # Intentar fallback automático
                return await self._try_automatic_fallback(*args, **kwargs)

    async def _execute_call(self, func: Callable, *args, **kwargs) -> Any:
        """Ejecutar llamada y manejar resultado"""
        start_time = time.time()

        try:
            if asyncio.iscoroutinefunction(func):
                result = await asyncio.wait_for(func(*args, **kwargs), timeout=self.config.timeout)
            else:
                result = func(*args, **kwargs)

            duration = time.time() - start_time
            self._record_success(duration)
            return result

        except Exception as e:
            duration = time.time() - start_time
            self._record_failure(e, duration)
            raise

    def _record_success(self, duration: float):
        """Registrar llamada exitosa"""
        self.metrics['successful_calls'] += 1
        self.call_history.append(CallResult(True, time.time(), duration))

        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                self._move_to_closed()
        else:
            self.failure_count = 0

    def _record_failure(self, exception: Exception, duration: float):
        """Registrar llamada fallida"""
        self.metrics['failed_calls'] += 1
        self.call_history.append(CallResult(False, time.time(), duration, exception))

        if isinstance(exception, self.config.expected_exception):
            self.failure_count += 1
            self.last_failure_time = time.time()

            if self.state == CircuitState.HALF_OPEN:
                self._move_to_open()
            elif self.failure_count >= self.config.failure_threshold:
                self._move_to_open()

    def _should_attempt_reset(self) -> bool:
        """Verificar si se debe intentar reset del circuit"""
        return time.time() - self.last_failure_time >= self.config.recovery_timeout

    def _move_to_closed(self):
        """Mover circuit breaker a estado CLOSED"""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.half_open_calls = 0
        logger.info(f"Circuit breaker CLOSED for {self.service_name}")

    def _move_to_open(self):
        """Mover circuit breaker a estado OPEN"""
        self.state = CircuitState.OPEN
        self.metrics['circuit_opens'] += 1
        logger.warning(f"Circuit breaker OPENED for {self.service_name}")

    def _move_to_half_open(self):
        """Mover circuit breaker a estado HALF_OPEN"""
        self.state = CircuitState.HALF_OPEN
        self.half_open_calls = 0
        self.success_count = 0
        logger.info(f"Circuit breaker HALF_OPEN for {self.service_name}")

    async def _try_automatic_fallback(self, *args, **kwargs) -> Any:
        """Intentar fallback automático basado en el tipo de servicio"""
        if self.service_name.lower() == 'binance':
            return await self._binance_fallback(*args, **kwargs)
        elif self.service_name.lower() in ['openai', 'anthropic']:
            return await self._llm_fallback(*args, **kwargs)
        elif self.service_name.lower() == 'mlx':
            return await self._mlx_fallback(*args, **kwargs)
        else:
            raise ServiceUnavailableException(f"No fallback available for {self.service_name}")

    async def _binance_fallback(self, *args, **kwargs) -> Any:
        """Fallback para Binance - usar datos en cache o simulados"""
        logger.warning("Using Binance fallback - cached/simulated data")
        # Implementar lógica de fallback específica para Binance
        return {"status": "fallback", "data": "cached_market_data"}

    async def _llm_fallback(self, *args, **kwargs) -> Any:
        """Fallback para LLM - usar respuesta simplificada"""
        logger.warning("Using LLM fallback - simplified response")
        return {"analysis": "Service temporarily unavailable", "confidence": 0.1}

    async def _mlx_fallback(self, *args, **kwargs) -> Any:
        """Fallback para MLX - usar CPU o modelo simplificado"""
        logger.warning("Using MLX fallback - CPU computation")
        return {"prediction": 0.0, "confidence": 0.0, "fallback": True}

    def get_metrics(self) -> dict[str, Any]:
        """Obtener métricas del circuit breaker"""
        success_rate = 0
        if self.metrics['total_calls'] > 0:
            success_rate = self.metrics['successful_calls'] / self.metrics['total_calls']

        avg_response_time = 0
        if self.call_history:
            avg_response_time = sum(call.duration for call in self.call_history) / len(self.call_history)

        return {
            'service_name': self.service_name,
            'state': self.state.value,
            'failure_count': self.failure_count,
            'success_rate': success_rate,
            'avg_response_time': avg_response_time,
            **self.metrics
        }

    def reset(self):
        """Reset manual del circuit breaker"""
        self._move_to_closed()
        logger.info(f"Circuit breaker manually reset for {self.service_name}")


class CircuitBreakerManager:
    """Gestor centralizado de circuit breakers"""

    def __init__(self):
        self.circuit_breakers: dict[str, UniversalCircuitBreaker] = {}
        self.global_config = CircuitBreakerConfig()

    def get_circuit_breaker(self, service_name: str, config: CircuitBreakerConfig | None = None) -> UniversalCircuitBreaker:
        """Obtener o crear circuit breaker para un servicio"""
        if service_name not in self.circuit_breakers:
            self.circuit_breakers[service_name] = UniversalCircuitBreaker(
                service_name,
                config or self.global_config
            )
        return self.circuit_breakers[service_name]

    def get_all_metrics(self) -> dict[str, dict[str, Any]]:
        """Obtener métricas de todos los circuit breakers"""
        return {name: cb.get_metrics() for name, cb in self.circuit_breakers.items()}

    def reset_all(self):
        """Reset de todos los circuit breakers"""
        for cb in self.circuit_breakers.values():
            cb.reset()
        logger.info("All circuit breakers reset")

    async def health_check(self) -> dict[str, str]:
        """Verificar salud de todos los servicios"""
        health_status = {}
        for name, cb in self.circuit_breakers.items():
            if cb.state == CircuitState.OPEN:
                health_status[name] = "unhealthy"
            elif cb.state == CircuitState.HALF_OPEN:
                health_status[name] = "recovering"
            else:
                health_status[name] = "healthy"
        return health_status


# Instancia global del gestor
_circuit_breaker_manager = CircuitBreakerManager()


def get_circuit_breaker(service_name: str, config: CircuitBreakerConfig | None = None) -> UniversalCircuitBreaker:
    """Función de conveniencia para obtener circuit breaker"""
    return _circuit_breaker_manager.get_circuit_breaker(service_name, config)


def get_circuit_breaker_manager() -> CircuitBreakerManager:
    """Obtener gestor de circuit breakers"""
    return _circuit_breaker_manager
