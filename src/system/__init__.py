"""
Fenix Trading Bot Unified System
Centralized module for system imports and configuration
SAFE VERSION - Optional imports to prevent freezing
"""

import os
from typing import Optional
import warnings
import logging

# Disable TensorFlow by default to prevent mutex errors on macOS
if os.environ.get('DISABLE_TENSORFLOW', '0') != '1':
    os.environ['DISABLE_TENSORFLOW'] = '1'

# Configure logging before use in stubs
import logging
logger = logging.getLogger(__name__)

# If TensorFlow is disabled, register a lightweight stub to avoid real imports
if os.environ.get('DISABLE_TENSORFLOW') == '1':
    import sys, types
    if 'tensorflow' not in sys.modules:
        logger.warning('Registering TensorFlow stub: disabled by DISABLE_TENSORFLOW=1')
        tf_stub = types.ModuleType('tensorflow')
        def _disabled(*args, **kwargs):
            raise RuntimeError('TensorFlow is disabled via DISABLE_TENSORFLOW=1')
        # Provide commonly used sub-modules and attributes
        tf_stub.config = types.SimpleNamespace(
            threading=types.SimpleNamespace(
                set_intra_op_parallelism_threads=_disabled,
                set_inter_op_parallelism_threads=_disabled
            ),
            experimental=types.SimpleNamespace(set_memory_growth=_disabled),
            set_visible_devices=_disabled,
            list_physical_devices=lambda *_: []
        )
        import importlib.machinery
        tf_stub.__spec__ = importlib.machinery.ModuleSpec(name='tensorflow', loader=None)
        tf_stub.constant = _disabled
        class DummyTensor:
            pass
        tf_stub.Tensor = DummyTensor
        sys.modules['tensorflow'] = tf_stub

# Configure logging for imports
logger = logging.getLogger(__name__)

# Helper function for safe imports
def safe_import(module_name, class_name=None, fallback=None):
    """Safely import a module with a fallback.

    It tries to import using importlib.import_module with both relative
    (e.g., when this package is imported as 'src.system') and absolute
    (e.g., 'system') support. This covers both execution environments:
    from tests or as an installed package.
    """
    from importlib import import_module
    candidates = []
    try:
        pkg = __package__ or ''
    except Exception:
        pkg = ''
    # We try relative and absolute forms
    if pkg:
        candidates.append(f"{pkg}.{module_name}")
        candidates.append(f".{module_name}")
    candidates.append(f"system.{module_name}")
    candidates.append(f"src.system.{module_name}")

    last_exc = None
    for candidate in candidates:
        try:
            if candidate.startswith('.'):
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
        logger.warning("Could not import %s.%s: %s", module_name, class_name or '', last_exc)
    else:
        logger.debug("safe_import: returning fallback for %s.%s due to: %s", module_name, class_name or '', last_exc)
    return fallback


def should_load_legacy() -> bool:
    """Determines if legacy modules should be loaded; checks ENV or config."""
    # First, check the FENIX_LOAD_LEGACY_SYSTEM environment variable
    env_val = os.getenv("FENIX_LOAD_LEGACY_SYSTEM", "0").lower()
    if env_val in ("1", "true", "yes"):
        return True

    # Avoid importing settings if not necessary (reduces circular imports)
    try:
        from src.config.settings import get_config
        cfg = get_config()
        return getattr(cfg, 'system', None) and getattr(cfg.system, 'enable_legacy_systems', False)
    except Exception:
        return False

# Core System Components (SAFE)
try:
    from .intelligent_cache import IntelligentCache, get_cache, clear_all_caches, cached
except ImportError as e:
    logger.warning(f"Cache not available: {e}")
    IntelligentCache = None
    get_cache = lambda *args, **kwargs: None
    clear_all_caches = lambda: None
    cached = lambda func: func

try:
    from .advanced_memory_manager import AdvancedMemoryManager, get_memory_manager, init_memory_management
except ImportError as e:
    logger.warning(f"Memory manager not available: {e}")
    AdvancedMemoryManager = None
    get_memory_manager = lambda: None
    init_memory_management = lambda: None

