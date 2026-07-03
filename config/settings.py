#!/usr/bin/env python3
# config/settings.py
"""
Configuración Centralizada para Fenix Trading Bot.

Este módulo reemplaza los valores hardcodeados con una configuración
flexible que puede cargarse desde archivos YAML/ENV.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class TradingSettings:
    """Configuración de trading."""
    symbol: str = "BTCUSDT"
    timeframe: str = "15m"
    min_klines_to_start: int = 20
    analysis_interval_seconds: int = 60
    
    # Riesgo
    max_risk_per_trade: float = 0.02  # 2%
    max_total_exposure: float = 0.05  # 5%
    max_concurrent_trades: int = 3
    
    # Target
    min_risk_reward_ratio: float = 1.5
    default_stop_loss_atr_multiplier: float = 1.5
    default_take_profit_atr_multiplier: float = 3.0


@dataclass
class AgentSettings:
    """Configuración de agentes."""
    enable_technical: bool = True
    enable_qabba: bool = True
    enable_visual: bool = False  # Desactivado por defecto (requiere más recursos)
    enable_sentiment: bool = False  # Desactivado por defecto
    
    # Ponderaciones
    technical_weight: float = 0.30
    qabba_weight: float = 0.30
    visual_weight: float = 0.25
    sentiment_weight: float = 0.15
    
    # Consenso
    consensus_threshold: float = 0.65
    min_confidence_to_trade: str = "MEDIUM"  # LOW, MEDIUM, HIGH

    # Sentiment (v2.1)
    sentiment_timeout_short: int = 8
    sentiment_cache_ttl: int = 900

    # Trailing stop (v2.1)
    trailing_stop_escalated: bool = True


@dataclass
class LLMSettings:
    """Configuración de LLM."""
    default_provider: str = "ollama_local"
    default_model: str = "qwen2.5:7b"
    temperature: float = 0.1
    max_tokens: int = 500
    timeout_seconds: int = 30
    
    # Fallback
    fallback_provider: str = "ollama_local"
    fallback_model: str = "gemma3:1b"


@dataclass  
class BinanceSettings:
    """Configuración de Binance."""
    testnet: bool = True
    recv_window: int = 5000
    min_notional: float = 5.0
    
    # Rate limiting
    max_requests_per_minute: int = 1200
    max_orders_per_second: int = 10


@dataclass
class LoggingSettings:
    """Configuración de logging."""
    level: str = "INFO"
    format: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    log_to_file: bool = True
    log_directory: str = "logs"
    max_log_files: int = 30


@dataclass
class MonitoringSettings:
    """Configuración de monitoreo."""
    enable_metrics: bool = True
    metrics_port: int = 9090
    health_check_interval: int = 300  # 5 minutos
    
    # Alertas
    cpu_alert_threshold: float = 80.0
    memory_alert_threshold: float = 80.0
    latency_alert_threshold_ms: float = 1000.0


@dataclass
class ResilienceSettings:
    """Configuración de resiliencia."""
    # Retry
    max_retries: int = 3
    base_retry_delay: float = 1.0
    max_retry_delay: float = 60.0
    
    # Circuit Breaker
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout: float = 30.0


@dataclass
class SystemSettings:
    """Configuración del sistema para manejo de módulos pesados/legacy."""
    enable_legacy_systems: bool = False
    legacy_modules: list[str] = field(default_factory=list)


@dataclass
class FenixConfig:
    """Configuración completa de Fenix."""
    trading: TradingSettings = field(default_factory=TradingSettings)
    agents: AgentSettings = field(default_factory=AgentSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    binance: BinanceSettings = field(default_factory=BinanceSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    monitoring: MonitoringSettings = field(default_factory=MonitoringSettings)
    resilience: ResilienceSettings = field(default_factory=ResilienceSettings)
    system: SystemSettings = field(default_factory=SystemSettings)
    
    @classmethod
    def from_yaml(cls, path: str | Path) -> "FenixConfig":
        """Carga configuración desde archivo YAML."""
        import yaml
        
        path = Path(path)
        if not path.exists():
            logger.warning(f"Config file {path} not found, using defaults")
            return cls()
        
        with open(path) as f:
            data = yaml.safe_load(f)
        
        return cls.from_dict(data)
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FenixConfig":
        """Carga configuración desde diccionario.

        Las claves desconocidas se ignoran (con warning) para que añadir
        campos nuevos a fenix.yaml no rompa la carga con TypeError.
        """
        import dataclasses

        def _build(section_cls: type, section_data: dict[str, Any], section_name: str) -> Any:
            valid = {f.name for f in dataclasses.fields(section_cls)}
            unknown = set(section_data) - valid
            if unknown:
                logger.warning(
                    "Ignorando claves desconocidas en sección '%s': %s",
                    section_name,
                    sorted(unknown),
                )
            return section_cls(**{k: v for k, v in section_data.items() if k in valid})

        config = cls()

        if "trading" in data:
            config.trading = _build(TradingSettings, data["trading"], "trading")
        if "agents" in data:
            config.agents = _build(AgentSettings, data["agents"], "agents")
        if "llm" in data:
            config.llm = _build(LLMSettings, data["llm"], "llm")
        if "binance" in data:
            config.binance = _build(BinanceSettings, data["binance"], "binance")
        if "logging" in data:
            config.logging = _build(LoggingSettings, data["logging"], "logging")
        if "monitoring" in data:
            config.monitoring = _build(MonitoringSettings, data["monitoring"], "monitoring")
        if "resilience" in data:
            config.resilience = _build(ResilienceSettings, data["resilience"], "resilience")
        if "system" in data:
            config.system = _build(SystemSettings, data["system"], "system")

        return config
    
    @classmethod
    def from_env(cls) -> "FenixConfig":
        """Carga configuración desde variables de entorno."""
        config = cls()
        
        # Trading
        if env_symbol := os.getenv("FENIX_SYMBOL"):
            config.trading.symbol = env_symbol
        if env_timeframe := os.getenv("FENIX_TIMEFRAME"):
            config.trading.timeframe = env_timeframe
        if env_risk := os.getenv("FENIX_MAX_RISK"):
            config.trading.max_risk_per_trade = float(env_risk)
        
        # Binance
        if os.getenv("FENIX_LIVE_MODE", "").lower() == "true":
            config.binance.testnet = False
        
        # LLM
        if env_model := os.getenv("FENIX_LLM_MODEL"):
            config.llm.default_model = env_model
        
        return config
    
    def to_dict(self) -> dict[str, Any]:
        """Convierte configuración a diccionario."""
        return {
            "trading": self.trading.__dict__,
            "agents": self.agents.__dict__,
            "llm": self.llm.__dict__,
            "binance": self.binance.__dict__,
            "logging": self.logging.__dict__,
            "monitoring": self.monitoring.__dict__,
            "resilience": self.resilience.__dict__,
        }
    
    def save_yaml(self, path: str | Path) -> None:
        """Guarda configuración a archivo YAML."""
        import yaml
        
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)
        
        logger.info(f"Configuration saved to {path}")


# ============================================================================
# SINGLETON GLOBAL
# ============================================================================

_config: FenixConfig | None = None


def get_config() -> FenixConfig:
    """Obtiene la configuración global."""
    global _config
    if _config is None:
        # Intentar cargar desde archivo, sino usar defaults
        config_paths = [
            Path("config/fenix.yaml"),
            Path("fenix.yaml"),
            Path.home() / ".fenix" / "config.yaml",
        ]
        
        for path in config_paths:
            if path.exists():
                _config = FenixConfig.from_yaml(path)
                logger.info(f"Loaded config from {path}")
                return _config
        
        # Usar defaults con override de env vars
        _config = FenixConfig.from_env()
        logger.info("Using default configuration with env overrides")
    
    return _config


def reload_config() -> FenixConfig:
    """Recarga la configuración."""
    global _config
    _config = None
    return get_config()


# ============================================================================
# CONFIGURACIÓN POR DEFECTO EXPORTABLE
# ============================================================================

DEFAULT_CONFIG_YAML = """
# Fenix Trading Bot Configuration
# ================================

