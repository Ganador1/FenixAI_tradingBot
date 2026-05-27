"""
HuggingFace Inference API Client v2 for Fenix Trading Bot
Uses the new huggingface_hub.InferenceClient (2025 API)
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from huggingface_hub import InferenceClient as HFInferenceClient

logger = logging.getLogger(__name__)


@dataclass
class HFModelInfo:
    """Información de modelo HuggingFace"""

    model_id: str
    status: str = "unknown"  # unknown, loading, ready, error
    estimated_time: float = 0.0
    last_used: float = 0.0


class HuggingFaceInferenceClient:
    """
    Cliente asíncrono para HuggingFace Inference API (2025 version)

    Features:
    - Usa huggingface_hub.InferenceClient (API actualizada)
    - Chat completions con formato OpenAI-compatible
    - Rate limiting automático
    - Manejo de errores y retry logic
    - Estadísticas de uso
    """

    # Rate limiting para tier gratuito
    MAX_REQUESTS_PER_MINUTE = 25  # Conservador

    def __init__(self, api_key: str | None = None):
        """
        Inicializar cliente HuggingFace

        Args:
            api_key: Token de HuggingFace (opcional, usa env var si no se provee)
        """
        self.api_key = api_key or os.getenv("HUGGINGFACE_API_KEY") or os.getenv("HF_TOKEN")

        # Cliente base (puede recrearse por modelo con provider específico)
        self.client = HFInferenceClient(api_key=self.api_key)

        # Rate limiting
        self.rate_limiter = {"requests": [], "max_per_minute": self.MAX_REQUESTS_PER_MINUTE}

        # Tracking de modelos
        self.model_cache: dict[str, HFModelInfo] = {}

        # Estadísticas
        self.stats = {
            "requests": 0,
            "errors": 0,
            "rate_limited": 0,
            "model_loading_waits": 0,
            "avg_latency_ms": 0.0,
            "total_latency_ms": 0.0,
        }

        if not self.api_key:
            logger.warning("⚠️ HuggingFace API key not found. Using anonymous mode (limited)")

    async def __aenter__(self):
        """Context manager: inicializar"""
        logger.info("✅ HuggingFace client (v2) initialized")
        return self

    async def __aexit__(self, *args):
        """Context manager: cleanup"""
        logger.info("🔒 HuggingFace client (v2) closed")

    async def _check_rate_limit(self):
        """Verificar y aplicar rate limiting"""
        now = time.time()

        # Limpiar requests antiguos (>60s)
        self.rate_limiter["requests"] = [t for t in self.rate_limiter["requests"] if now - t < 60]

        # Si alcanzamos límite, esperar
        if len(self.rate_limiter["requests"]) >= self.rate_limiter["max_per_minute"]:
            oldest_request = self.rate_limiter["requests"][0]
            wait_time = 60 - (now - oldest_request)

            if wait_time > 0:
                logger.warning(f"⏳ Rate limit alcanzado, esperando {wait_time:.1f}s")
                self.stats["rate_limited"] += 1
                await asyncio.sleep(wait_time)

        # Registrar request
        self.rate_limiter["requests"].append(now)

    async def generate(
        self,
        model_id: str,
        prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.1,
        top_p: float = 0.9,
        timeout: int = 45,
        retry_on_loading: bool = True,
        provider_override: str | None = None,
    ) -> str:
        """
        Generar texto usando HuggingFace Inference API (v2 con chat completions)

        Args:
            model_id: ID del modelo (e.g., 'meta-llama/Llama-3.2-1B-Instruct')
            prompt: Prompt para el modelo
            max_tokens: Máximo tokens a generar
            temperature: Temperature para sampling
            top_p: Nucleus sampling parameter
            timeout: Timeout en segundos (no usado en v2, pero mantenido por compatibilidad)
            retry_on_loading: Si True, espera si modelo está cargándose

        Returns:
            Texto generado

        Raises:
            Exception: Si falla la generación
        """
        max_attempts = 3
        for attempt in range(max_attempts):
            # Rate limiting
            await self._check_rate_limit()

            start_time = time.time()

            # Seleccionar provider por modelo
            provider = provider_override or (
                "novita" if "moonshotai/" in model_id.lower() else "hf-inference"
            )
            local_client = HFInferenceClient(api_key=self.api_key, provider=provider)
            try:
                response = await asyncio.to_thread(
                    local_client.chat.completions.create,
                    model=model_id,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                )

                latency_ms = (time.time() - start_time) * 1000

                # Extraer texto generado
                msg = response.choices[0].message
                text = getattr(msg, "content", "") or ""
                if not text:
                    text = getattr(msg, "reasoning_content", "") or ""

                # Actualizar estadísticas
                self._update_stats(latency_ms, success=True)

                # Update model cache
                self.model_cache[model_id] = HFModelInfo(
                    model_id=model_id, status="ready", last_used=time.time()
                )

                logger.info(f"✅ HF API v2: {model_id} → {len(text)} chars en {latency_ms:.0f}ms")
                return text

            except StopIteration:
                try:
                    text = await asyncio.to_thread(
                        local_client.text_generation,
                        model=model_id,
                        prompt=prompt,
                        max_new_tokens=max_tokens,
                        temperature=temperature,
                        top_p=top_p,
                    )
                    latency_ms = (time.time() - start_time) * 1000
                    self._update_stats(latency_ms, success=True)
                    logger.info(
                        f"✅ HF text_generation fallback: {model_id} → {len(text)} chars en {latency_ms:.0f}ms"
                    )
                    return text
                except Exception:
                    logger.error(f"❌ Modelo {model_id} no soporta chat y text_generation falló")
                    self._update_stats(0, success=False)
                    raise Exception(f"Model {model_id} not available or streaming error")

            except Exception as e:
                error_msg = str(e)
                latency_ms = (time.time() - start_time) * 1000

                # Detectar si es modelo cargándose
                if "not ready" in error_msg.lower() or "warm" in error_msg.lower():
                    if retry_on_loading and attempt == 0:
                        logger.warning(f"⏳ Modelo {model_id} cargándose. Esperando 20s...")
                        self.stats["model_loading_waits"] += 1
                        await asyncio.sleep(20)
                        # Reintentar con retry_on_loading desactivado
                        retry_on_loading = False
                        continue
                    else:
                        logger.error(f"❌ Modelo {model_id} no está listo")
                        self._update_stats(latency_ms, success=False)
                        raise Exception(f"Model {model_id} not ready")

                # Rate limit 429
                if (
                    "429" in error_msg
                    or "too many requests" in error_msg.lower()
                    or "rate limit" in error_msg.lower()
                ):
                    backoff_s = min(2 * (attempt + 1), 10)
                    logger.warning(
                        f"🟡 Rate limit HF para {model_id}. Intento {attempt + 1}/{max_attempts}, esperando {backoff_s}s"
                    )
                    self.stats["rate_limited"] += 1
                    await asyncio.sleep(backoff_s)
                    # Reintentar si quedan intentos
                    if attempt < max_attempts - 1:
                        continue

                # Intentar fallback a text_generation si chat no soportado
                try:
                    text = await asyncio.to_thread(
                        local_client.text_generation,
                        model=model_id,
                        prompt=prompt,
                        max_new_tokens=max_tokens,
                        temperature=temperature,
                        top_p=top_p,
                    )
                    self._update_stats(latency_ms, success=True)
                    logger.info(
                        f"✅ HF text_generation fallback: {model_id} → {len(text)} chars en {latency_ms:.0f}ms"
                    )
                    return text
                except Exception:
                    logger.error(f"❌ HF API v2 error: {error_msg[:200]}")
                    self._update_stats(latency_ms, success=False)
                    raise Exception(f"HF API error: {error_msg[:200]}")

    async def generate_with_vision(
        self,
        model_id: str,
        prompt: str,
        image_path: str,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        top_p: float = 0.9,
        retry_on_loading: bool = True,
    ) -> str:
        """
        Generar texto analizando una imagen usando modelo multimodal

        Args:
            model_id: ID del modelo de visión (e.g., 'Qwen/Qwen3-VL-235B-A22B-Instruct')
            prompt: Prompt describiendo qué analizar
            image_path: Ruta a la imagen local
            max_tokens: Máximo tokens a generar
            temperature: Temperature para sampling
            top_p: Nucleus sampling parameter
            retry_on_loading: Si True, espera si modelo está cargándose

        Returns:
            Análisis de la imagen

        Raises:
            Exception: Si falla la generación o imagen no existe
        """
        import base64
        from pathlib import Path

        # Verificar que la imagen existe
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        # Leer y codificar imagen en base64
        with open(image_path, "rb") as img_file:
            image_data = base64.b64encode(img_file.read()).decode("utf-8")

        # Detectar tipo de imagen
        image_ext = Path(image_path).suffix.lower()
        mime_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(image_ext, "image/png")

        max_attempts = 3
        for attempt in range(max_attempts):
            # Rate limiting
            await self._check_rate_limit()

            start_time = time.time()

            try:
                # Crear mensaje con imagen en formato base64
                response = await asyncio.to_thread(
                    self.client.chat.completions.create,
                    model=model_id,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime_type};base64,{image_data}"},
                                },
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                )

                latency_ms = (time.time() - start_time) * 1000

                # Extraer texto generado
                msg = response.choices[0].message
                text = getattr(msg, "content", "") or ""
                if not text:
                    text = getattr(msg, "reasoning_content", "") or ""

                # Actualizar estadísticas
                self._update_stats(latency_ms, success=True)

                # Update model cache
                self.model_cache[model_id] = HFModelInfo(
                    model_id=model_id, status="ready", last_used=time.time()
                )

                logger.info(
                    f"✅ HF API v2 (Vision): {model_id} → {len(text)} chars en {latency_ms:.0f}ms"
                )
                return text

            except Exception as e:
                error_msg = str(e)
                latency_ms = (time.time() - start_time) * 1000

                # Detectar si es modelo cargándose
                if "not ready" in error_msg.lower() or "warm" in error_msg.lower():
                    if retry_on_loading and attempt == 0:
                        logger.warning(f"⏳ Modelo {model_id} cargándose. Esperando 25s...")
                        self.stats["model_loading_waits"] += 1
                        await asyncio.sleep(25)
                        retry_on_loading = False
                        continue
                    else:
                        logger.error(f"❌ Modelo {model_id} no está listo")
                        self._update_stats(latency_ms, success=False)
                        raise Exception(f"Model {model_id} not ready")

                # Rate limit 429
                if (
                    "429" in error_msg
                    or "too many requests" in error_msg.lower()
                    or "rate limit" in error_msg.lower()
                ):
                    backoff_s = min(3 * (attempt + 1), 15)
                    logger.warning(
                        f"🟡 Rate limit HF (Vision) para {model_id}. Intento {attempt + 1}/{max_attempts}, esperando {backoff_s}s"
                    )
                    self.stats["rate_limited"] += 1
                    await asyncio.sleep(backoff_s)
                    if attempt < max_attempts - 1:
                        continue

                # Tarea de visión no soportada
                if "not supported" in error_msg.lower() or "unsupported" in error_msg.lower():
                    logger.error(
                        f"❌ Modelo {model_id} no soporta entrada de visión vía chat.completions"
                    )
                    self._update_stats(latency_ms, success=False)
                    raise Exception(f"Vision not supported for model {model_id}: {error_msg[:200]}")

                # Otros errores
                logger.error(f"❌ HF API v2 (Vision) error: {error_msg[:200]}")
                self._update_stats(latency_ms, success=False)
                raise Exception(f"HF API vision error: {error_msg[:200]}")

    def _update_stats(self, latency_ms: float, success: bool):
        """Actualizar estadísticas de uso"""
        self.stats["requests"] += 1

        if not success:
            self.stats["errors"] += 1

        self.stats["total_latency_ms"] += latency_ms
        self.stats["avg_latency_ms"] = self.stats["total_latency_ms"] / max(
            self.stats["requests"], 1
        )

    async def check_model_status(self, model_id: str) -> str:
        """
        Verificar estado de un modelo (ready, loading, error)

        Returns:
            Status string: 'ready', 'loading', 'error', 'unknown'
        """
        try:
            # Intentar request simple
            await self.generate(
                model_id=model_id, prompt="test", max_tokens=5, retry_on_loading=False
            )
            return "ready"
        except Exception as e:
            error_msg = str(e).lower()
            if "not ready" in error_msg or "warm" in error_msg:
                return "loading"
            return "error"

    def get_stats(self) -> dict[str, Any]:
        """Obtener estadísticas de uso"""
        total_requests = self.stats["requests"]
        successful = total_requests - self.stats["errors"]

        return {
            **self.stats,
            "success_rate": (successful / max(total_requests, 1)) * 100,
            "requests_remaining_this_minute": max(
                0, self.rate_limiter["max_per_minute"] - len(self.rate_limiter["requests"])
            ),
        }

    def reset_stats(self):
        """Resetear estadísticas"""
        self.stats = {
            "requests": 0,
            "errors": 0,
            "rate_limited": 0,
            "model_loading_waits": 0,
            "avg_latency_ms": 0.0,
            "total_latency_ms": 0.0,
        }


# Función de conveniencia para uso standalone
async def quick_generate(model_id: str, prompt: str, api_key: str | None = None, **kwargs) -> str:
    """
    Función de conveniencia para generación rápida

    Ejemplo:
        text = await quick_generate(
            'meta-llama/Llama-3.2-1B-Instruct',
            'Analyze this market data...'
        )
    """
    async with HuggingFaceInferenceClient(api_key) as client:
        return await client.generate(model_id, prompt, **kwargs)
