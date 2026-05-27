# config/llm_provider_config.py
"""
Configuración de providers LLM para cada agente.
Permite elegir diferentes providers (Ollama, HuggingFace, OpenAI, etc.) por agente.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, validator

# Tipos de providers disponibles
ProviderType = Literal[
    "ollama_local",
    "ollama_cloud",
    "huggingface_mlx",
    "huggingface_inference",
    "openai",
    "anthropic",
    "groq",
]


class AgentProviderConfig(BaseModel):
    """Configuración de provider para un agente específico."""

    # Provider configuration
    provider_type: ProviderType = "ollama_local"
    model_name: str = Field(
        default="qwen2.5:7b",
        description="Nombre del modelo a usar (ej: 'qwen2.5:7b', 'gpt-4', 'claude-3-sonnet')",
    )

    # Provider authentication (opcional, se usa env var si no se especifica)
    api_key: SecretStr | None = Field(
        default=None,
        description="API key del provider (opcional, se lee de ENV si no se proporciona)",
    )
    api_base: str | None = Field(
        default=None, description="URL base del API (para Ollama cloud o APIs custom)"
    )

    # Model parameters
    temperature: float = Field(default=0.15, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1500, ge=1)
    timeout: int = Field(default=90, ge=1)

    # Vision support (para agentes que usan imágenes)
    supports_vision: bool = False

    # Extra configuration (para parámetros específicos del provider)
    extra_config: dict[str, Any] = Field(default_factory=dict)

    # Fallback configuration
    fallback_provider_type: ProviderType | None = None
    fallback_model_name: str | None = None

    @validator("api_key", pre=True, always=True)
    def load_api_key_from_env(cls, v, values):
        """Carga la API key desde variables de entorno si no está configurada."""
        if v is not None:
            return v

        # Intentar cargar desde ENV según el provider type
        provider_type = values.get("provider_type")
        if provider_type == "openai":
            env_key = os.getenv("OPENAI_API_KEY")
            if env_key:
                return SecretStr(env_key)
        elif provider_type == "anthropic":
            env_key = os.getenv("ANTHROPIC_API_KEY")
            if env_key:
                return SecretStr(env_key)
        elif provider_type == "groq":
            env_key = os.getenv("GROQ_API_KEY")
            if env_key:
                return SecretStr(env_key)
        elif provider_type == "huggingface_inference":
            env_key = os.getenv("HUGGINGFACE_API_KEY")
            if env_key:
                return SecretStr(env_key)
        elif provider_type == "ollama_cloud":
            env_key = os.getenv("OLLAMA_API_KEY")
            if env_key:
                return SecretStr(env_key)

        return None

    @validator("api_base", pre=True, always=True)
    def set_default_api_base(cls, v, values):
        """Establece la URL base por defecto según el provider."""
        if v is not None:
            return v

        provider_type = values.get("provider_type")
        if provider_type == "ollama_local":
            return "http://localhost:11434"
        elif provider_type == "ollama_cloud":
            return os.getenv("OLLAMA_CLOUD_URL", "https://api.ollama.ai")
        elif provider_type == "huggingface_inference":
            return "https://api-inference.huggingface.co"

        return None


class LLMProvidersConfig(BaseModel):
    """Configuración de providers para todos los agentes."""

    # Configuración por agente - Optimizada SOTA con Diversificación de Providers

    sentiment: AgentProviderConfig = Field(
        default_factory=lambda: AgentProviderConfig(
            provider_type="huggingface_inference",
            model_name="Qwen/Qwen2.5-72B-Instruct",  # SOTA para sentiment, mejor en análisis multilingual
            temperature=0.5,
            max_tokens=1500,
            timeout=90,
            supports_vision=False,
            fallback_provider_type="ollama_local",
            fallback_model_name="qwen2.5:7b",
        )
    )

    technical: AgentProviderConfig = Field(
        default_factory=lambda: AgentProviderConfig(
            provider_type="huggingface_inference",
            model_name="meta-llama/Llama-3.3-70B-Instruct",  # SOTA rápido para análisis técnico (163 tokens/s)
            temperature=0.3,
            max_tokens=2000,
            timeout=90,
            supports_vision=False,
            fallback_provider_type="ollama_local",
            fallback_model_name="deepseek-r1:7b-qwen-distill-q4_K_M",
        )
    )

    visual: AgentProviderConfig = Field(
        default_factory=lambda: AgentProviderConfig(
            provider_type="huggingface_inference",
            model_name="Qwen/Qwen2.5-VL-72B-Instruct",  # SOTA vision model (591K downloads)
            temperature=0.4,
            max_tokens=1200,
            timeout=120,
            supports_vision=True,
            fallback_provider_type="ollama_local",
            fallback_model_name="qwen2.5vl:7b-q4_K_M",
        )
    )

    qabba: AgentProviderConfig = Field(
        default_factory=lambda: AgentProviderConfig(
            provider_type="huggingface_inference",
            model_name="Qwen/Qwen2.5-72B-Instruct",  # Diversificación: Qwen vs Llama para perspectiva diferente
            temperature=0.4,
            max_tokens=800,
            timeout=60,
            supports_vision=False,
            fallback_provider_type="ollama_local",
            fallback_model_name="adrienbrault/nous-hermes2pro-llama3-8b:q4_K_M",
        )
    )

    # NOTA: Risk Manager NO USA LLM - Es pura lógica matemática y reglas de gestión de riesgo
    # Esta configuración está aquí solo como fallback legacy, pero no se debe usar
    risk_manager: AgentProviderConfig = Field(
        default_factory=lambda: AgentProviderConfig(
            provider_type="ollama_local",
            model_name="qwen2.5:7b-instruct-q5_k_m",
            temperature=0.15,
            max_tokens=1000,
            timeout=45,
            supports_vision=False,
        )
    )

    # Decision Agent - Síntesis final con DeepSeek-V3.1-Terminus
    # Recibe análisis de Technical, Sentiment, Visual y QABBA y toma decisión final
    decision: AgentProviderConfig = Field(
        default_factory=lambda: AgentProviderConfig(
            provider_type="huggingface_inference",
            model_name="deepseek-ai/DeepSeek-V3.1-Terminus",  # Mejor razonamiento estratégico
            temperature=0.2,  # Balance entre creatividad y determinismo
            max_tokens=1500,  # Suficiente para razonamiento comprehensivo
            timeout=120,  # Tiempo extra para síntesis compleja
            supports_vision=False,
            fallback_provider_type="ollama_local",
            fallback_model_name="qwen2.5:7b-instruct-q5_k_m",
        )
    )

    def get_agent_config(self, agent_type: str) -> AgentProviderConfig:
        """Obtiene la configuración de provider para un agente específico."""
        agent_configs = {
            "sentiment": self.sentiment,
            "technical": self.technical,
            "visual": self.visual,
            "qabba": self.qabba,
            "risk_manager": self.risk_manager,
            "decision": self.decision,
        }

        config = agent_configs.get(agent_type)
        if config is None:
            raise ValueError(f"Unknown agent type: {agent_type}")

        return config


# Ejemplo de configuración para diferentes escenarios

# Configuración 1: Todo local con Ollama
EXAMPLE_ALL_LOCAL = LLMProvidersConfig(
    sentiment=AgentProviderConfig(
        provider_type="ollama_local",
        model_name="qwen2.5:7b",
    ),
    technical=AgentProviderConfig(
        provider_type="ollama_local",
        model_name="deepseek-r1:7b-qwen-distill-q4_K_M",
    ),
    visual=AgentProviderConfig(
        provider_type="ollama_local", model_name="qwen2.5vl:7b-q4_K_M", supports_vision=True
    ),
    qabba=AgentProviderConfig(
        provider_type="ollama_local",
        model_name="adrienbrault/nous-hermes2pro-llama3-8b:q4_K_M",
    ),
    decision=AgentProviderConfig(
        provider_type="ollama_local",
        model_name="qwen2.5:7b-instruct-q5_k_m",
    ),
)

# Configuración 2: Mix de providers (producción con fallbacks)
EXAMPLE_MIXED_PROVIDERS = LLMProvidersConfig(
    sentiment=AgentProviderConfig(
        provider_type="ollama_local",
        model_name="qwen2.5:7b",
        fallback_provider_type="groq",
        fallback_model_name="mixtral-8x7b-32768",
    ),
    technical=AgentProviderConfig(
        provider_type="groq",  # Ultra rápido para análisis técnico
        model_name="mixtral-8x7b-32768",
        fallback_provider_type="ollama_local",
        fallback_model_name="deepseek-r1:7b-qwen-distill-q4_K_M",
    ),
    visual=AgentProviderConfig(
        provider_type="openai",  # GPT-4 Vision para mejor análisis de gráficos
        model_name="gpt-4-vision-preview",
        supports_vision=True,
        fallback_provider_type="ollama_local",
        fallback_model_name="qwen2.5vl:7b-q4_K_M",
    ),
    qabba=AgentProviderConfig(
        provider_type="ollama_local",
        model_name="adrienbrault/nous-hermes2pro-llama3-8b:q4_K_M",
    ),
    decision=AgentProviderConfig(
        provider_type="anthropic",  # Claude para decisiones críticas
        model_name="claude-3-sonnet-20240229",
        fallback_provider_type="ollama_local",
        fallback_model_name="qwen2.5:7b-instruct-q5_k_m",
    ),
)

# Configuración 3: HuggingFace MLX (optimizado para Mac M-series)
EXAMPLE_MLX_OPTIMIZED = LLMProvidersConfig(
    sentiment=AgentProviderConfig(
        provider_type="huggingface_mlx",
        model_name="mlx-community/Qwen2.5-7B-Instruct-4bit",
    ),
    technical=AgentProviderConfig(
        provider_type="huggingface_mlx",
        model_name="mlx-community/DeepSeek-R1-Distill-Qwen-7B-4bit",
    ),
    visual=AgentProviderConfig(
        provider_type="ollama_local",  # MLX vision aún en desarrollo
        model_name="qwen2.5vl:7b-q4_K_M",
        supports_vision=True,
    ),
    qabba=AgentProviderConfig(
        provider_type="huggingface_mlx",
        model_name="mlx-community/Hermes-2-Pro-Llama-3-8B-4bit",
    ),
    decision=AgentProviderConfig(
        provider_type="huggingface_mlx",
        model_name="mlx-community/Qwen2.5-7B-Instruct-4bit",
    ),
)

# Configuración 4: Todo en la nube (APIs)
EXAMPLE_ALL_CLOUD = LLMProvidersConfig(
    sentiment=AgentProviderConfig(
        provider_type="groq",
        model_name="mixtral-8x7b-32768",
    ),
    technical=AgentProviderConfig(
        provider_type="groq",
        model_name="mixtral-8x7b-32768",
    ),
    visual=AgentProviderConfig(
        provider_type="openai", model_name="gpt-4-vision-preview", supports_vision=True
    ),
    qabba=AgentProviderConfig(
        provider_type="groq",
        model_name="mixtral-8x7b-32768",
    ),
    decision=AgentProviderConfig(
        provider_type="anthropic",
        model_name="claude-3-opus-20240229",
    ),
)

if __name__ == "__main__":
    # Test de configuración
    print("=== Ejemplo: Configuración All Local ===")
    config = EXAMPLE_ALL_LOCAL
    print(f"Sentiment: {config.sentiment.provider_type} - {config.sentiment.model_name}")
    print(f"Technical: {config.technical.provider_type} - {config.technical.model_name}")
    print(
        f"Visual: {config.visual.provider_type} - {config.visual.model_name} (Vision: {config.visual.supports_vision})"
    )
    print(f"QABBA: {config.qabba.provider_type} - {config.qabba.model_name}")
    print(f"Decision: {config.decision.provider_type} - {config.decision.model_name}")

    print("\n=== Ejemplo: Configuración Mixed Providers ===")
    config = EXAMPLE_MIXED_PROVIDERS
    for agent_type in ["sentiment", "technical", "visual", "qabba", "decision"]:
        agent_config = config.get_agent_config(agent_type)
        print(
            f"{agent_type.capitalize()}: {agent_config.provider_type} - {agent_config.model_name}"
        )
        if agent_config.fallback_provider_type:
            print(
                f"  └─ Fallback: {agent_config.fallback_provider_type} - {agent_config.fallback_model_name}"
            )