trading:
  symbol: BTCUSDT
  timeframe: 15m
  min_klines_to_start: 20
  analysis_interval_seconds: 60
  max_risk_per_trade: 0.02
  max_total_exposure: 0.05
  max_concurrent_trades: 3
  min_risk_reward_ratio: 1.5

agents:
  enable_technical: true
  enable_qabba: true
  enable_visual: false
  enable_sentiment: false
  technical_weight: 0.30
  qabba_weight: 0.30
  visual_weight: 0.25
  sentiment_weight: 0.15
  consensus_threshold: 0.65
  min_confidence_to_trade: MEDIUM

llm:
  default_provider: ollama_local
  default_model: qwen2.5:7b
  temperature: 0.1
  max_tokens: 500
  timeout_seconds: 30

binance:
  testnet: true
  recv_window: 5000
  min_notional: 5.0

logging:
  level: INFO
  log_to_file: true
  log_directory: logs

monitoring:
  enable_metrics: true
  health_check_interval: 300
  cpu_alert_threshold: 80.0
  memory_alert_threshold: 80.0

resilience:
  max_retries: 3
  base_retry_delay: 1.0
  circuit_breaker_failure_threshold: 5

system:
    enable_legacy_systems: false
"""


def generate_default_config(path: str | Path = "config/fenix.yaml") -> None:
    """Genera archivo de configuración por defecto."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(path, "w") as f:
        f.write(DEFAULT_CONFIG_YAML)
    
    print(f"Default configuration generated at {path}")


if __name__ == "__main__":
    # Generar config por defecto si no existe
    config_path = Path("config/fenix.yaml")
    if not config_path.exists():
        generate_default_config()
    
    # Mostrar configuración actual
    config = get_config()
    print("=== Current Configuration ===")
    import json
    print(json.dumps(config.to_dict(), indent=2))
