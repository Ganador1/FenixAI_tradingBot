"""
Sistema Unificado Fenix Trading Bot
Módulo centralizado para importaciones y configuración del sistema
VERSIÓN SEGURA - Importaciones opcionales para evitar congelamiento
"""

import logging
import os
import warnings
from typing import Optional

# Desactivar TensorFlow por defecto para evitar errores de mutex en macOS
if os.environ.get("DISABLE_TENSORFLOW", "0") != "1":
    os.environ["DISABLE_TENSORFLOW"] = "1"

# Configurar logging antes de usarlo en stubs
logger = logging.getLogger(__name__)

# Si TensorFlow está deshabilitado, registrar stub ligero para evitar importaciones reales
if os.environ.get("DISABLE_TENSORFLOW") == "1":
    import sys
    import types

    if "tensorflow" not in sys.modules:
        logger.warning("Registrando stub de TensorFlow: deshabilitado por DISABLE_TENSORFLOW=1")
        tf_stub = types.ModuleType("tensorflow")

        def _disabled(*args, **kwargs):
            raise RuntimeError("TensorFlow está deshabilitado mediante DISABLE_TENSORFLOW=1")

        # Proveer sub-módulos y atributos comúnmente usados
        tf_stub.config = types.SimpleNamespace(
            threading=types.SimpleNamespace(
                set_intra_op_parallelism_threads=_disabled,
                set_inter_op_parallelism_threads=_disabled,
            ),
            experimental=types.SimpleNamespace(set_memory_growth=_disabled),
            set_visible_devices=_disabled,
            list_physical_devices=lambda *_: [],
        )
        import importlib.machinery

        tf_stub.__spec__ = importlib.machinery.ModuleSpec(name="tensorflow", loader=None)
        tf_stub.constant = _disabled

        class DummyTensor:
            pass

        tf_stub.Tensor = DummyTensor
        sys.modules["tensorflow"] = tf_stub

# Configurar logging para importaciones
logger = logging.getLogger(__name__)


# Función auxiliar para importaciones seguras
def safe_import(module_name, class_name=None, fallback=None):
    """Importar módulo de manera segura con fallback.

    Se intenta importar usando importlib.import_module con soporte tanto relativo
    (por ejemplo, cuando este paquete se importa como 'src.system') como absoluto
    (por ejemplo 'system'). Esto cubre ambos entornos: ejecución desde tests o
    como paquete instalado.
    """
    from importlib import import_module

    candidates = []
    try:
        pkg = __package__ or ""
    except Exception:
        pkg = ""
    # Intentamos formas relativas y absolutas
    if pkg:
        candidates.append(f"{pkg}.{module_name}")
        candidates.append(f".{module_name}")
    candidates.append(f"system.{module_name}")
    candidates.append(f"src.system.{module_name}")

    last_exc = None
    for candidate in candidates:
        try:
            if candidate.startswith("."):
                module = import_module(candidate, package=pkg)
            else:
                module = import_module(candidate)
            if class_name:
                return getattr(module, class_name)
            return module
        except Exception as e:
            # Silently continue to next candidate; only log at debug level
            last_exc = e
            logger.debug("safe_import: failed candidate %s for %s: %s", candidate, module_name, e)
            continue

    if fallback is None:
        logger.warning("No se pudo importar %s.%s: %s", module_name, class_name or "", last_exc)
    else:
        logger.debug(
            "safe_import: returning fallback for %s.%s due to: %s",
            module_name,
            class_name or "",
            last_exc,
        )
    return fallback


def should_load_legacy() -> bool:
    """Determina si se deben cargar módulos legacy; chequea ENV o config."""
    # Primero revisar la variable de entorno FENIX_LOAD_LEGACY_SYSTEM
    env_val = os.getenv("FENIX_LOAD_LEGACY_SYSTEM", "0").lower()
    if env_val in ("1", "true", "yes"):
        return True

    # Evitar importar settings si no es necesario (reduce circular imports)
    try:
        from src.config.settings import get_config

        cfg = get_config()
        return getattr(cfg, "system", None) and getattr(cfg.system, "enable_legacy_systems", False)
    except Exception:
        return False


