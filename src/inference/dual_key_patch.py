"""Agent-specific Ollama Cloud key selection without exposing secret material."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


_AGENT_KEY_ENV_NAMES = {
    "qabba": ("OLLAMA_CLOUD_API_KEY_QABBA", "OLLAMA_CLOUD_API_KEY_1"),
    "technical": ("OLLAMA_CLOUD_API_KEY_TECHNICAL", "OLLAMA_CLOUD_API_KEY_1"),
    "decision": ("OLLAMA_CLOUD_API_KEY_DECISION", "OLLAMA_CLOUD_API_KEY_2"),
    "risk": ("OLLAMA_CLOUD_API_KEY_RISK", "OLLAMA_CLOUD_API_KEY_2"),
}


def _first_configured(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def get_api_key_for_agent(agent_type: str) -> str:
    """Return the agent-specific key, then global fallbacks, without logging it."""
    agent_key = agent_type.lower().replace("_analyst", "").replace("_manager", "")
    key = _first_configured(_AGENT_KEY_ENV_NAMES.get(agent_key, ()))
    if key:
        logger.debug("Using an agent-specific Ollama Cloud key for %s", agent_type)
        return key

    global_key = _first_configured(("OLLAMA_CLOUD_API_KEY", "OLLAMA_CLOUD_API_KEY_2"))
    if global_key:
        logger.debug("Using the global Ollama Cloud key for %s", agent_type)
        return global_key

    return os.getenv("OLLAMA_API_KEY", "").strip()


def is_dual_key_enabled() -> bool:
    """Return whether both shared cloud keys are configured."""
    return bool(os.getenv("OLLAMA_CLOUD_API_KEY_1") and os.getenv("OLLAMA_CLOUD_API_KEY_2"))


def get_key_distribution() -> dict:
    """Return key-slot labels for diagnostics without returning any key value."""
    return {
        "qabba": "Key 1" if _first_configured(_AGENT_KEY_ENV_NAMES["qabba"]) else "Global",
        "technical": (
            "Key 1" if _first_configured(_AGENT_KEY_ENV_NAMES["technical"]) else "Global"
        ),
        "decision": (
            "Key 2" if _first_configured(_AGENT_KEY_ENV_NAMES["decision"]) else "Global"
        ),
        "risk": "Key 2" if _first_configured(_AGENT_KEY_ENV_NAMES["risk"]) else "Global",
    }


if is_dual_key_enabled():
    logger.info("Dual-key Ollama Cloud mode enabled: %s", get_key_distribution())
else:
    logger.debug("Single-key mode (OLLAMA_CLOUD_API_KEY)")
