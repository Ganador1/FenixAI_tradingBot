from __future__ import annotations

"""
Configuration helpers for the ReasoningBank LLM-as-a-Judge component.

The defaults prioritize Ollama Cloud to avoid external providers unless configured. You can
override every field via environment variables:

- FENIX_JUDGE_PROVIDER  (default: "ollama")
- FENIX_JUDGE_MODEL     (default: "deepseek-r1:8b")
- FENIX_JUDGE_TEMPERATURE (default: 0.1)
- FENIX_JUDGE_MAX_TOKENS  (default: 512)
- FENIX_JUDGE_SYSTEM_PROMPT (optional custom instructions)
"""

import os
from dataclasses import dataclass


@dataclass
class JudgeModelConfig:
    """Small container describing which provider/model the judge should use."""

    provider: str
    model_id: str
    temperature: float = 0.1
    max_tokens: int = 512
    system_prompt: str = (
        "You are ReasoningBank-Judge, a strict auditor that labels agent "
        "reasoning traces as APPROVE/REJECT/INCONCLUSIVE and highlights risks."
    )


def get_judge_model_config() -> JudgeModelConfig:
    """Read judge configuration from environment variables."""

    provider = os.getenv("FENIX_JUDGE_PROVIDER", "ollama").strip() or "ollama"
    model_id = os.getenv("FENIX_JUDGE_MODEL", "nemotron-3-nano:30b-cloud").strip() or "nemotron-3-nano:30b-cloud"
    temperature = float(os.getenv("FENIX_JUDGE_TEMPERATURE", "0.1"))
    max_tokens = int(os.getenv("FENIX_JUDGE_MAX_TOKENS", "512"))
    system_prompt = os.getenv("FENIX_JUDGE_SYSTEM_PROMPT")

    return JudgeModelConfig(
        provider=provider,
        model_id=model_id,
        temperature=temperature,
        max_tokens=max_tokens,
        system_prompt=system_prompt.strip() if system_prompt else JudgeModelConfig.system_prompt,
    )
