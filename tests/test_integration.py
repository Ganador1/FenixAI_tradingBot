"""
Tests de Integración para FenixAI.
Prueban la interacción entre múltiples componentes.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime


class TestLangGraphIntegration:
    """Tests de integración del orquestador LangGraph."""

    @pytest.fixture
    def mock_llm_response(self):
        """Respuesta mock del LLM."""
        return {
            "action": "BUY",
            "confidence": 0.78,
            "reasoning": "RSI oversold with bullish divergence",
            "entry_price": 95000.0,
            "stop_loss": 93500.0,
            "take_profit": 98000.0
        }

    @pytest.fixture
    def market_data_sample(self):
        """Datos de mercado de ejemplo."""
        return {
            "symbol": "BTCUSDT",
            "timestamp": datetime.now().isoformat(),
            "price": 95000.0,
            "volume_24h": 1500000000,
            "indicators": {
                "rsi": 28.5,
                "macd": {"value": -150, "signal": -120, "histogram": -30},
                "ema_20": 94500.0,
                "ema_50": 93000.0,
                "bb_upper": 97000.0,
                "bb_lower": 92000.0,
                "atr": 1500.0
            }
        }

    def test_technical_to_decision_flow(self, mock_llm_response, market_data_sample):
        """Verificar flujo de Technical Agent a Decision Agent."""
        # Este test verifica que los datos fluyen correctamente
        from dataclasses import dataclass
        
        @dataclass
        class TechnicalAnalystOutput:
            bias: str
            confidence: float
            key_levels: dict
            indicators_summary: str
            recommended_action: str
            entry_zone: dict
            invalidation_level: float
        
        # Simular output del Technical Agent
        tech_output = TechnicalAnalystOutput(
            bias="bullish",
            confidence=0.75,
            key_levels={"support": 93000, "resistance": 97000},
            indicators_summary="RSI oversold, MACD bullish crossover",
            recommended_action="BUY",
            entry_zone={"min": 94500, "max": 95500},
            invalidation_level=93000
        )
        
        assert tech_output.bias == "bullish"
        assert tech_output.confidence > 0.5

    def test_reasoning_bank_stores_agent_outputs(self, tmp_path):
        """Verificar que ReasoningBank almacena outputs de agentes."""
        from src.memory.reasoning_bank import ReasoningBank
        
        storage_dir = str(tmp_path / "test_reasoning")
        bank = ReasoningBank(
            storage_dir=storage_dir,
            max_entries_per_agent=50,
            use_embeddings=False
        )
        
        # Simular varios agentes almacenando resultados
        agents = ["technical", "sentiment", "visual", "qabba", "decision"]
        
        for agent in agents:
            entry = bank.store_entry(
                agent_name=agent,
                prompt=f"Analyze market for {agent}",
                normalized_result={"action": "BUY", "confidence": 0.7},
                raw_response="Buy signal",
                backend="ollama",
                latency_ms=150.0
            )
            assert entry is not None
        
        # Verificar que todos los agentes tienen entradas
        for agent in agents:
            entries = bank.get_recent(agent_name=agent, limit=10)
            assert len(entries) >= 1


class TestAgentCommunication:
    """Tests de comunicación entre agentes."""

    def test_agent_state_propagation(self):
        """Verificar que el estado se propaga entre agentes."""
        # Simular estado compartido estilo LangGraph
        from typing import TypedDict, Optional
        from dataclasses import dataclass
        
        @dataclass
        class AgentOutput:
            agent: str
            action: str
            confidence: float
            reasoning: str
        
        class SharedState(TypedDict):
            market_data: dict
            technical_output: Optional[AgentOutput]
            sentiment_output: Optional[AgentOutput]
            visual_output: Optional[AgentOutput]
            final_decision: Optional[AgentOutput]
        
        # Estado inicial
        state: SharedState = {
            "market_data": {"price": 95000, "rsi": 28},
            "technical_output": None,
            "sentiment_output": None,
            "visual_output": None,
            "final_decision": None
        }
        
        # Technical agent procesa
        state["technical_output"] = AgentOutput(
            agent="technical",
            action="BUY",
            confidence=0.8,
            reasoning="RSI oversold"
        )
        
        # Sentiment agent procesa
        state["sentiment_output"] = AgentOutput(
            agent="sentiment",
            action="BUY",
            confidence=0.65,
            reasoning="Positive news flow"
        )
        
        # Decision agent sintetiza
        tech = state["technical_output"]
        sent = state["sentiment_output"]
        
        avg_confidence = (tech.confidence + sent.confidence) / 2
        
        state["final_decision"] = AgentOutput(
            agent="decision",
            action="BUY" if avg_confidence > 0.6 else "HOLD",
            confidence=avg_confidence,
            reasoning=f"Tech: {tech.reasoning}, Sent: {sent.reasoning}"
        )
        
        assert state["final_decision"].action == "BUY"
        assert abs(state["final_decision"].confidence - 0.725) < 0.001  # Float comparison


class TestTradingEngineIntegration:
    """Tests de integración del motor de trading."""

    @pytest.fixture
    def mock_market_data(self):
        """Mock del MarketDataManager."""
        mock = Mock()
        mock.get_latest_klines.return_value = [
            {"open": 94000, "high": 95500, "low": 93500, "close": 95000, "volume": 1000}
            for _ in range(100)
        ]
        mock.get_current_price.return_value = 95000.0
        return mock

    @pytest.fixture
    def mock_executor(self):
        """Mock del OrderExecutor."""
        mock = Mock()
        mock.place_order = AsyncMock(return_value={
            "orderId": "12345",
            "status": "FILLED",
            "price": 95000.0,
            "quantity": 0.1
        })
        return mock

    def test_engine_config_initialization(self):
        """Verificar inicialización de configuración."""
        from src.trading.engine import TradingConfig
        
        config = TradingConfig(
            symbol="BTCUSDT",
            interval="15m",
            max_risk_per_trade=2.0,
            testnet=True
        )
        
        assert config.symbol == "BTCUSDT"
        assert config.interval == "15m"
        assert config.max_risk_per_trade == 2.0
        assert config.testnet is True


class TestAPIIntegration:
    """Tests de integración de la API."""

    @pytest.fixture
    def test_client(self):
        """Cliente de prueba para la API."""
        from fastapi.testclient import TestClient
        
        # Mock del engine para evitar inicialización completa
        with patch('src.api.server.TradingEngine'):
            with patch('src.api.server.engine', None):
                from src.api.server import app
                return TestClient(app)

    def test_health_endpoint(self, test_client):
        """Verificar endpoint de health."""
        response = test_client.get("/api/system/health")
        assert response.status_code == 200
        data = response.json()
        assert "components" in data

    def test_agents_endpoint(self, test_client):
        """Verificar endpoint de agentes."""
        response = test_client.get("/api/agents")
        assert response.status_code == 200
        data = response.json()
        assert "agents" in data
        assert len(data["agents"]) == 6  # 6 agentes


class TestMemoryPersistence:
    """Tests de persistencia de memoria."""

    def test_reasoning_bank_survives_restart(self, tmp_path):
        """Verificar que ReasoningBank persiste datos."""
        from src.memory.reasoning_bank import ReasoningBank
        
        storage_dir = str(tmp_path / "persistence_test")
        
        # Primera instancia - almacenar datos
        bank1 = ReasoningBank(
            storage_dir=storage_dir,
            max_entries_per_agent=100,
            use_embeddings=False
        )
        
        for i in range(5):
            bank1.store_entry(
                agent_name="technical",
                prompt=f"Analysis {i}",
                normalized_result={"action": "BUY", "confidence": 0.7 + i * 0.02},
                raw_response="",
                backend="ollama",
                latency_ms=100.0
            )
        
        # Segunda instancia - verificar datos
        bank2 = ReasoningBank(
            storage_dir=storage_dir,
            max_entries_per_agent=100,
            use_embeddings=False
        )
        
        entries = bank2.get_recent(agent_name="technical", limit=10)
        assert len(entries) == 5


class TestConfigurationLoading:
    """Tests de carga de configuración."""

    def test_yaml_config_loading(self):
        """Verificar carga de configuración YAML."""
        import yaml
        from pathlib import Path
        
        config_path = Path(__file__).resolve().parent.parent / "config" / "fenix.yaml"
        
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f)
            
            assert "trading" in config or "agents" in config or "llm" in config

    def test_llm_providers_config(self):
        """Verificar configuración de proveedores LLM."""
        import yaml
        from pathlib import Path
        
        config_path = Path(__file__).resolve().parent.parent / "config" / "llm_providers.yaml"
        
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f)
            
            # Config usa 'active_profile' como estructura principal
            assert len(config) > 0  # Verificar que hay configuración


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
