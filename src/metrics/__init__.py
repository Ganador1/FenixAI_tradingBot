"""Metrics module for FenixAI."""

from src.metrics.trading_metrics import (
    AgentMetrics,
    TradeMetrics,
    TradingMetricsDashboard,
    format_metrics_for_display,
    get_metrics_dashboard,
)

__all__ = [
    "TradingMetricsDashboard",
    "get_metrics_dashboard",
    "format_metrics_for_display",
    "TradeMetrics",
    "AgentMetrics",
]
