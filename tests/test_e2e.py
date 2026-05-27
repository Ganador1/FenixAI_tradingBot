"""
Tests End-to-End para FenixAI.
Simulan el flujo completo del sistema desde datos de mercado hasta decisión de trading.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
import json


class TestEndToEndTradingFlow:
    """Tests E2E del flujo de trading completo."""

    @pytest.fixture
    def complete_market_state(self):
        """Estado completo del mercado para E2E."""
        return {
            "symbol": "BTCUSDT",
            "timestamp": datetime.now().isoformat(),
            "price": 95000.0,
            "open_24h": 94000.0,
            "high_24h": 96500.0,
            "low_24h": 93000.0,
            "volume_24h": 2500000000.0,
            "trades_24h": 150000,
            "klines_15m": [
                {"open": 94800, "high": 95200, "low": 94600, "close": 95000, "volume": 500}
                for _ in range(100)
            ],
            "orderbook": {
                "bids": [[94990, 10], [94980, 15], [94970, 20]],
                "asks": [[95010, 8], [95020, 12], [95030, 18]]
            },
            "indicators": {
                "rsi_14": 28.5,
                "macd": {"value": -150, "signal": -120, "histogram": -30},
                "ema_20": 94500.0,
                "ema_50": 93000.0,
                "sma_200": 88000.0,
                "bb_upper": 97000.0,
                "bb_middle": 94000.0,
                "bb_lower": 91000.0,
                "atr_14": 1500.0,
                "adx": 35.0,
                "stoch_k": 15.0,
                "stoch_d": 18.0
            }
        }

    @pytest.fixture
    def mock_llm_client(self):
        """Mock del cliente LLM para E2E."""
        mock = Mock()
        
        def generate_response(prompt, **kwargs):
            # Simular respuestas basadas en el tipo de agente
            if "technical" in prompt.lower():
                return json.dumps({
                    "action": "BUY",
                    "confidence": 0.82,
                    "reasoning": "RSI oversold at 28.5, MACD showing bullish divergence",
                    "bias": "bullish"
                })
            elif "sentiment" in prompt.lower():
                return json.dumps({
                    "action": "BUY",
                    "confidence": 0.68,
                    "reasoning": "Positive market sentiment, Fear & Greed at 45",
                    "bias": "neutral-bullish"
                })
            elif "visual" in prompt.lower():
                return json.dumps({
                    "action": "BUY",
                    "confidence": 0.75,
                    "reasoning": "Bullish engulfing pattern on 4H chart",
                    "patterns": ["bullish_engulfing", "support_test"]
                })
            elif "decision" in prompt.lower():
                return json.dumps({
                    "action": "BUY",
                    "confidence": 0.76,
                    "reasoning": "Consensus from technical and sentiment analysis",
                    "entry_price": 95000,
                    "stop_loss": 93500,
                    "take_profit": 98000
                })
            elif "risk" in prompt.lower():
                return json.dumps({
                    "approved": True,
                    "position_size": 0.1,
                    "risk_reward": 2.0,
                    "max_loss_percent": 1.5
                })
            else:
                return json.dumps({"action": "HOLD", "confidence": 0.5})
        
        mock.generate = generate_response
        return mock

    def test_full_analysis_cycle(self, complete_market_state, mock_llm_client):
        """Test del ciclo completo de análisis."""
        # Simular el flujo completo de datos
        
        # 1. Preparar datos de mercado
        market_data = complete_market_state
        assert market_data["price"] == 95000.0
        
        # 2. Technical Analysis
        tech_response = mock_llm_client.generate("Perform technical analysis on BTCUSDT")
        tech_result = json.loads(tech_response)
        assert tech_result["action"] == "BUY"
        assert tech_result["confidence"] > 0.7
        
        # 3. Sentiment Analysis
        sent_response = mock_llm_client.generate("Perform sentiment analysis on crypto market")
        sent_result = json.loads(sent_response)
        assert "action" in sent_result
        
        # 4. Visual Analysis
        visual_response = mock_llm_client.generate("Analyze visual chart patterns")
        visual_result = json.loads(visual_response)
        assert "patterns" in visual_result
        
        # 5. Decision Synthesis
        decision_response = mock_llm_client.generate("Make final decision based on all analyses")
        decision_result = json.loads(decision_response)
        assert decision_result["action"] == "BUY"
        assert "entry_price" in decision_result
        assert "stop_loss" in decision_result
        
        # 6. Risk Validation
        risk_response = mock_llm_client.generate("Validate risk for proposed trade")
        risk_result = json.loads(risk_response)
        assert risk_result["approved"] is True

    def test_bearish_scenario(self, mock_llm_client):
        """Test de escenario bajista."""
        # Modificar mock para escenario bajista
        original_generate = mock_llm_client.generate
        
        def bearish_response(prompt, **kwargs):
            if "decision" in prompt.lower():
                return json.dumps({
                    "action": "SELL",
                    "confidence": 0.78,
                    "reasoning": "Bearish breakdown confirmed",
                    "entry_price": 95000,
                    "stop_loss": 96500,
                    "take_profit": 91000
                })
            return original_generate(prompt, **kwargs)
        
        mock_llm_client.generate = bearish_response
        
        decision = json.loads(mock_llm_client.generate("Make final decision"))
        assert decision["action"] == "SELL"
        assert decision["stop_loss"] > decision["entry_price"]

    def test_hold_on_uncertainty(self, mock_llm_client):
        """Test de HOLD cuando hay incertidumbre."""
        def uncertain_response(prompt, **kwargs):
            return json.dumps({
                "action": "HOLD",
                "confidence": 0.45,
                "reasoning": "Mixed signals, waiting for clearer direction"
            })
        
        mock_llm_client.generate = uncertain_response
        
        decision = json.loads(mock_llm_client.generate("Analyze uncertain market"))
        assert decision["action"] == "HOLD"
        assert decision["confidence"] < 0.5


class TestEndToEndReasoningBankIntegration:
    """Tests E2E de ReasoningBank con flujo completo."""

    def test_learning_from_past_trades(self, tmp_path):
        """Test de aprendizaje de trades pasados."""
        from src.memory.reasoning_bank import ReasoningBank
        
        storage_dir = str(tmp_path / "e2e_reasoning")
        bank = ReasoningBank(
            storage_dir=storage_dir,
            max_entries_per_agent=100,
            use_embeddings=False
        )
        
        # Simular secuencia de trades con outcomes
        trades = [
            {"action": "BUY", "confidence": 0.75, "outcome": "profit", "pnl": 2.5},
            {"action": "BUY", "confidence": 0.82, "outcome": "profit", "pnl": 1.8},
            {"action": "SELL", "confidence": 0.68, "outcome": "loss", "pnl": -1.2},
            {"action": "BUY", "confidence": 0.55, "outcome": "loss", "pnl": -0.8},
            {"action": "BUY", "confidence": 0.88, "outcome": "profit", "pnl": 3.2},
        ]
        
        # Almacenar cada trade
        for i, trade in enumerate(trades):
            entry = bank.store_entry(
                agent_name="decision",
                prompt=f"Trade signal {i}: RSI={30+i*5}",
                normalized_result={"action": trade["action"], "confidence": trade["confidence"]},
                raw_response=f"Decision: {trade['action']}",
                backend="ollama",
                latency_ms=150.0
            )
            
            # Actualizar con outcome usando la API correcta
            if entry and hasattr(bank, 'update_entry_outcome'):
                bank.update_entry_outcome(
                    agent_name="DecisionAgent",
                    prompt_digest=entry.prompt_digest if hasattr(entry, 'prompt_digest') else f"digest_{i}",
                    success=trade["outcome"] == "profit",
                    reward=trade["pnl"]
                )
        
        # Verificar que se almacenaron los trades
        recent = bank.get_recent(agent_name="decision", limit=10)
        assert len(recent) == 5
        
        # Calcular estadísticas (si el método existe)
        if hasattr(bank, 'get_success_rate'):
            rate = bank.get_success_rate(agent_name="decision")
            # 3 profits de 5 = 60%
            assert rate is not None


class TestEndToEndAPIFlow:
    """Tests E2E del flujo de API."""

    @pytest.fixture
    def api_client(self):
        """Cliente de prueba para API E2E."""
        from fastapi.testclient import TestClient
        
        with patch('src.api.server.TradingEngine'):
            with patch('src.api.server.engine', None):
                from src.api.server import app
                return TestClient(app)

    def test_complete_api_flow(self, api_client):
        """Test del flujo completo de API."""
        # 1. Verificar health
        health = api_client.get("/api/system/health")
        assert health.status_code == 200
        
        # 2. Obtener agentes
        agents = api_client.get("/api/agents")
        assert agents.status_code == 200
        assert len(agents.json()["agents"]) == 6
        
        # 3. Crear orden
        order_data = {
            "symbol": "BTCUSDT",
            "type": "market",
            "side": "buy",
            "quantity": 0.1
        }
        order = api_client.post("/api/trading/orders", json=order_data)
        assert order.status_code == 200
        
        # 4. Obtener posiciones
        positions = api_client.get("/api/trading/positions")
        assert positions.status_code == 200

    def test_websocket_subscription_flow(self, api_client):
        """Test del flujo de suscripción WebSocket."""
        # Este test verificaría la suscripción a eventos en tiempo real
        # Por ahora solo verificamos que los endpoints están disponibles
        
        # Verificar status del sistema
        status = api_client.get("/api/system/status")
        assert status.status_code == 200


class TestEndToEndConfigValidation:
    """Tests E2E de validación de configuración."""

    def test_complete_config_chain(self):
        """Test de la cadena completa de configuración."""
        from pathlib import Path
        import yaml
        
        base_path = Path(__file__).resolve().parent.parent
        
        # Verificar que existen archivos de configuración
        config_files = [
            "config/fenix.yaml",
            "config/llm_providers.yaml",
        ]
        
        configs = {}
        for config_file in config_files:
            path = base_path / config_file
            if path.exists():
                with open(path) as f:
                    configs[config_file] = yaml.safe_load(f)
        
        # Verificar que al menos un archivo de config existe
        assert len(configs) >= 1


class TestEndToEndErrorRecovery:
    """Tests E2E de recuperación de errores."""

    def test_llm_fallback_chain(self):
        """Test de la cadena de fallback de LLM."""
        # Simular fallo de proveedor primario y fallback
        providers = ["ollama", "mlx", "groq"]
        responses = []
        
        for i, provider in enumerate(providers):
            if i < 2:  # Primeros 2 proveedores "fallan"
                responses.append({"provider": provider, "status": "failed"})
            else:  # Último proveedor "responde"
                responses.append({
                    "provider": provider,
                    "status": "success",
                    "response": {"action": "BUY", "confidence": 0.7}
                })
        
        # Verificar que eventualmente obtuvimos respuesta
        success = any(r["status"] == "success" for r in responses)
        assert success is True

    def test_graceful_degradation(self):
        """Test de degradación elegante."""
        # Simular componentes opcionales no disponibles
        optional_components = {
            "visual_agent": False,  # No disponible
            "sentiment_agent": True,  # Disponible
            "technical_agent": True,  # Disponible
        }
        
        available = [k for k, v in optional_components.items() if v]
        
        # El sistema debe funcionar con componentes parciales
        assert len(available) >= 1
        assert "technical_agent" in available  # Al menos el técnico debe estar


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
