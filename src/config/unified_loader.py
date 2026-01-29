"""
Unified Configuration Loader for FenixAI Trading Bot.

This module provides a single entry point for loading all configuration,
including trading settings, LLM providers, system config, and environment secrets.

Usage:
    from src.config.unified_loader import load_config, get_config
    
    config = get_config()  # Singleton access
"""

import os
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Any

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Base paths
CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
SRC_CONFIG_DIR = Path(__file__).parent


@dataclass
class TradingConfig:
    """Trading configuration."""
    symbol: str = "BTCUSDT"
    timeframe: str = "15m"
    min_klines_to_start: int = 20
    analysis_interval_seconds: int = 60
    max_risk_per_trade: float = 0.02
    max_total_exposure: float = 0.05
    max_concurrent_trades: int = 3
    min_risk_reward_ratio: float = 1.5
    default_stop_loss_atr_multiplier: float = 1.5
    default_take_profit_atr_multiplier: float = 3.0


@dataclass
class AgentsConfig:
    """Agent configuration."""
    enable_technical: bool = True
    enable_qabba: bool = True
    enable_visual: bool = True
    enable_sentiment: bool = True
    technical_weight: float = 0.30
    qabba_weight: float = 0.30
    visual_weight: float = 0.25
    sentiment_weight: float = 0.15
    consensus_threshold: float = 0.65
    min_confidence_to_trade: str = "MEDIUM"


@dataclass
class LLMConfig:
    """LLM configuration."""
    default_provider: str = "ollama_local"
    default_model: str = "qwen2.5:7b"
    temperature: float = 0.1
    max_tokens: int = 500
    timeout_seconds: int = 30
    fallback_provider: str = "ollama_local"
    fallback_model: str = "gemma3:1b"


@dataclass
class BinanceConfig:
    """Binance configuration."""
    testnet: bool = True
    recv_window: int = 5000
    min_notional: float = 5.0
    max_requests_per_minute: int = 1200
    max_orders_per_second: int = 10
    # Secrets from environment
    api_key: Optional[str] = field(default_factory=lambda: os.getenv("BINANCE_API_KEY"))
    api_secret: Optional[str] = field(default_factory=lambda: os.getenv("BINANCE_API_SECRET"))


@dataclass  
class MonitoringConfig:
    """Monitoring configuration."""
    enable_metrics: bool = True
    metrics_port: int = 9090
    health_check_interval: int = 300
    cpu_alert_threshold: float = 80.0
    memory_alert_threshold: float = 80.0
    latency_alert_threshold_ms: float = 1000.0
    prometheus_enabled: bool = True


@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"
    format: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    log_to_file: bool = True
    log_directory: str = "logs"
    max_log_files: int = 30


@dataclass
class ResilienceConfig:
    """Resilience/retry configuration."""
    max_retries: int = 3
    base_retry_delay: float = 1.0
    max_retry_delay: float = 60.0
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout: float = 30.0


@dataclass
class SecretsConfig:
    """Secrets loaded from environment variables."""
    jwt_secret: Optional[str] = field(default_factory=lambda: os.getenv("JWT_SECRET"))
    openai_api_key: Optional[str] = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    anthropic_api_key: Optional[str] = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    ollama_host: str = field(default_factory=lambda: os.getenv("OLLAMA_HOST", "http://localhost:11434"))


@dataclass
class FenixConfig:
    """Unified FenixAI configuration."""
    trading: TradingConfig = field(default_factory=TradingConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    binance: BinanceConfig = field(default_factory=BinanceConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    resilience: ResilienceConfig = field(default_factory=ResilienceConfig)
    secrets: SecretsConfig = field(default_factory=SecretsConfig)


def _load_yaml_file(path: Path) -> dict:
    """Load a YAML file safely."""
    try:
        if path.exists():
            with open(path, 'r') as f:
                return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Failed to load {path}: {e}")
    return {}


def _dict_to_dataclass(data: dict, cls: type) -> Any:
    """Convert dictionary to dataclass, ignoring unknown fields."""
    import dataclasses
    if not dataclasses.is_dataclass(cls):
        return data
    
    fields = {f.name for f in dataclasses.fields(cls)}
    filtered = {k: v for k, v in data.items() if k in fields}
    return cls(**filtered)


def load_config(config_path: Optional[Path] = None) -> FenixConfig:
    """
    Load unified configuration from YAML files and environment variables.
    
    Priority (highest to lowest):
    1. Environment variables (for secrets)
    2. fenix.yaml (main config)
    3. Default values
    
    Args:
        config_path: Optional path to fenix.yaml. Defaults to config/fenix.yaml
        
    Returns:
        FenixConfig with all settings loaded
    """
    # Load main config file
    if config_path is None:
        config_path = CONFIG_DIR / "fenix.yaml"
    
    raw_config = _load_yaml_file(config_path)
    
    # Build configuration from file + defaults
    config = FenixConfig(
        trading=_dict_to_dataclass(raw_config.get("trading", {}), TradingConfig),
        agents=_dict_to_dataclass(raw_config.get("agents", {}), AgentsConfig),
        llm=_dict_to_dataclass(raw_config.get("llm", {}), LLMConfig),
        binance=_dict_to_dataclass(raw_config.get("binance", {}), BinanceConfig),
        monitoring=_dict_to_dataclass(raw_config.get("monitoring", {}), MonitoringConfig),
        logging=_dict_to_dataclass(raw_config.get("logging", {}), LoggingConfig),
        resilience=_dict_to_dataclass(raw_config.get("resilience", {}), ResilienceConfig),
        secrets=SecretsConfig(),  # Always from environment
    )
    
    logger.info(f"Loaded configuration from {config_path}")
    return config


# Singleton instance
_config_instance: Optional[FenixConfig] = None


def get_config(force_reload: bool = False) -> FenixConfig:
    """
    Get the singleton configuration instance.
    
    Args:
        force_reload: If True, reload configuration from files
        
    Returns:
        FenixConfig singleton
    """
    global _config_instance
    
    if _config_instance is None or force_reload:
        _config_instance = load_config()
    
    return _config_instance


def get_trading_config() -> TradingConfig:
    """Convenience function to get trading config."""
    return get_config().trading


def get_binance_config() -> BinanceConfig:
    """Convenience function to get Binance config."""
    return get_config().binance


def get_llm_config() -> LLMConfig:
    """Convenience function to get LLM config."""
    return get_config().llm
