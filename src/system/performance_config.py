"""
Configuración de optimización de rendimiento para Fenix Trading Bot
Permite personalizar los parámetros de optimización sin modificar el código
"""


class PerformanceConfig:
    """Configuración centralizada para optimización de rendimiento"""

    # Cache Configuration
    CACHE_TTL_SECONDS = {
        "balance": 60,  # Cache de balance por 60 segundos
        "market_data": 30,  # Cache de datos de mercado por 30 segundos
        "technical_indicators": 45,  # Cache de indicadores técnicos por 45 segundos
        "agent_results": 15,  # Cache de resultados de agentes por 15 segundos
    }

    # Timeout Configuration (seconds)
    AGENT_TIMEOUTS = {
        "sentiment": 30,  # Timeout para Sentiment Agent
        "technical": 45,  # Timeout para Technical Agent
        "visual": 60,  # Timeout para Visual Agent (incluye captura de gráfico)
        "qabba": 45,  # Timeout para QABBA Agent
        "decision": 60,  # Timeout para Decision Agent
        "risk": 30,  # Timeout para Risk Manager
    }

    # Circuit Breaker Configuration
    CIRCUIT_BREAKER_CONFIG = {
        "binance": {
            "failure_threshold": 5,  # Número de fallos antes de abrir el circuito
            "recovery_timeout": 60,  # Segundos antes de intentar recuperación
            "expected_exception": Exception,
        },
        "mlx": {
            "failure_threshold": 3,  # Menos tolerante con fallos de LLM
            "recovery_timeout": 30,  # Recuperación más rápida
            "expected_exception": Exception,
        },
    }

    # Memory Management
    MEMORY_CONFIG = {
        "max_memory_gb": 6.0,  # Límite de memoria antes de activar limpieza
        "cleanup_threshold_gb": 5.5,  # Umbral para iniciar limpieza preventiva
        "force_cleanup_gb": 6.5,  # Límite crítico para limpieza forzada
        "gc_interval_seconds": 300,  # Intervalo entre garbage collection
    }

    # Performance Monitoring
    MONITORING_CONFIG = {
        "report_interval_seconds": 300,  # Intervalo de reportes de rendimiento
        "alert_thresholds": {
            "memory_usage_gb": 6.0,
            "api_success_rate": 80.0,
            "avg_response_time_ms": 5000.0,
        },
    }

    # Retry Configuration
    RETRY_CONFIG = {
        "max_retries": 3,
        "base_delay": 1.0,
        "max_delay": 60.0,
        "backoff_factor": 2.0,
    }

    # WebSocket Configuration
    WS_CONFIG = {
        "max_reconnect_delay": 60,  # Máximo delay de reconexión en segundos
        "initial_reconnect_delay": 5,  # Delay inicial de reconexión
        "heartbeat_interval": 30,  # Intervalo de heartbeat
    }


# Instancia global para importar
performance_config = PerformanceConfig()
