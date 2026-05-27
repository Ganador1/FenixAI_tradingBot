"""
Progressive Loading System - Carga gradual de componentes
Optimizado para M4 chip con 16GB RAM
"""

import asyncio
import gc
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import psutil

logger = logging.getLogger("progressive_loader")


class ProgressiveLoader:
    """Sistema de carga progresiva para optimizar uso de RAM"""

    def __init__(self):
        self.loaded_components = {}
        self.loading_queue = []
        self.memory_threshold = 0.8  # 80% uso de RAM
        self.component_stats = {}
        self._lock = asyncio.Lock()

    def get_memory_usage(self) -> float:
        """Obtener porcentaje de uso de RAM"""
        memory = psutil.virtual_memory()
        return memory.percent / 100.0

    async def can_load_component(self, estimated_memory_mb: int = 100) -> bool:
        """Verificar si hay suficiente memoria para cargar componente"""
        current_usage = self.get_memory_usage()
        available_mb = psutil.virtual_memory().available / (1024 * 1024)

        # Dejar al menos 2GB libres para el sistema
        min_free_mb = 2048

        return current_usage < self.memory_threshold and available_mb > (
            estimated_memory_mb + min_free_mb
        )

    async def load_component_async(
        self, component_name: str, loader_func, estimated_memory_mb: int = 100
    ):
        """Cargar componente de forma asíncrona con control de memoria"""
        async with self._lock:
            if component_name in self.loaded_components:
                logger.info(f"{component_name} already loaded")
                return self.loaded_components[component_name]

            # Verificar memoria disponible
            if not await self.can_load_component(estimated_memory_mb):
                logger.warning(f"Insufficient memory to load {component_name}")
                return None

            try:
                start_time = datetime.now()
                logger.info(f"Loading {component_name}...")

                # Cargar componente
                component = await loader_func()

                # Forzar garbage collection
                gc.collect()

                load_time = (datetime.now() - start_time).total_seconds()
                memory_after = self.get_memory_usage()

                self.loaded_components[component_name] = component
                self.component_stats[component_name] = {
                    "load_time": load_time,
                    "memory_usage": memory_after,
                    "loaded_at": datetime.now(),
                }

                logger.info(
                    f"{component_name} loaded in {load_time:.2f}s, memory: {memory_after:.1%}"
                )
                return component

            except Exception as e:
                logger.error(f"Failed to load {component_name}: {e}")
                return None

    async def unload_component(self, component_name: str):
        """Descargar componente para liberar memoria"""
        async with self._lock:
            if component_name in self.loaded_components:
                try:
                    # Limpiar referencias
                    del self.loaded_components[component_name]

                    # Forzar garbage collection
                    gc.collect()

                    memory_after = self.get_memory_usage()
                    logger.info(f"{component_name} unloaded, memory: {memory_after:.1%}")

                except Exception as e:
                    logger.error(f"Failed to unload {component_name}: {e}")

    def get_loaded_components(self) -> list[str]:
        """Obtener lista de componentes cargados"""
        return list(self.loaded_components.keys())

    def get_memory_status(self) -> dict[str, Any]:
        """Obtener estado de memoria"""
        memory = psutil.virtual_memory()
        return {
            "total_gb": memory.total / (1024**3),
            "available_gb": memory.available / (1024**3),
            "used_gb": memory.used / (1024**3),
            "usage_percent": memory.percent,
            "loaded_components": len(self.loaded_components),
        }