# Risk Management (SAFE) - deferred import to reduce log noise
AdvancedRiskManager = None
PortfolioRiskEngine = None
AdvancedPortfolioRiskManager = None

def get_advanced_risk_manager():
    global AdvancedRiskManager
    if AdvancedRiskManager is None:
        AdvancedRiskManager = safe_import("advanced_risk_manager", "AdvancedRiskManager")
        if AdvancedRiskManager is None:
            # Prefer the version in agents if it exists (current pipeline)
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
        logger.debug("Legacy modules disabled: get_advanced_portfolio_risk_manager will return None")
        return None
    # If legacy modules are requested, always try to import the legacy implementation to ensure
    # we don't return a shim that was loaded when legacy was disabled.
    AdvancedPortfolioRiskManager = safe_import("advanced_portfolio_risk_manager", "AdvancedPortfolioRiskManager")
    return AdvancedPortfolioRiskManager

# Processing & Performance (SAFE) - deferred
AdvancedParallelProcessor = None
def get_advanced_parallel_processor():
    global AdvancedParallelProcessor
    if AdvancedParallelProcessor is None:
        AdvancedParallelProcessor = safe_import("advanced_parallel_processor", "AdvancedParallelProcessor")
    return AdvancedParallelProcessor
try:
    from .performance_optimizer import PerformanceCache, MemoryManager, PerformanceMonitor, TimeoutManager, CircuitBreaker
except ImportError as e:
    logger.warning(f"Performance optimizer not available: {e}")
    PerformanceCache = MemoryManager = PerformanceMonitor = TimeoutManager = CircuitBreaker = None

# Realtime performance and monitoring - deferred
RealtimePerformanceAnalyzer = None
AdvancedMetricsSystem = None
RealTimeMonitor = None
ComprehensiveHealthMonitor = None

def get_realtime_performance_analyzer():
    global RealtimePerformanceAnalyzer
    if RealtimePerformanceAnalyzer is None or should_load_legacy():
        # Always try to (re)import when legacy modules are enabled to pick up the legacy version
        RealtimePerformanceAnalyzer = safe_import("realtime_performance_analyzer", "RealtimePerformanceAnalyzer")
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
        ComprehensiveHealthMonitor = safe_import("comprehensive_health_monitor", "ComprehensiveHealthMonitor")
    return ComprehensiveHealthMonitor

# Data & Quality (SAFE) - deferred
AdvancedDataQualityEngine = None
DataValidationEngine = None

def get_advanced_data_quality_engine():
    global AdvancedDataQualityEngine
    if AdvancedDataQualityEngine is None or should_load_legacy():
        AdvancedDataQualityEngine = safe_import("advanced_data_quality_engine", "AdvancedDataQualityEngine")
    return AdvancedDataQualityEngine

def get_data_validation_engine():
    global DataValidationEngine
    if DataValidationEngine is None:
        DataValidationEngine = safe_import("data_validation_engine", "DataValidationEngine")
    return DataValidationEngine

# Learning & Optimization (PROBLEMATIC - OPTIONAL IMPORT)
logger.warning("⚠️  Importing ML/AI components - may cause freezing")
ContinuousLearningEngine = None
BayesianStrategyOptimizer = None
AdvancedMarketRegimeDetector = None

# Try to import only if explicitly requested
def get_learning_engine():
    global ContinuousLearningEngine
    if ContinuousLearningEngine is None:
        if not should_load_legacy():
            logger.debug("Legacy modules disabled: get_learning_engine will return None")
            return None
        ContinuousLearningEngine = safe_import("continuous_learning_engine", "ContinuousLearningEngine")
    return ContinuousLearningEngine

def get_bayesian_optimizer():
    global BayesianStrategyOptimizer
    if not should_load_legacy():
        BayesianStrategyOptimizer = None
        logger.debug("Legacy modules disabled: get_bayesian_optimizer will return None")
        return None
    BayesianStrategyOptimizer = safe_import("bayesian_strategy_optimizer", "BayesianStrategyOptimizer")
    return BayesianStrategyOptimizer

