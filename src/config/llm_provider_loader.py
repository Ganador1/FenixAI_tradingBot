# config/llm_provider_loader.py
"""
Loader para configuración de providers LLM.
Carga configuración desde YAML y crea instancias de providers.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

import yaml

from .llm_provider_config import EXAMPLE_ALL_LOCAL, AgentProviderConfig, LLMProvidersConfig

# Imports from llm_providers module
# Note: llm_providers module is not currently available in this environment.
# Setting these to None to avoid ImportErrors and warnings.
BaseLLMProvider = None
create_provider = None
ProviderConfig = None

logger = logging.getLogger(__name__)

# Path al archivo de configuración
CONFIG_DIR = Path(__file__).parent
_ENV_CONFIG_PATH = os.getenv("FENIX_LLM_CONFIG_PATH") or os.getenv("LLM_PROVIDER_CONFIG_PATH")
if _ENV_CONFIG_PATH:
    PROVIDERS_CONFIG_PATH = Path(_ENV_CONFIG_PATH).expanduser()
else:
    # Prefer project-root `config/llm_providers.yaml` if it exists to support repo-level config
    ROOT_CONFIG_PATH = Path.cwd() / "config" / "llm_providers.yaml"
    PROVIDERS_CONFIG_PATH = (
        ROOT_CONFIG_PATH if ROOT_CONFIG_PATH.exists() else CONFIG_DIR / "llm_providers.yaml"
    )


class LLMProviderLoader:
    """Carga y gestiona la configuración de providers LLM."""

    def __init__(
        self,
        config_path: Path | None = None,
        active_profile: str | None = None,
    ):
        """
        Inicializa el loader.

        Args:
            config_path: Path al archivo YAML de configuración.
                        Si es None, usa el path por defecto.
        """
        self.config_path = config_path or PROVIDERS_CONFIG_PATH
        self._config: LLMProvidersConfig | None = None
        self._active_profile: str = "all_local"
        # Allow overriding the profile via environment variable `LLM_PROFILE`
        env_profile = os.getenv("LLM_PROFILE")
        self._forced_profile: str | None = active_profile or env_profile
        self._provider_cache: dict[str, BaseLLMProvider] = {}

        # Cargar configuración
        self._load_config()

    def _load_config(self):
        """Carga la configuración desde el archivo YAML."""
        try:
            if not self.config_path.exists():
                logger.warning(
                    f"Configuration file not found at {self.config_path}. "
                    f"Using default ALL_LOCAL configuration."
                )
                self._config = EXAMPLE_ALL_LOCAL
                self._active_profile = "all_local"
                return

            # If config_path is None, pick the precomputed PROVIDERS_CONFIG_PATH
            self.config_path = self.config_path or PROVIDERS_CONFIG_PATH
            # Check for duplicate config in src/config and repo-root config/ directory
            src_path = CONFIG_DIR / "llm_providers.yaml"
            root_path = Path.cwd() / "config" / "llm_providers.yaml"
            try:
                if src_path.exists() and root_path.exists():
                    with (
                        open(src_path, encoding="utf-8") as f1,
                        open(root_path, encoding="utf-8") as f2,
                    ):
                        if f1.read() != f2.read():
                            logger.warning(
                                "Found two different llm_providers.yaml files: 'src/config/llm_providers.yaml' and "
                                "'/config/llm_providers.yaml'. Using %s. Consider unifying them to avoid inconsistent provider behavior.",
                                str(self.config_path),
                            )
            except Exception:
                # Do not fail on diagnostic checks
                pass
            logger.debug(f"LLM Provider config path: {self.config_path}")
            with open(self.config_path, encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f)

            if not yaml_data:
                logger.warning("Empty configuration file. Using default ALL_LOCAL.")
                self._config = EXAMPLE_ALL_LOCAL
                return

            # Obtener perfil activo
            requested_profile = self._forced_profile or yaml_data.get("active_profile", "all_local")
            self._active_profile = requested_profile
            logger.info(f"Loading LLM provider profile: {self._active_profile}")

            # Obtener configuración del perfil
            profile_data = yaml_data.get(self._active_profile)

            if not profile_data:
                logger.error(
                    f"Profile '{self._active_profile}' not found in config. "
                    f"Using default ALL_LOCAL."
                )
                self._config = EXAMPLE_ALL_LOCAL
                return

            # Crear configuración desde YAML
            self._config = self._parse_profile(profile_data)
            logger.info("Successfully loaded provider configuration: %s", self._active_profile)

        except Exception:
            logger.exception("Error loading provider configuration")
            logger.warning("Falling back to default ALL_LOCAL configuration.")
            self._config = EXAMPLE_ALL_LOCAL

    def _parse_profile(self, profile_data: dict) -> LLMProvidersConfig:
        """
        Parsea datos de perfil desde YAML a LLMProvidersConfig.

        Args:
            profile_data: Diccionario con la configuración del perfil

        Returns:
            LLMProvidersConfig instanciada
        """
        agent_configs = {}

        for agent_type in ["sentiment", "technical", "visual", "qabba", "risk_manager", "decision"]:
            agent_data = profile_data.get(agent_type, {})
            if agent_data:
                agent_configs[agent_type] = AgentProviderConfig(**agent_data)

        return LLMProvidersConfig(**agent_configs)

    def get_config(self) -> LLMProvidersConfig:
        """
        Obtiene la configuración cargada.

        Returns:
            LLMProvidersConfig con la configuración actual
        """
        return self._config

    @property
    def active_profile(self) -> str:
        return self._active_profile

    def get_agent_config(self, agent_type: str) -> AgentProviderConfig:
        """
        Obtiene la configuración de provider para un agente específico.

        Args:
            agent_type: Tipo de agente ('sentiment', 'technical', 'visual', 'qabba', 'decision')

        Returns:
            AgentProviderConfig para el agente
        """
        return self._config.get_agent_config(agent_type)

    def _create_provider_config(self, agent_config: AgentProviderConfig) -> ProviderConfig | None:
        """Crea ProviderConfig desde AgentProviderConfig."""
        if ProviderConfig is None:
            return None

        return ProviderConfig(
            model_name=agent_config.model_name,
            api_key=agent_config.api_key.get_secret_value() if agent_config.api_key else None,
            api_base=agent_config.api_base,
            temperature=agent_config.temperature,
            max_tokens=agent_config.max_tokens,
            timeout=agent_config.timeout,
            extra_params=agent_config.extra_config,
        )

    def _attempt_provider_creation(
        self, agent_type: str, agent_config: AgentProviderConfig, use_cache: bool
    ) -> BaseLLMProvider | None:
        """Intenta crear provider con la configuración dada."""
        provider_config = self._create_provider_config(agent_config)
        if not provider_config:
            return None

        provider = create_provider(
            provider_type=agent_config.provider_type,
            model_name=provider_config.model_name,
            temperature=provider_config.temperature,
            max_tokens=provider_config.max_tokens,
            api_key=provider_config.api_key,
            api_base=provider_config.api_base,
            extra_config=provider_config.extra_config,
            timeout=provider_config.timeout,
        )

        if use_cache and provider:
            self._provider_cache[agent_type] = provider

        logger.info(
            f"Created {agent_config.provider_type} provider for {agent_type} "
            f"with model {agent_config.model_name}"
        )

        return provider

    def create_provider_for_agent(
        self, agent_type: str, use_cache: bool = True
    ) -> BaseLLMProvider | None:
        """
        Crea una instancia de provider para un agente específico.

        Args:
            agent_type: Tipo de agente
            use_cache: Si True, usa provider en caché si existe

        Returns:
            Instancia de BaseLLMProvider o None si falla
        """
        if create_provider is None:
            logger.error(
                "llm_providers module not available. "
                "Make sure it's installed and imported correctly."
            )
            return None

        if use_cache and agent_type in self._provider_cache:
            logger.debug(f"Using cached provider for {agent_type}")
            return self._provider_cache[agent_type]

        try:
            agent_config = self.get_agent_config(agent_type)
            return self._attempt_provider_creation(agent_type, agent_config, use_cache)

        except Exception as e:
            logger.error(f"Error creating provider for {agent_type}: {e}", exc_info=True)

            agent_config = self.get_agent_config(agent_type)
            if agent_config.fallback_provider_type:
                logger.info(f"Attempting fallback provider: {agent_config.fallback_provider_type}")
                return self._create_fallback_provider(agent_type, agent_config)

            return None

    def _create_fallback_provider(
        self, agent_type: str, agent_config: AgentProviderConfig
    ) -> BaseLLMProvider | None:
        """
        Crea provider de fallback cuando el principal falla.

        Args:
            agent_type: Tipo de agente
            agent_config: Configuración del agente

        Returns:
            Provider de fallback o None
        """
        try:
            if not agent_config.fallback_provider_type:
                return None

                # Build arguments similar to _attempt_provider_creation to ensure API parity
                provider = create_provider(
                    provider_type=agent_config.fallback_provider_type,
                    model_name=agent_config.fallback_model_name or agent_config.model_name,
                    temperature=agent_config.temperature,
                    max_tokens=agent_config.max_tokens,
                    api_key=agent_config.api_key.get_secret_value()
                    if agent_config.api_key
                    else None,
                    api_base=agent_config.api_base,
                    extra_config=agent_config.extra_config,
                    timeout=agent_config.timeout,
                )

            logger.info(
                f"Successfully created fallback provider {agent_config.fallback_provider_type} "
                f"for {agent_type}"
            )

            return provider

        except Exception as e:
            logger.error(f"Error creating fallback provider for {agent_type}: {e}", exc_info=True)
            return None

    def reload_config(self):
        """Recarga la configuración desde el archivo."""
        logger.info("Reloading provider configuration...")
        self._provider_cache.clear()
        self._load_config()

    def clear_cache(self):
        """Limpia el caché de providers."""
        logger.info("Clearing provider cache...")
        self._provider_cache.clear()

    @property
    def active_profile(self) -> str:
        """Obtiene el nombre del perfil activo."""
        return self._active_profile


# Singleton global
_loader_instance: LLMProviderLoader | None = None


@lru_cache(maxsize=1)
def get_provider_loader() -> LLMProviderLoader:
    """
    Obtiene la instancia singleton del loader.

    Returns:
        LLMProviderLoader singleton
    """
    global _loader_instance
    if _loader_instance is None:
        _loader_instance = LLMProviderLoader()
    return _loader_instance


def get_provider_for_agent(agent_type: str) -> BaseLLMProvider | None:
    """
    Función de conveniencia para obtener un provider para un agente.

    Args:
        agent_type: Tipo de agente

    Returns:
        Provider instanciado o None
    """
    loader = get_provider_loader()
    return loader.create_provider_for_agent(agent_type)


def get_agent_provider_config(agent_type: str) -> AgentProviderConfig:
    """
    Función de conveniencia para obtener configuración de un agente.

    Args:
        agent_type: Tipo de agente

    Returns:
        AgentProviderConfig
    """
    loader = get_provider_loader()
    return loader.get_agent_config(agent_type)


if __name__ == "__main__":
    # Test del loader
    logging.basicConfig(level=logging.INFO)

    print("=== Testing LLM Provider Loader ===\n")

    loader = get_provider_loader()
    print(f"Active profile: {loader.active_profile}")

    print("\n=== Agent Configurations ===")
    for agent_type in ["sentiment", "technical", "visual", "qabba", "decision"]:
        config = loader.get_agent_config(agent_type)
        print(f"\n{agent_type.upper()}:")
        print(f"  Provider: {config.provider_type}")
        print(f"  Model: {config.model_name}")
        print(f"  Temperature: {config.temperature}")
        print(f"  Max Tokens: {config.max_tokens}")
        print(f"  Supports Vision: {config.supports_vision}")
        if config.fallback_provider_type:
            print(f"  Fallback: {config.fallback_provider_type} ({config.fallback_model_name})")

    print("\n=== Testing Provider Creation ===")
    if create_provider is not None:
        for agent_type in ["sentiment", "technical"]:
            print(f"\nCreating provider for {agent_type}...")
            provider = loader.create_provider_for_agent(agent_type)
            if provider:
                print(f"  ✅ Success: {provider.__class__.__name__}")
                print(f"  Available: {provider.is_available()}")
            else:
                print("  ❌ Failed to create provider")
    else:
        print("⚠️ llm_providers module not available, skipping provider creation test")
