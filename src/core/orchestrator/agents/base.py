# src/core/orchestrator/agents/base.py
"""
Base utilities for Fenix Trading Bot agents.

Provides shared functionality used across all agent node creators.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from src.core.orchestrator.bank_helper import (
    REASONING_BANK_AVAILABLE,
    store_agent_decision,
)
from src.core.orchestrator.retry_system import invoke_with_retry_and_validation

logger = logging.getLogger(__name__)


def save_legacy_agent_log(
    agent_name: str,
    prompt: list[dict[str, str]] | list[Any],
    response_content: str,
    parsed_json: dict | None,
) -> None:
    """
    Saves detailed legacy-style logs (input/output/prompt/raw) to logs/llm_responses.
    Useful for debugging and detailed analysis.
    """
    try:
        log_dir = Path(os.getenv("FENIX_LLM_RESPONSE_LOG_DIR", "logs/llm_responses"))
        log_dir.mkdir(parents=True, exist_ok=True)

        # Include microseconds to avoid collisions when the same agent logs
        # multiple times within the same second (e.g., before/after fallback normalization).
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{agent_name}_{timestamp}.json"

        log_data = {
            "agent": agent_name,
            "timestamp": timestamp,
            "prompt": prompt,
            "raw_response": response_content[:2000] if response_content else None,
            "parsed": parsed_json,
        }

        log_path = log_dir / filename
        log_path.write_text(json.dumps(log_data, indent=2, default=str))

    except Exception as e:
        logger.debug(f"Failed to save legacy log for {agent_name}: {e}")


async def invoke_agent_with_context(
    llm: Any,
    messages: list[dict[str, str]],
    agent_type: str,
    agent_name: str,
    reasoning_bank: Any | None,
    required_keys: list[str],
    max_retries: int = 3,
) -> tuple[dict, str, int, list[str]]:
    """
    Invoke an agent with retry, validation, and reasoning bank integration.

    Args:
        llm: LLM instance
        messages: Formatted messages for the LLM
        agent_type: Type for validation (e.g., "technical_analyst")
        agent_name: Name for logging (e.g., "technical_agent")
        reasoning_bank: Optional ReasoningBank instance
        required_keys: Keys that must be present in response
        max_retries: Maximum retry attempts

    Returns:
        Tuple of (parsed_response, raw_response, attempts, errors)
    """
    # Convert to LLM format
    llm_messages = [
        {"role": "system", "content": messages[0]["content"]},
        {"role": "user", "content": messages[1]["content"]},
    ]

    # Invoke with retry and validation
    report, attempts, errors = await invoke_with_retry_and_validation(
        llm=llm,
        messages=llm_messages,
        agent_type=agent_type,
        max_retries=max_retries,
        base_delay=0.3,
        required_keys=required_keys,
    )

    # Get raw response for logging
    raw_response = report.get("raw_response", json.dumps(report))

    # Legacy logging
    save_legacy_agent_log(agent_name, llm_messages, raw_response, report)

    return report, raw_response, attempts, errors


def store_to_reasoning_bank(
    reasoning_bank: Any | None,
    agent_name: str,
    prompt: str,
    result: dict,
    raw_response: str,
    llm: Any,
    elapsed_ms: float,
) -> str | None:
    """
    Store agent decision in ReasoningBank if available.

    Returns:
        prompt_digest if stored successfully, None otherwise
    """
    if not reasoning_bank or not REASONING_BANK_AVAILABLE:
        return None

    try:
        return store_agent_decision(
            reasoning_bank=reasoning_bank,
            agent_name=agent_name,
            prompt=prompt[:500],  # Truncate for storage
            result=result,
            raw_response=raw_response,
            backend=getattr(llm, "model", "langchain"),
            latency_ms=elapsed_ms,
        )
    except Exception as e:
        logger.debug(f"{agent_name} ReasoningBank store failed: {e}")
        return None


def create_error_response(
    signal: str = "HOLD",
    error: str = "Unknown error",
    agent_name: str = "agent",
) -> dict:
    """Create a standardized error response for agents."""
    return {
        "signal": signal,
        "confidence": 0.0,
        "rationale": f"Error in {agent_name}: {error}",
        "error": error,
        "_validation_failed": True,
    }