# Core System Components (SEGUROS)
try:
    from .intelligent_cache import IntelligentCache, cached, clear_all_caches, get_cache
except ImportError as e:
    logger.warning(f"Cache no disponible: {e}")
    IntelligentCache = None
    get_cache = lambda *args, **kwargs: None
    clear_all_caches = lambda: None
    cached = lambda func: func

try:
    from .advanced_memory_manager import (
        AdvancedMemoryManager,
        get_memory_manager,
        init_memory_management,
    )
except ImportError as e:
    logger.warning(f"Memory manager no disponible: {e}")
    AdvancedMemoryManager = None
    get_memory_manager = lambda: None
    init_memory_management = lambda: None

# Risk Management (SEGUROS) - Importación diferida para reducir ruido en logs
AdvancedRiskManager = None
PortfolioRiskEngine = None
AdvancedPortfolioRiskManager = None


def get_advanced_risk_manager():
    global AdvancedRiskManager
    if AdvancedRiskManager is None:
        AdvancedRiskManager = safe_import("advanced_risk_manager", "AdvancedRiskManager")
        if AdvancedRiskManager is None:
            # Preferir la versión en agentes si existe (pipeline actual)
            try:
                from src.agents.risk import AdvancedRiskManager as AgentsAdvancedRiskManager

                AdvancedRiskManager = AgentsAdvancedRiskManager
            except Exception:
                AdvancedRiskManager = None
    return AdvancedRiskManager


def get_portfolio_risk_engine():
    global PortfolioRiskEngine
    if not should_load_legacy():
        # Ensure we don't accidentally return a shim loaded earlier
        PortfolioRiskEngine = None
        logger.debug("Legacy modules disabled: get_portfolio_risk_engine will return None")
        return None
    # If legacy modules are enabled, always attempt to (re)load the legacy implementation.
    PortfolioRiskEngine = safe_import("portfolio_risk_engine", "PortfolioRiskEngine")
    return PortfolioRiskEngine


def get_advanced_portfolio_risk_manager():
    global AdvancedPortfolioRiskManager
    if not should_load_legacy():
        # Clear any previously cached shim to avoid returning a non-legacy class later
        AdvancedPortfolioRiskManager = None
        logger.debug(
            "Legacy modules disabled: get_advanced_portfolio_risk_manager will return None"
        )
        return None
    # If legacy modules are requested, always try to import the legacy implementation to ensure
    # we don't return a shim that was loaded when legacy was disabled.
    AdvancedPortfolioRiskManager = safe_import(
        "advanced_portfolio_risk_manager", "AdvancedPortfolioRiskManager"
    )
    return AdvancedPortfolioRiskManager


# Processing & Performance (SEGUROS) - diferido
AdvancedParallelProcessor = None


def get_advanced_parallel_processor():
    global AdvancedParallelProcessor
    if AdvancedParallelProcessor is None:
        AdvancedParallelProcessor = safe_import(
            "advanced_parallel_processor", "AdvancedParallelProcessor"
        )
    return AdvancedParallelProcessor


try:
    from .performance_optimizer import (
        CircuitBreaker,
        MemoryManager,
        PerformanceCache,
        PerformanceMonitor,
        TimeoutManager,
    )
except ImportError as e:
    logger.warning(f"Performance optimizer no disponible: {e}")
    PerformanceCache = MemoryManager = PerformanceMonitor = TimeoutManager = CircuitBreaker = None

# Realtime performance and monitoring - diferido
RealtimePerformanceAnalyzer = None
AdvancedMetricsSystem = None
RealTimeMonitor = None
ComprehensiveHealthMonitor = None


def get_realtime_performance_analyzer():
    global RealtimePerformanceAnalyzer
    if RealtimePerformanceAnalyzer is None or should_load_legacy():
        # Always try to (re)import when legacy modules are enabled to pick up the legacy version
        RealtimePerformanceAnalyzer = safe_import(
            "realtime_performance_analyzer", "RealtimePerformanceAnalyzer"
        )
    return RealtimePerformanceAnalyzer


