from __future__ import annotations

from .providers.base import InferenceProvider


class ProviderRegistry:
    """Simple registry for inference providers by name."""

    def __init__(self):
        self._providers: dict[str, InferenceProvider] = {}

    def register(self, provider: InferenceProvider) -> None:
        name = provider.name()
        self._providers[name] = provider
        # Offer a couple of common aliases for Ollama providers
        if name == "ollama":
            self._providers["ollama_cloud"] = provider
            self._providers["ollama_local"] = provider

    def get(self, name: str) -> InferenceProvider | None:
        return self._providers.get(name)

    def available(self) -> dict[str, InferenceProvider]:
        return dict(self._providers)


# Global singleton
registry = ProviderRegistry()


def setup_default_providers() -> None:
    """Register the default providers (HF Inference and MLX)."""
    try:
        from .providers.hf_inference import HFInferenceProvider

        registry.register(HFInferenceProvider())
    except Exception:
        # HF may not be configured; skip silently
        pass

    try:
        from .providers.mlx_provider import MLXProvider

        registry.register(MLXProvider())
    except Exception:
        # MLX may be unavailable; skip silently
        pass
    try:
        from .providers.ollama_provider import OllamaProvider

        registry.register(OllamaProvider())
    except Exception:
        # Ollama CLI/provider may be unavailable; skip silently
        pass
    try:
        from .providers.openai_provider import OpenAIProvider

        registry.register(OpenAIProvider())
    except Exception:
        # OpenAI package or config may be unavailable; skip silently
        pass

    try:
        from .providers.groq_provider import GroqProvider

        registry.register(GroqProvider())
    except Exception:
        # Groq package or config may be unavailable; skip silently
        pass
