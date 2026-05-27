"""
Sistema de Fallback Bidireccional Avanzado para FenixAI Trading Bot
Incluye health checks predictivos, recuperación automática y balanceado inteligente
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ProviderHealth(Enum):
    """Estados de salud del proveedor"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    MAINTENANCE = "maintenance"


class FallbackStrategy(Enum):
    """Estrategias de fallback"""

    IMMEDIATE = "immediate"  # Fallback inmediato en error
    RETRY_FIRST = "retry_first"  # Reintentar antes de fallback
    CIRCUIT_BREAKER = "circuit_breaker"  # Circuit breaker pattern
    ADAPTIVE = "adaptive"  # Adaptativo según historial


@dataclass
class HealthMetrics:
    """Métricas de salud de un proveedor"""

    provider: str
    health_status: ProviderHealth = ProviderHealth.UNKNOWN
    response_times: deque = field(default_factory=lambda: deque(maxlen=100))
    error_rate: float = 0.0
    success_rate: float = 100.0
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    last_success: float | None = None
    last_failure: float | None = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    avg_response_time: float = 0.0
    p95_response_time: float = 0.0
    cost_per_request: float = 0.0
    total_cost: float = 0.0

    def update_success(self, response_time: float, cost: float = 0.0):
        """Actualizar métricas de éxito"""
        current_time = time.time()
        self.total_requests += 1
        self.successful_requests += 1
        self.consecutive_successes += 1
        self.consecutive_failures = 0
        self.last_success = current_time
        self.total_cost += cost

        self.response_times.append(response_time)
        self.success_rate = (self.successful_requests / self.total_requests) * 100
        self.error_rate = 100 - self.success_rate

        if self.response_times:
            self.avg_response_time = sum(self.response_times) / len(self.response_times)
            sorted_times = sorted(self.response_times)
            self.p95_response_time = sorted_times[int(len(sorted_times) * 0.95)]

        # Actualizar estado de salud
        self._update_health_status()

    def update_failure(self, error: str):
        """Actualizar métricas de fallo"""
        current_time = time.time()
        self.total_requests += 1
        self.failed_requests += 1
        self.consecutive_failures += 1
        self.consecutive_successes = 0
        self.last_failure = current_time

        self.success_rate = (self.successful_requests / self.total_requests) * 100
        self.error_rate = 100 - self.success_rate

        # Actualizar estado de salud
        self._update_health_status()

    def _update_health_status(self):
        """Actualizar estado de salud basado en métricas"""
        current_time = time.time()

        # Si hay muchos fallos consecutivos
        if self.consecutive_failures >= 5:
            self.health_status = ProviderHealth.UNHEALTHY
            return

        # Si el error rate es muy alto en las últimas requests
        if self.error_rate > 50 and self.total_requests >= 10:
            self.health_status = ProviderHealth.UNHEALTHY
            return

        # Si no ha habido éxito reciente
        if self.last_success and current_time - self.last_success > 300:  # 5 minutos
            self.health_status = ProviderHealth.DEGRADED
            return

        # Si el tiempo de respuesta es muy alto
        if self.avg_response_time > 10000:  # 10 segundos
            self.health_status = ProviderHealth.DEGRADED
            return

        # Si todo está bien
        if self.error_rate < 10 and self.avg_response_time < 5000:
            self.health_status = ProviderHealth.HEALTHY
        elif self.error_rate < 25:
            self.health_status = ProviderHealth.DEGRADED
        else:
            self.health_status = ProviderHealth.UNHEALTHY


@dataclass
class FallbackConfig:
    """Configuración del sistema de fallback"""

    max_retries: int = 3
    retry_delay: float = 1.0
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: float = 60.0
    health_check_interval: float = 30.0
    response_timeout: float = 10.0
    fallback_strategy: FallbackStrategy = FallbackStrategy.ADAPTIVE
    enable_predictive_health: bool = True
    enable_load_balancing: bool = True
    cost_optimization: bool = True


