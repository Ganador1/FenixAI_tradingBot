# src/utils/indicator_cache.py
"""
TTL Cache for Technical Indicators.

Caches indicator calculations to avoid redundant computations
within the same timeframe.
"""

import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)


class TTLCache:
    """
    Simple TTL (Time-To-Live) cache implementation.

    Thread-safe cache that automatically expires entries after a configurable TTL.
    """

    def __init__(self, maxsize: int = 100, ttl: float = 60.0):
        """
        Initialize TTL cache.

        Args:
            maxsize: Maximum number of entries to keep
            ttl: Time-to-live in seconds for each entry
        """
        self.maxsize = maxsize
        self.ttl = ttl
        self._cache: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        """
        Get value from cache if exists and not expired.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/expired
        """
        if key not in self._cache:
            return None

        value, timestamp = self._cache[key]
        if time.time() - timestamp > self.ttl:
            # Expired
            del self._cache[key]
            return None

        return value

    def set(self, key: str, value: Any) -> None:
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache
        """
        # Evict oldest entries if at max size
        if len(self._cache) >= self.maxsize:
            self._evict_oldest()

        self._cache[key] = (value, time.time())

    def _evict_oldest(self) -> None:
        """Evict the oldest (or expired) entries."""
        now = time.time()

        # First, remove expired entries
        expired = [k for k, (_, ts) in self._cache.items() if now - ts > self.ttl]
        for key in expired:
            del self._cache[key]

        # If still at max, remove oldest
        if len(self._cache) >= self.maxsize:
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()

    def __contains__(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        return self.get(key) is not None

    def __len__(self) -> int:
        """Return number of non-expired entries."""
        now = time.time()
        return sum(1 for _, (_, ts) in self._cache.items() if now - ts <= self.ttl)


# Global indicator cache with 60 second TTL (configurable per timeframe)
_indicator_cache = TTLCache(maxsize=50, ttl=60.0)

# TTL configurations per timeframe
TIMEFRAME_TTL = {
    "1m": 30.0,  # 30 seconds for 1 minute candles
    "5m": 60.0,  # 1 minute for 5 minute candles
    "15m": 120.0,  # 2 minutes for 15 minute candles
    "1h": 300.0,  # 5 minutes for 1 hour candles
    "4h": 600.0,  # 10 minutes for 4 hour candles
    "1d": 1800.0,  # 30 minutes for daily candles
}


def get_indicator_cache_key(symbol: str, timeframe: str, indicator_name: str = "all") -> str:
    """
    Generate cache key for indicator data.

    Args:
        symbol: Trading pair symbol
        timeframe: Timeframe string
        indicator_name: Name of specific indicator or "all"

    Returns:
        Cache key string
    """
    return f"{symbol}:{timeframe}:{indicator_name}"


def cached_indicators(func: Callable) -> Callable:
    """
    Decorator to cache indicator calculations.

    Usage:
        @cached_indicators
        def calculate_indicators(symbol: str, timeframe: str) -> dict:
            ...
    """

    @wraps(func)
    def wrapper(symbol: str, timeframe: str, *args, **kwargs) -> dict:
        # Get TTL for this timeframe
        ttl = TIMEFRAME_TTL.get(timeframe, 60.0)

        # Update cache TTL if different
        global _indicator_cache
        if _indicator_cache.ttl != ttl:
            _indicator_cache = TTLCache(maxsize=50, ttl=ttl)

        # Check cache
        key = get_indicator_cache_key(symbol, timeframe)
        cached = _indicator_cache.get(key)

        if cached is not None:
            logger.debug(f"Cache hit for {key}")
            return cached

        # Calculate indicators
        logger.debug(f"Cache miss for {key}, calculating...")
        result = func(symbol, timeframe, *args, **kwargs)

        # Store in cache
        _indicator_cache.set(key, result)

        return result

    return wrapper


def clear_indicator_cache() -> None:
    """Clear all cached indicators."""
    _indicator_cache.clear()
    logger.info("Indicator cache cleared")


def get_cache_stats() -> dict[str, Any]:
    """Get cache statistics."""
    return {
        "size": len(_indicator_cache),
        "maxsize": _indicator_cache.maxsize,
        "ttl": _indicator_cache.ttl,
    }
