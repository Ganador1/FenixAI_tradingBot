from __future__ import annotations

import os
import time
from typing import Any

try:
    import openai  # type: ignore
except Exception:
    openai = None

from src.inference.providers.base import (
    GenerationParams,
    InferenceProvider,
    ProviderError,
    _metadata,
)


class OpenAIProvider(InferenceProvider):
    """OpenAI provider using the official openai python package.

    Supports text and chat completions. Vision/few-shot features depend on model.
    """

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._api_base = api_base
        if openai is None:
            raise ProviderError("openai package not installed")
        if not self._api_key:
            raise ProviderError("OPENAI_API_KEY not found in env and no api_key supplied")
        openai.api_key = self._api_key
        if self._api_base:
            openai.api_base = self._api_base

    def name(self) -> str:
        return "openai"

    def capabilities(self) -> dict[str, bool]:
        return {
            "supports_chat": True,
            "supports_text": True,
            "supports_vision": False,
            "supports_tools": True,
        }

    def generate_text(self, model_id: str, prompt: str, params: GenerationParams) -> dict[str, Any]:
        start_ts = time.time()
        try:
            model = model_id or "gpt-4o"
            resp = openai.Completion.create(
                model=model,
                prompt=prompt,
                max_tokens=params.max_tokens or 256,
                temperature=params.temperature or 0.7,
            )
            text = resp.choices[0].text if getattr(resp, "choices", None) else ""
            return {"text": text, "metadata": _metadata(self.name(), model, start_ts)}
        except openai.error.OpenAIError as e:
            raise ProviderError(f"OpenAI generation failed: {e}", cause=e)

    def chat_completions(
        self, model_id: str, messages: list[dict[str, str]], params: GenerationParams
    ) -> dict[str, Any]:
        start_ts = time.time()
        try:
            model = model_id or "gpt-4o"
            resp = openai.ChatCompletion.create(
                model=model,
                messages=messages,
                max_tokens=params.max_tokens or 256,
                temperature=params.temperature or 0.7,
            )
            text = ""
            if getattr(resp, "choices", None):
                text = (
                    resp.choices[0].message["content"]
                    if "message" in resp.choices[0]
                    else getattr(resp.choices[0], "text", "")
                )
            return {"text": text, "metadata": _metadata(self.name(), model, start_ts)}
        except openai.error.OpenAIError as e:
            raise ProviderError(f"OpenAI chat failed: {e}", cause=e)

    def generate_with_vision(
        self, model_id: str, prompt: str, images: list[str], params: GenerationParams
    ) -> dict[str, Any]:
        # Vision depends on the chosen model and OpenAI features (gpt-4o multimodal, etc.)
        # Fallback: embed images references into the prompt
        start_ts = time.time()
        try:
            refs = "\n".join([f"[image]: {u}" for u in images])
            prompt = f"{prompt}\n{refs}" if refs else prompt
            # Use chat for multimodal
            resp = openai.ChatCompletion.create(
                model=model_id,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=params.max_tokens or 256,
                temperature=params.temperature or 0.7,
            )
            text = ""
            if getattr(resp, "choices", None):
                text = (
                    resp.choices[0].message["content"]
                    if "message" in resp.choices[0]
                    else getattr(resp.choices[0], "text", "")
                )
            return {"text": text, "metadata": _metadata(self.name(), model_id, start_ts)}
        except openai.error.OpenAIError as e:
            raise ProviderError(f"OpenAI vision failed: {e}", cause=e)
