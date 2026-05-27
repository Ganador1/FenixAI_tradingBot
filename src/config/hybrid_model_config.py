"""
Hybrid Model Configuration for Fenix Trading Bot
Configura qué agentes usan MLX local vs APIs externas (HuggingFace, Ollama Cloud, etc.)
"""

import logging
import os
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 0.2


@dataclass
class ModelConfig:
    """Configuración simplificada para el sistema de inferencia híbrido."""

    provider: str
    model_id: str
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = DEFAULT_TEMPERATURE
    timeout: int = 60
    supports_vision: bool = False


class ModelBackend(Enum):
    """Backends disponibles para ejecución de modelos"""

    MLX_LOCAL = "mlx_local"
    HUGGINGFACE_API = "huggingface_api"
    OLLAMA_CLOUD = "ollama_cloud"
    OPENAI_API = "openai_api"


class HybridModelConfig:
    """
    Configuración híbrida que decide qué backend usar por agente.

    Prioriza MLX local para agentes críticos de baja latencia,
    y usa APIs cloud para agentes que toleran más latencia.

    Esto permite paralelización real sin consumir RAM local en exceso.
    """

    # Agentes que pueden beneficiarse de modelos más potentes via API
    # Nota: Con modelos SOTA, priorizamos calidad sobre latencia ultra-baja
    CRITICAL_LOCAL_AGENTS = {
        "risk"  # Risk manager no usa LLM, siempre local (cálculos matemáticos)
    }

    # Agentes que se benefician de modelos grandes (72B+) via API
    SOTA_MODELS_AGENTS = {
        "technical",  # Qwen 2.5 72B - Superior math reasoning
        "qabba",  # Qwen 2.5 72B - Mejor instruction following
        "decision",  # DeepSeek V3 - Best reasoning disponible
    }

    # Configuración detallada por agente - MODELOS ESPECIALIZADOS SOTA
    AGENT_BACKEND_CONFIG = {
        "technical": {
            "primary": ModelBackend.HUGGINGFACE_API,
            "fallback": ModelBackend.MLX_LOCAL,
            "model_local": "mlx-community/gemma-3-4b-it-4bit",
            "model_api": "Qwen/Qwen2.5-72B-Instruct",  # SOTA: Superior en math & reasoning (86% MATH-500)
            "max_latency_ms": 8000,
            "description": "Technical analysis con Qwen 2.5 72B (best math reasoning)",
            "specialty": "mathematical_reasoning",
        },
        "sentiment": {
            "primary": ModelBackend.HUGGINGFACE_API,
            "fallback": ModelBackend.MLX_LOCAL,
            "model_local": "mlx-community/Qwen2.5-3B-Instruct-4bit",
            "model_api": "yiyanghkust/finbert-tone",  # SPECIALIZED: FinBERT fine-tuned en 10K financial samples
            "max_latency_ms": 5000,
            "description": "Financial sentiment con FinBERT (specialized)",
            "specialty": "financial_sentiment",
        },
        "visual": {
            "primary": ModelBackend.HUGGINGFACE_API,
            "fallback": ModelBackend.MLX_LOCAL,
            "model_local": "mlx-community/gemma-3-4b-it-4bit",
            "model_api": "Qwen/Qwen2.5-VL-72B-Instruct",  # MULTIMODAL: Modelo grande (72B) con visión + lenguaje
            "max_latency_ms": 12000,
            "description": "Chart analysis con Qwen2.5-VL-72B (modelo grande multimodal)",
            "specialty": "multimodal_vision",
            "is_vision_model": True,
        },
        "qabba": {
            "primary": ModelBackend.HUGGINGFACE_API,
            "fallback": ModelBackend.MLX_LOCAL,
            "model_local": "mlx-community/gemma-3-4b-it-4bit",
            "model_api": "deepseek-ai/DeepSeek-V3",  # SOTA: Best reasoning & math (89% MATH, 56% GPQA)
            "max_latency_ms": 8000,
            "description": "Quality validation con DeepSeek V3 (advanced reasoning)",
            "specialty": "advanced_reasoning",
        },
        "decision": {
            "primary": ModelBackend.HUGGINGFACE_API,
            "fallback": ModelBackend.MLX_LOCAL,
            "model_local": "mlx-community/gemma-3-4b-it-4bit",
            "model_api": "deepseek-ai/DeepSeek-V3",  # SOTA: Best reasoning (89% MATH, 56% GPQA)
            "max_latency_ms": 10000,
            "description": "Final decision con DeepSeek V3 (best reasoning)",
            "specialty": "advanced_reasoning",
        },
    }

    @classmethod
    def get_backend_for_agent(
        cls, agent_type: str, force_local: bool = False
    ) -> tuple[ModelBackend, str | None]:
        """
        Determina qué backend usar para un agente específico.

        Args:
            agent_type: Tipo de agente ('technical', 'sentiment', etc.)
            force_local: Forzar uso de MLX local (para testing o fallback)

        Returns:
            Tupla de (backend, model_name)
        """
        config = cls.AGENT_BACKEND_CONFIG.get(agent_type)

        if not config:
            logger.warning(f"No config for agent {agent_type}, using MLX local")
            return ModelBackend.MLX_LOCAL, None

        override = get_backend_override(agent_type)
        if override is not None:
            chosen_backend = override
            if (
                chosen_backend == ModelBackend.HUGGINGFACE_API
                and not cls._is_huggingface_available()
            ):
                logger.warning(
                    f"⚠️ HuggingFace override solicitado para {agent_type}, pero no hay API key disponible. Usando fallback local."
                )
                chosen_backend = ModelBackend.MLX_LOCAL
            if chosen_backend == ModelBackend.MLX_LOCAL:
                model = config.get("model_local")
            else:
                model = config.get("model_api")
            logger.info(f"🔁 Override backend for {agent_type}: {chosen_backend.value} -> {model}")
            return chosen_backend, model

        # Forzar local si se solicita o si es agente crítico
        if force_local or agent_type in cls.CRITICAL_LOCAL_AGENTS:
            backend = (
                config.get("primary")
                if config.get("primary") == ModelBackend.MLX_LOCAL
                else config.get("fallback")
            )
            model = config.get("model_local")
            logger.info(f"🔧 {agent_type}: Usando MLX local (forzado o crítico)")
            return backend, model

        # Verificar disponibilidad de APIs
        primary_backend = config["primary"]

        if primary_backend == ModelBackend.HUGGINGFACE_API:
            if cls._is_huggingface_available():
                model = config.get("model_api")
                logger.info(f"🌐 {agent_type}: Usando HuggingFace API")
                return primary_backend, model
            else:
                # Fallback a local si API no disponible
                logger.warning(
                    f"⚠️ HuggingFace API no disponible, fallback a local para {agent_type}"
                )
                return config["fallback"], config.get("model_local")

        elif primary_backend == ModelBackend.OLLAMA_CLOUD:
            if cls._is_ollama_cloud_available():
                model = config.get("model_api")
                logger.info(f"☁️ {agent_type}: Usando Ollama Cloud")
                return primary_backend, model
            else:
                logger.warning(f"⚠️ Ollama Cloud no disponible, fallback a local para {agent_type}")
                return config["fallback"], config.get("model_local")

        # Default: usar configuración primary
        model = (
            config.get("model_local")
            if primary_backend == ModelBackend.MLX_LOCAL
            else config.get("model_api")
        )
        return primary_backend, model

    @classmethod
    def _is_huggingface_available(cls) -> bool:
        """Verificar si HuggingFace API está configurada"""
        api_key = os.getenv("HUGGINGFACE_API_KEY") or os.getenv("HF_TOKEN")
        return api_key is not None and api_key.strip() != ""

    @classmethod
    def _is_ollama_cloud_available(cls) -> bool:
        """Verificar si Ollama Cloud está configurado"""
        api_key = os.getenv("OLLAMA_CLOUD_API_KEY")
        return api_key is not None and api_key.strip() != ""

    @classmethod
    def get_config_for_agent(cls, agent_type: str) -> dict | None:
        """Obtener configuración completa para un agente"""
        return cls.AGENT_BACKEND_CONFIG.get(agent_type)

    @classmethod
    def list_agents_by_backend(cls, backend: ModelBackend) -> list:
        """Listar agentes que usan un backend específico como primario"""
        agents = []
        for agent_type, config in cls.AGENT_BACKEND_CONFIG.items():
            if config.get("primary") == backend:
                agents.append(agent_type)
        return agents

    @classmethod
    def get_parallel_groups(cls) -> dict[str, list]:
        """
        Agrupar agentes que pueden ejecutarse en paralelo.

        Returns:
            Dict con grupos de agentes paralelos:
            - 'parallel_api': Agentes que usan API (pueden correr juntos)
            - 'sequential_local': Agentes locales (deben correr secuencialmente)
        """
        parallel_api = []
        sequential_local = []

        for agent_type, config in cls.AGENT_BACKEND_CONFIG.items():
            if config.get("primary") == ModelBackend.HUGGINGFACE_API:
                parallel_api.append(agent_type)
            elif config.get("primary") == ModelBackend.MLX_LOCAL:
                sequential_local.append(agent_type)

        return {"parallel_api": parallel_api, "sequential_local": sequential_local}

    @classmethod
    def estimate_memory_usage(cls, agents_to_run: list) -> dict[str, float]:
        """
        Estimar uso de memoria RAM para una lista de agentes.

        Args:
            agents_to_run: Lista de agent_types a ejecutar

        Returns:
            Dict con estimación de memoria por tipo
        """
        # Estimaciones de RAM por modelo (GB)
        MODEL_MEMORY = {
            "mlx-community/gemma-3-4b-it-4bit": 3.5,
            "mlx-community/Qwen2.5-3B-Instruct-4bit": 2.5,
            "api": 0.0,  # APIs no consumen RAM local
        }

        local_memory = 0.0
        api_memory = 0.0

        for agent_type in agents_to_run:
            backend, model = cls.get_backend_for_agent(agent_type)

            if backend == ModelBackend.MLX_LOCAL:
                local_memory = max(local_memory, MODEL_MEMORY.get(model, 4.0))
            else:
                api_memory += 0  # APIs no consumen RAM local

        return {
            "local_ram_gb": local_memory,
            "api_ram_gb": api_memory,
            "total_local_ram_gb": local_memory,
            "can_parallelize": local_memory < 6.0,  # Safe threshold
        }


