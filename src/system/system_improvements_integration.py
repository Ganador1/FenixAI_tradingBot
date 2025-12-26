"""
Central integration of all improvements for the Fenix Trading Bot system.
This module coordinates the initialization and management of all enhanced components.
"""

import asyncio
import logging
import os
import yaml
from typing import Dict, Any, Optional, List
from datetime import datetime
import signal
import sys

# Import enhanced components
from src.config.secrets_manager import SecretsManager
from src.utils.universal_circuit_breaker import CircuitBreakerManager, CircuitBreakerConfig
from src.system.advanced_memory_manager import get_memory_manager, init_memory_management
from src.utils.structured_logger import get_logger, system_logger, AlertSeverity
from src.system.intelligent_cache import get_cache, clear_all_caches
from src.system.advanced_parallel_processor import get_processor, ProcessingMode, TaskPriority

logger = get_logger("system_integration")

class SystemImprovementsManager:
    """Central manager for all system improvements"""
    def __init__(self):
        self.initialized = False
        self.components = {}
        self.startup_time = None
        self.shutdown_handlers = []
        
        # Lazily import advanced systems using getters from the `src.system` package
        try:
            from src.system import (
                get_market_regime_detector,
                get_bayesian_optimizer,
                get_advanced_data_quality_engine,
                get_advanced_portfolio_risk_manager,
                get_advanced_backtesting_engine,
                get_multi_exchange_integration,
                get_advanced_metrics_system,
                get_structured_logging_system,
                get_container_orchestrator,
                get_dynamic_configuration_system,
                get_automatic_documentation_system,
                get_realtime_performance_analyzer
            )

            def _lazy_factory(getter):
                def factory():
                    cls_or_inst = getter()
                    if cls_or_inst is None:
                        return None
                    try:
                        # If the getter returns a class, instantiate it; if it returns an instance, return directly
                        return cls_or_inst() if callable(cls_or_inst) else cls_or_inst
                    except Exception:
                        # If instantiation fails, assume getter returned an instance
                        return cls_or_inst
                return factory

            # Defer instantiation of heavy legacy components until explicit initialization
            self.advanced_system_classes = {
                'market_regime_detector': _lazy_factory(get_market_regime_detector),
                'bayesian_optimizer': _lazy_factory(get_bayesian_optimizer),
                'data_quality_engine': _lazy_factory(get_advanced_data_quality_engine),
                'portfolio_risk_manager': _lazy_factory(get_advanced_portfolio_risk_manager),
                'backtesting_engine': _lazy_factory(get_advanced_backtesting_engine),
                'multi_exchange': _lazy_factory(get_multi_exchange_integration),
                'metrics_system': _lazy_factory(get_advanced_metrics_system),
                'logging_system': _lazy_factory(get_structured_logging_system),
                'container_orchestrator': _lazy_factory(get_container_orchestrator),
                'config_system': _lazy_factory(get_dynamic_configuration_system),
                'documentation_system': _lazy_factory(get_automatic_documentation_system),
                'performance_analyzer': _lazy_factory(get_realtime_performance_analyzer)
            }
            self.advanced_systems = {}
        except Exception as e:
            logger.warning(f"Some advanced systems not available (or getters missing): {e}")
            self.advanced_systems = {}
        
        # Load configuration from YAML file
        self.config = self._load_config_from_file()

    def _load_config_from_file(self):
        """Load configuration from the YAML file"""
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'system_improvements_config.yaml')
        logger.info(f"Attempting to load configuration from: {config_path}")
        
        # Default configuration
        default_config = {
            'secrets_manager': {
                'vault_path': 'security/secrets.vault',
                'auto_rotate_hours': 24,
                'backup_count': 5
            },
            'circuit_breaker': {
                'default_failure_threshold': 5,
                'default_timeout_seconds': 60,
                'default_half_open_max_calls': 3
            },
            'memory_manager': {
                'monitoring_interval': 30,
                'warning_threshold': 0.75,
                'critical_threshold': 0.85,
                'emergency_threshold': 0.95
            },
            'cache_system': {
                'default_cache_size_mb': 100,
                'cleanup_interval': 300,
                'default_ttl': 3600
            },
            'parallel_processor': {
                'max_workers': None,  # Auto-detect
                'mode': ProcessingMode.HYBRID,
                'enable_monitoring': True
            },
            'logging': {
                'log_level': 'INFO',
                'log_dir': 'logs',
                'structured_logging': True,
                'security_logging': True
            }
        }
        
        try:
            logger.info(f"Checking if config file exists: {os.path.exists(config_path)}")
            if os.path.exists(config_path):
                logger.info(f"Loading YAML configuration from {config_path}")
                with open(config_path, 'r', encoding='utf-8') as f:
                    yaml_config = yaml.safe_load(f)
                
                logger.info(f"YAML config loaded: {yaml_config}")
                if yaml_config:
                    # Convert parallel processor configuration
                    if 'parallel_processing' in yaml_config:
                        pp_config = yaml_config['parallel_processing']
                        
                        # Map valid modes
                        mode_mapping = {
                            'thread': ProcessingMode.THREAD,
                            'process': ProcessingMode.PROCESS,
                            'async': ProcessingMode.ASYNC,
                            'hybrid': ProcessingMode.HYBRID
                        }
                        
                        mode_str = pp_config.get('default_mode', 'hybrid')
                        mode = mode_mapping.get(mode_str, ProcessingMode.HYBRID)
                        
                        default_config['parallel_processor'] = {
                            'max_workers': pp_config.get('max_workers', 1),
                            'mode': mode,
                            'enable_monitoring': pp_config.get('enabled', True)
                        }
                        logger.info(f"Loaded parallel processing config: {pp_config.get('max_workers', 1)} workers, {mode_str} mode")
                    
                    # Update other configurations if they exist in the YAML
                    for section, values in yaml_config.items():
                        if section in default_config and isinstance(values, dict):
                            default_config[section].update(values)
                
                logger.info(f"Configuration loaded from {config_path}")
            else:
                logger.warning(f"Configuration file not found at {config_path}, using defaults")
        
        except Exception as e:
            logger.error(f"Error loading configuration from {config_path}: {e}, using defaults")
        
        return default_config

    async def initialize(self, custom_config: Optional[Dict[str, Any]] = None):
        """Initialize all system improvements"""
        if self.initialized:
            logger.warning("System improvements already initialized")
            return
        
        self.startup_time = datetime.now()
        
        try:
            # Update configuration if provided
            if custom_config:
                self._update_config(custom_config)
            
            logger.info("Starting Fenix Trading Bot system improvements initialization...")
            
            # 1. Initialize secrets management
            await self._init_secrets_manager()
            
            # 2. Initialize circuit breakers
            await self._init_circuit_breakers()
            
            # 3. Initialize memory management
            await self._init_memory_manager()
            
            # 4. Initialize cache system
            await self._init_cache_system()
            
            # 5. Initialize parallel processor
            await self._init_parallel_processor()
            
            # 6. Configure structured logging
            await self._init_structured_logging()
            
            # 7. Initialize advanced systems
            await self._init_advanced_systems()
            
            # 8. Configure shutdown handlers
            self._setup_shutdown_handlers()
            
            # 9. Run health checks
            await self._health_checks()
            
            self.initialized = True
            
            startup_duration = (datetime.now() - self.startup_time).total_seconds()
            logger.info(f"System improvements initialized successfully in {startup_duration:.2f} seconds")
            
            # Log initial configuration
            await self._log_system_status()
            
        except Exception as e:
            logger.critical(f"Failed to initialize system improvements: {e}", exception=e)
            await self.shutdown()
            raise

    def _update_config(self, custom_config: Dict[str, Any]):
        """Update configuration with custom values"""
        for component, config in custom_config.items():
            if component in self.config:
                self.config[component].update(config)
            else:
                self.config[component] = config

    async def _init_secrets_manager(self):
        """Initialize secrets manager"""
        logger.info("Initializing secure secrets manager...")
        
        config = self.config['secrets_manager']
        # Use the unified manager which delegates to SecureSecretsManager if available
        secrets_manager = SecretsManager()

        self.components['secrets_manager'] = secrets_manager
        logger.info("Secure secrets manager initialized")

    async def _init_circuit_breakers(self):
        """Initialize circuit breakers"""
        logger.info("Initializing circuit breakers...")
        
        config = self.config['circuit_breaker']
        cb_manager = CircuitBreakerManager()
        
        # Configure circuit breakers for critical services
        services = [
            'binance_api',
            'llm_service',
            'mlx_inference',
            'database',
            'cache_service',
            'news_scraper',
            'sentiment_analysis'
        ]
        
        for service in services:
            cb_config = CircuitBreakerConfig(
                failure_threshold=config['default_failure_threshold'],
                recovery_timeout_seconds=config['default_timeout_seconds'],
                half_open_max_calls=config['default_half_open_max_calls']
            )
            cb_manager.get_circuit_breaker(service, cb_config)
        
        self.components['circuit_breaker_manager'] = cb_manager
        logger.info(f"Circuit breakers initialized for {len(services)} services")

    async def _init_memory_manager(self):
        """Initialize memory manager"""
        logger.info("Initializing advanced memory manager...")
        
        memory_manager = init_memory_management()
        
        # Configure custom thresholds
        config = self.config['memory_manager']
        memory_manager.thresholds.warning = config['warning_threshold']
        memory_manager.thresholds.critical = config['critical_threshold']
        memory_manager.thresholds.emergency = config['emergency_threshold']
        memory_manager.monitoring_interval = config['monitoring_interval']
        
        # Register cleanup callbacks
        memory_manager.register_cleanup_callback(self._on_memory_cleanup)
        
        self.components['memory_manager'] = memory_manager
        logger.info("Advanced memory manager initialized")

    async def _init_cache_system(self):
        """Initialize cache system"""
        logger.info("Initializing intelligent cache system...")
        
        config = self.config['cache_system']
        
        # Create specialized caches
        caches = {
            'market_data': get_cache('market_data', max_size_mb=config['default_cache_size_mb']),
            'api_responses': get_cache('api_responses', max_size_mb=50),
            'ml_predictions': get_cache('ml_predictions', max_size_mb=75),
            'technical_indicators': get_cache('technical_indicators', max_size_mb=30),
            'news_sentiment': get_cache('news_sentiment', max_size_mb=25),
            'user_sessions': get_cache('user_sessions', max_size_mb=10)
        }
        
        # Configure cache callbacks
        for cache_name, cache in caches.items():
            cache.register_eviction_callback(lambda key, value: 
                logger.debug(f"Cache eviction in {cache_name}: {key}"))
        
        self.components['caches'] = caches
        logger.info(f"Intelligent cache system initialized with {len(caches)} specialized caches")

    async def _init_parallel_processor(self):
        """Initialize parallel processor"""
        logger.info("Initializing advanced parallel processor...")
        
        config = self.config['parallel_processor']
        processor = await get_processor()
        
        # The processor is already automatically initialized in get_processor()
        self.components['parallel_processor'] = processor
        
        stats = processor.get_stats()
        logger.info(f"Advanced parallel processor initialized: {stats['max_workers']} workers in {stats['mode']} mode")

    async def _init_structured_logging(self):
        """Configure structured logging"""
        logger.info("Configuring structured logging...")
        
        config = self.config['logging']
        
        # Configure critical alerts
        async def critical_alert_handler(severity: AlertSeverity, message: str, context: Dict[str, Any]):
            if severity in [AlertSeverity.HIGH, AlertSeverity.CRITICAL]:
                # Here you could integrate with external notification systems
                # For now, just an additional log
                logger.critical(f"ALERT [{severity.value}]: {message}", alert_context=context)
        
        system_logger.register_alert_callback(critical_alert_handler)
        
        self.components['structured_logger'] = system_logger
        logger.info("Structured logging configured with alert system")

    async def _init_advanced_systems(self):
        """Initialize advanced systems"""
        logger.info("Initializing advanced systems...")
        
        initialized_systems = []
        
        for system_name, system_class in self.advanced_system_classes.items():
            try:
                # Instantiate the class lazily
                system_instance = system_class()
                if hasattr(system_instance, 'initialize'):
                    await system_instance.initialize()
                elif hasattr(system_instance, 'start'):
                    await system_instance.start()
                
                self.components[f'advanced_{system_name}'] = system_instance
                initialized_systems.append(system_name)
                logger.info(f"Advanced system '{system_name}' initialized successfully")
                
            except Exception as e:
                logger.error(f"Failed to initialize advanced system '{system_name}': {e}")
        
        logger.info(f"Advanced systems initialized: {len(initialized_systems)} of {len(self.advanced_system_classes)} systems")

    def _setup_shutdown_handlers(self):
        """Configure shutdown handlers"""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, initiating graceful shutdown...")
            asyncio.create_task(self.shutdown())
        
        try:
            import threading
            # Ensure we are in the main thread and signal is available
            if threading.current_thread() is threading.main_thread():
                try:
                    signal.signal(signal.SIGINT, signal_handler)
                    signal.signal(signal.SIGTERM, signal_handler)
                except (ValueError, AttributeError, RuntimeError) as e:
                    logger.warning(f"Could not register signal handlers (likely not in main thread or signal not supported): {e}")
            else:
                logger.warning("Skipping signal handler setup: not running in main thread")
        except Exception as e:
            logger.warning(f"Unexpected error setting up signal handlers: {e}")

    async def _health_checks(self):
        """Run system health checks"""
        logger.info("Running system health checks...")
        
        health_status = {}
        
        # Check memory
        memory_manager = self.components.get('memory_manager')
        if memory_manager:
            stats = memory_manager.get_memory_stats()
            health_status['memory'] = {
                'status': 'healthy' if stats.usage_percent < 0.8 else 'warning',
                'usage_percent': stats.usage_percent,
                'available_gb': stats.available_gb
            }
        
        # Check caches
        caches = self.components.get('caches', {})
        cache_health = {}
        for name, cache in caches.items():
            cache_info = cache.get_info()
            cache_health[name] = {
                'status': 'healthy',
                'hit_rate': cache_info['hit_rate'],
                'size_mb': cache_info['current_size_mb']
            }
        health_status['caches'] = cache_health
        
        # Check parallel processor
        processor = self.components.get('parallel_processor')
        if processor:
            proc_stats = processor.get_stats()
            health_status['parallel_processor'] = {
                'status': 'healthy' if proc_stats['running'] else 'error',
                'active_workers': proc_stats['active_workers'],
                'success_rate': proc_stats['success_rate']
            }
        
        # Check circuit breakers
        cb_manager = self.components.get('circuit_breaker_manager')
        if cb_manager:
            cb_status = {}
            for service_name in ['binance_api', 'llm_service', 'mlx_inference']:
                cb = cb_manager.get_circuit_breaker(service_name)
                if cb:
                    cb_status[service_name] = {
                        'state': cb.state.value,
                        'failure_count': cb.failure_count,
                        'success_count': cb.success_count
                    }
            health_status['circuit_breakers'] = cb_status
        
        # Log health status
        logger.info("System health check completed", health_status=health_status)
        
        # Check for critical issues
        critical_issues = []
        if health_status.get('memory', {}).get('status') == 'warning':
            critical_issues.append("High memory usage detected")
        
        if critical_issues:
            logger.warning(f"Health check found issues: {critical_issues}")
        else:
            logger.info("All system health checks passed")

    async def _log_system_status(self):
        """Log the initial state of the system"""
        # Create a copy of the configuration with enums converted to strings
        config_copy = {}
        for key, value in self.config.items():
            if isinstance(value, dict):
                config_copy[key] = {}
                for sub_key, sub_value in value.items():
                    if hasattr(sub_value, 'value'):  # It's an enum
                        config_copy[key][sub_key] = sub_value.value
                    else:
                        config_copy[key][sub_key] = sub_value
            else:
                config_copy[key] = value
        
        status = {
            'startup_time': self.startup_time.isoformat(),
            'components_initialized': list(self.components.keys()),
            'configuration': config_copy,
            'system_info': {
                'python_version': sys.version,
                'platform': sys.platform,
                'pid': os.getpid()
            }
        }
        
        logger.info("System improvements status", system_status=status)

    async def _on_memory_cleanup(self, level: str):
        """Callback for memory cleanup"""
        logger.info(f"Memory cleanup triggered: {level}")
        
        if level in ['critical', 'emergency']:
            # Aggressively clear caches
            clear_all_caches()
            logger.info("All caches cleared due to memory pressure")

    async def get_system_metrics(self) -> Dict[str, Any]:
        """Get system metrics"""
        metrics = {
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': (datetime.now() - self.startup_time).total_seconds() if self.startup_time else 0,
            'initialized': self.initialized
        }
        
        # Memory metrics
        memory_manager = self.components.get('memory_manager')
        if memory_manager:
            memory_stats = memory_manager.get_memory_stats()
            metrics['memory'] = {
                'usage_percent': memory_stats.usage_percent,
                'used_gb': memory_stats.used_gb,
                'available_gb': memory_stats.available_gb,
                'mlx_memory_gb': memory_stats.mlx_memory_gb
            }
            # Add to the top level for compatibility
            metrics['memory_usage_gb'] = memory_stats.used_gb
        
        # Cache metrics
        caches = self.components.get('caches', {})
        cache_metrics = {}
        for name, cache in caches.items():
            cache_info = cache.get_info()
            cache_metrics[name] = {
                'hit_rate': cache_info['hit_rate'],
                'size_mb': cache_info['current_size_mb'],
                'entry_count': cache_info['entry_count']
            }
        metrics['caches'] = cache_metrics
        
        # Parallel processor metrics
        processor = self.components.get('parallel_processor')
        if processor:
            proc_stats = processor.get_stats()
            metrics['parallel_processor'] = proc_stats
        
        return metrics

    async def shutdown(self):
        """Graceful shutdown of all improvements"""
        if not self.initialized:
            return
        
        logger.info("Starting graceful shutdown of system improvements...")
        
        try:
            # Stop parallel processor
            processor = self.components.get('parallel_processor')
            if processor:
                await processor.stop()
                logger.info("Parallel processor stopped")
            
            # Stop memory manager
            memory_manager = self.components.get('memory_manager')
            if memory_manager:
                memory_manager.stop_monitoring()
                logger.info("Memory manager stopped")
            
            # Clear caches
            clear_all_caches()
            logger.info("All caches cleared")
            
            # Shutdown advanced systems
            for system_name in list(self.components.keys()):
                if system_name.startswith('advanced_'):
                    system_instance = self.components[system_name]
                    try:
                        if hasattr(system_instance, 'shutdown'):
                            await system_instance.shutdown()
                        elif hasattr(system_instance, 'stop'):
                            await system_instance.stop()
                        logger.info(f"Advanced system '{system_name}' stopped")
                    except Exception as e:
                        logger.error(f"Error stopping advanced system '{system_name}': {e}")
            
            # Lock down secrets manager
            secrets_manager = self.components.get('secrets_manager')
            if secrets_manager:
                secrets_manager.emergency_lockdown()
                logger.info("Secrets manager locked down")
            
            self.initialized = False
            shutdown_duration = (datetime.now() - self.startup_time).total_seconds() if self.startup_time else 0
            
            logger.info(f"System improvements shutdown completed in {shutdown_duration:.2f} seconds")
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exception=e)

    def get_component(self, name: str) -> Any:
        """Get a component by name"""
        return self.components.get(name)

    def is_healthy(self) -> bool:
        """Check if the system is healthy"""
        if not self.initialized:
            return False
        
        # Basic checks
        memory_manager = self.components.get('memory_manager')
        if memory_manager:
            stats = memory_manager.get_memory_stats()
            if stats.usage_percent > 0.9:  # 90% memory usage
                return False
        
        processor = self.components.get('parallel_processor')
        if processor:
            proc_stats = processor.get_stats()
            if not proc_stats['running']:
                return False
        
        return True

    async def health_check(self) -> Dict[str, Any]:
        """Perform a full system health check"""
        health_status = {
            'status': 'healthy' if self.is_healthy() else 'unhealthy',
            'timestamp': datetime.now().isoformat(),
            'initialized': self.initialized,
            'uptime_seconds': (datetime.now() - self.startup_time).total_seconds() if self.startup_time else 0
        }
        
        # Check individual components
        components_health = {}
        
        # Memory manager
        memory_manager = self.components.get('memory_manager')
        if memory_manager:
            stats = memory_manager.get_memory_stats()
            components_health['memory_manager'] = {
                'status': 'healthy' if stats.usage_percent < 0.9 else 'warning',
                'usage_percent': stats.usage_percent,
                'used_gb': stats.used_gb
            }
        
        # Parallel processor
        processor = self.components.get('parallel_processor')
        if processor:
            proc_stats = processor.get_stats()
            components_health['parallel_processor'] = {
                'status': 'healthy' if proc_stats.get('running', False) else 'unhealthy',
                'active_tasks': proc_stats.get('active_tasks', 0),
                'completed_tasks': proc_stats.get('completed_tasks', 0)
            }
        
        # Circuit breakers
        cb_manager = self.components.get('circuit_breaker_manager')
        if cb_manager:
            cb_status = {}
            services = [
                'binance_api', 'llm_service', 'mlx_inference', 'database',
                'cache_service', 'news_scraper', 'sentiment_analysis'
            ]
            for service_name in services:
                cb = cb_manager.get_circuit_breaker(service_name)
                if cb:
                    cb_status[service_name] = {
                        'state': cb.state.value,
                        'failure_count': cb.failure_count,
                        'success_count': cb.success_count
                    }
            
            open_breakers = [name for name, status in cb_status.items() if status['state'] == 'open']
            
            components_health['circuit_breakers'] = {
                'status': 'unhealthy' if open_breakers else 'healthy',
                'details': cb_status
            }
        
        health_status['components'] = components_health
        
        # Determine overall status
        unhealthy_components = [name for name, comp in components_health.items() 
                              if comp.get('status') == 'unhealthy']
        if unhealthy_components:
            health_status['status'] = 'unhealthy'
            health_status['issues'] = f"Unhealthy components: {', '.join(unhealthy_components)}"
        
        return health_status

    async def get_advanced_system(self, system_name: str) -> Any:
        """Get an advanced system by name"""
        return self.components.get(f'advanced_{system_name}')

    def list_advanced_systems(self) -> List[str]:
        """List available advanced systems"""
        return [name.replace('advanced_', '') for name in self.components.keys() 
                if name.startswith('advanced_')]

    async def restart_advanced_system(self, system_name: str) -> bool:
        """Restart a specific advanced system"""
        try:
            component_name = f'advanced_{system_name}'
            system_instance = self.components.get(component_name)
            
            if not system_instance:
                logger.error(f"Advanced system '{system_name}' not found")
                return False
            
            # Stop the system
            if hasattr(system_instance, 'shutdown'):
                await system_instance.shutdown()
            elif hasattr(system_instance, 'stop'):
                await system_instance.stop()
            
            # Restart the system
            if hasattr(system_instance, 'initialize'):
                await system_instance.initialize()
            elif hasattr(system_instance, 'start'):
                await system_instance.start()
            
            logger.info(f"Advanced system '{system_name}' restarted successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to restart advanced system '{system_name}': {e}")
            return False

_system_improvements_manager_instance: Optional[SystemImprovementsManager] = None

def get_system_improvements_manager() -> SystemImprovementsManager:
    """Returns the unique instance of the system improvements manager (lazy)."""
    global _system_improvements_manager_instance
    if _system_improvements_manager_instance is None:
        _system_improvements_manager_instance = SystemImprovementsManager()
    return _system_improvements_manager_instance
