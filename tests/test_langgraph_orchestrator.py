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
        assert hasattr(FenixAgentState, "__annotations__")

        annotations = FenixAgentState.__annotations__
        assert "symbol" in annotations
        assert "timeframe" in annotations
        assert "current_price" in annotations


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

    def test_compact_technical_hold_is_normalized_conservatively(self):
        from src.core.langgraph_orchestrator import (
            _normalize_compact_agent_response,
            validate_agent_response,
        )

        normalized = _normalize_compact_agent_response(
            "technical_analyst",
            {"signal": "HOLD", "confidence": 0.75, "indicator_validations": {}},
        )

        assert normalized["confidence_level"] == "HIGH"
        assert "abstained" in normalized["reasoning"]
        assert validate_agent_response("technical_analyst", normalized) == []

    def test_compact_qabba_hold_is_normalized_without_directional_invention(self):
        from src.core.langgraph_orchestrator import (
            _normalize_compact_agent_response,
            validate_agent_response,
        )

        normalized = _normalize_compact_agent_response(
            "qabba_analyst",
            {"signal": "HOLD", "qabba_scores": {"obi": 0.5}},
        )

        assert normalized["signal"] == "HOLD_QABBA"
        assert normalized["qabba_confidence"] == 0.0
        assert normalized["order_flow_bias"] == "neutral"
        assert normalized["absorption_detected"] is False
        assert validate_agent_response("qabba_analyst", normalized) == []

    def test_compact_directional_response_still_requires_reasoning(self):
        from src.core.langgraph_orchestrator import (
            _normalize_compact_agent_response,
            validate_agent_response,
        )

        normalized = _normalize_compact_agent_response(
            "technical_analyst",
            {"signal": "BUY", "confidence": 0.9},
        )

        assert "reasoning" not in normalized
        assert "Missing required field: 'reasoning'" in validate_agent_response(
            "technical_analyst", normalized
        )

    def test_compact_decision_hold_gets_fail_safe_confidence(self):
        from src.core.langgraph_orchestrator import (
            _normalize_compact_agent_response,
            validate_agent_response,
        )

        normalized = _normalize_compact_agent_response(
            "decision_agent",
            {"final_decision": "HOLD"},
        )

        assert normalized["confidence_in_decision"] == "LOW"
        assert normalized["convergence_score"] == 0.0
        assert validate_agent_response("decision_agent", normalized) == []

    def test_compact_directional_decision_stays_strict(self):
        from src.core.langgraph_orchestrator import (
            _normalize_compact_agent_response,
            validate_agent_response,
        )

        normalized = _normalize_compact_agent_response(
            "decision_agent",
            {"final_decision": "BUY"},
        )

        assert "confidence_in_decision" not in normalized
        assert "Missing required field: 'confidence_in_decision'" in validate_agent_response(
            "decision_agent", normalized
        )


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
    monkeypatch.setattr(
        mod, "invoke_with_retry_and_validation", fake_invoke_with_retry_and_validation
    )
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


