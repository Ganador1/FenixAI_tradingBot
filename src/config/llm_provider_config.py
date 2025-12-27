# config/llm_provider_config.py
"""
LLM provider configuration for each agent.
Allows choosing different providers (Ollama, HuggingFace, OpenAI, etc.) per agent.
"""
from __future__ import annotations

import os
from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, Field, SecretStr, validator
from pathlib import Path

# The single Ollama model for all agents, configurable via environment variable
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

# Available provider types
ProviderType = Literal[
    "ollama_local",
    "ollama_cloud",
    "huggingface_mlx",
    "huggingface_inference",
    "openai",
    "anthropic",
    "groq"
]

class AgentProviderConfig(BaseModel):
    """Provider configuration for a specific agent."""

    # Provider configuration
    provider_type: ProviderType = "ollama_local"
    model_name: str = Field(
        default="qwen2.5:7b",
        description="Name of the model to use (e.g., 'qwen2.5:7b', 'gpt-4', 'claude-3-sonnet')"
    )

    # Provider authentication (optional, env var is used if not specified)
    api_key: Optional[SecretStr] = Field(
        default=None,
        description="Provider API key (optional, read from ENV if not provided)"
    )
    api_base: Optional[str] = Field(
        default=None,
        description="Base URL of the API (for Ollama cloud or custom APIs)"
    )

    # Model parameters
    temperature: float = Field(default=0.15, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1500, ge=1)
    timeout: int = Field(default=90, ge=1)

    # Vision support (for agents that use images)
    supports_vision: bool = False

    # Extra configuration (for provider-specific parameters)
    extra_config: Dict[str, Any] = Field(default_factory=dict)

    # Fallback configuration
    fallback_provider_type: Optional[ProviderType] = None
    fallback_model_name: Optional[str] = None

    @validator('model_name', pre=True, always=True)
    def set_ollama_model_from_env(cls, v, values):
        """Sets the Ollama model from the OLLAMA_MODEL environment variable."""
        if values.get('provider_type') == 'ollama_local':
            return OLLAMA_MODEL
        return v
    
    @validator('fallback_model_name', pre=True, always=True)
    def set_fallback_ollama_model_from_env(cls, v, values):
        """Sets the fallback Ollama model from the OLLAMA_MODEL environment variable."""
        if values.get('fallback_provider_type') == 'ollama_local':
            return OLLAMA_MODEL
        return v

    @validator('api_key', pre=True, always=True)
    def load_api_key_from_env(cls, v, values):
        """Loads the API key from environment variables if not configured."""
        if v is not None:
            return v

        # Try to load from ENV based on the provider type
        provider_type = values.get('provider_type')
        if provider_type == 'openai':
            env_key = os.getenv('OPENAI_API_KEY')
            if env_key:
                return SecretStr(env_key)
        elif provider_type == 'anthropic':
            env_key = os.getenv('ANTHROPIC_API_KEY')
            if env_key:
                return SecretStr(env_key)
        elif provider_type == 'groq':
            env_key = os.getenv('GROQ_API_KEY')
            if env_key:
                return SecretStr(env_key)
        elif provider_type == 'huggingface_inference':
            env_key = os.getenv('HUGGINGFACE_API_KEY')
            if env_key:
                return SecretStr(env_key)
        elif provider_type == 'ollama_cloud':
            env_key = os.getenv('OLLAMA_API_KEY')
            if env_key:
                return SecretStr(env_key)

        return None

    @validator('api_base', pre=True, always=True)
    def set_default_api_base(cls, v, values):
        """Sets the default base URL according to the provider."""
        if v is not None:
            return v

        provider_type = values.get('provider_type')
        if provider_type == 'ollama_local':
            return 'http://localhost:11434'
        elif provider_type == 'ollama_cloud':
            return os.getenv('OLLAMA_CLOUD_URL', 'https://api.ollama.ai')
        elif provider_type == 'huggingface_inference':
            return 'https://api-inference.huggingface.co'

        return None

