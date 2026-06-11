# src/core/orchestrator/llm_factory.py
"""
LLM Factory for Fenix Trading Bot.

Supports multiple providers: OpenAI, Anthropic, Groq, Ollama (local/cloud),
HuggingFace Inference. Includes per-agent JSON schema enforcement,
fallback chains, and NoopStub for dev/test resilience.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from config.llm_provider_config import AgentProviderConfig, LLMProvidersConfig
from src.models.outputs import (
    FinalDecisionOutput,
    QABBAAgentOutput,
    RiskManagerOutput,
    SentimentOutput,
    TechnicalAgentOutput,
    VisualAgentOutput,
)

logger = logging.getLogger(__name__)

_ROTATION_USAGE_LOG = os.getenv("FENIX_ROTATE_MODELS_LOG", "logs/llm_rotation_usage.jsonl")


def _parse_rotation_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


class RotatingLLM:
    """Rotates across a list of models per call, repeating each model N times."""

    def __init__(
        self,
        *,
        factory: LLMFactory,
        agent_type: str,
        base_config: AgentProviderConfig,
        models: list[str],
        repeat: int,
    ) -> None:
        self._factory = factory
        self._agent_type = agent_type
        self._base_config = base_config
        self._models = models
        self._repeat = max(1, repeat)
        self._index = 0
        self._repeat_count = 0
        self._lock = asyncio.Lock()
        self.model = models[0] if models else base_config.model_name

    async def _next_model(self) -> str:
        async with self._lock:
            model = self._models[self._index]
            self._repeat_count += 1
            if self._repeat_count >= self._repeat:
                self._repeat_count = 0
                self._index = (self._index + 1) % len(self._models)
        self.model = model
        return model

    def _log_rotation(self, model: str) -> None:
        try:
            import json
            from datetime import datetime, timezone

            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "agent_type": self._agent_type,
                "model": model,
            }
            with open(_ROTATION_USAGE_LOG, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            # Never fail the call on logging.
            pass

    def _build_llm(self, model: str) -> Any:
        # Clone base config with the selected model.
        try:
            cfg = self._base_config.model_copy(update={"model_name": model})
        except Exception:
            cfg = AgentProviderConfig(**{**self._base_config.model_dump(), "model_name": model})
        return self._factory._create_llm(cfg, self._agent_type)

    async def ainvoke(self, messages: list) -> Any:
        model = await self._next_model()
        self._log_rotation(model)
        llm = self._build_llm(model)
        self.model = model
        return await llm.ainvoke(messages)

    def invoke(self, messages: list) -> Any:
        # Sync fallback
        model = self._models[self._index]
        self._repeat_count = (self._repeat_count + 1) % self._repeat
        if self._repeat_count == 0:
            self._index = (self._index + 1) % len(self._models)
        self.model = model
        self._log_rotation(model)
        llm = self._build_llm(model)
        return llm.invoke(messages)


class _MLXChatAdapter:
    """Small async-compatible adapter to use local MLX models as chat LLMs."""

    def __init__(
        self,
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> None:
        from src.config.mlx_interface import get_client  # lazy import

        self.model = model
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.timeout = int(timeout)
        self._client = get_client()

    @staticmethod
    def _normalize_role(raw_role: str) -> str:
        role = (raw_role or "").strip().lower()
        mapping = {
            "human": "user",
            "ai": "assistant",
            "system": "system",
            "assistant": "assistant",
            "user": "user",
        }
        return mapping.get(role, "user")

    @staticmethod
    def _normalize_content(raw_content: Any) -> str:
        if raw_content is None:
            return ""
        if isinstance(raw_content, str):
            return raw_content
        if isinstance(raw_content, list):
            chunks: list[str] = []
            for part in raw_content:
                if isinstance(part, dict):
                    text = str(part.get("text", "")).strip()
                    if text:
                        chunks.append(text)
                else:
                    text = str(part).strip()
                    if text:
                        chunks.append(text)
            return "\n".join(chunks).strip()
        return str(raw_content)

    def _to_mlx_messages(self, messages: list[Any]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for message in messages or []:
            if isinstance(message, dict):
                role = self._normalize_role(str(message.get("role", "user")))
                content = self._normalize_content(message.get("content", ""))
            else:
                role = self._normalize_role(
                    str(getattr(message, "role", None) or getattr(message, "type", None) or "user")
                )
                content = self._normalize_content(getattr(message, "content", ""))
            normalized.append({"role": role, "content": content})

        if not normalized:
            normalized = [{"role": "user", "content": ""}]
        return normalized

    @staticmethod
    def _as_response_message(content: str, model: str, elapsed_ms: float) -> Any:
        response_metadata = {
            "provider": "huggingface_mlx",
            "model_name": model,
            "elapsed_ms": elapsed_ms,
        }
        try:
            from langchain_core.messages import AIMessage

            return AIMessage(content=content, response_metadata=response_metadata)
        except Exception:
            return type(
                "MLXAIResponse",
                (),
                {
                    "content": content,
                    "response_metadata": response_metadata,
                },
            )()

    def invoke(self, messages: list[Any]) -> Any:
        payload = self._to_mlx_messages(messages)
        start = time.perf_counter()
        response = self._client.chat(
            model=self.model,
            messages=payload,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        content = str(getattr(response, "content", "") or "")
        return self._as_response_message(content, self.model, elapsed_ms)

    async def ainvoke(self, messages: list[Any]) -> Any:
        return await asyncio.to_thread(self.invoke, messages)


class LLMFactory:
    """LLM Factory supporting multiple providers."""

    def __init__(self, config: LLMProvidersConfig | None = None):
        # If no explicit config is passed, attempt to use the LLMProviderLoader
        if config is None:
            try:
                from src.config.llm_provider_loader import get_provider_loader

                loader = get_provider_loader()
                config = loader.get_config() or LLMProvidersConfig()
                logger.info(
                    f"LLMFactory: using config from provider loader (profile={loader.active_profile})"
                )
            except Exception as e:
                logger.warning(
                    f"LLMFactory: could not initialize loader config, falling back to default. Error: {e}"
                )

        self.config = config or LLMProvidersConfig()
        self._llm_cache: dict[str, Any] = {}

    def get_llm_for_agent(self, agent_type: str) -> Any:
        """Gets the configured LLM for an agent type."""
        if agent_type in self._llm_cache:
            return self._llm_cache[agent_type]

        agent_config = self.config.get_agent_config(agent_type)
        rotation_disabled = bool(agent_config.extra_config.get("disable_rotation", False))
        rotation_models = (
            []
            if rotation_disabled
            else _parse_rotation_list(os.getenv(f"FENIX_ROTATE_MODELS_{agent_type.upper()}"))
        )
        rotation_repeat = int(os.getenv("FENIX_ROTATE_MODELS_REPEAT", "1") or "1")
        if rotation_models and agent_config.provider_type in ("ollama_local", "ollama_cloud"):
            logger.info(
                "🏭 LLMFactory: Using rotation for %s (models=%s, repeat=%s)",
                agent_type,
                rotation_models,
                rotation_repeat,
            )
            llm = RotatingLLM(
                factory=self,
                agent_type=agent_type,
                base_config=agent_config,
                models=rotation_models,
                repeat=rotation_repeat,
            )
            self._llm_cache[agent_type] = llm
            return llm
        elif rotation_models:
            logger.warning(
                "LLM rotation requested for %s but provider_type=%s is not supported. Ignoring rotation.",
                agent_type,
                agent_config.provider_type,
            )
        elif rotation_disabled and os.getenv(f"FENIX_ROTATE_MODELS_{agent_type.upper()}"):
            logger.info("🏭 LLMFactory: Rotation disabled for %s via extra_config", agent_type)

        logger.info(
            f"🏭 LLMFactory: Creating LLM for {agent_type} "
            f"with provider={agent_config.provider_type}, model={agent_config.model_name}, "
            f"timeout={agent_config.timeout}"
        )
        llm = self._create_llm(agent_config, agent_type)
        self._llm_cache[agent_type] = llm
        return llm

    def _get_json_schema_for_agent(self, agent_type: str | None) -> dict | None:
        """Get the JSON schema for a specific agent type."""
        if agent_type is None:
            return None

        schema_map = {
            "technical": TechnicalAgentOutput,
            "visual": VisualAgentOutput,
            "qabba": QABBAAgentOutput,
            "sentiment": SentimentOutput,
            "decision": FinalDecisionOutput,
            "risk_manager": RiskManagerOutput,
        }

        model_class = schema_map.get(agent_type)
        if model_class:
            return model_class.model_json_schema()

        return None

    def _effective_max_tokens(self, config: AgentProviderConfig, agent_type: str | None) -> int:
        max_tokens = max(1, int(config.max_tokens))
        if agent_type is None:
            return max_tokens

        env_slug_map = {
            "technical": "TECHNICAL",
            "visual": "VISUAL",
            "qabba": "QABBA",
            "sentiment": "SENTIMENT",
            "decision": "DECISION",
            "risk_manager": "RISK_MANAGER",
        }
        env_slug = env_slug_map.get(agent_type)
        if not env_slug:
            return max_tokens

        raw_override = os.getenv(f"FENIX_{env_slug}_MAX_TOKENS")
        if raw_override is None:
            return max_tokens

        try:
            return max(1, int(raw_override))
        except Exception:
            logger.warning(
                "Invalid token override for %s: %r",
                f"FENIX_{env_slug}_MAX_TOKENS",
                raw_override,
            )
            return max_tokens

    def _create_llm(self, config: AgentProviderConfig, agent_type: str | None = None) -> Any:
        """Creates an LLM instance based on configuration."""
        provider = config.provider_type
        model = config.model_name
        temperature = config.temperature
        max_tokens = self._effective_max_tokens(config, agent_type)
        api_key = config.api_key.get_secret_value() if config.api_key else None
        api_base = config.api_base

        try:
            if provider == "openai":
                from langchain_openai import ChatOpenAI

                return ChatOpenAI(
                    model=model,
                    temperature=temperature,
                    api_key=api_key,
                    max_tokens=max_tokens,
                    timeout=config.timeout,
                )

            elif provider == "anthropic":
                from langchain_anthropic import ChatAnthropic

                return ChatAnthropic(
                    model=model,
                    temperature=temperature,
                    api_key=api_key,
                    max_tokens=max_tokens,
                )

            elif provider == "groq":
                from langchain_groq import ChatGroq

                return ChatGroq(
                    model=model,
                    temperature=temperature,
                    api_key=api_key,
                    max_tokens=max_tokens,
                )

            elif provider in ("ollama_local", "ollama_cloud"):
                from langchain_ollama import ChatOllama

                json_schema = self._get_json_schema_for_agent(agent_type)

                ollama_kwargs = {
                    "model": model,
                    "temperature": temperature,
                    "base_url": api_base or "http://localhost:11434",
                    "num_predict": max_tokens,
                    "timeout": float(config.timeout) if config.timeout else None,
                    "request_timeout": float(config.timeout) if config.timeout else None,
                }

                # Reasoning control for Ollama:
                # In langchain_ollama the supported flag is `reasoning` (not `think`).
                # Keep backward compatibility with existing `extra_config["think"]`.
                reasoning_override = None
                if config.extra_config:
                    if "reasoning" in config.extra_config:
                        reasoning_override = config.extra_config.get("reasoning")
                    elif "think" in config.extra_config:
                        reasoning_override = config.extra_config.get("think")

                model_name_lc = str(model or "").lower()
                strict_json_output = bool(json_schema)

                if reasoning_override is not None:
                    ollama_kwargs["reasoning"] = reasoning_override
                elif agent_type == "risk_manager":
                    # Risk agent must prioritize strict JSON over chain-of-thought.
                    # For Qwen3.5 local, leaving reasoning enabled can exhaust num_predict
                    # and return empty `content`, breaking JSON parsing.
                    ollama_kwargs["reasoning"] = False
                elif agent_type == "qabba" and strict_json_output and "deepseek" in model_name_lc:
                    # DeepSeek V3.x on Ollama Cloud can consume the whole generation budget in
                    # `reasoning_content` and return empty `content` when strict JSON is required.
                    # QABBA needs the final JSON more than hidden chain-of-thought.
                    ollama_kwargs["reasoning"] = False

                if json_schema:
                    ollama_kwargs["format"] = json_schema
                    logger.info(
                        f"🏭 LLMFactory: Using JSON schema for {agent_type} to prevent thinking blocks"
                    )
                else:
                    ollama_kwargs["format"] = "json"
                    logger.info(f"🏭 LLMFactory: Using simple JSON format for {agent_type}")

                if provider == "ollama_cloud":
                    cloud_key = self._resolve_ollama_cloud_api_key(
                        agent_type=agent_type,
                        explicit_api_key=api_key,
                    )
                    if cloud_key:
                        auth_header = {"Authorization": f"Bearer {cloud_key}"}
                        sync_client_kwargs = dict(ollama_kwargs.get("sync_client_kwargs") or {})
                        sync_headers = dict(sync_client_kwargs.get("headers") or {})
                        sync_headers.update(auth_header)
                        sync_client_kwargs["headers"] = sync_headers
                        ollama_kwargs["sync_client_kwargs"] = sync_client_kwargs

                        async_client_kwargs = dict(ollama_kwargs.get("async_client_kwargs") or {})
                        async_headers = dict(async_client_kwargs.get("headers") or {})
                        async_headers.update(auth_header)
                        async_client_kwargs["headers"] = async_headers
                        ollama_kwargs["async_client_kwargs"] = async_client_kwargs

                        # Some langchain/ollama versions read only client_kwargs.
                        client_kwargs = dict(ollama_kwargs.get("client_kwargs") or {})
                        client_headers = dict(client_kwargs.get("headers") or {})
                        client_headers.update(auth_header)
                        client_kwargs["headers"] = client_headers
                        ollama_kwargs["client_kwargs"] = client_kwargs

                return ChatOllama(**ollama_kwargs)

            elif provider == "huggingface_inference":
                from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint

                endpoint = HuggingFaceEndpoint(
                    repo_id=model,
                    huggingfacehub_api_token=api_key,
                    max_new_tokens=max_tokens,
                    temperature=temperature,
                )
                return ChatHuggingFace(llm=endpoint)

            elif provider == "huggingface_mlx":
                return _MLXChatAdapter(
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=config.timeout,
                )

            else:
                logger.warning(f"Provider {provider} not supported, using local Ollama")
                from langchain_ollama import ChatOllama

                return ChatOllama(model="qwen2.5:7b", temperature=0.1)

        except ImportError as e:
            logger.warning("Provider import failed: %s - attempting fallback if configured", str(e))
            if config.fallback_provider_type and config.fallback_model_name:
                fallback_config = AgentProviderConfig(
                    provider_type=config.fallback_provider_type,
                    model_name=config.fallback_model_name,
                    temperature=config.temperature,
                )
                logger.info(
                    "Attempting fallback from ImportError: %s/%s",
                    config.fallback_provider_type,
                    config.fallback_model_name,
                )
                return self._create_llm(fallback_config)
            raise
        except Exception as e:
            logger.error(f"Error creando LLM para {provider}/{model}: {e}")
            if config.fallback_provider_type and config.fallback_model_name:
                fallback_config = AgentProviderConfig(
                    provider_type=config.fallback_provider_type,
                    model_name=config.fallback_model_name,
                    temperature=config.temperature,
                )
                logger.info(
                    f"Attempting fallback: {config.fallback_provider_type}/{config.fallback_model_name}"
                )
                return self._create_llm(fallback_config)
            allow_stub = os.getenv("LLM_ALLOW_NOOP_STUB", "0") == "1"
            if allow_stub:
                logger.warning("Returning NoopStub LLM to allow graph initialization in dev/test")

                class NoopStub:
                    def __init__(self, name="noop"):
                        self.name = name

                    def invoke(self, messages):
                        return type(
                            "R",
                            (),
                            {
                                "content": '{"action": "HOLD", "confidence": 0.0, "reason": "LLM unavailable (stub)"}'
                            },
                        )

                    def generate(self, prompt, **kwargs):
                        return type(
                            "R",
                            (),
                            {
                                "success": True,
                                "content": '{"action": "HOLD", "confidence": 0.0, "reason": "LLM unavailable (stub)"}',
                                "model": "noop",
                                "provider": "noop",
                                "latency_ms": 0,
                            },
                        )

                return NoopStub(name=f"noop_{provider}")
            raise

    def _resolve_ollama_cloud_api_key(
        self,
        *,
        agent_type: str | None,
        explicit_api_key: str | None,
    ) -> str | None:
        """Resolve Ollama Cloud key with per-agent routing support.

        Priority:
        1) explicit key in config
        2) per-agent dedicated env key (e.g. OLLAMA_CLOUD_API_KEY_RISK)
        3) canonical selected cloud key / generic Ollama key
        4) dual-key pool split (Key1 for most agents, Key2 for risk)
        """
        if explicit_api_key:
            return explicit_api_key

        normalized = (agent_type or "").strip().lower()
        dedicated_env_by_agent = {
            "technical": "OLLAMA_CLOUD_API_KEY_TECHNICAL",
            "qabba": "OLLAMA_CLOUD_API_KEY_QABBA",
            "decision": "OLLAMA_CLOUD_API_KEY_DECISION",
            "sentiment": "OLLAMA_CLOUD_API_KEY_SENTIMENT",
            "visual": "OLLAMA_CLOUD_API_KEY_VISUAL",
            "risk_manager": "OLLAMA_CLOUD_API_KEY_RISK",
        }

        dedicated_env = dedicated_env_by_agent.get(normalized)
        if dedicated_env:
            dedicated_key = os.getenv(dedicated_env)
            if dedicated_key:
                return dedicated_key

        canonical_key = os.getenv("OLLAMA_CLOUD_API_KEY") or os.getenv("OLLAMA_API_KEY")
        if canonical_key:
            return canonical_key

        key1 = os.getenv("OLLAMA_CLOUD_API_KEY_1")
        key2 = os.getenv("OLLAMA_CLOUD_API_KEY_2")
        if key1 or key2:
            if normalized == "risk_manager":
                return key2 or key1
            return key1 or key2

        return None
