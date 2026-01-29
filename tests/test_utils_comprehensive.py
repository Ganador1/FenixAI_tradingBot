"""
Tests for utility modules with corrected APIs.
"""
import pytest
from unittest.mock import MagicMock, patch
import tempfile
import os


class TestStructuredLogger:
    """Tests for structured logger."""

    def test_logger_import(self):
        """Test structured logger can be imported."""
        from src.utils.structured_logger import StructuredLogger
        assert StructuredLogger is not None

    def test_logger_creation_with_log_dir(self):
        """Test creating a structured logger with log_dir."""
        from src.utils.structured_logger import StructuredLogger
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = StructuredLogger(name="test", log_dir=tmpdir)
            assert logger is not None

    def test_logger_info_method(self):
        """Test info logging."""
        from src.utils.structured_logger import StructuredLogger
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = StructuredLogger(name="test_info", log_dir=tmpdir)
            # Should not raise
            logger.info("Test message")

    def test_logger_debug_method(self):
        """Test debug logging."""
        from src.utils.structured_logger import StructuredLogger
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = StructuredLogger(name="test_debug", log_dir=tmpdir)
            logger.debug("Debug message")

    def test_logger_warning_method(self):
        """Test warning logging."""
        from src.utils.structured_logger import StructuredLogger
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = StructuredLogger(name="test_warning", log_dir=tmpdir)
            logger.warning("Warning message")

    def test_logger_error_method(self):
        """Test error logging."""
        from src.utils.structured_logger import StructuredLogger
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = StructuredLogger(name="test_error", log_dir=tmpdir)
            logger.error("Error message")

    def test_logger_with_context(self):
        """Test logging with context."""
        from src.utils.structured_logger import StructuredLogger
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = StructuredLogger(name="test_ctx", log_dir=tmpdir)
            logger.set_context(trade_id="123", symbol="BTCUSDT")
            logger.info("Test with context")


class TestLogLevel:
    """Tests for log levels."""

    def test_log_level_enum(self):
        """Test LogLevel enum values."""
        from src.utils.structured_logger import LogLevel
        
        assert LogLevel.DEBUG == 10
        assert LogLevel.INFO == 20
        assert LogLevel.WARNING == 30
        assert LogLevel.ERROR == 40


class TestLogContext:
    """Tests for log context dataclass."""

    def test_log_context_creation(self):
        """Test creating a log context."""
        from src.utils.structured_logger import LogContext
        
        ctx = LogContext(trade_id="123", symbol="BTCUSDT")
        assert ctx.trade_id == "123"
        assert ctx.symbol == "BTCUSDT"


class TestPerformanceMetric:
    """Tests for performance metric dataclass."""

    def test_performance_metric_creation(self):
        """Test creating a performance metric."""
        from src.utils.structured_logger import PerformanceMetric
        from datetime import datetime
        
        metric = PerformanceMetric(
            name="latency",
            value=150.5,
            unit="ms",
            timestamp=datetime.now()
        )
        
        assert metric.name == "latency"
        assert metric.value == 150.5
        assert metric.unit == "ms"


class TestSecurityEvent:
    """Tests for security event dataclass."""

    def test_security_event_creation(self):
        """Test creating a security event."""
        from src.utils.structured_logger import SecurityEvent, AlertSeverity
        
        event = SecurityEvent(
            event_type="login_attempt",
            severity=AlertSeverity.MEDIUM,
            description="Failed login attempt"
        )
        
        assert event.event_type == "login_attempt"
        assert event.severity == AlertSeverity.MEDIUM


class TestUniversalCircuitBreaker:
    """Tests for universal circuit breaker."""

    def test_circuit_breaker_import(self):
        """Test circuit breaker can be imported."""
        from src.utils.universal_circuit_breaker import UniversalCircuitBreaker
        assert UniversalCircuitBreaker is not None

    def test_circuit_breaker_creation(self):
        """Test creating a circuit breaker."""
        from src.utils.universal_circuit_breaker import UniversalCircuitBreaker
        
        cb = UniversalCircuitBreaker()
        assert cb is not None

    def test_circuit_breaker_has_call_method(self):
        """Test circuit breaker has call method."""
        from src.utils.universal_circuit_breaker import UniversalCircuitBreaker
        
        cb = UniversalCircuitBreaker()
        assert hasattr(cb, '__call__') or hasattr(cb, 'call') or hasattr(cb, 'execute')
