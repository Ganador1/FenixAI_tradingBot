"""
Ollama Cloud Provider con soporte Dual-Key
Permite asignar diferentes API keys a diferentes agents
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from src.inference.providers.base import (
    GenerationParams,
    InferenceProvider,
    ProviderError,
    _metadata,
)


class OllamaCloudProvider(InferenceProvider):
    """Ollama Cloud provider con soporte para múltiples API keys."""

    def __init__(self):
        self._base_url = os.getenv("OLLAMA_CLOUD_URL", "https://api.ollama.com")

    def name(self) -> str:
        return "ollama_cloud"

    def capabilities(self) -> dict[str, bool]:
        return {
            "supports_chat": True,
            "supports_text": True,
            "supports_vision": False,
            "supports_tools": False,
        }

    def _get_api_key_for_agent(self, agent_type: str) -> str:
        """Obtiene la API key específica para un agente."""
        agent = agent_type.lower().replace("_analyst", "").replace("_manager", "")

        # Mapeo de agents a keys
        agent_key_map = {
            "qabba": "OLLAMA_CLOUD_API_KEY_QABBA",
            "technical": "OLLAMA_CLOUD_API_KEY_TECHNICAL",
            "decision": "OLLAMA_CLOUD_API_KEY_DECISION",
            "risk": "OLLAMA_CLOUD_API_KEY_RISK",
            "visual": "OLLAMA_CLOUD_API_KEY_VISUAL",
        }

        # Buscar key específica
        env_var = agent_key_map.get(agent)
        if env_var:
            key = os.getenv(env_var)
            if key:
                return key

        # Distribución optimizada:
        # Key 1: QABBA, Technical, Decision, Visual (4 agents)
        # Key 2: Risk (1 agent dedicado)
        if agent in ["qabba", "technical", "decision", "visual"]:
            key = os.getenv("OLLAMA_CLOUD_API_KEY_1")
            if key:
                return key
        elif agent in ["risk"]:
            key = os.getenv("OLLAMA_CLOUD_API_KEY_2")
            if key:
                return key

        # Fallback global
        return os.getenv("OLLAMA_CLOUD_API_KEY", "")

    def _make_request(self, endpoint: str, data: dict, api_key: str) -> dict:
        """Make HTTP request to Ollama Cloud API."""
        url = f"{self._base_url}{endpoint}"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

        req = urllib.request.Request(
            url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ProviderError(f"Ollama Cloud HTTP error: {e.code} - {e.reason}")
        except urllib.error.URLError as e:
            raise ProviderError(f"Ollama Cloud connection error: {e.reason}")
        except TimeoutError:
            raise ProviderError("Ollama Cloud timeout after 45s")

    def generate_text(self, model_id: str, prompt: str, params: GenerationParams) -> dict[str, Any]:
        """Generate text using Ollama Cloud."""
        start_ts = time.time()

        # Obtener agent del contexto
        agent_type = (
            params.extra.get("system", "").replace("Agent: ", "") if params.extra else "unknown"
        )
        api_key = self._get_api_key_for_agent(agent_type)

        if not api_key:
            raise ProviderError("No API key available for Ollama Cloud")

        data = {
            "model": model_id,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": params.temperature or 0.7,
                "num_predict": params.max_tokens or 256,
            },
        }

        try:
            result = self._make_request("/api/generate", data, api_key)
            text = result.get("response", "")
            return {"text": text, "metadata": _metadata(self.name(), model_id, start_ts)}
        except Exception as e:
            raise ProviderError(f"Ollama Cloud generation failed: {e}")

    def chat_completions(
        self, model_id: str, messages: list[dict[str, str]], params: GenerationParams
    ) -> dict[str, Any]:
        """Chat completion using Ollama Cloud."""
        start_ts = time.time()

        agent_type = (
            params.extra.get("system", "").replace("Agent: ", "") if params.extra else "unknown"
        )
        api_key = self._get_api_key_for_agent(agent_type)

        if not api_key:
            raise ProviderError("No API key available for Ollama Cloud")

        # Convert messages to prompt
        prompt = "\n".join([f"{m['role']}: {m['content']}" for m in messages])

        data = {
            "model": model_id,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": params.temperature or 0.7,
                "num_predict": params.max_tokens or 256,
            },
        }

        try:
            result = self._make_request("/api/generate", data, api_key)
            text = result.get("response", "")
            return {"text": text, "metadata": _metadata(self.name(), model_id, start_ts)}
        except Exception as e:
            raise ProviderError(f"Ollama Cloud chat failed: {e}")

    def generate_with_vision(
        self, model_id: str, prompt: str, images: list[str], params: GenerationParams
    ) -> dict[str, Any]:
        """Vision generation - not fully supported, fallback to text."""
        # Ollama Cloud no soporta vision nativamente en todos los modelos
        # Fallback a texto con descripción de imágenes
        refs = "\n".join([f"[image]: {u}" for u in images])
        full_prompt = f"{prompt}\n{refs}" if refs else prompt
        return self.generate_text(model_id, full_prompt, params)
