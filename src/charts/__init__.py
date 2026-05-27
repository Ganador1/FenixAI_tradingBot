# src/charts/__init__.py
"""
Consolidated Chart System for Fenix Trading Bot.

Provides a Strategy Pattern-based chart generation system with:
- ChartStrategy ABC for pluggable renderers
- ChartService as the single entry point (replaces 7+ scattered files)
- Unified cache with TTL per timeframe
- Automatic fallback chain: Plotly → mplfinance → placeholder

Usage:
    from src.charts import get_chart_service

    service = get_chart_service()
    result = service.generate(kline_data, symbol="BTCUSDT", timeframe="15m")
    b64 = result.image_b64
"""

from src.charts.service import ChartService, get_chart_service
from src.charts.strategy import (
    ChartResult,
    ChartStrategy,
    RenderBackend,
    generate_indicator_summary,
)

__all__ = [
    "ChartStrategy",
    "ChartResult",
    "RenderBackend",
    "ChartService",
    "get_chart_service",
    "generate_indicator_summary",
]