def get_market_regime_detector():
    global AdvancedMarketRegimeDetector
    if not should_load_legacy():
        AdvancedMarketRegimeDetector = None
        logger.debug("Legacy modules disabled: get_market_regime_detector will return None")
        return None
    logger.warning("⚠️  CAUTION: AdvancedMarketRegimeDetector may cause freezing (mutex.cc error)")
    AdvancedMarketRegimeDetector = safe_import("advanced_market_regime_detector", "AdvancedMarketRegimeDetector")
    return AdvancedMarketRegimeDetector

def _load_advanced_market_regime_detector():
    global AdvancedMarketRegimeDetector
    if not should_load_legacy():
        logger.debug("Legacy modules disabled: _load_advanced_market_regime_detector will not import")
        AdvancedMarketRegimeDetector = None
        return None
    # Reimport the legacy detector when requested
    logger.warning("⚠️  CAUTION: AdvancedMarketRegimeDetector can be heavy and cause mutex.cc")
    AdvancedMarketRegimeDetector = safe_import("advanced_market_regime_detector", "AdvancedMarketRegimeDetector")
    return AdvancedMarketRegimeDetector

# Signal Processing (PROBLEMATIC - OPTIONAL IMPORT)
AdaptiveSignalManager = None
SignalEvolutionEngine = None

def get_adaptive_signal_manager():
    global AdaptiveSignalManager
    if not should_load_legacy():
        AdaptiveSignalManager = None
        logger.debug("Legacy modules disabled: get_adaptive_signal_manager will return None")
        return None
    logger.warning("⚠️  CAUTION: AdaptiveSignalManager may use heavy ML libraries")
    AdaptiveSignalManager = safe_import("adaptive_signal_manager", "AdaptiveSignalManager")
    return AdaptiveSignalManager

def get_signal_evolution_engine():
    global SignalEvolutionEngine
    if not should_load_legacy():
        SignalEvolutionEngine = None
        logger.debug("Legacy modules disabled: get_signal_evolution_engine will return None")
        return None
    logger.warning("⚠️  CAUTION: SignalEvolutionEngine may use heavy ML libraries")
    SignalEvolutionEngine = safe_import("signal_evolution_engine", "SignalEvolutionEngine")
    return SignalEvolutionEngine

# MultiTimeframeAnalyzer may have TensorFlow
MultiTimeframeAnalyzer = None
def get_multi_timeframe_analyzer():
    global MultiTimeframeAnalyzer
    if not should_load_legacy():
        MultiTimeframeAnalyzer = None
        logger.debug("Legacy modules disabled: get_multi_timeframe_analyzer will return None")
        return None
    logger.warning("⚠️  CAUTION: MultiTimeframeAnalyzer may use TensorFlow")
    MultiTimeframeAnalyzer = safe_import("multi_timeframe_analyzer", "MultiTimeframeAnalyzer")
    return MultiTimeframeAnalyzer

# Configuration & Documentation (SAFE) - deferred
DynamicConfigurationSystem = None
AutomaticDocumentationSystem = None

def get_dynamic_configuration_system():
    global DynamicConfigurationSystem
    if DynamicConfigurationSystem is None:
        DynamicConfigurationSystem = safe_import("dynamic_configuration_system", "DynamicConfigurationSystem")
    return DynamicConfigurationSystem

def get_automatic_documentation_system():
    global AutomaticDocumentationSystem
    if AutomaticDocumentationSystem is None:
        AutomaticDocumentationSystem = safe_import("automatic_documentation_system", "AutomaticDocumentationSystem")
    return AutomaticDocumentationSystem

# Integration & Orchestration (SAFE) - deferred
IntelligentDependencyManager = None
ContainerOrchestrator = None
MultiExchangeIntegration = None

def get_intelligent_dependency_manager():
    global IntelligentDependencyManager
    if IntelligentDependencyManager is None:
        IntelligentDependencyManager = safe_import("intelligent_dependency_manager", "IntelligentDependencyManager")
    return IntelligentDependencyManager

def get_container_orchestrator():
    global ContainerOrchestrator
    if ContainerOrchestrator is None:
        ContainerOrchestrator = safe_import("containerization_orchestration", "ContainerOrchestrator")
    return ContainerOrchestrator

