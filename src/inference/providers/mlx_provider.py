from __future__ import annotations

import time
from typing import Any

from src.inference.providers.base import (
    GenerationParams,
    InferenceProvider,
    ProviderError,
    _metadata,
)


class MLXProvider(InferenceProvider):
    """MLX local provider wrapping src.config.mlx_interface.MLXClient."""

    def __init__(self):
        try:
            from src.config.mlx_interface import get_client  # type: ignore
        except Exception as e:
            raise ProviderError("MLX interface not available", cause=e)
        self._get_client = get_client

    def name(self) -> str:
        return "mlx"

    def capabilities(self) -> dict[str, bool]:
        return {
            "supports_chat": True,
            "supports_text": True,
            "supports_vision": False,  # Vision path is prompt-based until a true vision model is added
            "supports_tools": False,
        }

    def generate_text(self, model_id: str, prompt: str, params: GenerationParams) -> dict[str, Any]:
        client = self._get_client()
        start_ts = time.time()
        try:
            resp = client.generate(
                model=model_id,
                prompt=prompt,
                max_tokens=params.max_tokens or 1024,
                temperature=params.temperature or 0.3,
            )
            text = getattr(resp, "content", "") or ""
            return {
                "text": text,
                "metadata": _metadata(self.name(), model_id, start_ts),
            }
        except Exception as e:
            raise ProviderError(f"MLX generate failed: {e}", cause=e)

    def chat_completions(
        self, model_id: str, messages: list[dict[str, str]], params: GenerationParams
    ) -> dict[str, Any]:
        client = self._get_client()
        start_ts = time.time()
        try:
            resp = client.chat(
                model=model_id,
                messages=messages,
                max_tokens=params.max_tokens or 1024,
                temperature=params.temperature or 0.3,
            )
            text = getattr(resp, "content", "") or ""
            return {
                "text": text,
                "metadata": _metadata(self.name(), model_id, start_ts),
            }
        except Exception as e:
            raise ProviderError(f"MLX chat failed: {e}", cause=e)

    def generate_with_vision(
        self, model_id: str, prompt: str, images: list[str], params: GenerationParams
    ) -> dict[str, Any]:
        # MLX vision: attach image references into the prompt for now
        refs = "\n".join([f"[image]: {u}" for u in images])
        combined_prompt = f"{prompt}\n{refs}" if refs else prompt
        return self.generate_text(model_id, combined_prompt, params)