def get_advanced_metrics_system():
    global AdvancedMetricsSystem
    if AdvancedMetricsSystem is None or should_load_legacy():
        AdvancedMetricsSystem = safe_import("advanced_metrics_system", "AdvancedMetricsSystem")
    return AdvancedMetricsSystem


def get_real_time_monitor():
    global RealTimeMonitor
    if RealTimeMonitor is None:
        RealTimeMonitor = safe_import("real_time_monitoring", "RealTimeMonitor")
    return RealTimeMonitor


def get_comprehensive_health_monitor():
    global ComprehensiveHealthMonitor
    if ComprehensiveHealthMonitor is None:
        ComprehensiveHealthMonitor = safe_import(
            "comprehensive_health_monitor", "ComprehensiveHealthMonitor"
        )
    return ComprehensiveHealthMonitor


# Data & Quality (SEGUROS) - diferido
AdvancedDataQualityEngine = None
DataValidationEngine = None


def get_advanced_data_quality_engine():
    global AdvancedDataQualityEngine
    if AdvancedDataQualityEngine is None or should_load_legacy():
        AdvancedDataQualityEngine = safe_import(
            "advanced_data_quality_engine", "AdvancedDataQualityEngine"
        )
    return AdvancedDataQualityEngine


def get_data_validation_engine():
    global DataValidationEngine
    if DataValidationEngine is None:
        DataValidationEngine = safe_import("data_validation_engine", "DataValidationEngine")
    return DataValidationEngine


# Learning & Optimization (PROBLEMÁTICOS - IMPORTACIÓN OPCIONAL)
logger.warning("⚠️  Importando componentes de ML/AI - pueden causar congelamiento")
ContinuousLearningEngine = None
BayesianStrategyOptimizer = None
AdvancedMarketRegimeDetector = None


# Intentar importar solo si se solicita explícitamente
def get_learning_engine():
    global ContinuousLearningEngine
    if ContinuousLearningEngine is None:
        if not should_load_legacy():
            logger.debug("Legacy modules disabled: get_learning_engine will return None")
            return None
        ContinuousLearningEngine = safe_import(
            "continuous_learning_engine", "ContinuousLearningEngine"
        )
    return ContinuousLearningEngine


def get_bayesian_optimizer():
    global BayesianStrategyOptimizer
    if not should_load_legacy():
        BayesianStrategyOptimizer = None
        logger.debug("Legacy modules disabled: get_bayesian_optimizer will return None")
        return None
    BayesianStrategyOptimizer = safe_import(
        "bayesian_strategy_optimizer", "BayesianStrategyOptimizer"
    )
    return BayesianStrategyOptimizer


def get_market_regime_detector():
    global AdvancedMarketRegimeDetector
    if not should_load_legacy():
        AdvancedMarketRegimeDetector = None
        logger.debug("Legacy modules disabled: get_market_regime_detector will return None")
        return None
    logger.warning(
        "⚠️  CUIDADO: AdvancedMarketRegimeDetector puede causar congelamiento (mutex.cc error)"
    )
    AdvancedMarketRegimeDetector = safe_import(
        "advanced_market_regime_detector", "AdvancedMarketRegimeDetector"
    )
    return AdvancedMarketRegimeDetector


def _load_advanced_market_regime_detector():
    global AdvancedMarketRegimeDetector
    if not should_load_legacy():
        logger.debug(
            "Legacy modules disabled: _load_advanced_market_regime_detector will not import"
        )
        AdvancedMarketRegimeDetector = None
        return None
    # Reimport the legacy detector when requested
    logger.warning("⚠️  CUIDADO: AdvancedMarketRegimeDetector puede ser pesado y provocar mutex.cc")
    AdvancedMarketRegimeDetector = safe_import(
        "advanced_market_regime_detector", "AdvancedMarketRegimeDetector"
    )
    return AdvancedMarketRegimeDetector