def _build_model_config(
    agent_type: str, backend: ModelBackend, model_id: str | None, config: dict
) -> ModelConfig | None:
    """Construye ModelConfig para compatibilidad con clientes de inferencia."""
    if backend == ModelBackend.HUGGINGFACE_API:
        provider = "huggingface"
        model_id = model_id or config.get("model_api")
    elif backend == ModelBackend.MLX_LOCAL:
        provider = "mlx"
        model_id = model_id or config.get("model_local")
    elif backend == ModelBackend.OLLAMA_CLOUD:
        provider = "ollama"
        # We'll map to Ollama Cloud model IDs
        model_id = model_id or config.get("model_api") or config.get("model_local")
    elif backend == ModelBackend.OPENAI_API:
        provider = "openai"
        model_id = model_id or config.get("model_api") or config.get("model_local")
    else:
        # Otros backends (Ollama/OpenAI) no están soportados aún por UnifiedInferenceClient
        provider = backend.value
        model_id = model_id or config.get("model_api") or config.get("model_local")

    if not model_id:
        logger.warning("No model_id resolved for %s backend %s", agent_type, backend.value)
        return None

    supports_vision = bool(config.get("is_vision_model", False)) and provider in {
        "huggingface",
        "ollama_cloud",
    }
    return ModelConfig(
        provider=provider,
        model_id=model_id,
        max_tokens=config.get("max_tokens", DEFAULT_MAX_TOKENS),
        temperature=config.get("temperature", DEFAULT_TEMPERATURE),
        supports_vision=supports_vision,
    )


