# src/trading/__init__.py
"""
Fenix Trading Module.

This module contains the refactored core of the trading system,
organized in a modular and maintainable way.

Main components:
- TradingEngine: Main trading engine
- MarketDataManager: Real-time market data management
"""

from .engine import TradingEngine
from .market_data import MarketDataManager

__all__ = [
    "TradingEngine",
    "MarketDataManager",
]
