# src/core/orchestrator/__init__.py
"""
Fenix Trading Bot Orchestrator Package.

This package contains the modularized components of the LangGraph orchestrator:
- state: Shared FenixAgentState TypedDict
- validation: Response validation rules and functions
- retry_system: Retry logic with exponential backoff
- json_parser: Robust JSON extraction from LLM responses
- bank_helper: ReasoningBank integration helpers
- llm_factory: Multi-provider LLM creation
- agents: Agent node creators (technical, sentiment, visual, qabba, decision, risk)
"""

from src.core.orchestrator.agents import (
    create_decision_agent_node,
    create_qabba_agent_node,
    create_risk_agent_node,
    create_sentiment_agent_node,
    create_technical_agent_node,
    create_visual_agent_node,
)
from src.core.orchestrator.agents.base import (
    create_error_response,
    invoke_agent_with_context,
    save_legacy_agent_log,
    store_to_reasoning_bank,
)
from src.core.orchestrator.bank_helper import (
    REASONING_BANK_AVAILABLE,
    get_agent_context_from_bank,
    store_agent_decision,
)
from src.core.orchestrator.json_parser import (
    extract_json_from_content,
)
from src.core.orchestrator.llm_factory import LLMFactory
from src.core.orchestrator.retry_system import (
    RetryStats,
    get_retry_stats,
    invoke_with_retry_and_validation,
    log_retry_stats,
    reset_retry_stats,
)
from src.core.orchestrator.state import (
    FenixAgentState,
    append_lists,
    merge_dicts,
)
from src.core.orchestrator.validation import (
    AGENT_VALIDATION_RULES,
    ResponseValidationError,
    build_validation_feedback,
    validate_agent_response,
)

__all__ = [
    # State
    "FenixAgentState",
    "merge_dicts",
    "append_lists",
    # Validation
    "AGENT_VALIDATION_RULES",
    "ResponseValidationError",
    "validate_agent_response",
    "build_validation_feedback",
    # Retry System
    "RetryStats",
    "get_retry_stats",
    "reset_retry_stats",
    "invoke_with_retry_and_validation",
    "log_retry_stats",
    # JSON Parser
    "extract_json_from_content",
    # Bank Helper
    "get_agent_context_from_bank",
    "store_agent_decision",
    "REASONING_BANK_AVAILABLE",
    # Agent Base
    "save_legacy_agent_log",
    "invoke_agent_with_context",
    "store_to_reasoning_bank",
    "create_error_response",
    # LLM Factory
    "LLMFactory",
    # Agents
    "create_technical_agent_node",
    "create_sentiment_agent_node",
    "create_visual_agent_node",
    "create_qabba_agent_node",
    "create_decision_agent_node",
    "create_risk_agent_node",
]
