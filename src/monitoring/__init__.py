# monitoring/__init__.py
"""
Sistema de monitoreo y métricas para FenixTradingBot
"""

try:
    from .scorecard_metrics_exporter import ScorecardMetricsExporter
except ImportError:
    ScorecardMetricsExporter = None  # type: ignore

try:
    from .prometheus_metrics import PrometheusMiddleware, metrics_endpoint
except ImportError:
    PrometheusMiddleware = None  # type: ignore
    metrics_endpoint = None  # type: ignore

__all__ = ["ScorecardMetricsExporter", "PrometheusMiddleware", "metrics_endpoint"]
