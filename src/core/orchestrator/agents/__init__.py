# src/core/orchestrator/agents/__init__.py
"""
Agent node creators for Fenix Trading Bot.

Each agent is a specialized LLM that analyzes market data from a specific perspective.
"""

from src.core.orchestrator.agents.decision import create_decision_agent_node
from src.core.orchestrator.agents.monolithic import create_monolithic_agent_node
from src.core.orchestrator.agents.qabba import create_qabba_agent_node
from src.core.orchestrator.agents.risk import create_risk_agent_node
from src.core.orchestrator.agents.sentiment import create_sentiment_agent_node
from src.core.orchestrator.agents.technical import create_technical_agent_node
from src.core.orchestrator.agents.visual import create_visual_agent_node
from src.core.orchestrator.agents.web3_intel import create_web3_intel_agent_node

__all__ = [
    "create_technical_agent_node",
    "create_sentiment_agent_node",
    "create_visual_agent_node",
    "create_qabba_agent_node",
    "create_decision_agent_node",
    "create_risk_agent_node",
    "create_monolithic_agent_node",
    "create_web3_intel_agent_node",
]