# Signal Processing (PROBLEMÁTICOS - IMPORTACIÓN OPCIONAL)
AdaptiveSignalManager = None
SignalEvolutionEngine = None


def get_adaptive_signal_manager():
    global AdaptiveSignalManager
    if not should_load_legacy():
        AdaptiveSignalManager = None
        logger.debug("Legacy modules disabled: get_adaptive_signal_manager will return None")
        return None
    logger.warning("⚠️  CUIDADO: AdaptiveSignalManager puede usar librerías de ML pesadas")
    AdaptiveSignalManager = safe_import("adaptive_signal_manager", "AdaptiveSignalManager")
    return AdaptiveSignalManager


def get_signal_evolution_engine():
    global SignalEvolutionEngine
    if not should_load_legacy():
        SignalEvolutionEngine = None
        logger.debug("Legacy modules disabled: get_signal_evolution_engine will return None")
        return None
    logger.warning("⚠️  CUIDADO: SignalEvolutionEngine puede usar librerías de ML pesadas")
    SignalEvolutionEngine = safe_import("signal_evolution_engine", "SignalEvolutionEngine")
    return SignalEvolutionEngine


# MultiTimeframeAnalyzer puede tener TensorFlow
MultiTimeframeAnalyzer = None


def get_multi_timeframe_analyzer():
    global MultiTimeframeAnalyzer
    if not should_load_legacy():
        MultiTimeframeAnalyzer = None
        logger.debug("Legacy modules disabled: get_multi_timeframe_analyzer will return None")
        return None
    logger.warning("⚠️  CUIDADO: MultiTimeframeAnalyzer puede usar TensorFlow")
    MultiTimeframeAnalyzer = safe_import("multi_timeframe_analyzer", "MultiTimeframeAnalyzer")
    return MultiTimeframeAnalyzer


# Configuration & Documentation (SEGUROS) - diferido
DynamicConfigurationSystem = None
AutomaticDocumentationSystem = None


def get_dynamic_configuration_system():
    global DynamicConfigurationSystem
    if DynamicConfigurationSystem is None:
        DynamicConfigurationSystem = safe_import(
            "dynamic_configuration_system", "DynamicConfigurationSystem"
        )
    return DynamicConfigurationSystem


def get_automatic_documentation_system():
    global AutomaticDocumentationSystem
    if AutomaticDocumentationSystem is None:
        AutomaticDocumentationSystem = safe_import(
            "automatic_documentation_system", "AutomaticDocumentationSystem"
        )
    return AutomaticDocumentationSystem


# Integration & Orchestration (SEGUROS) - diferido
IntelligentDependencyManager = None
ContainerOrchestrator = None
MultiExchangeIntegration = None


def get_intelligent_dependency_manager():
    global IntelligentDependencyManager
    if IntelligentDependencyManager is None:
        IntelligentDependencyManager = safe_import(
            "intelligent_dependency_manager", "IntelligentDependencyManager"
        )
    return IntelligentDependencyManager


def get_container_orchestrator():
    global ContainerOrchestrator
    if ContainerOrchestrator is None:
        ContainerOrchestrator = safe_import(
            "containerization_orchestration", "ContainerOrchestrator"
        )
    return ContainerOrchestrator


def get_multi_exchange_integration():
    global MultiExchangeIntegration
    if MultiExchangeIntegration is None:
        MultiExchangeIntegration = safe_import(
            "multi_exchange_integration", "MultiExchangeIntegration"
        )
    return MultiExchangeIntegration


# Backtesting (SEGURO) - diferido
AdvancedBacktestingEngine = None


def get_advanced_backtesting_engine():
    global AdvancedBacktestingEngine
    if AdvancedBacktestingEngine is None:
        AdvancedBacktestingEngine = safe_import(
            "advanced_backtesting_engine", "AdvancedBacktestingEngine"
        )
    return AdvancedBacktestingEngine


# Logging (SEGURO) - diferido
StructuredLoggingSystem = None