def get_multi_exchange_integration():
    global MultiExchangeIntegration
    if MultiExchangeIntegration is None:
        MultiExchangeIntegration = safe_import("multi_exchange_integration", "MultiExchangeIntegration")
    return MultiExchangeIntegration

# Backtesting (SAFE) - deferred
AdvancedBacktestingEngine = None
def get_advanced_backtesting_engine():
    global AdvancedBacktestingEngine
    if AdvancedBacktestingEngine is None:
        AdvancedBacktestingEngine = safe_import("advanced_backtesting_engine", "AdvancedBacktestingEngine")
    return AdvancedBacktestingEngine

# Logging (SAFE) - deferred
StructuredLoggingSystem = None
def get_structured_logging_system():
    global StructuredLoggingSystem
    if StructuredLoggingSystem is None:
        StructuredLoggingSystem = safe_import("structured_logging_system", "StructuredLoggingSystem")
    return StructuredLoggingSystem

# Model Management (PROBLEMATIC - OPTIONAL IMPORT)
OnDemandModelManager = None
def get_model_manager():
    global OnDemandModelManager
    if OnDemandModelManager is None:
        logger.warning("⚠️  CAUTION: OnDemandModelManager may use ML libraries")
        OnDemandModelManager = safe_import("on_demand_model_manager", "OnDemandModelManager")
    return OnDemandModelManager

# Orchestrators (LATE IMPORT)
SystemImprovementsManager = None
UnifiedSystemOrchestrator = None

def get_system_improvements_manager():
    global SystemImprovementsManager
    if SystemImprovementsManager is None:
        SystemImprovementsManager = safe_import("system_improvements_integration", "SystemImprovementsManager")
    return SystemImprovementsManager

def get_unified_orchestrator():
    global UnifiedSystemOrchestrator
    if UnifiedSystemOrchestrator is None:
        UnifiedSystemOrchestrator = safe_import("unified_system_orchestrator", "UnifiedSystemOrchestrator")
    return UnifiedSystemOrchestrator

__version__ = "2.0.0"
__author__ = "Fenix Trading Bot Team"

# Main unified system
class FenixTradingSystem:
    """Main unified system of Fenix Trading Bot - SAFE VERSION"""
    
    def __init__(self):
        self.memory_manager = None
        self.orchestrator = None
        self.improvements_manager = None
        self.cache = None
        self.initialized = False
        self.safe_mode = True  # Safe mode by default
    
    async def initialize(self, safe_mode=True):
        """Initialize the complete system"""
        if self.initialized:
            return
        
        self.safe_mode = safe_mode
        logger.info(f"🚀 Initializing Fenix Trading System (Safe mode: {safe_mode})")
        
        # Initialize memory management (SAFE)
        if init_memory_management:
            self.memory_manager = init_memory_management()
            logger.info("✅ Memory manager initialized")
        
        # Initialize main cache (SAFE)
        if get_cache:
            self.cache = get_cache("main", max_size_mb=512)
            logger.info("✅ Main cache initialized")
        
        # Initialize components only in non-safe mode
        if not safe_mode:
            logger.warning("⚠️  Non-safe mode - initializing problematic components")
            
            # Initialize improvements manager (CAN BE PROBLEMATIC)
            improvements_manager_class = get_system_improvements_manager()
            if improvements_manager_class:
                try:
                    self.improvements_manager = improvements_manager_class()
                    await self.improvements_manager.initialize()
                    logger.info("✅ System improvements manager initialized")
                except Exception as e:
                    logger.error(f"❌ Error initializing improvements manager: {e}")
            
            # Initialize orchestrator (CAN BE PROBLEMATIC)
            orchestrator_class = get_unified_orchestrator()
            if orchestrator_class:
                try:
                    self.orchestrator = orchestrator_class()
                    await self.orchestrator.initialize()
                    logger.info("✅ Unified orchestrator initialized")
                except Exception as e:
                    logger.error(f"❌ Error initializing orchestrator: {e}")
        else:
            logger.info("🛡️  Safe mode - skipping problematic components")
        
        self.initialized = True
        logger.info("🚀 Fenix Trading System initialized successfully")
    
    async def shutdown(self):
        """Shutdown the system gracefully"""
        if not self.initialized:
            return
        
        logger.info("🛑 Shutting down Fenix Trading System...")
        
        if self.orchestrator:
            try:
                await self.orchestrator.shutdown()
                logger.info("✅ Orchestrator shut down")
            except Exception as e:
                logger.error(f"❌ Error shutting down orchestrator: {e}")
        
        if self.improvements_manager:
            try:
                await self.improvements_manager.shutdown()
                logger.info("✅ Improvements manager shut down")
            except Exception as e:
                logger.error(f"❌ Error shutting down improvements manager: {e}")
        
        if self.memory_manager and hasattr(self.memory_manager, 'stop_monitoring'):
            try:
                self.memory_manager.stop_monitoring()
                logger.info("✅ Memory manager shut down")
            except Exception as e:
                logger.error(f"❌ Error shutting down memory manager: {e}")
        
        if clear_all_caches:
            try:
                clear_all_caches()
                logger.info("✅ Caches cleared")
            except Exception as e:
                logger.error(f"❌ Error clearing caches: {e}")
        
        self.initialized = False
        logger.info("🛑 Fenix Trading System shutdown complete")
    
    def enable_ml_components(self):
        """Enable ML/AI components (DANGEROUS)"""
        logger.warning("⚠️  ENABLING ML/AI COMPONENTS - MAY CAUSE FREEZING")
        
        # Load problematic components on demand
        learning_engine = get_learning_engine()
        market_detector = get_market_regime_detector()
        model_manager = get_model_manager()
        
        return {
            'learning_engine': learning_engine,
            'market_detector': market_detector,
            'model_manager': model_manager
        }

