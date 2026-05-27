# src/core/container.py
"""
Fenix Dependency Injection Container.

Centralizes dependency management and provides a cleaner way
to manage singletons and service instances throughout the application.
"""

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)


class FenixConfig:
    """Configuration holder for Fenix components."""

    def __init__(
        self,
        testnet: bool = True,
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
        mode: str = "paper",
        llm_profile: str = "all_local",
        api_key: str | None = None,
        api_secret: str | None = None,
    ):
        self.testnet = testnet
        self.symbol = symbol
        self.timeframe = timeframe
        self.mode = mode
        self.llm_profile = llm_profile

        # Load API keys from env if not provided
        if testnet:
            self.api_key = api_key or os.getenv("BINANCE_TESTNET_API_KEY")
            self.api_secret = api_secret or os.getenv("BINANCE_TESTNET_API_SECRET")
        else:
            self.api_key = api_key or os.getenv("BINANCE_API_KEY")
            self.api_secret = api_secret or os.getenv("BINANCE_API_SECRET")

    @classmethod
    def from_env(cls) -> "FenixConfig":
        """Create config from environment variables."""
        return cls(
            testnet=os.getenv("FENIX_TESTNET", "true").lower() == "true",
            symbol=os.getenv("FENIX_SYMBOL", "BTCUSDT"),
            timeframe=os.getenv("FENIX_TIMEFRAME", "15m"),
            mode=os.getenv("FENIX_MODE", "paper"),
            llm_profile=os.getenv("FENIX_LLM_PROFILE", "all_local"),
        )


class FenixContainer:
    """
    Centralized dependency container for FenixAI.

    Manages lifecycle of all major services without global state.
    """

    def __init__(self, config: FenixConfig):
        self.config = config
        self._lock = threading.RLock()

        # Lazy-initialized services
        self._binance_service: Any = None
        self._risk_manager: Any = None
        self._trading_graph: Any = None
        self._reasoning_bank: Any = None
        self._llm_config: Any = None

    @property
    def binance_service(self) -> Any:
        """Get or create BinanceService instance."""
        if self._binance_service is None:
            with self._lock:
                if self._binance_service is None:
                    from src.services.binance_service import BinanceService

                    self._binance_service = BinanceService(
                        api_key=self.config.api_key,
                        api_secret=self.config.api_secret,
                        testnet=self.config.testnet,
                    )
                    if self._binance_service.initialize():
                        logger.info(
                            f"✅ BinanceService initialized (testnet={self.config.testnet})"
                        )
                    else:
                        logger.error("❌ Failed to initialize BinanceService")
        return self._binance_service

    @property
    def risk_manager(self) -> Any:
        """Get or create RuntimeRiskManager instance."""
        if self._risk_manager is None:
            with self._lock:
                if self._risk_manager is None:
                    from src.risk.runtime_risk_manager import RuntimeRiskManager

                    self._risk_manager = RuntimeRiskManager()
                    logger.info("✅ RuntimeRiskManager initialized")
        return self._risk_manager

    @property
    def llm_config(self) -> Any:
        """Get or create LLM provider configuration."""
        if self._llm_config is None:
            with self._lock:
                if self._llm_config is None:
                    from src.config.llm_provider_loader import LLMProviderLoader

                    loader = LLMProviderLoader()
                    self._llm_config = loader.load_profile(self.config.llm_profile)
                    logger.info(f"✅ LLM config loaded (profile={self.config.llm_profile})")
        return self._llm_config

    @property
    def reasoning_bank(self) -> Any:
        """Get or create ReasoningBank instance."""
        if self._reasoning_bank is None:
            with self._lock:
                if self._reasoning_bank is None:
                    try:
                        from src.memory.reasoning_bank import get_reasoning_bank

                        self._reasoning_bank = get_reasoning_bank()
                        logger.info("✅ ReasoningBank initialized")
                    except ImportError:
                        logger.warning("ReasoningBank not available")
                        self._reasoning_bank = None
        return self._reasoning_bank

    @property
    def trading_graph(self) -> Any:
        """Get or create FenixTradingGraph instance."""
        if self._trading_graph is None:
            with self._lock:
                if self._trading_graph is None:
                    from src.core.langgraph_orchestrator import FenixTradingGraph

                    self._trading_graph = FenixTradingGraph(
                        llm_config=self.llm_config,
                        reasoning_bank=self.reasoning_bank,
                    )
                    logger.info("✅ FenixTradingGraph initialized")
        return self._trading_graph

    def reset(self):
        """Reset all services (useful for testing)."""
        with self._lock:
            self._binance_service = None
            self._risk_manager = None
            self._trading_graph = None
            self._reasoning_bank = None
            self._llm_config = None
            logger.info("Container reset")


# Global container instance (optional, for compatibility)
_container: FenixContainer | None = None
_container_lock = threading.Lock()


def get_container(config: FenixConfig | None = None) -> FenixContainer:
    """Get or create the global container instance."""
    global _container

    if _container is None:
        with _container_lock:
            if _container is None:
                if config is None:
                    config = FenixConfig.from_env()
                _container = FenixContainer(config)

    return _container


def reset_container():
    """Reset the global container (for testing)."""
    global _container
    with _container_lock:
        if _container:
            _container.reset()
        _container = None
