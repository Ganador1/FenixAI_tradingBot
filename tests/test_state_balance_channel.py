"""Regression test: account_balance_usdt must survive the LangGraph state.

2026-07-04: the orchestrator's local FenixAgentState TypedDict did not declare
``account_balance_usdt``, so LangGraph dropped the key when the graph ran and
every risk_manager prompt showed "USDT Balance: N/A". The LLM then assumed a
$1000 balance and approved minimum-notional sizes that were skipped by the
exchange minimum check ("Notional 19.68 < Min 20.00" on a HIGH-confidence
signal).
"""

import pytest


@pytest.mark.unit
def test_orchestrator_state_declares_account_balance():
    from src.core.langgraph_orchestrator import FenixAgentState

    assert "account_balance_usdt" in FenixAgentState.__annotations__, (
        "account_balance_usdt missing from the orchestrator's FenixAgentState — "
        "LangGraph will drop the balance and the risk manager goes sizing-blind"
    )


@pytest.mark.unit
def test_modular_state_declares_account_balance():
    from src.core.orchestrator.state import FenixAgentState

    assert "account_balance_usdt" in FenixAgentState.__annotations__


@pytest.mark.unit
def test_state_schemas_share_critical_keys():
    """Both TypedDict definitions must agree on the critical channel keys."""
    from src.core.langgraph_orchestrator import FenixAgentState as MonoState
    from src.core.orchestrator.state import FenixAgentState as ModularState

    critical = {
        "symbol",
        "timeframe",
        "indicators",
        "current_price",
        "account_balance_usdt",
        "technical_report",
        "qabba_report",
        "visual_report",
        "sentiment_report",
        "risk_assessment",
    }
    mono = set(MonoState.__annotations__)
    modular = set(ModularState.__annotations__)
    missing_mono = critical - mono
    missing_modular = critical - modular
    assert not missing_mono, f"Claves críticas ausentes en monolítico: {missing_mono}"
    assert not missing_modular, f"Claves críticas ausentes en modular: {missing_modular}"
