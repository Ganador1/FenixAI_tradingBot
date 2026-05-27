"""
Cliente unificado de inferencia para FenixAI Trading Bot v3.0
Maneja MLX local y HuggingFace API con Sistema Bidirectional Fallback Avanzado
"""

import logging
from typing import Any

from src.config.hybrid_model_config import ModelConfig, get_configs_for_agent
from src.inference.model_id_normalizer import normalize_model_id_for_provider
from src.inference.provider_rate_limiter import get_rate_limiter
from src.inference.provider_registry import registry, setup_default_providers
from src.inference.providers.base import GenerationParams

# Inicializar logger antes de cualquier uso
logger = logging.getLogger(__name__)

# Imports condicionales para cache
try:
    from src.cache.response_cache import get_response_cache

    CACHE_AVAILABLE = True
except ImportError:
    CACHE_AVAILABLE = False
    logger.warning("⚠️ Cache system not available")

# Import del sistema de fallback
try:
    from src.inference.bidirectional_fallback_system import (
        BidirectionalFallbackSystem,
        FallbackConfig,
        FallbackStrategy,
    )

    FALLBACK_AVAILABLE = True
except ImportError:
    FALLBACK_AVAILABLE = False


class UnifiedInferenceClient:
    """Cliente unificado para inferencia híbrida MLX + HuggingFace"""

    def __init__(self, hf_api_key: str | None = None):
        # Inicializar y registrar proveedores por defecto
        setup_default_providers()
        self._providers = registry.available()

        self.stats = {
            "mlx_requests": 0,
            "hf_requests": 0,
            "ollama_requests": 0,
            "openai_requests": 0,
            "fallbacks": 0,
            "errors": 0,
            "total_requests": 0,
            "by_agent": {},
        }

        logger.info("✅ UnifiedInferenceClient v3.0 initialized with provider registry")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def generate_for_agent(
        self,
        agent_type: str,
        prompt: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        force_local: bool = False,
        force_api: bool = False,
        image_path: str | None = None,
    ) -> str:
        """Generar respuesta usando el mejor backend para el agente"""
        self.stats["total_requests"] += 1

        if agent_type not in self.stats["by_agent"]:
            self.stats["by_agent"][agent_type] = {"requests": 0, "mlx": 0, "hf": 0, "errors": 0}

        self.stats["by_agent"][agent_type]["requests"] += 1

        configs = get_configs_for_agent(agent_type)

        if not configs:
            logger.warning("No configuration for '%s', using MLX", agent_type)
            return await self._generate_via_provider(
                provider_name="mlx",
                model_id=None,
                agent_type=agent_type,
                prompt=prompt,
                messages=None,
                images=[image_path] if image_path else None,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        if force_local:
            mlx_config = next((c for c in configs if c.provider == "mlx"), None)
            if mlx_config:
                return await self._try_generate(
                    mlx_config, agent_type, prompt, max_tokens, temperature, image_path
                )

        if force_api:
            hf_config = next((c for c in configs if c.provider == "huggingface"), None)
            if hf_config:
                return await self._try_generate(
                    hf_config, agent_type, prompt, max_tokens, temperature, image_path
                )

        last_error = None
        for i, config in enumerate(configs):
            # Saltar proveedores no disponibles en runtime (p.ej. HF sin token u Ollama sin CLI)
            if config.provider not in self._providers:
                logger.warning(
                    "Provider %s no disponible; saltando config %s",
                    config.provider,
                    config.model_id,
                )
                continue
            try:
                result = await self._try_generate(
                    config, agent_type, prompt, max_tokens, temperature, image_path
                )

                if i > 0:
                    self.stats["fallbacks"] += 1

                return result

            except Exception as e:
                logger.warning("Failed %s/%s: %s", config.provider, config.model_id, str(e)[:100])
                last_error = e
                continue

        self.stats["errors"] += 1
        self.stats["by_agent"][agent_type]["errors"] += 1
        raise RuntimeError(f"All backends failed for '{agent_type}': {last_error}")

    async def _try_generate(
        self,
        config: ModelConfig,
        agent_type: str,
        prompt: str,
        max_tokens: int | None,
        temperature: float | None,
        image_path: str | None = None,
    ) -> str:
        final_max_tokens = max_tokens or config.max_tokens
        final_temperature = temperature or config.temperature

        if config.provider == "mlx":
            logger.info("🍎 MLX: %s", config.model_id)
            self.stats["mlx_requests"] += 1
            self.stats["by_agent"][agent_type]["mlx"] += 1
            return await self._generate_via_provider(
                provider_name="mlx",
                model_id=config.model_id,
                agent_type=agent_type,
                prompt=prompt,
                messages=None,
                images=[image_path] if image_path else None,
                max_tokens=final_max_tokens,
                temperature=final_temperature,
            )

        elif config.provider == "huggingface":
            logger.info("🌐 HF: %s", config.model_id)
            self.stats["hf_requests"] += 1
            self.stats["by_agent"][agent_type]["hf"] += 1
            return await self._generate_via_provider(
                provider_name="hf_inference",
                model_id=config.model_id,
                agent_type=agent_type,
                prompt=prompt,
                messages=None,
                images=[image_path] if image_path else None,
                max_tokens=final_max_tokens,
                temperature=final_temperature,
            )

        elif config.provider in ("ollama", "ollama_cloud"):
            logger.info("☁️ Ollama: %s", config.model_id)
            self.stats["ollama_requests"] += 1
            self.stats["by_agent"][agent_type].setdefault("ollama", 0)
            self.stats["by_agent"][agent_type]["ollama"] += 1
            return await self._generate_via_provider(
                provider_name="ollama",
                model_id=config.model_id,
                agent_type=agent_type,
                prompt=prompt,
                messages=None,
                images=[image_path] if image_path else None,
                max_tokens=final_max_tokens,
                temperature=final_temperature,
            )
        elif config.provider == "openai":
            logger.info("🔓 OpenAI: %s", config.model_id)
            self.stats["openai_requests"] += 1
            self.stats["by_agent"][agent_type].setdefault("openai", 0)
            self.stats["by_agent"][agent_type]["openai"] += 1
            return self._generate_via_provider(
                provider_name="openai",
                model_id=config.model_id,
                agent_type=agent_type,
                prompt=prompt,
                messages=None,
                images=[image_path] if image_path else None,
                max_tokens=final_max_tokens,
                temperature=final_temperature,
            )

    async def _generate_via_provider(
        self,
        provider_name: str,
        model_id: str | None,
        agent_type: str,
        prompt: str,
        messages: list[dict[str, str]] | None,
        images: list[str] | None,
        max_tokens: int | None,
        temperature: float | None,
    ) -> str:
        provider = self._providers.get(provider_name)
        if not provider:
            raise RuntimeError(f"Provider '{provider_name}' no disponible")

        params = GenerationParams(
            max_tokens=max_tokens, temperature=temperature, extra={"system": f"Agent: {agent_type}"}
        )

        mdl = model_id
        if provider_name == "mlx" and not mdl:
            # Obtener modelo por defecto del agente si no se especificó
            from src.config.mlx_models import get_agent_model

            mdl = get_agent_model(agent_type)

        # Normalize model id for the provider if needed
        limiter = get_rate_limiter()
        try:
            # Try to acquire one request slot for the provider (non-blocking)
            has_slot = await limiter.acquire(provider_name, tokens=1, timeout=0)
            if not has_slot:
                logger.info(
                    "Rate limit exceeded for provider %s, attempting fallback", provider_name
                )
                raise RuntimeError("rate_limited")
            if mdl:
                mdl = normalize_model_id_for_provider(mdl, provider_name)
        except Exception:
            # If the normalization utility is unavailable or fails, continue with original id
            pass

        # Seleccionar modo
        try:
            if images and agent_type == "visual":
                result = provider.generate_with_vision(mdl or "", prompt, images, params)
            elif messages:
                result = provider.chat_completions(mdl or "", messages, params)
            else:
                result = provider.generate_text(mdl or "", prompt, params)

            # Update rate limiter from headers if available
            if isinstance(result, dict) and "headers" in result:
                headers = result["headers"]
                # Multi-provider header names: try several alternatives
                remaining_requests = (
                    headers.get("x-ratelimit-remaining-requests")
                    or headers.get("x-ratelimit-remaining")
                    or headers.get("ratelimit-remaining")
                    or headers.get("x-rate-limit-remaining")
                )
                if remaining_requests:
                    try:
                        rem = int(remaining_requests)
                        await limiter.update_limit(provider_name, rem)
                    except (ValueError, TypeError):
                        pass
        except Exception as e:
            # Try to detect provider rate-limiting (ProviderError) and attempt fallbacks
            try:
                from src.inference.providers.base import ProviderError
            except Exception:
                ProviderError = None

            if (
                ProviderError
                and isinstance(e, ProviderError)
                and (getattr(e, "is_rate_limit", False) or str(e) == "rate_limited")
            ):
                # Attempt fallbacks in preference order
                fallback_order = ["openai", "hf_inference", "mlx", "ollama"]
                for fallback_name in fallback_order:
                    if fallback_name == provider_name:
                        continue
                    alt_provider = self._providers.get(fallback_name)
                    if not alt_provider:
                        continue
                    try:
                        logger.info(
                            "Fallback to provider %s due to rate limit on %s",
                            fallback_name,
                            provider_name,
                        )
                        if images and agent_type == "visual":
                            result = alt_provider.generate_with_vision(
                                mdl or "", prompt, images, params
                            )
                        elif messages:
                            result = alt_provider.chat_completions(mdl or "", messages, params)
                        else:
                            result = alt_provider.generate_text(mdl or "", prompt, params)
                        # We succeeded with a fallback
                        self.stats["fallbacks"] += 1
                        break
                    except Exception as e2:
                        logger.debug("Fallback provider %s also failed: %s", fallback_name, e2)
                        continue
                else:
                    # No fallback succeeded; re-raise the original exception
                    raise
            # If error suggests model was decommissioned or invalid, try provider fallbacks too
            elif (
                ProviderError
                and isinstance(e, ProviderError)
                and (
                    "model_decommissioned" in str(e).lower()
                    or "invalid_request_error" in str(e).lower()
                    and "model" in str(e).lower()
                )
            ):
                logger.warning(
                    "Provider error indicates model invalid/decommissioned; attempting fallbacks: %s",
                    e,
                )
                fallback_order = ["openai", "hf_inference", "mlx", "ollama"]
                for fallback_name in fallback_order:
                    if fallback_name == provider_name:
                        continue
                    alt_provider = self._providers.get(fallback_name)
                    if not alt_provider:
                        continue
                    try:
                        logger.info(
                            "Fallback to provider %s due to model issue on %s",
                            fallback_name,
                            provider_name,
                        )
                        if images and agent_type == "visual":
                            result = alt_provider.generate_with_vision(
                                mdl or "", prompt, images, params
                            )
                        elif messages:
                            result = alt_provider.chat_completions(mdl or "", messages, params)
                        else:
                            result = alt_provider.generate_text(mdl or "", prompt, params)
                        self.stats["fallbacks"] += 1
                        break
                    except Exception as e2:
                        logger.debug("Fallback provider %s also failed: %s", fallback_name, e2)
                        continue
                else:
                    raise
            else:
                raise

        return str(result.get("text", ""))

    def get_stats(self) -> dict[str, Any]:
        total = self.stats["total_requests"]
        mlx_pct = (self.stats["mlx_requests"] / max(total, 1)) * 100
        hf_pct = (self.stats["hf_requests"] / max(total, 1)) * 100
        error_pct = (self.stats["errors"] / max(total, 1)) * 100

        return {
            **self.stats,
            "mlx_percentage": round(mlx_pct, 2),
            "hf_percentage": round(hf_pct, 2),
            "error_percentage": round(error_pct, 2),
            "success_rate": round(100 - error_pct, 2),
            "providers_available": list(self._providers.keys()),
        }


async def quick_generate(
    agent_type: str, prompt: str, hf_api_key: str | None = None, **kwargs
) -> str:
    """Función de conveniencia para generación rápida"""
    async with UnifiedInferenceClient(hf_api_key) as client:
        return await client.generate_for_agent(agent_type, prompt, **kwargs)