def get_structured_logging_system():
    global StructuredLoggingSystem
    if StructuredLoggingSystem is None:
        StructuredLoggingSystem = safe_import(
            "structured_logging_system", "StructuredLoggingSystem"
        )
    return StructuredLoggingSystem


# Model Management (PROBLEMÁTICO - IMPORTACIÓN OPCIONAL)
OnDemandModelManager = None


def get_model_manager():
    global OnDemandModelManager
    if OnDemandModelManager is None:
        logger.warning("⚠️  CUIDADO: OnDemandModelManager puede usar ML libraries")
        OnDemandModelManager = safe_import("on_demand_model_manager", "OnDemandModelManager")
    return OnDemandModelManager


# Orchestrators (IMPORTACIÓN TARDÍA)
SystemImprovementsManager = None
UnifiedSystemOrchestrator = None


def get_system_improvements_manager():
    global SystemImprovementsManager
    if SystemImprovementsManager is None:
        SystemImprovementsManager = safe_import(
            "system_improvements_integration", "SystemImprovementsManager"
        )
    return SystemImprovementsManager


def get_unified_orchestrator():
    global UnifiedSystemOrchestrator
    if UnifiedSystemOrchestrator is None:
        UnifiedSystemOrchestrator = safe_import(
            "unified_system_orchestrator", "UnifiedSystemOrchestrator"
        )
    return UnifiedSystemOrchestrator


__version__ = "2.0.0"
__author__ = "Fenix Trading Bot Team"


# Sistema principal unificado
class FenixTradingSystem:
    """Sistema principal unificado de Fenix Trading Bot - VERSIÓN SEGURA"""

    def __init__(self):
        self.memory_manager = None
        self.orchestrator = None
        self.improvements_manager = None
        self.cache = None
        self.initialized = False
        self.safe_mode = True  # Modo seguro por defecto

    async def initialize(self, safe_mode=True):
        """Inicializar sistema completo"""
        if self.initialized:
            return

        self.safe_mode = safe_mode
        logger.info(f"🚀 Inicializando Fenix Trading System (Modo seguro: {safe_mode})")

        # Inicializar gestión de memoria (SEGURO)
        if init_memory_management:
            self.memory_manager = init_memory_management()
            logger.info("✅ Memory manager inicializado")

        # Inicializar cache principal (SEGURO)
        if get_cache:
            self.cache = get_cache("main", max_size_mb=512)
            logger.info("✅ Cache principal inicializado")

        # Inicializar componentes solo en modo no seguro
        if not safe_mode:
            logger.warning("⚠️  Modo no seguro - inicializando componentes problemáticos")

            # Inicializar gestor de mejoras (PUEDE SER PROBLEMÁTICO)
            improvements_manager_class = get_system_improvements_manager()
            if improvements_manager_class:
                try:
                    self.improvements_manager = improvements_manager_class()
                    await self.improvements_manager.initialize()
                    logger.info("✅ System improvements manager inicializado")
                except Exception as e:
                    logger.error(f"❌ Error inicializando improvements manager: {e}")

            # Inicializar orquestador (PUEDE SER PROBLEMÁTICO)
            orchestrator_class = get_unified_orchestrator()
            if orchestrator_class:
                try:
                    self.orchestrator = orchestrator_class()
                    await self.orchestrator.initialize()
                    logger.info("✅ Unified orchestrator inicializado")
                except Exception as e:
                    logger.error(f"❌ Error inicializando orchestrator: {e}")
        else:
            logger.info("🛡️  Modo seguro - omitiendo componentes problemáticos")

        self.initialized = True
        logger.info("🚀 Fenix Trading System initialized successfully")

    async def shutdown(self):
        """Apagar sistema de manera ordenada"""
        if not self.initialized:
            return

        logger.info("🛑 Apagando Fenix Trading System...")

        if self.orchestrator:
            try:
                await self.orchestrator.shutdown()
                logger.info("✅ Orchestrator apagado")
            except Exception as e:
                logger.error(f"❌ Error apagando orchestrator: {e}")

        if self.improvements_manager:
            try:
                await self.improvements_manager.shutdown()
                logger.info("✅ Improvements manager apagado")
            except Exception as e:
                logger.error(f"❌ Error apagando improvements manager: {e}")

        if self.memory_manager and hasattr(self.memory_manager, "stop_monitoring"):
            try:
                self.memory_manager.stop_monitoring()
                logger.info("✅ Memory manager apagado")
            except Exception as e:
                logger.error(f"❌ Error apagando memory manager: {e}")

        if clear_all_caches:
            try:
                clear_all_caches()
                logger.info("✅ Caches limpiados")
            except Exception as e:
                logger.error(f"❌ Error limpiando caches: {e}")

        self.initialized = False
        logger.info("🛑 Fenix Trading System shutdown complete")

    def enable_ml_components(self):
        """Habilitar componentes de ML/AI (PELIGROSO)"""
        logger.warning("⚠️  HABILITANDO COMPONENTES ML/AI - PUEDE CAUSAR CONGELAMIENTO")

        # Cargar componentes problemáticos bajo demanda
        learning_engine = get_learning_engine()
        market_detector = get_market_regime_detector()
        model_manager = get_model_manager()

        return {
            "learning_engine": learning_engine,
            "market_detector": market_detector,
            "model_manager": model_manager,
        }