class LLMProvidersConfig(BaseModel):
    """Provider configuration for all agents."""

    # Per-agent configuration - SOTA optimized with Provider Diversification

    sentiment: AgentProviderConfig = Field(
        default_factory=lambda: AgentProviderConfig(
            provider_type="huggingface_inference",
            model_name="Qwen/Qwen2.5-72B-Instruct",  # SOTA for sentiment, better in multilingual analysis
            temperature=0.5,
            max_tokens=1500,
            timeout=90,
            supports_vision=False,
            fallback_provider_type="ollama_local",
            fallback_model_name="this-will-be-replaced-by-validator"
        )
    )

    technical: AgentProviderConfig = Field(
        default_factory=lambda: AgentProviderConfig(
            provider_type="huggingface_inference",
            model_name="meta-llama/Llama-3.3-70B-Instruct",  # Fast SOTA for technical analysis (163 tokens/s)
            temperature=0.3,
            max_tokens=2000,
            timeout=90,
            supports_vision=False,
            fallback_provider_type="ollama_local",
            fallback_model_name="this-will-be-replaced-by-validator"
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
            fallback_model_name="this-will-be-replaced-by-validator"
        )
    )

    qabba: AgentProviderConfig = Field(
        default_factory=lambda: AgentProviderConfig(
            provider_type="huggingface_inference",
            model_name="Qwen/Qwen2.5-72B-Instruct",  # Diversification: Qwen vs Llama for a different perspective
            temperature=0.4,
            max_tokens=800,
            timeout=60,
            supports_vision=False,
            fallback_provider_type="ollama_local",
            fallback_model_name="this-will-be-replaced-by-validator"
        )
    )

    # NOTE: Risk Manager DOES NOT USE LLM - It is pure mathematical logic and risk management rules
    # This configuration is here only as a legacy fallback, but should not be used
    risk_manager: AgentProviderConfig = Field(
        default_factory=lambda: AgentProviderConfig(
            provider_type="ollama_local",
            model_name="this-will-be-replaced-by-validator",
            temperature=0.15,
            max_tokens=1000,
            timeout=45,
            supports_vision=False
        )
    )

    # Decision Agent - Final synthesis with DeepSeek-V3.1-Terminus
    # Receives analysis from Technical, Sentiment, Visual and QABBA and makes the final decision
    decision: AgentProviderConfig = Field(
        default_factory=lambda: AgentProviderConfig(
            provider_type="huggingface_inference",
            model_name="deepseek-ai/DeepSeek-V3.1-Terminus",  # Better strategic reasoning
            temperature=0.2,  # Balance between creativity and determinism
            max_tokens=1500,  # Enough for comprehensive reasoning
            timeout=120,  # Extra time for complex synthesis
            supports_vision=False,
            fallback_provider_type="ollama_local",
            fallback_model_name="this-will-be-replaced-by-validator"
        )
    )

    def get_agent_config(self, agent_type: str) -> AgentProviderConfig:
        """Gets the provider configuration for a specific agent."""
        agent_configs = {
            'sentiment': self.sentiment,
            'technical': self.technical,
            'visual': self.visual,
            'qabba': self.qabba,
            'risk_manager': self.risk_manager,
            'decision': self.decision
        }

        config = agent_configs.get(agent_type)
        if config is None:
            raise ValueError(f"Unknown agent type: {agent_type}")

        return config

# Example configurations for different scenarios

# Configuration 1: All local with Ollama
EXAMPLE_ALL_LOCAL = LLMProvidersConfig(
    sentiment=AgentProviderConfig(
        provider_type="ollama_local",
    ),
    technical=AgentProviderConfig(
        provider_type="ollama_local",
    ),
    visual=AgentProviderConfig(
        provider_type="ollama_local",
        supports_vision=True
    ),
    qabba=AgentProviderConfig(
        provider_type="ollama_local",
    ),
    decision=AgentProviderConfig(
        provider_type="ollama_local",
    )
)

# Configuration 2: Mix of providers (production with fallbacks)
EXAMPLE_MIXED_PROVIDERS = LLMProvidersConfig(
    sentiment=AgentProviderConfig(
        provider_type="ollama_local",
        fallback_provider_type="groq",
        fallback_model_name="mixtral-8x7b-32768"
    ),
    technical=AgentProviderConfig(
        provider_type="groq",  # Ultra-fast for technical analysis
        model_name="mixtral-8x7b-32768",
        fallback_provider_type="ollama_local",
    ),
    visual=AgentProviderConfig(
        provider_type="openai",  # GPT-4 Vision for better chart analysis
        model_name="gpt-4-vision-preview",
        supports_vision=True,
        fallback_provider_type="ollama_local",
    ),
    qabba=AgentProviderConfig(
        provider_type="ollama_local",
    ),
    decision=AgentProviderConfig(
        provider_type="anthropic",  # Claude for critical decisions
        model_name="claude-3-sonnet-20240229",
        fallback_provider_type="ollama_local",
    )
)

# Configuration 3: HuggingFace MLX (optimized for Mac M-series)
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
        provider_type="ollama_local",  # MLX vision still in development
        supports_vision=True
    ),
    qabba=AgentProviderConfig(
        provider_type="huggingface_mlx",
        model_name="mlx-community/Hermes-2-Pro-Llama-3-8B-4bit",
    ),
    decision=AgentProviderConfig(
        provider_type="huggingface_mlx",
        model_name="mlx-community/Qwen2.5-7B-Instruct-4bit",
    )
)

# Configuration 4: All in the cloud (APIs)
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
        provider_type="openai",
        model_name="gpt-4-vision-preview",
        supports_vision=True
    ),
    qabba=AgentProviderConfig(
        provider_type="groq",
        model_name="mixtral-8x7b-32768",
    ),
    decision=AgentProviderConfig(
        provider_type="anthropic",
        model_name="claude-3-opus-20240229",
    )
)

if __name__ == "__main__":
    # Configuration test
    print("=== Example: All Local Configuration ===")
    config = EXAMPLE_ALL_LOCAL
    print(f"Sentiment: {config.sentiment.provider_type} - {config.sentiment.model_name}")
    print(f"Technical: {config.technical.provider_type} - {config.technical.model_name}")
    print(f"Visual: {config.visual.provider_type} - {config.visual.model_name} (Vision: {config.visual.supports_vision})")
    print(f"QABBA: {config.qabba.provider_type} - {config.qabba.model_name}")
    print(f"Decision: {config.decision.provider_type} - {config.decision.model_name}")
    
    print("\n=== Example: Mixed Providers Configuration ===")
    config = EXAMPLE_MIXED_PROVIDERS
    for agent_type in ['sentiment', 'technical', 'visual', 'qabba', 'decision']:
        agent_config = config.get_agent_config(agent_type)
        print(f"{agent_type.capitalize()}: {agent_config.provider_type} - {agent_config.model_name}")
        if agent_config.fallback_provider_type:
            print(f"  └─ Fallback: {agent_config.fallback_provider_type} - {agent_config.fallback_model_name}")
