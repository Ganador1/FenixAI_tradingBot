"""
Hybrid Model Configuration for Fenix Trading Bot
Configures which agents use local MLX vs external APIs (HuggingFace, Ollama Cloud, etc.)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple, List
import os
import logging

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 0.2


@dataclass
class ModelConfig:
    """Simplified configuration for the hybrid inference system."""
    provider: str
    model_id: str
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = DEFAULT_TEMPERATURE
    timeout: int = 60
    supports_vision: bool = False


class ModelBackend(Enum):
    """Available backends for model execution"""
    MLX_LOCAL = "mlx_local"
    HUGGINGFACE_API = "huggingface_api"
    OLLAMA_CLOUD = "ollama_cloud"
    OPENAI_API = "openai_api"


class HybridModelConfig:
    """
    Hybrid configuration that decides which backend to use per agent.

    Prioritizes local MLX for critical, low-latency agents,
    and uses cloud APIs for agents that can tolerate more latency.

    This allows for true parallelization without consuming excessive local RAM.
    """

    # Agents that can benefit from more powerful models via API
    # Note: With SOTA models, we prioritize quality over ultra-low latency
    CRITICAL_LOCAL_AGENTS = {
        'risk'        # Risk manager does not use LLM, always local (mathematical calculations)
    }

    # Agents that benefit from large models (72B+) via API
    SOTA_MODELS_AGENTS = {
        'technical',  # Qwen 2.5 72B - Superior math reasoning
        'qabba',      # Qwen 2.5 72B - Better instruction following
        'decision'    # DeepSeek V3 - Best reasoning available
    }

    # Detailed configuration per agent - SPECIALIZED SOTA MODELS
    AGENT_BACKEND_CONFIG = {
        'technical': {
            'primary': ModelBackend.HUGGINGFACE_API,
            'fallback': ModelBackend.MLX_LOCAL,
            'model_local': 'mlx-community/gemma-3-4b-it-4bit',
            'model_api': 'Qwen/Qwen2.5-72B-Instruct',  # SOTA: Superior in math & reasoning (86% MATH-500)
            'max_latency_ms': 8000,
            'description': 'Technical analysis with Qwen 2.5 72B (best math reasoning)',
            'specialty': 'mathematical_reasoning'
        },
        'sentiment': {
            'primary': ModelBackend.HUGGINGFACE_API,
            'fallback': ModelBackend.MLX_LOCAL,
            'model_local': 'mlx-community/Qwen2.5-3B-Instruct-4bit',
            'model_api': 'yiyanghkust/finbert-tone',  # SPECIALIZED: FinBERT fine-tuned on 10K financial samples
            'max_latency_ms': 5000,
            'description': 'Financial sentiment with FinBERT (specialized)',
            'specialty': 'financial_sentiment'
        },
        'visual': {
            'primary': ModelBackend.HUGGINGFACE_API,
            'fallback': ModelBackend.MLX_LOCAL,
            'model_local': 'mlx-community/gemma-3-4b-it-4bit',
            'model_api': 'Qwen/Qwen2.5-VL-72B-Instruct',  # MULTIMODAL: Large model (72B) with vision + language
            'max_latency_ms': 12000,
            'description': 'Chart analysis with Qwen2.5-VL-72B (large multimodal model)',
            'specialty': 'multimodal_vision',
            'is_vision_model': True
        },
        'qabba': {
            'primary': ModelBackend.HUGGINGFACE_API,
            'fallback': ModelBackend.MLX_LOCAL,
            'model_local': 'mlx-community/gemma-3-4b-it-4bit',
            'model_api': 'deepseek-ai/DeepSeek-V3',  # SOTA: Best reasoning & math (89% MATH, 56% GPQA)
            'max_latency_ms': 8000,
            'description': 'Quality validation with DeepSeek V3 (advanced reasoning)',
            'specialty': 'advanced_reasoning'
        },
        'decision': {
            'primary': ModelBackend.HUGGINGFACE_API,
            'fallback': ModelBackend.MLX_LOCAL,
            'model_local': 'mlx-community/gemma-3-4b-it-4bit',
            'model_api': 'deepseek-ai/DeepSeek-V3',  # SOTA: Best reasoning (89% MATH, 56% GPQA)
            'max_latency_ms': 10000,
            'description': 'Final decision with DeepSeek V3 (best reasoning)',
            'specialty': 'advanced_reasoning'
        }
    }

    @classmethod
    def get_backend_for_agent(
        cls,
        agent_type: str,
        force_local: bool = False
    ) -> Tuple[ModelBackend, Optional[str]]:
        """
        Determines which backend to use for a specific agent.

        Args:
            agent_type: Type of agent ('technical', 'sentiment', etc.)
            force_local: Force the use of local MLX (for testing or fallback)

        Returns:
            Tuple of (backend, model_name)
        """
        config = cls.AGENT_BACKEND_CONFIG.get(agent_type)

        if not config:
            logger.warning(f"No config for agent {agent_type}, using MLX local")
            return ModelBackend.MLX_LOCAL, None

        override = get_backend_override(agent_type)
        if override is not None:
            chosen_backend = override
            if chosen_backend == ModelBackend.HUGGINGFACE_API and not cls._is_huggingface_available():
                logger.warning(f"⚠️ HuggingFace override requested for {agent_type}, but no API key is available. Using local fallback.")
                chosen_backend = ModelBackend.MLX_LOCAL
            if chosen_backend == ModelBackend.MLX_LOCAL:
                model = config.get('model_local')
            else:
                model = config.get('model_api')
            logger.info(f"🔁 Override backend for {agent_type}: {chosen_backend.value} -> {model}")
            return chosen_backend, model

        # Force local if requested or if it is a critical agent
        if force_local or agent_type in cls.CRITICAL_LOCAL_AGENTS:
            backend = config.get('primary') if config.get('primary') == ModelBackend.MLX_LOCAL else config.get('fallback')
            model = config.get('model_local')
            logger.info(f"🔧 {agent_type}: Using local MLX (forced or critical)")
            return backend, model

        # Check availability of APIs
        primary_backend = config['primary']

        if primary_backend == ModelBackend.HUGGINGFACE_API:
            if cls._is_huggingface_available():
                model = config.get('model_api')
                logger.info(f"🌐 {agent_type}: Using HuggingFace API")
                return primary_backend, model
            else:
                # Fallback to local if API is not available
                logger.warning(f"⚠️ HuggingFace API not available, falling back to local for {agent_type}")
                return config['fallback'], config.get('model_local')

        elif primary_backend == ModelBackend.OLLAMA_CLOUD:
            if cls._is_ollama_cloud_available():
                model = config.get('model_api')
                logger.info(f"☁️ {agent_type}: Using Ollama Cloud")
                return primary_backend, model
            else:
                logger.warning(f"⚠️ Ollama Cloud not available, falling back to local for {agent_type}")
                return config['fallback'], config.get('model_local')

        # Default: use primary configuration
        model = config.get('model_local') if primary_backend == ModelBackend.MLX_LOCAL else config.get('model_api')
        return primary_backend, model

    @classmethod
    def _is_huggingface_available(cls) -> bool:
        """Check if HuggingFace API is configured"""
        api_key = os.getenv('HUGGINGFACE_API_KEY') or os.getenv('HF_TOKEN')
        return api_key is not None and api_key.strip() != ''

    @classmethod
    def _is_ollama_cloud_available(cls) -> bool:
        """Check if Ollama Cloud is configured"""
        api_key = os.getenv('OLLAMA_CLOUD_API_KEY')
        return api_key is not None and api_key.strip() != ''

    @classmethod
    def get_config_for_agent(cls, agent_type: str) -> Optional[Dict]:
        """Get the full configuration for an agent"""
        return cls.AGENT_BACKEND_CONFIG.get(agent_type)

    @classmethod
    def list_agents_by_backend(cls, backend: ModelBackend) -> list:
        """List agents that use a specific backend as primary"""
        agents = []
        for agent_type, config in cls.AGENT_BACKEND_CONFIG.items():
            if config.get('primary') == backend:
                agents.append(agent_type)
        return agents

    @classmethod
    def get_parallel_groups(cls) -> Dict[str, list]:
        """
        Group agents that can be executed in parallel.

        Returns:
            Dict with parallel agent groups:
            - 'parallel_api': Agents using API (can run together)
            - 'sequential_local': Local agents (must run sequentially)
        """
        parallel_api = []
        sequential_local = []

        for agent_type, config in cls.AGENT_BACKEND_CONFIG.items():
            if config.get('primary') == ModelBackend.HUGGINGFACE_API:
                parallel_api.append(agent_type)
            elif config.get('primary') == ModelBackend.MLX_LOCAL:
                sequential_local.append(agent_type)

        return {
            'parallel_api': parallel_api,
            'sequential_local': sequential_local
        }

    @classmethod
    def estimate_memory_usage(cls, agents_to_run: list) -> Dict[str, float]:
        """
        Estimate RAM usage for a list of agents.

        Args:
            agents_to_run: List of agent_types to be executed

        Returns:
            Dict with memory estimation by type
        """
        # RAM estimates per model (GB)
        MODEL_MEMORY = {
            'mlx-community/gemma-3-4b-it-4bit': 3.5,
            'mlx-community/Qwen2.5-3B-Instruct-4bit': 2.5,
            'api': 0.0  # APIs do not consume local RAM
        }

        local_memory = 0.0
        api_memory = 0.0

        for agent_type in agents_to_run:
            backend, model = cls.get_backend_for_agent(agent_type)

            if backend == ModelBackend.MLX_LOCAL:
                local_memory = max(local_memory, MODEL_MEMORY.get(model, 4.0))
            else:
                api_memory += 0  # APIs do not consume local RAM

        return {
            'local_ram_gb': local_memory,
            'api_ram_gb': api_memory,
            'total_local_ram_gb': local_memory,
            'can_parallelize': local_memory < 6.0  # Safe threshold
        }


def _build_model_config(agent_type: str, backend: ModelBackend, model_id: Optional[str], config: Dict) -> Optional[ModelConfig]:
    """Builds ModelConfig for compatibility with inference clients."""
    if backend == ModelBackend.HUGGINGFACE_API:
        provider = "huggingface"
        model_id = model_id or config.get('model_api')
    elif backend == ModelBackend.MLX_LOCAL:
        provider = "mlx"
        model_id = model_id or config.get('model_local')
    elif backend == ModelBackend.OLLAMA_CLOUD:
        provider = "ollama"
        # We'll map to Ollama Cloud model IDs
        model_id = model_id or config.get('model_api') or config.get('model_local')
    elif backend == ModelBackend.OPENAI_API:
        provider = "openai"
        model_id = model_id or config.get('model_api') or config.get('model_local')
    else:
        # Other backends (Ollama/OpenAI) are not yet supported by UnifiedInferenceClient
        provider = backend.value
        model_id = model_id or config.get('model_api') or config.get('model_local')

    if not model_id:
        logger.warning("No model_id resolved for %s backend %s", agent_type, backend.value)
        return None

    supports_vision = bool(config.get('is_vision_model', False)) and provider in {"huggingface", "ollama_cloud"}
    return ModelConfig(
        provider=provider,
        model_id=model_id,
        max_tokens=config.get('max_tokens', DEFAULT_MAX_TOKENS),
        temperature=config.get('temperature', DEFAULT_TEMPERATURE),
        supports_vision=supports_vision
    )


def get_configs_for_agent(agent_type: str) -> List[ModelConfig]:
    """
    Returns an ordered list of configurations (primary + fallback)
    compatible with UnifiedInferenceClient.
    """
    config = HybridModelConfig.AGENT_BACKEND_CONFIG.get(agent_type)
    if not config:
        logger.warning("No hybrid configuration for agent '%s'", agent_type)
        return []

    primary_backend = config.get('primary', ModelBackend.MLX_LOCAL)
    fallback_backend = config.get('fallback')

    configs: List[ModelConfig] = []

    primary_cfg = _build_model_config(agent_type, primary_backend, None, config)
    if primary_cfg:
        configs.append(primary_cfg)

    if fallback_backend and fallback_backend != primary_backend:
        fallback_cfg = _build_model_config(agent_type, fallback_backend, None, config)
        if fallback_cfg:
            configs.append(fallback_cfg)

    return configs


def get_primary_config(agent_type: str) -> Optional[ModelConfig]:
    """Returns the primary configuration of the agent."""
    configs = get_configs_for_agent(agent_type)
    return configs[0] if configs else None


# Environment variable override configuration
def get_backend_override(agent_type: str) -> Optional[ModelBackend]:
    """
    Allow backend override via environment variable.

    Example:
        FORCE_LOCAL_SENTIMENT=true → Force sentiment to local
        FORCE_API_TECHNICAL=true → Force technical to API
    """
    force_local_key = f'FORCE_LOCAL_{agent_type.upper()}'
    force_api_key = f'FORCE_API_{agent_type.upper()}'

    if os.getenv(force_local_key, '').lower() == 'true':
        return ModelBackend.MLX_LOCAL

    if os.getenv(force_api_key, '').lower() == 'true':
        return ModelBackend.HUGGINGFACE_API

    return None


# Configuration logging on import
if __name__ != "__main__":
    logger.info("=" * 60)
    logger.info("🔧 Hybrid Model Configuration Loaded")
    logger.info("=" * 60)

    # Show agent configuration
    for agent_type, config in HybridModelConfig.AGENT_BACKEND_CONFIG.items():
        backend, model = HybridModelConfig.get_backend_for_agent(agent_type)
        logger.info(f"  • {agent_type:12} → {backend.value:20} ({model})")

    # Show parallel groups
    groups = HybridModelConfig.get_parallel_groups()
    logger.info(f"\n📊 Parallel Groups:")
    logger.info(f"  • API (parallel):  {', '.join(groups['parallel_api'])}")
    logger.info(f"  • Local (sequential): {', '.join(groups['sequential_local'])}")

    # Estimate memory
    all_agents = list(HybridModelConfig.AGENT_BACKEND_CONFIG.keys())
    mem_estimate = HybridModelConfig.estimate_memory_usage(all_agents)
    logger.info(f"\n💾 Memory Estimate:")
    logger.info(f"  • Local RAM: {mem_estimate['total_local_ram_gb']:.1f} GB")
    logger.info(f"  • Can parallelize: {'✅ Yes' if mem_estimate['can_parallelize'] else '❌ No'}")
    logger.info("=" * 60)
