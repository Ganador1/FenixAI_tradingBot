"""
Compatibility wrapper for a unified LLM client.

The project uses `UnifiedInferenceClient` internally; tests and some legacy code expect
`src.inference.llm.UnifiedLLMClient`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.inference.unified_inference_client import UnifiedInferenceClient


class UnifiedLLMClient:
    """
    Thin wrapper around `UnifiedInferenceClient`.

    Exposes:
    - `infer(...)` for synchronous callers (no running event loop).
    - `ainfer(...)` for async callers.
    - `available_backends` for simple status checks.
    """

    def __init__(self, hf_api_key: str | None = None):
        self._client = UnifiedInferenceClient(hf_api_key=hf_api_key)

    @property
    def available_backends(self) -> list[str]:
        # Best-effort introspection; providers are registered inside UnifiedInferenceClient.
        providers = getattr(self._client, "_providers", None)
        if isinstance(providers, dict):
            return list(providers.keys())
        return []

    async def ainfer(self, agent_type: str, prompt: str, **kwargs: Any) -> str:
        return await self._client.generate_for_agent(agent_type=agent_type, prompt=prompt, **kwargs)

    def infer(self, agent_type: str, prompt: str, **kwargs: Any) -> str:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.ainfer(agent_type=agent_type, prompt=prompt, **kwargs))
        raise RuntimeError(
            "UnifiedLLMClient.infer() cannot be called from an async context; use `await ainfer(...)`."
        )
