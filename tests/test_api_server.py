"""
Tests for API server functionality.
Tests OpenAPI configuration, Pydantic schemas, and helper functions.
"""
import pytest
from unittest.mock import MagicMock
from datetime import datetime


class TestPydanticSchemas:
    """Tests for Pydantic request/response schemas."""

    def test_order_create_schema(self):
        """Test OrderCreate schema validation."""
        from src.api.server import OrderCreate
        
        order = OrderCreate(
            symbol="BTCUSDT",
            type="market",
            side="buy",
            quantity=0.001
        )
        
        assert order.symbol == "BTCUSDT"
        assert order.quantity == 0.001

    def test_order_create_with_price(self):
        """Test OrderCreate with limit price."""
        from src.api.server import OrderCreate
        
        order = OrderCreate(
            symbol="ETHUSDT",
            type="limit",
            side="sell",
            quantity=0.5,
            price=2000.0
        )
        
        assert order.price == 2000.0

    def test_order_create_with_stop(self):
        """Test OrderCreate with stop price."""
        from src.api.server import OrderCreate
        
        order = OrderCreate(
            symbol="BTCUSDT",
            type="stop",
            side="sell",
            quantity=0.01,
            stop_price=50000.0
        )
        
        assert order.stop_price == 50000.0

    def test_engine_config_update_schema(self):
        """Test EngineConfigUpdate schema."""
        from src.api.server import EngineConfigUpdate
        
        config = EngineConfigUpdate(
            symbol="ETHUSDT",
            timeframe="1h",
            paper_trading=True
        )
        
        assert config.symbol == "ETHUSDT"
        assert config.paper_trading is True

    def test_engine_config_update_partial(self):
        """Test EngineConfigUpdate with partial fields."""
        from src.api.server import EngineConfigUpdate
        
        config = EngineConfigUpdate(
            enable_visual_agent=False
        )
        
        assert config.enable_visual_agent is False
        assert config.symbol is None


class TestHelperFunctions:
    """Tests for helper/utility functions in server."""

    def test_serialize_agent_output_model(self):
        """Test agent output serialization."""
        from src.api.server import _serialize_agent_output_model
        
        mock_output = MagicMock()
        mock_output.id = "test-id"
        mock_output.agent_id = "agent-1"
        mock_output.agent_name = "technical"
        mock_output.timestamp = datetime(2026, 1, 28, 12, 0, 0)
        mock_output.reasoning = "Buy signal detected"
        mock_output.decision = "BUY"
        mock_output.confidence = 0.85
        mock_output.input_summary = "Market analysis"
        
        result = _serialize_agent_output_model(mock_output)
        
        assert result["id"] == "test-id"
        assert result["agent_name"] == "technical"
        assert result["decision"] == "BUY"
        assert result["confidence"] == 0.85

    def test_build_scorecards_empty(self):
        """Test scorecard building with empty list."""
        from src.api.server import _build_scorecards
        
        result = _build_scorecards([])
        
        assert result == []

    def test_build_scorecards_with_outputs(self):
        """Test scorecard building with agent outputs."""
        from src.api.server import _build_scorecards
        
        mock_output1 = MagicMock()
        mock_output1.agent_id = "agent-1"
        mock_output1.agent_name = "technical"
        mock_output1.confidence = 0.75
        mock_output1.timestamp = datetime.now()
        
        mock_output2 = MagicMock()
        mock_output2.agent_id = "agent-1"
        mock_output2.agent_name = "technical"
        mock_output2.confidence = 0.45
        mock_output2.timestamp = datetime.now()
        
        result = _build_scorecards([mock_output1, mock_output2])
        
        assert len(result) == 1
        assert result[0]["agent_name"] == "technical"
        assert result[0]["total_signals"] == 2
        assert result[0]["successful_signals"] == 1  # Only 0.75 >= 0.6

    def test_build_reasoning_analytics_empty(self):
        """Test reasoning analytics with no outputs."""
        from src.api.server import _build_reasoning_analytics
        
        result = _build_reasoning_analytics([])
        
        assert result["total_entries"] == 0
        assert result["avg_confidence"] == 0.0

    def test_summarize_metrics(self):
        """Test metrics summarization."""
        from src.api.server import _summarize_metrics
        
        test_metrics = {
            "cpu": {"usage": 50.0},
            "memory": {"percentage": 60.0},
            "disk": {"percentage": 70.0},
            "network": {"bytes_in": 1000, "bytes_out": 500},
            "process": {"uptime": 3600},
        }
        
        summary = _summarize_metrics(test_metrics)
        
        assert summary["cpu"] == 50.0
        assert summary["memory"] == 60.0
        assert summary["disk"] == 70.0
        assert summary["network"] == 1500  # bytes_in + bytes_out

    def test_engine_config_payload_with_none(self):
        """Test engine config payload when engine is None."""
        from src.api.server import _engine_config_payload
        
        result = _engine_config_payload(None)
        
        assert result == {}

    def test_engine_config_payload_with_engine(self):
        """Test engine config payload with engine."""
        from src.api.server import _engine_config_payload
        
        mock_engine = MagicMock()
        mock_engine.symbol = "ETHUSDT"
        mock_engine.timeframe = "1h"
        mock_engine.paper_trading = True
        mock_engine.allow_live_trading = False
        mock_engine.enable_visual = True
        mock_engine.enable_sentiment = True
        
        result = _engine_config_payload(mock_engine)
        
        assert result["symbol"] == "ETHUSDT"
        assert result["timeframe"] == "1h"
        assert result["paper_trading"] is True

    def test_build_connection_status(self):
        """Test connection status building."""
        from src.api.server import _build_connection_status
        
        connections = _build_connection_status()
        
        assert isinstance(connections, list)
        service_names = [c["service"] for c in connections]
        assert "binance" in service_names


class TestSystemMetrics:
    """Tests for system metrics functions."""

    def test_build_system_metrics_structure(self):
        """Test that system metrics have correct structure."""
        from src.api.server import build_system_metrics
        
        metrics = build_system_metrics()
        
        assert "cpu" in metrics
        assert "memory" in metrics
        assert "disk" in metrics
        assert "network" in metrics
        assert "process" in metrics
        assert "timestamp" in metrics

    def test_build_system_metrics_cpu_structure(self):
        """Test CPU metrics structure."""
        from src.api.server import build_system_metrics
        
        metrics = build_system_metrics()
        
        assert "usage" in metrics["cpu"]
        assert "cores" in metrics["cpu"]
        assert isinstance(metrics["cpu"]["usage"], (int, float))

    def test_build_system_metrics_memory_structure(self):
        """Test memory metrics structure."""
        from src.api.server import build_system_metrics
        
        metrics = build_system_metrics()
        
        assert "total" in metrics["memory"]
        assert "used" in metrics["memory"]
        assert "percentage" in metrics["memory"]
