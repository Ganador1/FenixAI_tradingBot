"""
Core agent constants.

This module exists primarily for backwards-compatibility with tests and legacy imports.
The authoritative execution order is defined in the LangGraph orchestrator.
"""

AGENTS: list[str] = [
    "technical_analyst",
    "sentiment_analyst",
    "visual_analyst",
    "qabba_analyst",
    "decision_agent",
    "risk_manager",
]
