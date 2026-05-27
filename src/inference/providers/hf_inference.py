from __future__ import annotations

import asyncio
import os
import logging
logger = logging.getLogger(__name__)
import time
from typing import Any

from src.inference.providers.base import (
    GenerationParams,
    InferenceProvider,
    ProviderError,
    _metadata,
)


class HFInferenceProvider(InferenceProvider):
    """Hugging Face Inference API provider (cloud).

    Uses huggingface_hub.InferenceClient. Requires HUGGINGFACE_API_KEY/HF_TOKEN.
    Integrates with the IntelligentRateLimiter if available.
    """

    def __init__(self, api_key: str | None = None):
        # Leer múltiples variables de entorno comunes para HF
        self._api_key = (
            api_key
            or os.getenv("HUGGINGFACE_API_KEY")
            or os.getenv("HF_TOKEN")
            or os.getenv("HUGGINGFACEHUB_API_TOKEN")
        )
        self._client = None
        self._init_client()

    def _init_client(self) -> None:
        # Ensure huggingface hub cache dir points to Ganador disk when not specified
        if not (os.getenv("HUGGINGFACE_HUB_CACHE") or os.getenv("HF_HOME")):
            suggested = os.path.join(os.path.abspath(os.getcwd()), "cache", "hf")
            os.environ["HUGGINGFACE_HUB_CACHE"] = suggested
            os.environ["HF_HOME"] = suggested
            logger.info(f"⚠️ HUGGINGFACE_HUB_CACHE unset, defaulting to: {suggested}")
        try:
            from huggingface_hub import InferenceClient  # type: ignore
        except Exception as e:
            raise ProviderError("huggingface_hub not available", cause=e)

        if not self._api_key:
            raise ProviderError(
                "HuggingFace API token not set (HUGGINGFACE_API_KEY/HF_TOKEN/HUGGINGFACEHUB_API_TOKEN)"
            )

        self._client = InferenceClient(token=self._api_key)

    def name(self) -> str:
        return "hf_inference"

    def capabilities(self) -> dict[str, bool]:
        return {
            "supports_chat": True,
            "supports_text": True,
            "supports_vision": True,  # Limited; depends on model
            "supports_tools": False,
        }

    def _acquire_rate_limit(self, model_id: str, priority: int) -> bool:
        try:
            from src.utils.rate_limiter import get_rate_limiter  # type: ignore

            limiter = get_rate_limiter()
            coro = limiter.acquire_for_model(model_id, priority=priority)
            # If we're already inside an event loop, avoid blocking; allow request
            try:
                asyncio.get_running_loop()
                return True
            except RuntimeError:
                return asyncio.run(coro)
        except Exception:
            # If limiter not available or any issue, allow request
            return True

    def _record_success(self, model_id: str) -> None:
        try:
            from src.utils.rate_limiter import get_rate_limiter  # type: ignore

            limiter = get_rate_limiter()
            limiter.record_success(model_id)
        except Exception:
            pass

    def _record_failure(self, model_id: str, is_rate_limit: bool = False) -> None:
        try:
            from src.utils.rate_limiter import get_rate_limiter  # type: ignore

            limiter = get_rate_limiter()
            limiter.record_failure(model_id, is_rate_limit=is_rate_limit)
        except Exception:
            pass

    def _is_classification_model(self, model_id: str) -> bool:
        """Detecta modelos de clasificación (no text-generation).
        Heurística simple basada en ids conocidos y palabras clave.
        """
        known = {
            "yiyanghkust/finbert-tone",
            "ProsusAI/finbert",
        }
        if model_id in known:
            return True
        keywords = ["bert", "finbert", "tone", "sentiment", "classification"]
        mid = model_id.lower()
        return any(k in mid for k in keywords)

    def generate_text(self, model_id: str, prompt: str, params: GenerationParams) -> dict[str, Any]:
        if self._client is None:
            self._init_client()

        if not self._acquire_rate_limit(model_id, params.priority):
            self._record_failure(model_id, is_rate_limit=True)
            raise ProviderError("Rate limited", is_rate_limit=True)

        start_ts = time.time()
        try:
            # Ruta especial: clasificación de texto (p. ej. FinBERT)
            if self._is_classification_model(model_id):
                # Firma correcta: text_classification(text, *, model=...)
                result = self._client.text_classification(prompt, model=model_id)
                # Normalizar salida: lista de dicts con labels/scores
                # Elegir el label con mayor score
                label = None
                score = None
                try:
                    if isinstance(result, list) and result:
                        best = max(result, key=lambda x: float(x.get("score", 0.0)))
                        label = best.get("label")
                        score = best.get("score")
                except Exception:
                    pass
                self._record_success(model_id)
                return {
                    "text": label or str(result),
                    "metadata": _metadata(
                        self.name(), model_id, start_ts, classification_score=score
                    ),
                }

            # Prefer HF text generation endpoint
            text = self._client.text_generation(
                model=model_id,
                prompt=prompt,
                max_new_tokens=params.max_tokens or 256,
                temperature=params.temperature or 0.7,
                top_p=params.top_p or 0.95,
                stream=params.stream,
            )
            self._record_success(model_id)
            return {
                "text": text or "",
                "metadata": _metadata(self.name(), model_id, start_ts),
            }
        except Exception as e:
            # Simple fallback via chat if available
            try:
                text = self._chat_fallback(model_id, prompt, params)
                self._record_success(model_id)
                return {
                    "text": text or "",
                    "metadata": _metadata(self.name(), model_id, start_ts, fallback="chat"),
                }
            except Exception:
                self._record_failure(model_id, is_rate_limit=False)
                raise ProviderError(f"HF text generation failed: {e}", cause=e)

    def _chat_api_available(self) -> bool:
        # huggingface_hub.InferenceClient exposes chat via .chat_completion
        return hasattr(self._client, "chat_completion")

    def _chat_fallback(self, model_id: str, prompt: str, params: GenerationParams) -> str:
        if not self._chat_api_available():
            # Degrade to text_generation prompt
            return self._client.text_generation(
                model=model_id,
                prompt=prompt,
                max_new_tokens=params.max_tokens or 256,
                temperature=params.temperature or 0.7,
                top_p=getattr(params, "top_p", None) or 0.95,
                stream=False,
            )

        # Try HF chat_completion
        messages = [
            {"role": "system", "content": (params.extra or {}).get("system", "")},
            {"role": "user", "content": prompt},
        ]
        try:
            resp = self._client.chat_completion(
                model=model_id,
                messages=messages,
                max_tokens=params.max_tokens or 256,
                temperature=params.temperature or 0.7,
                top_p=getattr(params, "top_p", None) or 0.95,
            )
            # Robust extraction across different return shapes
            choice = (getattr(resp, "choices", []) or [None])[0]
            if choice and getattr(choice, "message", None):
                return getattr(choice.message, "content", "") or ""
            return getattr(resp, "generated_text", "") or getattr(resp, "text", "") or ""
        except Exception:
            # Fallback to text generation
            return self._client.text_generation(
                model=model_id,
                prompt=prompt,
                max_new_tokens=params.max_tokens or 256,
                temperature=params.temperature or 0.7,
                top_p=getattr(params, "top_p", None) or 0.95,
                stream=False,
            )

    def chat_completions(
        self, model_id: str, messages: list[dict[str, str]], params: GenerationParams
    ) -> dict[str, Any]:
        if self._client is None:
            self._init_client()

        if not self._acquire_rate_limit(model_id, params.priority):
            self._record_failure(model_id, is_rate_limit=True)
            raise ProviderError("Rate limited", is_rate_limit=True)

        start_ts = time.time()
        try:
            if not self._chat_api_available():
                # Fallback: flatten messages into a prompt
                prompt = "\n".join(
                    [f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages]
                )
                text = self._client.text_generation(
                    model=model_id,
                    prompt=prompt,
                    max_new_tokens=params.max_tokens or 256,
                    temperature=params.temperature or 0.7,
                    top_p=getattr(params, "top_p", None) or 0.95,
                    stream=False,
                )
                self._record_success(model_id)
                return {
                    "text": text or "",
                    "metadata": _metadata(self.name(), model_id, start_ts, chat_fallback=True),
                }

            resp = self._client.chat_completion(
                model=model_id,
                messages=messages,
                max_tokens=params.max_tokens or 256,
                temperature=params.temperature or 0.7,
                top_p=getattr(params, "top_p", None) or 0.95,
            )
            choice = (getattr(resp, "choices", []) or [None])[0]
            text = ""
            if choice and getattr(choice, "message", None):
                text = getattr(choice.message, "content", "") or ""
            else:
                text = getattr(resp, "generated_text", "") or getattr(resp, "text", "") or ""
            self._record_success(model_id)
            return {
                "text": text,
                "metadata": _metadata(self.name(), model_id, start_ts),
            }
        except Exception as e:
            self._record_failure(model_id)
            raise ProviderError(f"HF chat failed: {e}", cause=e)

    def generate_with_vision(
        self, model_id: str, prompt: str, images: list[str], params: GenerationParams
    ) -> dict[str, Any]:
        # HF multimodal support varies; try chat completions if available
        msg_content = prompt
        if images:
            # Attach image URLs/base64 references into the user content
            refs = "\n".join([f"[image]: {u}" for u in images])
            msg_content = f"{prompt}\n{refs}"
        messages = [{"role": "user", "content": msg_content}]
        return self.chat_completions(model_id, messages, params)