class BidirectionalFallbackSystem:
    """Sistema de Fallback Bidireccional con Health Checks Predictivos"""

    def __init__(self, config: FallbackConfig | None = None):
        self.config = config or FallbackConfig()
        self.providers_health: dict[str, HealthMetrics] = {}
        self.circuit_breakers: dict[str, dict[str, Any]] = {}
        self.load_balancer_weights: dict[str, float] = {}
        self.fallback_chains: dict[str, list[str]] = {}
        self.health_check_task: asyncio.Task | None = None
        self.is_monitoring = False

        # Estadísticas del sistema
        self.system_stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "fallback_requests": 0,
            "circuit_breaker_trips": 0,
            "provider_switches": 0,
            "cost_optimizations": 0,
            "predictive_interventions": 0,
        }

        logger.info(
            "🔄 BidirectionalFallbackSystem initialized with strategy: %s",
            self.config.fallback_strategy.value,
        )

    def register_provider(self, provider_name: str, priority: int = 1):
        """Registrar un proveedor en el sistema"""
        if provider_name not in self.providers_health:
            self.providers_health[provider_name] = HealthMetrics(provider=provider_name)
            self.circuit_breakers[provider_name] = {
                "state": "closed",  # closed, open, half-open
                "failure_count": 0,
                "last_failure_time": 0,
                "next_attempt_time": 0,
            }
            self.load_balancer_weights[provider_name] = priority

            logger.info("✅ Provider '%s' registered with priority %d", provider_name, priority)

    def set_fallback_chain(self, agent_type: str, provider_chain: list[str]):
        """Configurar cadena de fallback para un tipo de agente"""
        self.fallback_chains[agent_type] = provider_chain
        logger.info("🔗 Fallback chain for '%s': %s", agent_type, " → ".join(provider_chain))

    async def start_monitoring(self):
        """Iniciar monitoreo de salud predictivo"""
        if not self.is_monitoring:
            self.is_monitoring = True
            self.health_check_task = asyncio.create_task(self._health_monitor_loop())
            logger.info("🔍 Predictive health monitoring started")

    async def stop_monitoring(self):
        """Detener monitoreo de salud"""
        if self.health_check_task:
            self.health_check_task.cancel()
            try:
                await self.health_check_task
            except asyncio.CancelledError:
                pass
            self.is_monitoring = False
            logger.info("⏹️ Health monitoring stopped")

    async def _health_monitor_loop(self):
        """Loop principal de monitoreo de salud"""
        while self.is_monitoring:
            try:
                await self._perform_health_checks()
                await self._update_load_balancer_weights()
                await self._check_predictive_interventions()
                await asyncio.sleep(self.config.health_check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in health monitor: %s", e)
                await asyncio.sleep(5)

    async def _perform_health_checks(self):
        """Realizar health checks de todos los proveedores"""
        for provider_name, metrics in self.providers_health.items():
            try:
                # Verificar circuit breaker
                cb = self.circuit_breakers[provider_name]
                current_time = time.time()

                if cb["state"] == "open":
                    if current_time >= cb["next_attempt_time"]:
                        cb["state"] = "half-open"
                        logger.info("🔄 Circuit breaker for '%s' moved to half-open", provider_name)

                # Health check predictivo basado en tendencias
                if self.config.enable_predictive_health:
                    await self._predictive_health_check(provider_name, metrics)

            except Exception as e:
                logger.error("Health check failed for '%s': %s", provider_name, e)

    async def _predictive_health_check(self, provider_name: str, metrics: HealthMetrics):
        """Health check predictivo basado en tendencias"""
        current_time = time.time()

        # Predicción basada en tendencia de errores
        if len(metrics.response_times) >= 10:
            recent_times = list(metrics.response_times)[-10:]
            avg_recent = sum(recent_times) / len(recent_times)

            # Si el tiempo de respuesta está aumentando dramáticamente
            if avg_recent > metrics.avg_response_time * 2:
                if metrics.health_status == ProviderHealth.HEALTHY:
                    metrics.health_status = ProviderHealth.DEGRADED
                    self.system_stats["predictive_interventions"] += 1
                    logger.warning(
                        "🔮 Predictive intervention: %s degraded due to response time trend",
                        provider_name,
                    )

        # Predicción basada en tiempo desde último éxito
        if metrics.last_success and current_time - metrics.last_success > 120:  # 2 minutos
            if metrics.health_status == ProviderHealth.HEALTHY:
                metrics.health_status = ProviderHealth.DEGRADED
                self.system_stats["predictive_interventions"] += 1
                logger.warning(
                    "🔮 Predictive intervention: %s degraded due to no recent success",
                    provider_name,
                )

    async def _update_load_balancer_weights(self):
        """Actualizar pesos del load balancer basado en salud"""
        if not self.config.enable_load_balancing:
            return

        for provider_name, metrics in self.providers_health.items():
            base_weight = 1.0

            # Ajustar peso basado en salud
            if metrics.health_status == ProviderHealth.HEALTHY:
                health_multiplier = 1.0
            elif metrics.health_status == ProviderHealth.DEGRADED:
                health_multiplier = 0.5
            elif metrics.health_status == ProviderHealth.UNHEALTHY:
                health_multiplier = 0.1
            else:
                health_multiplier = 0.0

            # Ajustar peso basado en performance
            if metrics.avg_response_time > 0:
                # Menor tiempo de respuesta = mayor peso
                time_multiplier = min(1.0, 1000 / max(metrics.avg_response_time, 100))
            else:
                time_multiplier = 1.0

            # Ajustar peso basado en costo si está habilitado
            cost_multiplier = 1.0
            if self.config.cost_optimization and metrics.cost_per_request > 0:
                # Menor costo = mayor peso
                avg_cost = sum(m.cost_per_request for m in self.providers_health.values()) / len(
                    self.providers_health
                )
                if avg_cost > 0:
                    cost_multiplier = min(2.0, avg_cost / max(metrics.cost_per_request, 0.001))

            # Calcular peso final
            final_weight = base_weight * health_multiplier * time_multiplier * cost_multiplier
            self.load_balancer_weights[provider_name] = max(0.01, final_weight)  # Mínimo peso

    async def _check_predictive_interventions(self):
        """Verificar si se necesitan intervenciones predictivas"""
        # Verificar si todos los proveedores están degradados
        healthy_providers = [
            name
            for name, metrics in self.providers_health.items()
            if metrics.health_status == ProviderHealth.HEALTHY
        ]

        if len(healthy_providers) == 0:
            logger.warning("🚨 All providers are unhealthy or degraded!")
            # Podrían implementarse notificaciones aquí

        # Verificar patrones de costo
        if self.config.cost_optimization:
            await self._optimize_cost_patterns()

    async def _optimize_cost_patterns(self):
        """Optimizar patrones de costo"""
        total_cost = sum(m.total_cost for m in self.providers_health.values())
        if total_cost > 0:
            # Identificar el proveedor más costoso
            most_expensive = max(
                self.providers_health.items(),
                key=lambda x: x[1].cost_per_request if x[1].total_requests > 0 else 0,
            )

            if most_expensive[1].cost_per_request > 0:
                # Reducir peso del proveedor más caro si hay alternativas saludables
                healthy_alternatives = [
                    name
                    for name, metrics in self.providers_health.items()
                    if (
                        metrics.health_status == ProviderHealth.HEALTHY
                        and name != most_expensive[0]
                    )
                ]

                if healthy_alternatives:
                    current_weight = self.load_balancer_weights.get(most_expensive[0], 1.0)
                    self.load_balancer_weights[most_expensive[0]] = current_weight * 0.8
                    self.system_stats["cost_optimizations"] += 1

    def _get_circuit_breaker_state(self, provider_name: str) -> str:
        """Obtener estado del circuit breaker"""
        return self.circuit_breakers.get(provider_name, {}).get("state", "closed")

    def _should_trip_circuit_breaker(self, provider_name: str) -> bool:
        """Verificar si debe activarse el circuit breaker"""
        cb = self.circuit_breakers.get(provider_name, {})
        metrics = self.providers_health.get(provider_name)

        if not metrics:
            return False

        return (
            cb.get("failure_count", 0) >= self.config.circuit_breaker_threshold
            or metrics.consecutive_failures >= self.config.circuit_breaker_threshold
        )

    def _trip_circuit_breaker(self, provider_name: str):
        """Activar circuit breaker"""
        current_time = time.time()
        cb = self.circuit_breakers[provider_name]
        cb["state"] = "open"
        cb["last_failure_time"] = current_time
        cb["next_attempt_time"] = current_time + self.config.circuit_breaker_timeout

        self.system_stats["circuit_breaker_trips"] += 1
        logger.warning(
            "⚡ Circuit breaker TRIPPED for '%s' - cooling down for %.1fs",
            provider_name,
            self.config.circuit_breaker_timeout,
        )

    def get_optimal_provider(self, agent_type: str, exclude: list[str] | None = None) -> str | None:
        """Obtener el proveedor óptimo para un agente"""
        exclude = exclude or []

        # Obtener cadena de fallback para el agente
        available_providers = self.fallback_chains.get(
            agent_type, list(self.providers_health.keys())
        )

        # Filtrar proveedores excluidos y con circuit breaker abierto
        candidates = []
        for provider_name in available_providers:
            if provider_name in exclude:
                continue

            cb_state = self._get_circuit_breaker_state(provider_name)
            if cb_state == "open":
                continue

            candidates.append(provider_name)

        if not candidates:
            return None

        # Si no hay load balancing, usar el primero disponible
        if not self.config.enable_load_balancing:
            return candidates[0]

        # Selección basada en pesos
        if self.config.fallback_strategy == FallbackStrategy.ADAPTIVE:
            # Seleccionar basado en salud, performance y costo
            best_provider = None
            best_score = 0

            for provider_name in candidates:
                metrics = self.providers_health.get(provider_name)
                if not metrics:
                    continue

                weight = self.load_balancer_weights.get(provider_name, 1.0)
                health_score = {
                    ProviderHealth.HEALTHY: 1.0,
                    ProviderHealth.DEGRADED: 0.5,
                    ProviderHealth.UNHEALTHY: 0.1,
                    ProviderHealth.UNKNOWN: 0.3,
                }.get(metrics.health_status, 0.1)

                total_score = weight * health_score

                if total_score > best_score:
                    best_score = total_score
                    best_provider = provider_name

            return best_provider or candidates[0]

        # Para otras estrategias, usar el primero disponible
        return candidates[0]

    async def execute_with_fallback(
        self, agent_type: str, execute_func, *args, **kwargs
    ) -> tuple[Any, str]:
        """
        Ejecutar función con sistema de fallback
        Retorna (resultado, provider_usado)
        """
        self.system_stats["total_requests"] += 1

        attempted_providers = []
        last_error = None

        for attempt in range(self.config.max_retries + 1):
            # Obtener proveedor óptimo
            provider_name = self.get_optimal_provider(agent_type, exclude=attempted_providers)

            if not provider_name:
                break

            attempted_providers.append(provider_name)

            # Verificar circuit breaker
            cb_state = self._get_circuit_breaker_state(provider_name)
            if cb_state == "open":
                continue

            start_time = time.time()

            try:
                # Ejecutar función con timeout
                result = await asyncio.wait_for(
                    execute_func(provider_name, *args, **kwargs),
                    timeout=self.config.response_timeout,
                )

                # Registrar éxito
                response_time = (time.time() - start_time) * 1000  # ms
                cost = kwargs.get("estimated_cost", 0.0)

                metrics = self.providers_health[provider_name]
                metrics.update_success(response_time, cost)

                # Resetear circuit breaker si estaba en half-open
                cb = self.circuit_breakers[provider_name]
                if cb["state"] == "half-open":
                    cb["state"] = "closed"
                    cb["failure_count"] = 0
                    logger.info(
                        "✅ Circuit breaker for '%s' closed after successful request", provider_name
                    )

                self.system_stats["successful_requests"] += 1

                if attempt > 0:
                    self.system_stats["fallback_requests"] += 1

                if provider_name != self.get_optimal_provider(agent_type):
                    self.system_stats["provider_switches"] += 1

                return result, provider_name

            except asyncio.TimeoutError:
                error_msg = f"Timeout after {self.config.response_timeout}s"
                logger.warning("⏰ %s timeout for '%s'", provider_name, agent_type)
                last_error = error_msg

            except Exception as e:
                error_msg = str(e)
                logger.warning("❌ %s failed for '%s': %s", provider_name, agent_type, error_msg)
                last_error = e

            # Registrar fallo
            metrics = self.providers_health[provider_name]
            metrics.update_failure(str(last_error))

            # Actualizar circuit breaker
            cb = self.circuit_breakers[provider_name]
            cb["failure_count"] += 1

            # Activar circuit breaker si es necesario
            if self._should_trip_circuit_breaker(provider_name):
                self._trip_circuit_breaker(provider_name)

            # Delay antes del siguiente intento
            if attempt < self.config.max_retries:
                delay = self.config.retry_delay * (2**attempt)  # Exponential backoff
                await asyncio.sleep(delay)

        # Todos los intentos fallaron
        error_msg = f"All fallback attempts failed for '{agent_type}'. Last error: {last_error}"
        logger.error("💥 %s", error_msg)
        raise RuntimeError(error_msg)

    def get_health_summary(self) -> dict[str, Any]:
        """Obtener resumen de salud del sistema"""
        provider_summaries = {}

        for provider_name, metrics in self.providers_health.items():
            cb_state = self._get_circuit_breaker_state(provider_name)
            weight = self.load_balancer_weights.get(provider_name, 1.0)

            provider_summaries[provider_name] = {
                "health_status": metrics.health_status.value,
                "success_rate": round(metrics.success_rate, 2),
                "error_rate": round(metrics.error_rate, 2),
                "avg_response_time": round(metrics.avg_response_time, 2),
                "p95_response_time": round(metrics.p95_response_time, 2),
                "total_requests": metrics.total_requests,
                "consecutive_failures": metrics.consecutive_failures,
                "consecutive_successes": metrics.consecutive_successes,
                "circuit_breaker_state": cb_state,
                "load_balancer_weight": round(weight, 3),
                "total_cost": round(metrics.total_cost, 4),
                "cost_per_request": round(metrics.cost_per_request, 4),
                "last_success_ago": time.time() - metrics.last_success
                if metrics.last_success
                else None,
                "last_failure_ago": time.time() - metrics.last_failure
                if metrics.last_failure
                else None,
            }

        return {
            "system_stats": self.system_stats,
            "providers": provider_summaries,
            "fallback_chains": self.fallback_chains,
            "config": {
                "strategy": self.config.fallback_strategy.value,
                "max_retries": self.config.max_retries,
                "circuit_breaker_threshold": self.config.circuit_breaker_threshold,
                "health_check_interval": self.config.health_check_interval,
                "predictive_health_enabled": self.config.enable_predictive_health,
                "load_balancing_enabled": self.config.enable_load_balancing,
                "cost_optimization_enabled": self.config.cost_optimization,
            },
        }

    def get_recommendations(self) -> list[str]:
        """Obtener recomendaciones de optimización"""
        recommendations = []

        # Análisis de salud general
        unhealthy_providers = [
            name
            for name, metrics in self.providers_health.items()
            if metrics.health_status == ProviderHealth.UNHEALTHY
        ]

        if unhealthy_providers:
            recommendations.append(
                f"🚨 Proveedores no saludables detectados: {', '.join(unhealthy_providers)}. "
                "Considere revisar su configuración o contactar soporte."
            )

        # Análisis de costos
        if self.config.cost_optimization:
            high_cost_providers = [
                name
                for name, metrics in self.providers_health.items()
                if metrics.cost_per_request > 0.01  # Umbral configurable
            ]

            if high_cost_providers:
                recommendations.append(
                    f"💰 Proveedores con alto costo detectados: {', '.join(high_cost_providers)}. "
                    "Considere optimizar el uso o negociar mejores tarifas."
                )

        # Análisis de performance
        slow_providers = [
            name
            for name, metrics in self.providers_health.items()
            if metrics.avg_response_time > 5000  # 5 segundos
        ]

        if slow_providers:
            recommendations.append(
                f"🐌 Proveedores lentos detectados: {', '.join(slow_providers)}. "
                "Considere revisar la conectividad o cambiar de región."
            )

        # Análisis de fallbacks
        if self.system_stats["fallback_requests"] > self.system_stats["successful_requests"] * 0.1:
            recommendations.append(
                "🔄 Alto número de fallbacks detectado. Revise la salud de los proveedores primarios."
            )

        # Análisis de circuit breakers
        if self.system_stats["circuit_breaker_trips"] > 0:
            recommendations.append(
                f"⚡ {self.system_stats['circuit_breaker_trips']} circuit breakers activados. "
                "Algunos proveedores pueden estar experimentando problemas."
            )

        return recommendations or ["✅ Sistema funcionando óptimamente"]

    async def check_provider_health(self, provider: str) -> bool:
        """
        Verifica la salud de un proveedor específico

        Args:
            provider: Nombre del proveedor ('mlx', 'huggingface')

        Returns:
            True si el proveedor está saludable
        """
        try:
            if provider not in self.providers_health:
                # Inicializar métricas si no existen
                self.providers_health[provider] = HealthMetrics(provider=provider)
                return True  # Asumir saludable hasta probar lo contrario

            metrics = self.providers_health[provider]

            # Criterios de salud
            is_healthy = (
                metrics.health_status in [ProviderHealth.HEALTHY, ProviderHealth.UNKNOWN]
                and metrics.error_rate < 0.5  # Menos de 50% de errores
                and metrics.consecutive_failures < 5  # Menos de 5 fallos consecutivos
                and metrics.avg_response_time < 10000  # Menos de 10 segundos promedio
            )

            return is_healthy

        except Exception as e:
            logger.error("Error checking provider health for %s: %s", provider, e)
            return False

    async def select_optimal_provider(
        self,
        request_type: str,
        complexity: str = "medium",
        requirements: dict[str, Any] | None = None,
    ) -> str | None:
        """
        Selecciona el proveedor óptimo basado en el tipo de request y requerimientos

        Args:
            request_type: Tipo de request ('sentiment', 'technical', 'visual', etc.)
            complexity: Complejidad del análisis ('low', 'medium', 'high')
            requirements: Requerimientos específicos (timeout, quality, etc.)

        Returns:
            Nombre del proveedor óptimo o None si ninguno disponible
        """
        try:
            requirements = requirements or {}
            timeout = requirements.get("timeout", 10)

            # Evaluar proveedores disponibles
            provider_scores = {}

            for provider in ["mlx", "huggingface"]:
                # Verificar si el proveedor está saludable
                is_healthy = await self.check_provider_health(provider)
                if not is_healthy:
                    continue

                metrics = self.providers_health.get(provider)
                if not metrics:
                    continue

                # Calcular score basado en diferentes factores
                score = 100  # Score base

                # Factor de salud
                if metrics.health_status == ProviderHealth.HEALTHY:
                    score += 20
                elif metrics.health_status == ProviderHealth.DEGRADED:
                    score -= 10
                else:
                    score -= 30

                # Factor de velocidad (importante para timeout)
                if metrics.avg_response_time > 0:
                    if metrics.avg_response_time < timeout * 1000 * 0.5:  # Menos de 50% del timeout
                        score += 15
                    elif metrics.avg_response_time > timeout * 1000:  # Más que el timeout
                        score -= 50

                # Factor de éxito
                score += metrics.success_rate * 0.3

                # Factor de costo (MLX es gratis)
                if provider == "mlx":
                    score += 10  # Bonus por ser gratuito

                # Factor específico por tipo de request
                if request_type == "visual" and provider == "huggingface":
                    score += 5  # HF generalmente mejor para visual
                elif request_type in ["sentiment", "technical"] and provider == "mlx":
                    score += 5  # MLX puede ser suficiente para análisis simples

                # Factor de complejidad
                if complexity == "high" and provider == "huggingface":
                    score += 10  # HF mejor para tareas complejas
                elif complexity == "low" and provider == "mlx":
                    score += 10  # MLX suficiente para tareas simples

                provider_scores[provider] = max(0, score)  # No scores negativos

            # Seleccionar el mejor proveedor
            if not provider_scores:
                return None

            best_provider = max(provider_scores.items(), key=lambda x: x[1])[0]
            return best_provider

        except Exception as e:
            logger.error("Error selecting optimal provider: %s", e)
            return "mlx"  # Fallback por defecto

    async def get_health_summary(self) -> dict[str, dict[str, Any]]:
        """
        Obtiene resumen de salud de todos los proveedores

        Returns:
            Dict con información de salud por proveedor
        """
        try:
            summary = {}

            for provider, metrics in self.providers_health.items():
                is_healthy = await self.check_provider_health(provider)

                summary[provider] = {
                    "healthy": is_healthy,
                    "status": metrics.health_status.value,
                    "success_rate": metrics.success_rate,
                    "error_rate": metrics.error_rate,
                    "avg_response_time": metrics.avg_response_time,
                    "total_requests": metrics.total_requests,
                    "consecutive_failures": metrics.consecutive_failures,
                    "last_success": metrics.last_success,
                    "last_failure": metrics.last_failure,
                }

            # Agregar proveedores que no están en métricas pero deberían estar
            for provider in ["mlx", "huggingface"]:
                if provider not in summary:
                    summary[provider] = {
                        "healthy": True,
                        "status": "unknown",
                        "success_rate": 100.0,
                        "error_rate": 0.0,
                        "avg_response_time": 0.0,
                        "total_requests": 0,
                        "consecutive_failures": 0,
                        "last_success": None,
                        "last_failure": None,
                    }

            return summary

        except Exception as e:
            logger.error("Error getting health summary: %s", e)
            return {"error": str(e)}