# Global system instance
_system_instance = None

def get_system() -> FenixTradingSystem:
    """Get the singleton instance of the system"""
    global _system_instance
    if _system_instance is None:
        _system_instance = FenixTradingSystem()
    return _system_instance

async def init_system(safe_mode=True):
    """Initialize the global system"""
    system = get_system()
    await system.initialize(safe_mode=safe_mode)
    return system

async def init_system_unsafe():
    """Initialize the system in non-safe mode (DANGEROUS)"""
    logger.warning("⚠️  INITIALIZING SYSTEM IN NON-SAFE MODE")
    return await init_system(safe_mode=False)

async def shutdown_system():
    """Shutdown the global system"""
    system = get_system()
    await system.shutdown()

# Export main components
__all__ = [
    # Main system
    'FenixTradingSystem', 'get_system', 'init_system', 'init_system_unsafe', 'shutdown_system',
    
    # Core (safe)
    'AdvancedMemoryManager', 'get_memory_manager', 'init_memory_management',
    'IntelligentCache', 'get_cache', 'clear_all_caches', 'cached',
    
    # Safe getters for problematic components
    'get_system_improvements_manager', 'get_unified_orchestrator',
    'get_learning_engine', 'get_bayesian_optimizer', 'get_market_regime_detector',
    'get_multi_timeframe_analyzer', 'get_model_manager',
    
    # Risk (safe)
    'AdvancedRiskManager', 'PortfolioRiskEngine', 'AdvancedPortfolioRiskManager',
    
    # Processing (safe)
    'AdvancedParallelProcessor', 'PerformanceCache', 'MemoryManager', 'PerformanceMonitor', 'TimeoutManager', 'CircuitBreaker', 'RealtimePerformanceAnalyzer',
    
    # Monitoring (safe)
    'AdvancedMetricsSystem', 'RealTimeMonitor', 'ComprehensiveHealthMonitor',
    
    # Data (safe)
    'AdvancedDataQualityEngine', 'DataValidationEngine',
    
    # Signals (partially safe)
    'AdaptiveSignalManager', 'SignalEvolutionEngine',
    
    # Config (safe)
    'DynamicConfigurationSystem', 'AutomaticDocumentationSystem',
    
    # Integration (safe)
    'IntelligentDependencyManager', 'ContainerOrchestrator', 'MultiExchangeIntegration',
    
    # Backtesting (safe)
    'AdvancedBacktestingEngine',
    
    # Logging (safe)
    'StructuredLoggingSystem',
    
    # Utilities
    'safe_import',
]