def get_configs_for_agent(agent_type: str) -> list[ModelConfig]:
    """
    Devuelve una lista ordenada de configuraciones (primaria + fallback)
    compatibles con UnifiedInferenceClient.
    """
    config = HybridModelConfig.AGENT_BACKEND_CONFIG.get(agent_type)
    if not config:
        logger.warning("No hybrid configuration for agent '%s'", agent_type)
        return []

    primary_backend = config.get("primary", ModelBackend.MLX_LOCAL)
    fallback_backend = config.get("fallback")

    configs: list[ModelConfig] = []

    primary_cfg = _build_model_config(agent_type, primary_backend, None, config)
    if primary_cfg:
        configs.append(primary_cfg)

    if fallback_backend and fallback_backend != primary_backend:
        fallback_cfg = _build_model_config(agent_type, fallback_backend, None, config)
        if fallback_cfg:
            configs.append(fallback_cfg)

    return configs


def get_primary_config(agent_type: str) -> ModelConfig | None:
    """Devuelve la configuración primaria del agente."""
    configs = get_configs_for_agent(agent_type)
    return configs[0] if configs else None


# Configuración de variables de entorno para override
def get_backend_override(agent_type: str) -> ModelBackend | None:
    """
    Permitir override de backend por variable de entorno.

    Ejemplo:
        FORCE_LOCAL_SENTIMENT=true → Forzar sentiment a local
        FORCE_API_TECHNICAL=true → Forzar technical a API
    """
    force_local_key = f"FORCE_LOCAL_{agent_type.upper()}"
    force_api_key = f"FORCE_API_{agent_type.upper()}"

    if os.getenv(force_local_key, "").lower() == "true":
        return ModelBackend.MLX_LOCAL

    if os.getenv(force_api_key, "").lower() == "true":
        return ModelBackend.HUGGINGFACE_API

    return None


# Logging de configuración al importar
if __name__ != "__main__":
    logger.info("=" * 60)
    logger.info("🔧 Hybrid Model Configuration Loaded")
    logger.info("=" * 60)

    # Mostrar configuración de agentes
    for agent_type, config in HybridModelConfig.AGENT_BACKEND_CONFIG.items():
        backend, model = HybridModelConfig.get_backend_for_agent(agent_type)
        logger.info(f"  • {agent_type:12} → {backend.value:20} ({model})")

    # Mostrar grupos paralelos
    groups = HybridModelConfig.get_parallel_groups()
    logger.info("\n📊 Parallel Groups:")
    logger.info(f"  • API (parallel):  {', '.join(groups['parallel_api'])}")
    logger.info(f"  • Local (sequential): {', '.join(groups['sequential_local'])}")

    # Estimar memoria
    all_agents = list(HybridModelConfig.AGENT_BACKEND_CONFIG.keys())
    mem_estimate = HybridModelConfig.estimate_memory_usage(all_agents)
    logger.info("\n💾 Memory Estimate:")
    logger.info(f"  • Local RAM: {mem_estimate['total_local_ram_gb']:.1f} GB")
    logger.info(f"  • Can parallelize: {'✅ Yes' if mem_estimate['can_parallelize'] else '❌ No'}")
    logger.info("=" * 60)