# Instancia global del sistema
_system_instance = None


def get_system() -> FenixTradingSystem:
    """Obtener instancia singleton del sistema"""
    global _system_instance
    if _system_instance is None:
        _system_instance = FenixTradingSystem()
    return _system_instance


async def init_system(safe_mode=True):
    """Inicializar sistema global"""
    system = get_system()
    await system.initialize(safe_mode=safe_mode)
    return system


async def init_system_unsafe():
    """Inicializar sistema en modo no seguro (PELIGROSO)"""
    logger.warning("⚠️  INICIALIZANDO SISTEMA EN MODO NO SEGURO")
    return await init_system(safe_mode=False)


async def shutdown_system():
    """Apagar sistema global"""
    system = get_system()
    await system.shutdown()


# Exportar componentes principales
__all__ = [
    # Sistema principal
    "FenixTradingSystem",
    "get_system",
    "init_system",
    "init_system_unsafe",
    "shutdown_system",
    # Core (seguros)
    "AdvancedMemoryManager",
    "get_memory_manager",
    "init_memory_management",
    "IntelligentCache",
    "get_cache",
    "clear_all_caches",
    "cached",
    # Getters seguros para componentes problemáticos
    "get_system_improvements_manager",
    "get_unified_orchestrator",
    "get_learning_engine",
    "get_bayesian_optimizer",
    "get_market_regime_detector",
    "get_multi_timeframe_analyzer",
    "get_model_manager",
    # Risk (seguros)
    "AdvancedRiskManager",
    "PortfolioRiskEngine",
    "AdvancedPortfolioRiskManager",
    # Processing (seguros)
    "AdvancedParallelProcessor",
    "PerformanceCache",
    "MemoryManager",
    "PerformanceMonitor",
    "TimeoutManager",
    "CircuitBreaker",
    "RealtimePerformanceAnalyzer",
    # Monitoring (seguros)
    "AdvancedMetricsSystem",
    "RealTimeMonitor",
    "ComprehensiveHealthMonitor",
    # Data (seguros)
    "AdvancedDataQualityEngine",
    "DataValidationEngine",
    # Signals (parcialmente seguros)
    "AdaptiveSignalManager",
    "SignalEvolutionEngine",
    # Config (seguros)
    "DynamicConfigurationSystem",
    "AutomaticDocumentationSystem",
    # Integration (seguros)
    "IntelligentDependencyManager",
    "ContainerOrchestrator",
    "MultiExchangeIntegration",
    # Backtesting (seguro)
    "AdvancedBacktestingEngine",
    # Logging (seguro)
    "StructuredLoggingSystem",
    # Utilidades
    "safe_import",
]
