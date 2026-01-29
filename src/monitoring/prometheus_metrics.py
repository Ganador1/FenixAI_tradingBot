"""
Prometheus metrics instrumentation for FenixAI Trading Bot.

This module provides Prometheus metrics for monitoring trading activity,
agent performance, and system health.
"""

from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import time

# ============================================================================
# Trading Metrics
# ============================================================================

TRADES_TOTAL = Counter(
    "fenix_trades_total",
    "Total trades executed",
    ["symbol", "side", "status"]
)

TRADE_PNL = Histogram(
    "fenix_trade_pnl",
    "Trade PnL distribution",
    buckets=[-100, -50, -20, -10, 0, 10, 20, 50, 100, 500]
)

ACTIVE_POSITIONS = Gauge(
    "fenix_active_positions",
    "Current number of open positions"
)

PORTFOLIO_VALUE = Gauge(
    "fenix_portfolio_value_usd",
    "Current portfolio value in USD"
)

# ============================================================================
# Agent Metrics
# ============================================================================

AGENT_DECISIONS = Counter(
    "fenix_agent_decisions_total",
    "Agent decision count",
    ["agent_name", "decision"]
)

AGENT_LATENCY = Histogram(
    "fenix_agent_latency_seconds",
    "Agent response latency",
    ["agent_name"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
)

AGENT_CONFIDENCE = Histogram(
    "fenix_agent_confidence",
    "Agent confidence distribution",
    ["agent_name"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
)

REASONING_BANK_ENTRIES = Gauge(
    "fenix_reasoning_bank_entries",
    "Total entries in ReasoningBank",
    ["agent_name"]
)

# ============================================================================
# HTTP Metrics
# ============================================================================

HTTP_REQUESTS = Counter(
    "fenix_http_requests_total",
    "HTTP requests",
    ["method", "endpoint", "status"]
)

HTTP_LATENCY = Histogram(
    "fenix_http_latency_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

HTTP_IN_PROGRESS = Gauge(
    "fenix_http_requests_in_progress",
    "HTTP requests currently in progress",
    ["method"]
)

# ============================================================================
# System Metrics
# ============================================================================

SYSTEM_CPU_USAGE = Gauge(
    "fenix_system_cpu_usage_percent",
    "System CPU usage percentage"
)

SYSTEM_MEMORY_USAGE = Gauge(
    "fenix_system_memory_usage_percent",
    "System memory usage percentage"
)

ENGINE_STATUS = Gauge(
    "fenix_engine_running",
    "Trading engine running status (1 = running, 0 = stopped)"
)


# ============================================================================
# Middleware
# ============================================================================

class PrometheusMiddleware(BaseHTTPMiddleware):
    """Middleware to collect HTTP metrics for all requests."""
    
    async def dispatch(self, request: Request, call_next):
        method = request.method
        # Normalize path to avoid cardinality explosion
        path = request.url.path
        if path.startswith("/api/"):
            # Keep API paths but remove dynamic segments
            parts = path.split("/")
            normalized_parts = []
            for part in parts:
                # Replace UUIDs and numeric IDs with placeholder
                if len(part) == 36 and "-" in part:
                    normalized_parts.append("{id}")
                elif part.isdigit():
                    normalized_parts.append("{id}")
                else:
                    normalized_parts.append(part)
            path = "/".join(normalized_parts)
        else:
            path = "other"
        
        HTTP_IN_PROGRESS.labels(method=method).inc()
        start = time.perf_counter()
        
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            raise
        finally:
            latency = time.perf_counter() - start
            HTTP_IN_PROGRESS.labels(method=method).dec()
            HTTP_REQUESTS.labels(method=method, endpoint=path, status=status).inc()
            HTTP_LATENCY.labels(method=method, endpoint=path).observe(latency)
        
        return response


# ============================================================================
# Helper Functions
# ============================================================================

def record_trade(symbol: str, side: str, status: str, pnl: float = 0.0):
    """Record a trade execution."""
    TRADES_TOTAL.labels(symbol=symbol, side=side, status=status).inc()
    if pnl != 0.0:
        TRADE_PNL.observe(pnl)


def record_agent_decision(agent_name: str, decision: str, confidence: float, latency_seconds: float):
    """Record an agent decision."""
    AGENT_DECISIONS.labels(agent_name=agent_name, decision=decision).inc()
    AGENT_CONFIDENCE.labels(agent_name=agent_name).observe(confidence)
    AGENT_LATENCY.labels(agent_name=agent_name).observe(latency_seconds)


def update_system_metrics(cpu_percent: float, memory_percent: float, engine_running: bool):
    """Update system metrics."""
    SYSTEM_CPU_USAGE.set(cpu_percent)
    SYSTEM_MEMORY_USAGE.set(memory_percent)
    ENGINE_STATUS.set(1 if engine_running else 0)


async def metrics_endpoint():
    """Endpoint that exposes Prometheus metrics."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )
