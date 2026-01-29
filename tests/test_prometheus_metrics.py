"""
Tests for Prometheus metrics module.
Tests are designed to work independently without importing full monitoring package.
"""
import pytest
from unittest.mock import AsyncMock


class TestPrometheusMetricsImport:
    """Tests for Prometheus metrics module import and basic functionality."""

    def test_direct_import(self):
        """Test direct import of prometheus_metrics module."""
        # Import directly to bypass __init__.py issues
        from src.monitoring.prometheus_metrics import (
            TRADES_TOTAL,
            TRADE_PNL,
            ACTIVE_POSITIONS,
            AGENT_DECISIONS,
            AGENT_LATENCY,
            HTTP_REQUESTS,
            HTTP_LATENCY,
            record_trade,
            record_agent_decision,
            update_system_metrics,
        )
        
        assert TRADES_TOTAL is not None
        assert TRADE_PNL is not None
        assert ACTIVE_POSITIONS is not None

    def test_record_trade(self):
        """Test recording a trade."""
        from src.monitoring.prometheus_metrics import record_trade, TRADES_TOTAL
        
        # Get initial value - prometheus counters start at 0
        initial = TRADES_TOTAL.labels(symbol="TESTUSDT", side="BUY", status="test")._value.get()
        
        record_trade("TESTUSDT", "BUY", "test", pnl=10.5)
        
        # Verify counter incremented
        new_value = TRADES_TOTAL.labels(symbol="TESTUSDT", side="BUY", status="test")._value.get()
        assert new_value == initial + 1

    def test_record_agent_decision(self):
        """Test recording agent decisions."""
        from src.monitoring.prometheus_metrics import record_agent_decision, AGENT_DECISIONS
        
        initial = AGENT_DECISIONS.labels(agent_name="test_agent", decision="TEST")._value.get()
        
        record_agent_decision("test_agent", "TEST", confidence=0.85, latency_seconds=1.5)
        
        new_value = AGENT_DECISIONS.labels(agent_name="test_agent", decision="TEST")._value.get()
        assert new_value == initial + 1

    def test_update_system_metrics(self):
        """Test updating system metrics."""
        from src.monitoring.prometheus_metrics import (
            update_system_metrics,
            SYSTEM_CPU_USAGE,
            SYSTEM_MEMORY_USAGE,
            ENGINE_STATUS,
        )
        
        update_system_metrics(cpu_percent=45.5, memory_percent=60.0, engine_running=True)
        
        assert SYSTEM_CPU_USAGE._value.get() == 45.5
        assert SYSTEM_MEMORY_USAGE._value.get() == 60.0
        assert ENGINE_STATUS._value.get() == 1

    def test_engine_status_stopped(self):
        """Test engine status when stopped."""
        from src.monitoring.prometheus_metrics import update_system_metrics, ENGINE_STATUS
        
        update_system_metrics(cpu_percent=10.0, memory_percent=20.0, engine_running=False)
        
        assert ENGINE_STATUS._value.get() == 0

    def test_active_positions_gauge(self):
        """Test active positions gauge."""
        from src.monitoring.prometheus_metrics import ACTIVE_POSITIONS
        
        ACTIVE_POSITIONS.set(5)
        assert ACTIVE_POSITIONS._value.get() == 5
        
        ACTIVE_POSITIONS.set(3)
        assert ACTIVE_POSITIONS._value.get() == 3

    def test_portfolio_value_gauge(self):
        """Test portfolio value gauge."""
        from src.monitoring.prometheus_metrics import PORTFOLIO_VALUE
        
        PORTFOLIO_VALUE.set(10000.50)
        assert PORTFOLIO_VALUE._value.get() == 10000.50

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self):
        """Test the /metrics endpoint response."""
        from src.monitoring.prometheus_metrics import metrics_endpoint
        
        response = await metrics_endpoint()
        
        assert response.status_code == 200
        # Our custom metrics should be in the response
        content = response.body.decode('utf-8')
        assert "fenix_" in content


class TestPrometheusMiddleware:
    """Tests for Prometheus HTTP middleware."""

    def test_middleware_import(self):
        """Test that middleware can be imported."""
        from src.monitoring.prometheus_metrics import PrometheusMiddleware
        
        assert PrometheusMiddleware is not None

    @pytest.mark.asyncio
    async def test_middleware_with_starlette(self):
        """Test middleware integration with Starlette."""
        from src.monitoring.prometheus_metrics import PrometheusMiddleware
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient
        
        async def homepage(request):
            return PlainTextResponse("OK")
        
        app = Starlette(routes=[Route("/api/test", homepage)])
        app.add_middleware(PrometheusMiddleware)
        
        client = TestClient(app)
        response = client.get("/api/test")
        
        assert response.status_code == 200
        assert response.text == "OK"
