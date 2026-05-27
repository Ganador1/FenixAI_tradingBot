"""
Trading Constants - Centralized configuration for trading parameters
Replaces hardcoded global constants with proper configuration management
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SymbolConfig:
    """Configuration for a specific trading symbol"""

    symbol: str
    tick_size: float
    step_size: float
    min_notional: float
    price_precision: int
    quantity_precision: int

    @classmethod
    def from_filters(cls, symbol: str, filters: list) -> "SymbolConfig":
        """Create SymbolConfig from Binance filters"""
        tick_size = 0.01
        step_size = 0.001
        min_notional = 10.0
        price_precision = 2
        quantity_precision = 3

        for filter_data in filters:
            filter_type = filter_data.get("filterType")
            if filter_type == "PRICE_FILTER":
                tick_size = float(filter_data.get("tickSize", tick_size))
                price_precision = len(str(tick_size).split(".")[-1]) if "." in str(tick_size) else 0
            elif filter_type == "LOT_SIZE":
                step_size = float(filter_data.get("stepSize", step_size))
                quantity_precision = (
                    len(str(step_size).split(".")[-1]) if "." in str(step_size) else 0
                )
            elif filter_type == "MIN_NOTIONAL":
                min_notional = float(
                    filter_data.get("minNotional", filter_data.get("notional", min_notional))
                )

        return cls(
            symbol=symbol,
            tick_size=tick_size,
            step_size=step_size,
            min_notional=min_notional,
            price_precision=price_precision,
            quantity_precision=quantity_precision,
        )


@dataclass
class TimeframeConfig:
    """Configuration for timeframe analysis"""

    timeframe: str
    minutes: int
    websocket_enabled: bool = True
    min_candles_for_start: int = 200

    @classmethod
    def from_string(cls, timeframe: str) -> "TimeframeConfig":
        """Create TimeframeConfig from timeframe string (e.g., '15m', '1h')"""
        timeframe_map = {
            "1m": 1,
            "3m": 3,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "2h": 120,
            "4h": 240,
            "6h": 360,
            "8h": 480,
            "12h": 720,
            "1d": 1440,
            "3d": 4320,
            "1w": 10080,
        }

        minutes = timeframe_map.get(timeframe, 15)  # Default to 15m
        return cls(
            timeframe=timeframe, minutes=minutes, websocket_enabled=True, min_candles_for_start=200
        )


@dataclass
class TradingPaths:
    """File paths for trading operations"""

    log_directory: str = "logs"
    signal_trace_path: str = "logs/signal_trace.log"
    trade_performance_path: str = "logs/trade_performance.log"
    portfolio_state_file: str = "logs/portfolio_state.json"

    def ensure_directories(self) -> None:
        """Ensure all required directories exist"""
        Path(self.log_directory).mkdir(parents=True, exist_ok=True)
        Path(self.signal_trace_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.trade_performance_path).parent.mkdir(parents=True, exist_ok=True)


@dataclass
class WebSocketConfig:
    """WebSocket connection configuration"""

    base_url: str = "wss://stream.binance.com:9443/ws"
    base_url_futures: str = "wss://fstream.binance.com/ws"
    reconnect_interval: int = 5
    max_reconnect_attempts: int = 10
    ping_interval: int = 30
    ping_timeout: int = 10


class TradingConstants:
    """
    Centralized trading constants manager
    Replaces hardcoded global constants with configurable values
    """

    def __init__(self, config_path: str | None = None):
        self.config_path = config_path
        self.symbol_configs: dict[str, SymbolConfig] = {}
        self.timeframe_configs: dict[str, TimeframeConfig] = {}
        self.paths = TradingPaths()
        self.websocket = WebSocketConfig()

        # Default trading parameters
        self.trade_cooldown_after_close_seconds = 300  # 5 minutes
        self.min_candles_for_bot_start = 200

        # Load configuration if provided
        if config_path:
            self.load_configuration(config_path)

    def load_configuration(self, config_path: str) -> None:
        """Load configuration from JSON file"""
        try:
            config_file = Path(config_path)
            if config_file.exists():
                with open(config_file) as f:
                    config = json.load(f)

                # Load symbol configurations
                for symbol_data in config.get("symbols", []):
                    symbol = symbol_data["symbol"]
                    self.symbol_configs[symbol] = SymbolConfig(**symbol_data)

                # Load timeframe configurations
                for timeframe_data in config.get("timeframes", []):
                    timeframe = timeframe_data["timeframe"]
                    self.timeframe_configs[timeframe] = TimeframeConfig(**timeframe_data)

                # Load paths if provided
                if "paths" in config:
                    self.paths = TradingPaths(**config["paths"])

                # Load WebSocket config if provided
                if "websocket" in config:
                    self.websocket = WebSocketConfig(**config["websocket"])

                # Load trading parameters
                self.trade_cooldown_after_close_seconds = config.get(
                    "trade_cooldown_after_close_seconds", self.trade_cooldown_after_close_seconds
                )
                self.min_candles_for_bot_start = config.get(
                    "min_candles_for_bot_start", self.min_candles_for_bot_start
                )

                logger.info(f"Loaded trading constants from {config_path}")
            else:
                logger.warning(f"Configuration file not found: {config_path}")

        except Exception as e:
            logger.error(f"Error loading configuration: {e}")

    def save_configuration(self, config_path: str) -> None:
        """Save current configuration to JSON file"""
        try:
            config = {
                "symbols": [
                    {
                        "symbol": config.symbol,
                        "tick_size": config.tick_size,
                        "step_size": config.step_size,
                        "min_notional": config.min_notional,
                        "price_precision": config.price_precision,
                        "quantity_precision": config.quantity_precision,
                    }
                    for config in self.symbol_configs.values()
                ],
                "timeframes": [
                    {
                        "timeframe": config.timeframe,
                        "minutes": config.minutes,
                        "websocket_enabled": config.websocket_enabled,
                        "min_candles_for_start": config.min_candles_for_start,
                    }
                    for config in self.timeframe_configs.values()
                ],
                "paths": {
                    "log_directory": self.paths.log_directory,
                    "signal_trace_path": self.paths.signal_trace_path,
                    "trade_performance_path": self.paths.trade_performance_path,
                    "portfolio_state_file": self.paths.portfolio_state_file,
                },
                "websocket": {
                    "base_url": self.websocket.base_url,
                    "base_url_futures": self.websocket.base_url_futures,
                    "reconnect_interval": self.websocket.reconnect_interval,
                    "max_reconnect_attempts": self.websocket.max_reconnect_attempts,
                    "ping_interval": self.websocket.ping_interval,
                    "ping_timeout": self.websocket.ping_timeout,
                },
                "trade_cooldown_after_close_seconds": self.trade_cooldown_after_close_seconds,
                "min_candles_for_bot_start": self.min_candles_for_bot_start,
            }

            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)

            logger.info(f"Saved trading constants to {config_path}")

        except Exception as e:
            logger.error(f"Error saving configuration: {e}")

    def get_symbol_config(self, symbol: str) -> SymbolConfig | None:
        """Get configuration for specific symbol"""
        return self.symbol_configs.get(symbol)

    def add_symbol_config(self, symbol: str, filters: list) -> SymbolConfig:
        """Add or update symbol configuration from Binance filters"""
        config = SymbolConfig.from_filters(symbol, filters)
        self.symbol_configs[symbol] = config
        logger.info(f"Added configuration for {symbol}")
        return config

    def get_timeframe_config(self, timeframe: str) -> TimeframeConfig:
        """Get configuration for specific timeframe"""
        if timeframe not in self.timeframe_configs:
            self.timeframe_configs[timeframe] = TimeframeConfig.from_string(timeframe)
        return self.timeframe_configs[timeframe]

    def ensure_directories(self) -> None:
        """Ensure all required directories exist"""
        self.paths.ensure_directories()


# Global instance
_trading_constants: TradingConstants | None = None


def get_trading_constants(config_path: str | None = None) -> TradingConstants:
    """Get or create the global trading constants instance"""
    global _trading_constants
    if _trading_constants is None:
        _trading_constants = TradingConstants(config_path)
    return _trading_constants


def reset_trading_constants() -> None:
    """Reset the global trading constants instance (for testing)"""
    global _trading_constants
    _trading_constants = None
