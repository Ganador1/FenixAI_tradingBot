# config/config_loader_simple.py
"""
Simplified configuration to avoid issues with Pydantic
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import watchdog.events
import watchdog.observers
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from src.risk.runtime_feedback import RiskFeedbackLoopConfig

logger = logging.getLogger(__name__)

# Function to robustly load .env
def load_env_variables():
    """Robustly loads environment variables"""
    # Try to load from multiple locations
    possible_paths = [
        Path(__file__).parent.parent / ".env",  # From config/
        Path.cwd() / ".env",                    # From working directory
        Path(".env")                            # Relative to current
    ]
    
    for env_path in possible_paths:
        if env_path.exists():
            load_dotenv(env_path, override=True)
            return True
    
    return False

# Load .env immediately
load_env_variables()

# Secure placeholder keys for test environments
_PLACEHOLDER_KEY = "FENIX_TEST_KEY"
_PLACEHOLDER_SECRET = "FENIX_TEST_SECRET"

@dataclass
class TradingConfig:
    """Trading configuration"""
    mode: str = "paper"  # "paper" or "live"
    symbol: str = "BTCUSDT"
    base_asset: str = "BTC"
    quote_asset: str = "USDT"
    timeframe: str = "5m"
    use_testnet: bool = False
    min_candles_for_bot_start: int = 51
    trade_cooldown_after_close_seconds: int = 60
    sentiment_refresh_cooldown_seconds: int = 600
    active_timeframes: Optional[list] = None
    tradingview_chart_urls: Optional[dict] = None
    order_status_max_retries: int = 7
    order_status_initial_delay: float = 0.5
    
    def __post_init__(self):
        if self.active_timeframes is None:
            self.active_timeframes = ["5m", "15m", "30m", "1h"]
        if self.tradingview_chart_urls is None:
            self.tradingview_chart_urls = {
                "5m": "https://www.tradingview.com/chart/iERzAcI8/",
                "15m": "https://www.tradingview.com/chart/iERzAcI8/?interval=15",
                "30m": "https://www.tradingview.com/chart/iERzAcI8/?interval=30",
                "1h": "https://www.tradingview.com/chart/iERzAcI8/?interval=60"
            }

class BinanceConfig(BaseModel):
    api_key: str
    api_secret: str

class RiskManagementConfig(BaseModel):
    """Base risk configuration.

    Now acts as the resulting "effective profile". Concrete profiles are
    defined in YAML under risk_profiles and applied in create_app_config.
    """

    base_risk_per_trade: float = 0.02
    max_risk_per_trade: float = 0.04
    min_risk_per_trade: float = 0.005
    atr_sl_multiplier: float = 1.5
    atr_tp_multiplier: float = 3.0  # FIXED: Changed from 2.0 to 3.0 to use YAML configuration
    min_reward_risk_ratio: float = 1.5
    target_reward_risk_ratio: float = 2.0
    max_daily_loss_pct: float = 0.05
    max_consecutive_losses: int = 6
    max_trades_per_day: int = 60

    # Dynamic factors used by AdvancedRiskManager
    volatility_adjustment_factor: float = 1.0
    performance_adjustment_factor: float = 1.0
    market_condition_factor: float = 1.0
    time_of_day_factor: float = 1.0
    confidence_adjustment_factor: float = 1.0

    # Logical profile applied (conservative, moderate, aggressive, etc.)
    profile: str = "moderate"
    feedback_loop: RiskFeedbackLoopConfig = Field(default_factory=RiskFeedbackLoopConfig)

class LLMConfig(BaseModel):
    default_timeout: int = 90
    default_temperature: float = 0.15
    default_max_tokens: int = 1500

class NewsScraperConfig(BaseModel):
    cryptopanic_api_tokens: list[str] = []

class ChartGeneratorConfig(BaseModel):
    save_charts_to_disk: bool = True
    charts_dir: str = "logs/charts"

class ToolsConfig(BaseModel):
    news_scraper: NewsScraperConfig = Field(default_factory=NewsScraperConfig)
    chart_generator: ChartGeneratorConfig = Field(default_factory=ChartGeneratorConfig)

class LoggingConfig(BaseModel):
    level: str = "INFO"
    log_file: str = "logs/fenix_live_trading.log"

class TechnicalToolsConfig(BaseModel):
    maxlen_buffer: int = 300
    min_candles_for_reliable_calc: int = 30

class TechnicalAnalysisConfig(BaseModel):
    thresholds: Dict[str, Dict[str, float]] = Field(default_factory=dict)

class AgentsEnabledConfig(BaseModel):
    technical: bool = True
    qabba: bool = True
    sentiment: bool = True
    visual: bool = True
    decision: bool = True
    risk: bool = True

class AgentsConfig(BaseModel):
    enabled: AgentsEnabledConfig = Field(default_factory=AgentsEnabledConfig)
    active_agents: Optional[List[str]] = None
    agent_weights: Optional[Dict[str, float]] = None
    consensus_threshold: float = 0.7

class AppConfig(BaseModel):
    trading: TradingConfig = Field(default_factory=TradingConfig)
    binance: BinanceConfig
    risk_management: RiskManagementConfig = Field(default_factory=RiskManagementConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    technical_tools: TechnicalToolsConfig = Field(default_factory=TechnicalToolsConfig)
    technical_analysis: TechnicalAnalysisConfig = Field(default_factory=TechnicalAnalysisConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)

def _resolve_api_credentials(use_testnet_flag: bool) -> Dict[str, str]:
    """
    Returns credentials for the Binance API.
    - Prioritizes real credentials if they exist.
    - Otherwise, generates a secure placeholder pair for test environments.
    """
    testnet_key = os.getenv("BINANCE_TESTNET_API_KEY")
    testnet_secret = os.getenv("BINANCE_TESTNET_API_SECRET")
    main_key = os.getenv("BINANCE_API_KEY")
    main_secret = os.getenv("BINANCE_API_SECRET")

    if use_testnet_flag:
        api_key = testnet_key or main_key
        api_secret = testnet_secret or main_secret
    else:
        api_key = main_key or testnet_key
        api_secret = main_secret or testnet_secret

    if not api_key or not api_secret:
        logger.warning(
            "Binance API credentials not found. Using placeholder values suitable for tests "
            "and forcing paper trading mode. Provide real credentials via environment variables "
            "for live trading."
        )
        api_key = _PLACEHOLDER_KEY
        api_secret = _PLACEHOLDER_SECRET

    return {"api_key": api_key, "api_secret": api_secret}


def _load_yaml_config(config_path: Path) -> Dict[str, Dict[str, object]]:
    """Loads config.yaml if it exists, returning a safe dict."""
    if not config_path.exists():
        return {}

    with open(config_path, "r", encoding="utf-8") as config_file:
        return yaml.safe_load(config_file) or {}


def _compute_use_testnet_flag(trading_cfg: Dict[str, object]) -> bool:
    """Determines whether to use testnet, considering YAML and environment."""
    use_testnet_flag = False
    if isinstance(trading_cfg, dict):
        use_testnet_flag = bool(trading_cfg.get("use_testnet", False))

    if os.getenv("TRADING_MODE", "").lower() == "testnet":
        return True
    return use_testnet_flag


def _ensure_safe_trading_mode(
    trading_cfg: Dict[str, object],
    credentials: Dict[str, str]
) -> Dict[str, object]:
    """Forces paper/testnet mode if only placeholder credentials are available."""
    if credentials["api_key"] == _PLACEHOLDER_KEY or credentials["api_secret"] == _PLACEHOLDER_SECRET:
        safe_cfg = dict(trading_cfg) if isinstance(trading_cfg, dict) else {}
        safe_cfg.setdefault("mode", "paper")
        safe_cfg.setdefault("use_testnet", True)
        return safe_cfg
    return trading_cfg


def _determine_selected_profile(trading_cfg: Dict[str, object], raw_rm_cfg: Optional[Dict[str, object]]) -> str:
    """Gets the explicit selected profile or derives one by mode."""
    if isinstance(raw_rm_cfg, dict):
        profile = str(raw_rm_cfg.get("profile", "")).strip()
        if profile:
            return profile

    mode_val = "paper"
    if isinstance(trading_cfg, dict):
        mode_val = str(trading_cfg.get("mode", "paper")).lower()
    return "moderate" if mode_val == "paper" else "conservative"


def _extract_base_risk_config(raw_rm_cfg: Optional[Dict[str, object]]) -> Dict[str, object]:
    """Extracts base keys, excluding profile metadata."""
    if not isinstance(raw_rm_cfg, dict):
        return {}

    base_cfg: Dict[str, object] = {}
    for key, value in raw_rm_cfg.items():
        if key in {"risk_profiles", "profile"}:
            continue
        base_cfg[key] = value
    return base_cfg


def _apply_profile_overrides(
    effective_cfg: Dict[str, object],
    raw_profiles: Optional[Dict[str, object]],
    selected_profile: str,
) -> Dict[str, object]:
    """Applies overrides from the selected profile if defined."""
    if not isinstance(raw_profiles, dict):
        return effective_cfg

    profile_cfg = raw_profiles.get(selected_profile)
    if not isinstance(profile_cfg, dict):
        return effective_cfg

    merged_cfg = dict(effective_cfg)
    merged_cfg.update(profile_cfg)
    return merged_cfg


def _build_effective_risk_config(
    trading_cfg: Dict[str, object],
    raw_rm_cfg: Optional[Dict[str, object]]
) -> Dict[str, object]:
    """Combines base configuration with the selected risk profile."""
    raw_rm_cfg = raw_rm_cfg or {}
    raw_profiles = raw_rm_cfg.get("risk_profiles", {}) if isinstance(raw_rm_cfg, dict) else {}

    selected_profile = _determine_selected_profile(trading_cfg, raw_rm_cfg)
    effective_rm_cfg = _extract_base_risk_config(raw_rm_cfg)
    effective_rm_cfg = _apply_profile_overrides(effective_rm_cfg, raw_profiles, selected_profile)
    effective_rm_cfg.setdefault("profile", selected_profile)
    return effective_rm_cfg


def create_app_config() -> AppConfig:
    """Creates the application configuration."""
    try:
        # Load YAML configuration if it exists
        config_path = Path(__file__).parent / "config.yaml"
        yaml_config = _load_yaml_config(config_path)

        # Detect if we are in testnet/paper mode from YAML or environment variables
        trading_cfg = yaml_config.get("trading", {}) if isinstance(yaml_config, dict) else {}
        use_testnet_flag = _compute_use_testnet_flag(trading_cfg)

        credentials = _resolve_api_credentials(use_testnet_flag)

        # Force paper mode if using placeholder credentials
        trading_cfg = _ensure_safe_trading_mode(trading_cfg, credentials)

        # Build risk management configuration by applying profiles, if they exist
        raw_rm_cfg = yaml_config.get("risk_management", {}) if isinstance(yaml_config, dict) else {}
        effective_rm_cfg = _build_effective_risk_config(trading_cfg, raw_rm_cfg)

        config_data = {
            "trading": trading_cfg,
            "binance": credentials,
            "risk_management": effective_rm_cfg,
            "llm": yaml_config.get("llm", {}),
            "tools": yaml_config.get("tools", {}),
            "logging": yaml_config.get("logging", {}),
            "technical_tools": yaml_config.get("technical_tools", {}),
            "technical_analysis": yaml_config.get("technical_analysis", {}),
            "agents": yaml_config.get("agents", {}),
        }

        return AppConfig(**config_data)

    except Exception as e:
        logger.exception("Error loading configuration: %s", e)
        raise


_APP_CONFIG: Optional[AppConfig] = None


class _AppConfigProxy:
    """Lazy proxy to maintain compatibility with global `APP_CONFIG`."""

    def __getattr__(self, item):
        return getattr(get_app_config(), item)

    def __setattr__(self, key, value):
        setattr(get_app_config(), key, value)

    def __repr__(self) -> str:
        return repr(get_app_config())


def get_app_config(force_reload: bool = False) -> AppConfig:
    """Returns the application configuration, loading it on demand."""
    global _APP_CONFIG
    if force_reload or _APP_CONFIG is None:
        _APP_CONFIG = create_app_config()
    return _APP_CONFIG


# Create a compatible global proxy
APP_CONFIG = _AppConfigProxy()

class ConfigWatcher:
    def __init__(self, config_path):
        self.config_path = config_path
        self.observer = watchdog.observers.Observer()
        self.handler = watchdog.events.FileSystemEventHandler()
        self.handler.on_modified = self.on_modified
        self.observer.schedule(self.handler, str(config_path.parent), recursive=False)

    def start(self):
        self.observer.start()

    def stop(self):
        self.observer.stop()
        self.observer.join()

    def on_modified(self, event):
        if event.src_path == str(self.config_path):
            print('Config file changed, reloading...')
            global APP_CONFIG
            APP_CONFIG = create_app_config()

# ConfigWatcher will be explicitly initialized when needed
_watcher_instance = None

def start_config_watcher():
    """Starts the configuration watcher in a controlled manner"""
    global _watcher_instance
    if _watcher_instance is None:
        _watcher_instance = ConfigWatcher(Path(__file__).parent / 'config.yaml')
        _watcher_instance.start()
        logger.info("ConfigWatcher started successfully")
    return _watcher_instance

def stop_config_watcher():
    """Stops the configuration watcher in a controlled manner"""
    global _watcher_instance
    if _watcher_instance is not None:
        _watcher_instance.stop()
        _watcher_instance = None
        logger.info("ConfigWatcher stopped successfully")

def get_config_watcher():
    """Gets the watcher instance (may be None if not started)"""
    return _watcher_instance

if __name__ == "__main__":
    print("Configuration loaded successfully!")
    print(f"Symbol: {APP_CONFIG.trading.symbol}")
    print(f"Timeframe: {APP_CONFIG.trading.timeframe}")
    print(f"Use Testnet: {APP_CONFIG.trading.use_testnet}")
    print(f"API Key: {'*' * 20}...")
