# src/charts/service.py
"""
Unified Chart Service for Fenix Trading Bot.

Single entry-point that replaces chart_provider, chart_capture_scheduler,
unified_chart_system, and dual_chart_system.

Features:
- Strategy-pattern fallback chain (Plotly → mplfinance → placeholder)
- Unified TTL cache per symbol/timeframe
- Thread-safe singleton access via ``get_chart_service()``
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from src.charts.strategy import (
    ChartResult,
    ChartStrategy,
    prepare_dataframe,
)

logger = logging.getLogger(__name__)

# TTL per timeframe (seconds)
TIMEFRAME_TTL: dict[str, float] = {
    "1m": 45,
    "3m": 90,
    "5m": 120,
    "15m": 300,
    "30m": 600,
    "1h": 1800,
    "4h": 3600,
    "1d": 7200,
}


class ChartService:
    """
    Unified chart generation service with caching and fallback.

    Usage::

        svc = ChartService()           # or get_chart_service()
        result = svc.generate(kline_data, symbol="BTCUSDT", timeframe="15m")
        if result.ok:
            b64 = result.image_b64
    """

    def __init__(
        self,
        strategies: list[ChartStrategy] | None = None,
        cache_max_size: int = 100,
    ):
        if strategies is not None:
            self._strategies = strategies
        else:
            # Build default fallback chain (lazy imports to avoid import-time deps)
            self._strategies = self._build_default_chain()

        self._cache: dict[str, ChartResult] = {}
        self._cache_max = cache_max_size
        self._lock = threading.RLock()

        backends = [s.backend.value for s in self._strategies if s.available]
        logger.info("ChartService ready — backends: %s", backends)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        kline_data: dict[str, list],
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
        indicators: list[str] | None = None,
        *,
        show_volume: bool = True,
        show_rsi: bool = True,
        show_macd: bool = True,
        last_n_candles: int = 100,
        use_cache: bool = True,
    ) -> ChartResult:
        """
        Generate (or retrieve from cache) a chart image.

        Walks the fallback chain until one strategy succeeds.
        """
        # Check cache first
        if use_cache:
            cached = self.get_cached(symbol, timeframe)
            if cached is not None:
                return cached

        # Prepare DataFrame
        df = prepare_dataframe(kline_data)
        if df is None:
            return self._placeholder("Insufficient data", symbol, timeframe)

        # Trim to last N candles
        if len(df) > last_n_candles:
            df = df.tail(last_n_candles).copy()

        # Walk fallback chain
        for strategy in self._strategies:
            if not strategy.available:
                continue
            result = strategy.render(
                df,
                symbol=symbol,
                timeframe=timeframe,
                indicators=indicators,
                show_volume=show_volume,
                show_rsi=show_rsi,
                show_macd=show_macd,
            )
            if result.ok:
                self._store(symbol, timeframe, result)
                return result
            logger.warning(
                "Strategy %s failed for %s/%s: %s — trying next",
                strategy.backend.value,
                symbol,
                timeframe,
                result.error,
            )

        # Everything failed → placeholder
        return self._placeholder("All strategies failed", symbol, timeframe)

    def get_cached(
        self,
        symbol: str,
        timeframe: str,
        max_age: float | None = None,
    ) -> ChartResult | None:
        """Return cached result if still fresh, else None."""
        key = f"{symbol}_{timeframe}"
        with self._lock:
            result = self._cache.get(key)
        if result is None:
            return None
        ttl = max_age or TIMEFRAME_TTL.get(timeframe, 120)
        if result.age_seconds > ttl:
            return None
        logger.debug("Cache hit for %s (age %.1fs)", key, result.age_seconds)
        return result

    def invalidate(self, symbol: str, timeframe: str) -> None:
        """Remove a specific entry from cache."""
        key = f"{symbol}_{timeframe}"
        with self._lock:
            self._cache.pop(key, None)

    def clear_cache(self) -> None:
        """Clear entire cache."""
        with self._lock:
            self._cache.clear()
        logger.info("Chart cache cleared")

    def get_stats(self) -> dict[str, Any]:
        """Return cache and backend statistics."""
        with self._lock:
            entries = {
                k: {
                    "backend": v.backend.value,
                    "age_s": round(v.age_seconds, 1),
                    "ok": v.ok,
                }
                for k, v in self._cache.items()
            }
        return {
            "cache_entries": len(entries),
            "cache_max": self._cache_max,
            "backends": [s.backend.value for s in self._strategies if s.available],
            "entries": entries,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _store(self, symbol: str, timeframe: str, result: ChartResult) -> None:
        key = f"{symbol}_{timeframe}"
        with self._lock:
            # Evict oldest if full
            if len(self._cache) >= self._cache_max:
                oldest = min(self._cache, key=lambda k: self._cache[k].generated_at)
                del self._cache[oldest]
            self._cache[key] = result

    def _placeholder(self, message: str, symbol: str, timeframe: str) -> ChartResult:
        """Generate a placeholder via the last strategy (always PlaceholderChartStrategy)."""
        for s in reversed(self._strategies):
            if s.available:
                return s.render(None, symbol=symbol, timeframe=timeframe, message=message)
        # Absolute fallback — return empty result
        return ChartResult(error=message, symbol=symbol, timeframe=timeframe)

    @staticmethod
    def _build_default_chain() -> list[ChartStrategy]:
        """Build the default strategy chain: Plotly → mplfinance → placeholder."""
        chain: list[ChartStrategy] = []
        try:
            from src.charts.plotly_strategy import PlotlyChartStrategy

            chain.append(PlotlyChartStrategy())
        except Exception:
            pass
        try:
            from src.charts.mpl_strategy import MplfinanceChartStrategy

            chain.append(MplfinanceChartStrategy())
        except Exception:
            pass
        try:
            from src.charts.placeholder_strategy import PlaceholderChartStrategy

            chain.append(PlaceholderChartStrategy())
        except Exception:
            pass
        return chain


# ---------- Singleton ----------

_singleton: ChartService | None = None
_singleton_lock = threading.Lock()


def get_chart_service() -> ChartService:
    """Get or create the global ChartService singleton."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ChartService()
    return _singleton


# ---------- Backward-compatible helpers ----------
# These match the old chart_provider.py API so engine.py can migrate gradually.


def get_chart(symbol: str, timeframe: str, max_age_seconds: float | None = None):
    """
    Backward-compat wrapper matching old chart_provider.get_chart().

    Returns a ChartResult (which has .image_b64 like ChartSnapshot).
    """
    return get_chart_service().get_cached(symbol, timeframe, max_age_seconds)