@pytest.mark.asyncio
async def test_visual_node_prompt_indicators_match_professional_chart_contract(
    monkeypatch, tmp_path
):
    from src.core import langgraph_orchestrator as mod

    captured: dict[str, object] = {}

    def fake_format_prompt(agent_name: str, **kwargs):
        captured["agent_name"] = agent_name
        captured["kwargs"] = kwargs
        return [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user"},
        ]

    class FakeLLM:
        model = "gemma4:31b:cloud"

        async def ainvoke(self, messages):
            return type(
                "Response",
                (),
                {
                    "content": (
                        '{"action":"HOLD","confidence":0.4,"pattern":"Consolidation",'
                        '"trend":"neutral","analysis":"Range-bound market"}'
                    )
                },
            )()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mod, "format_prompt", fake_format_prompt)
    monkeypatch.setattr(mod, "save_legacy_agent_log", lambda *args, **kwargs: None)

    node = mod.create_visual_agent_node(llm=FakeLLM(), reasoning_bank=None)

    result = await node(
        {
            "chart_image_b64": "iVBORw0KGgo=",
            "symbol": "ETHUSDC",
            "timeframe": "15m",
            "current_price": "1613.36",
            "chart_candles_count": 80,
            "chart_indicators_summary": {
                "ema_50": {"value": 1617.3, "position": "above"},
                "vwap": {"value": 1612.2, "position": "above"},
                "pivots": {"r3": 1626.4, "pp": 1616.5, "s3": 1607.7},
                "macd": {"trend": "bearish"},
            },
            "execution_times": {},
            "errors": [],
        }
    )

    indicators = captured["kwargs"]["visible_indicators"]
    indicator_values = captured["kwargs"]["indicator_values"]
    assert captured["agent_name"] == "visual_analyst"
    assert captured["kwargs"]["timeframe"] == "15m"
    assert captured["kwargs"]["candle_count"] == 80
    assert "EMA 9/21/50" in indicators
    assert "Bollinger Bands" in indicators
    assert "SuperTrend" in indicators
    assert "VWAP" in indicators
    assert "Pivot Levels" in indicators
    assert "Volume" in indicators
    assert "SMA 50" not in indicators
    assert '"ema_50"' in indicator_values
    assert '"vwap"' in indicator_values
    assert '"pivots"' in indicator_values
    assert '"macd"' not in indicator_values
    assert result["visual_report"]["action"] == "HOLD"


class TestFenixTradingGraph:
    """Tests para el grafo de trading."""

    def test_langgraph_availability_check(self):
        """Verificar check de disponibilidad de LangGraph."""
        from src.core.langgraph_orchestrator import LANGGRAPH_AVAILABLE

        # Solo verificar que la variable existe
        assert isinstance(LANGGRAPH_AVAILABLE, bool)

    def test_graph_uses_waiting_edges_for_visual_and_decision_fan_in(self, monkeypatch):
        from src.core import langgraph_orchestrator as mod

        captured: list[object] = []

        class GraphSpy:
            def __init__(self, _state):
                self.nodes = []
                self.edges = []
                captured.append(self)

            def add_node(self, name, _node):
                self.nodes.append(name)

            def add_edge(self, start, end):
                self.edges.append((start, end))

            def compile(self):
                return self

        class FactorySpy:
            def __init__(self, _config):
                pass

            def get_llm_for_agent(self, _name):
                return object()

        monkeypatch.setattr(mod, "StateGraph", GraphSpy)
        monkeypatch.setattr(mod, "LLMFactory", FactorySpy)
        monkeypatch.setattr(mod, "create_technical_agent_node", lambda *_: object())
        monkeypatch.setattr(mod, "create_qabba_agent_node", lambda *_: object())
        monkeypatch.setattr(mod, "create_sentiment_agent_node", lambda *_: object())
        monkeypatch.setattr(mod, "create_visual_agent_node", lambda *_: object())
        monkeypatch.setattr(mod, "create_decision_agent_node", lambda *_: object())

        mod.FenixTradingGraph(
            enable_visual=True,
            enable_sentiment=True,
            enable_risk=False,
            reasoning_bank=None,
        )

        graph = captured[0]
        assert (["Technical Agent", "QABBA Agent"], "Visual Agent") in graph.edges
        assert (["Visual Agent", "Sentiment Agent"], "Decision Agent") in graph.edges
        assert ("Sentiment Agent", "Decision Agent") not in graph.edges
        assert ("Visual Agent", "Decision Agent") not in graph.edges

    @pytest.mark.skipif(
        True,  # Skip por defecto, requiere LangGraph instalado
        reason="Requiere LangGraph instalado",
    )
    def test_graph_creation(self):
        """Verificar creación del grafo."""
        from src.core.langgraph_orchestrator import get_trading_graph

        graph = get_trading_graph()
        assert graph is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
