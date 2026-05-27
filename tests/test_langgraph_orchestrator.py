"""
Tests para el LangGraph Orchestrator.
"""
import pytest


class TestFenixAgentState:
    """Tests para el estado del agente."""

    def test_state_type_definition(self):
        """Verificar definición del tipo de estado."""
        from src.core.langgraph_orchestrator import FenixAgentState

        # FenixAgentState es un TypedDict
        assert hasattr(FenixAgentState, '__annotations__')

        annotations = FenixAgentState.__annotations__
        assert 'symbol' in annotations
        assert 'timeframe' in annotations
        assert 'current_price' in annotations


class TestHelperFunctions:
    """Tests para funciones auxiliares."""

    def test_merge_dicts(self):
        """Verificar merge de diccionarios."""
        from src.core.langgraph_orchestrator import merge_dicts

        a = {"key1": "value1"}
        b = {"key2": "value2"}
        result = merge_dicts(a, b)

        assert result == {"key1": "value1", "key2": "value2"}

    def test_merge_dicts_override(self):
        """Verificar que b sobreescribe a."""
        from src.core.langgraph_orchestrator import merge_dicts

        a = {"key": "old"}
        b = {"key": "new"}
        result = merge_dicts(a, b)

        assert result["key"] == "new"

    def test_append_lists(self):
        """Verificar concatenación de listas."""
        from src.core.langgraph_orchestrator import append_lists

        a = [1, 2]
        b = [3, 4]
        result = append_lists(a, b)

        assert result == [1, 2, 3, 4]

    def test_normalize_technical_report_adds_numeric_confidence(self):
        """Verificar normalización de confianza técnica."""
        from src.core.langgraph_orchestrator import _normalize_technical_report

        report = {"signal": "BUY", "confidence_level": "HIGH"}
        normalized = _normalize_technical_report(report)

        assert normalized["confidence"] == pytest.approx(0.85)
        assert normalized["confidence_level"] == "HIGH"


class TestReasoningBankHelpers:
    """Tests para helpers de ReasoningBank."""

    def test_get_agent_context_no_bank(self):
        """Verificar comportamiento sin ReasoningBank."""
        from src.core.langgraph_orchestrator import get_agent_context_from_bank

        result = get_agent_context_from_bank(
            reasoning_bank=None,
            agent_name="technical",
            current_prompt="Test prompt",
        )

        assert result == ""

    def test_store_agent_decision_no_bank(self):
        """Verificar almacenamiento sin ReasoningBank."""
        from src.core.langgraph_orchestrator import store_agent_decision

        result = store_agent_decision(
            reasoning_bank=None,
            agent_name="technical",
            prompt="Test",
            result={"action": "BUY"},
            raw_response="",
            backend="ollama",
            latency_ms=100.0,
        )

        assert result is None


@pytest.mark.asyncio
async def test_risk_node_uses_live_symbol_and_entry_price(monkeypatch):
    from src.core import langgraph_orchestrator as mod

    captured: dict[str, object] = {}

    def fake_format_prompt(agent_name: str, **kwargs):
        captured["agent_name"] = agent_name
        captured["kwargs"] = kwargs
        return [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user"},
        ]

    async def fake_invoke_with_retry_and_validation(**kwargs):
        return (
            {
                "verdict": "APPROVE",
                "risk_score": 3.0,
                "order_details": {
                    "approved_size": 1.0,
                    "stop_loss": 86.0,
                    "take_profit": 89.0,
                    "max_loss_usd": 0.5,
                },
            },
            1,
            [],
        )

    monkeypatch.setattr(mod, "format_prompt", fake_format_prompt)
    monkeypatch.setattr(mod, "invoke_with_retry_and_validation", fake_invoke_with_retry_and_validation)
    monkeypatch.setattr(mod, "save_legacy_agent_log", lambda *args, **kwargs: None)

    node = mod.create_risk_agent_node(llm=object(), reasoning_bank=None)
    state = {
        "symbol": "SOLUSDT",
        "current_price": 87.28,
        "account_balance_usdt": 60.25,
        "open_positions": 1,
        "daily_pnl": 0.1,
        "current_drawdown": "0.5%",
        "indicators": {"atr": 0.61},
        "final_trade_decision": {"final_decision": "BUY", "confidence_in_decision": "MEDIUM"},
    }

    result = await node(state)

    assert captured["agent_name"] == "risk_manager"
    assert captured["kwargs"]["symbol"] == "SOLUSDT"
    assert captured["kwargs"]["entry_price"] == "87.28"
    assert captured["kwargs"]["balance"] == "60.25"
    assert result["risk_assessment"]["verdict"] == "APPROVE"


class TestFenixTradingGraph:
    """Tests para el grafo de trading."""

    def test_langgraph_availability_check(self):
        """Verificar check de disponibilidad de LangGraph."""
        from src.core.langgraph_orchestrator import LANGGRAPH_AVAILABLE

        # Solo verificar que la variable existe
        assert isinstance(LANGGRAPH_AVAILABLE, bool)

    @pytest.mark.skipif(
        True,  # Skip por defecto, requiere LangGraph instalado
        reason="Requiere LangGraph instalado"
    )
    def test_graph_creation(self):
        """Verificar creación del grafo."""
        from src.core.langgraph_orchestrator import get_trading_graph

        graph = get_trading_graph()
        assert graph is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