class EssentialComponentsLoader:
    """Cargador de componentes esenciales con prioridad"""

    def __init__(self, progressive_loader: ProgressiveLoader):
        self.loader = progressive_loader
        self.component_priorities = {
            "secrets_manager": {"priority": 1, "memory_mb": 50},
            "circuit_breakers": {"priority": 2, "memory_mb": 30},
            "memory_manager": {"priority": 3, "memory_mb": 20},
            "cache_system": {"priority": 4, "memory_mb": 100},
            "hmm_system": {"priority": 5, "memory_mb": 200},
            "risk_manager": {"priority": 6, "memory_mb": 150},
            "technical_analyst": {"priority": 7, "memory_mb": 300},
            "decision_agent": {"priority": 8, "memory_mb": 150},
            "sentiment_agent": {"priority": 9, "memory_mb": 400},
            "qabba_agent": {"priority": 10, "memory_mb": 500},
        }

    async def load_essential_only(self):
        """Cargar solo componentes críticos"""
        essential = ["secrets_manager", "circuit_breakers", "memory_manager", "cache_system"]

        for component in essential:
            if component in self.component_priorities:
                config = self.component_priorities[component]
                await self.load_single_component(component, config["memory_mb"])

    async def load_single_component(self, component_name: str, estimated_memory_mb: int = 100):
        """Cargar un componente específico"""

        loaders = {
            "secrets_manager": self._load_secrets_manager,
            "circuit_breakers": self._load_circuit_breakers,
            "memory_manager": self._load_memory_manager,
            "cache_system": self._load_cache_system,
            "hmm_system": self._load_hmm_system,
            "risk_manager": self._load_risk_manager,
            "technical_analyst": self._load_technical_analyst,
            "decision_agent": self._load_decision_agent,
            "sentiment_agent": self._load_sentiment_agent,
            "qabba_agent": self._load_qabba_agent,
        }

        if component_name in loaders:
            return await self.loader.load_component_async(
                component_name, loaders[component_name], estimated_memory_mb
            )
        else:
            logger.error(f"No loader found for {component_name}")
            return None

    async def load_trading_mode(self, mode: str = "minimal"):
        """Cargar componentes según modo de trading"""

        resolved = self._resolve_mode_components(mode)
        for component in resolved:
            config = self.component_priorities.get(component)
            if config:
                await self.load_single_component(component, config["memory_mb"])

    def _resolve_mode_components(self, mode: str) -> list[str]:
        modes = {
            "minimal": [
                "secrets_manager",
                "circuit_breakers",
                "memory_manager",
                "cache_system",
                "hmm_system",
            ],
            "standard": ["minimal", "risk_manager", "technical_analyst", "decision_agent"],
            "full": ["standard", "sentiment_agent", "qabba_agent"],
        }

        if mode not in modes:
            mode = "minimal"

        resolved: list[str] = []
        for item in modes[mode]:
            if item in modes:
                resolved.extend(self._resolve_mode_components(item))
            else:
                resolved.append(item)

        # Mantener orden y eliminar duplicados preservando prioridad
        seen = set()
        ordered_unique = []
        for comp in resolved:
            if comp not in seen:
                ordered_unique.append(comp)
                seen.add(comp)
        return ordered_unique

    # Component loaders
    async def _load_secrets_manager(self):
        # Usar gestor unificado que delega en SecureSecretsManager si está disponible
        from src.config.secrets_manager import SecretsManager

        secrets_manager = SecretsManager()
        return secrets_manager

    async def _load_circuit_breakers(self):
        from src.utils.universal_circuit_breaker import CircuitBreakerConfig, CircuitBreakerManager

        cb_manager = CircuitBreakerManager()

        # Configurar circuit breakers básicos
        services = ["binance_api", "database", "cache_service"]
        for service in services:
            cb_config = CircuitBreakerConfig(
                failure_threshold=5, recovery_timeout_seconds=60, half_open_max_calls=3
            )
            cb_manager.register_breaker(service, cb_config)
        return cb_manager

    async def _load_memory_manager(self):
        from src.system.advanced_memory_manager import get_memory_manager

        memory_manager = get_memory_manager()
        memory_manager.set_thresholds(warning=0.75, critical=0.85, emergency=0.95)
        return memory_manager

    async def _load_cache_system(self):
        from src.system.intelligent_cache import get_cache

        cache = get_cache()
        cache.create_cache("market_data", max_size_mb=50)
        cache.create_cache("technical_indicators", max_size_mb=30)
        return cache

    async def _load_hmm_system(self):
        from . import should_load_legacy

        # Avoid importing heavy legacy HMM system unless explicitly enabled
        if not should_load_legacy():
            # Return a lightweight placeholder or None so loader can continue
            return None
        from src.system.advanced_market_regime_detector import AdvancedMarketRegimeDetector

        regime_detector = AdvancedMarketRegimeDetector()

        # Configuración optimizada para M4
        regime_detector.hmm_params.update(
            {"n_components": 3, "n_iter": 50, "random_state": 42, "covariance_type": "diag"}
        )
        return regime_detector

    async def _load_risk_manager(self):
        from src.agents.risk import AdvancedRiskManager

        return AdvancedRiskManager()

    async def _load_technical_analyst(self):
        from src.agents.enhanced_technical_analyst import EnhancedTechnicalAnalyst

        return EnhancedTechnicalAnalyst()

    async def _load_sentiment_agent(self):
        from src.agents.sentiment_enhanced import EnhancedSentimentAnalyst

        return EnhancedSentimentAnalyst()

    async def _load_qabba_agent(self):
        from src.agents.qabba_compatibility_shim import EnhancedQabbaAgent

        return EnhancedQabbaAgent()

    async def _load_decision_agent(self):
        from src.agents.decision import EnhancedDecisionAgent

        return EnhancedDecisionAgent()


# Instancias globales
_progressive_loader_instance: ProgressiveLoader | None = None
_essential_loader_instance: EssentialComponentsLoader | None = None


def get_progressive_loader() -> ProgressiveLoader:
    global _progressive_loader_instance
    if _progressive_loader_instance is None:
        _progressive_loader_instance = ProgressiveLoader()
    return _progressive_loader_instance


def get_essential_components_loader() -> EssentialComponentsLoader:
    global _essential_loader_instance
    if _essential_loader_instance is None:
        _essential_loader_instance = EssentialComponentsLoader(get_progressive_loader())
    return _essential_loader_instance


@asynccontextmanager
async def progressive_system_context(mode="minimal"):
    """Context manager para carga progresiva"""
    try:
        await get_essential_components_loader().load_trading_mode(mode)
        yield {
            "loader": get_progressive_loader(),
            "components": get_progressive_loader().loaded_components,
            "memory_status": get_progressive_loader().get_memory_status(),
        }
    finally:
        # Descargar componentes al salir
        for component in list(get_progressive_loader().loaded_components.keys()):
            await get_progressive_loader().unload_component(component)
