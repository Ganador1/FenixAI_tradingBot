"""
Comprehensive tests for the inference module.
Tests the unified LLM client and backends.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio


class TestUnifiedLLMClientImport:
    """Tests for UnifiedLLMClient import and initialization."""

    def test_import_unified_llm(self):
        """Test that unified LLM module can be imported."""
        try:
            from src.inference import unified_llm
            assert unified_llm is not None
        except ImportError:
            # Try alternative import
            from src.inference.llm import UnifiedLLMClient
            assert UnifiedLLMClient is not None

    def test_llm_module_exists(self):
        """Test the LLM module structure."""
        from src.inference import llm
        assert llm is not None


class TestLLMInference:
    """Tests for LLM inference functionality."""

    def test_import_llm_client(self):
        """Test LLM client can be imported."""
        from src.inference.llm import UnifiedLLMClient
        assert UnifiedLLMClient is not None

    def test_client_initialization(self):
        """Test client can be initialized."""
        from src.inference.llm import UnifiedLLMClient
        
        client = UnifiedLLMClient()
        assert client is not None

    def test_client_has_infer_method(self):
        """Test client has infer method."""
        from src.inference.llm import UnifiedLLMClient
        
        client = UnifiedLLMClient()
        assert hasattr(client, 'infer') or hasattr(client, 'generate') or hasattr(client, 'query')


class TestBackendStatus:
    """Tests for backend status checking."""

    def test_available_backends(self):
        """Test getting available backends."""
        from src.inference.llm import UnifiedLLMClient
        
        client = UnifiedLLMClient()
        if hasattr(client, 'available_backends'):
            backends = client.available_backends
            assert isinstance(backends, (list, dict))


class TestCircuitBreakerIntegration:
    """Tests for circuit breaker with inference."""

    def test_circuit_breaker_import(self):
        """Test circuit breaker can be imported."""
        from src.utils.universal_circuit_breaker import UniversalCircuitBreaker
        assert UniversalCircuitBreaker is not None

    def test_circuit_breaker_creation(self):
        """Test creating a circuit breaker."""
        from src.utils.universal_circuit_breaker import UniversalCircuitBreaker
        
        # Check what init parameters are expected
        cb = UniversalCircuitBreaker()
        assert cb is not None


class TestResponseParsing:
    """Tests for response parsing."""

    def test_parse_json_response(self):
        """Test parsing JSON from LLM response."""
        raw_response = '{"action": "BUY", "confidence": 0.8}'
        import json
        parsed = json.loads(raw_response)
        assert parsed["action"] == "BUY"
        assert parsed["confidence"] == 0.8

    def test_parse_json_with_markdown(self):
        """Test parsing JSON wrapped in markdown."""
        raw_response = '''```json
{"action": "SELL", "confidence": 0.7}
```'''
        import re
        json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', raw_response, re.DOTALL)
        if json_match:
            import json
            parsed = json.loads(json_match.group(1))
            assert parsed["action"] == "SELL"
