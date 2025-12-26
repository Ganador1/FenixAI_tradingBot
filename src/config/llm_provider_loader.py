# config/llm_provider_loader.py
"""
Loader for LLM provider configuration.
Loads configuration from YAML and creates provider instances.
"""
from __future__ import annotations

import yaml
import logging
from pathlib import Path
import os
from typing import Optional, Dict
from functools import lru_cache

from .llm_provider_config import (
    LLMProvidersConfig, 
    AgentProviderConfig,
    EXAMPLE_ALL_LOCAL
)

# Imports from llm_providers module
# Note: llm_providers module is not currently available in this environment.
# Setting these to None to avoid ImportErrors and warnings.
BaseLLMProvider = None
create_provider = None
ProviderConfig = None

logger = logging.getLogger(__name__)

# Path to the configuration file
CONFIG_DIR = Path(__file__).parent
# Prefer project-root `config/llm_providers.yaml` if it exists to support repo-level config
ROOT_CONFIG_PATH = Path.cwd() / "config" / "llm_providers.yaml"
PROVIDERS_CONFIG_PATH = ROOT_CONFIG_PATH if ROOT_CONFIG_PATH.exists() else CONFIG_DIR / "llm_providers.yaml"

class LLMProviderLoader:
    """Loads and manages LLM provider configuration."""
    
    def __init__(
        self,
        config_path: Optional[Path] = None,
        active_profile: Optional[str] = None,
    ):
        """
        Initializes the loader.
        
        Args:
            config_path: Path to the YAML configuration file.
                        If None, uses the default path.
        """
        self.config_path = config_path or PROVIDERS_CONFIG_PATH
        self._config: Optional[LLMProvidersConfig] = None
        self._active_profile: str = "all_local"
        # Allow overriding the profile via environment variable `LLM_PROFILE`
        env_profile = os.getenv("LLM_PROFILE")
        self._forced_profile: Optional[str] = active_profile or env_profile
        self._provider_cache: Dict[str, BaseLLMProvider] = {}
        
        # Load configuration
        self._load_config()
    
    def _load_config(self):
        """Loads the configuration from the YAML file."""
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
                    with open(src_path, 'r', encoding='utf-8') as f1, open(root_path, 'r', encoding='utf-8') as f2:
                        if f1.read() != f2.read():
                            logger.warning(
                                "Found two different llm_providers.yaml files: 'src/config/llm_providers.yaml' and "
                                "'/config/llm_providers.yaml'. Using %s. Consider unifying them to avoid inconsistent provider behavior.",
                                str(self.config_path)
                            )
            except Exception:
                # Do not fail on diagnostic checks
                pass
            logger.debug(f"LLM Provider config path: {self.config_path}")
            with open(self.config_path, 'r', encoding='utf-8') as f:
                yaml_data = yaml.safe_load(f)
            
            if not yaml_data:
                logger.warning("Empty configuration file. Using default ALL_LOCAL.")
                self._config = EXAMPLE_ALL_LOCAL
                return
            
            # Get active profile
            requested_profile = self._forced_profile or yaml_data.get('active_profile', 'all_local')
            self._active_profile = requested_profile
            logger.info(f"Loading LLM provider profile: {self._active_profile}")
            
            # Get profile configuration
            profile_data = yaml_data.get(self._active_profile)
            
            if not profile_data:
                logger.error(
                    f"Profile '{self._active_profile}' not found in config. "
                    f"Using default ALL_LOCAL."
                )
                self._config = EXAMPLE_ALL_LOCAL
                return
            
            # Create configuration from YAML
            self._config = self._parse_profile(profile_data)
            logger.info("Successfully loaded provider configuration: %s", self._active_profile)
            
        except Exception:
            logger.exception("Error loading provider configuration")
            logger.warning("Falling back to default ALL_LOCAL configuration.")
            self._config = EXAMPLE_ALL_LOCAL
    
    def _parse_profile(self, profile_data: Dict) -> LLMProvidersConfig:
        """
        Parses profile data from YAML to LLMProvidersConfig.
        
        Args:
            profile_data: Dictionary with the profile configuration
            
        Returns:
            Instantiated LLMProvidersConfig
        """
        agent_configs = {}
        
        for agent_type in ['sentiment', 'technical', 'visual', 'qabba', 'risk_manager', 'decision']:
            agent_data = profile_data.get(agent_type, {})
            if agent_data:
                agent_configs[agent_type] = AgentProviderConfig(**agent_data)
        
        return LLMProvidersConfig(**agent_configs)
    
    def get_config(self) -> LLMProvidersConfig:
        """
        Gets the loaded configuration.
        
        Returns:
            LLMProvidersConfig with the current configuration
        """
        return self._config

    @property
    def active_profile(self) -> str:
        return self._active_profile
    
    def get_agent_config(self, agent_type: str) -> AgentProviderConfig:
        """
        Gets the provider configuration for a specific agent.
        
        Args:
            agent_type: Type of agent ('sentiment', 'technical', 'visual', 'qabba', 'decision')
            
        Returns:
            AgentProviderConfig for the agent
        """
        return self._config.get_agent_config(agent_type)
    
    def _create_provider_config(self, agent_config: AgentProviderConfig) -> Optional['ProviderConfig']:
        """Creates ProviderConfig from AgentProviderConfig."""
        if ProviderConfig is None:
            return None
            
        return ProviderConfig(
            model_name=agent_config.model_name,
            api_key=agent_config.api_key.get_secret_value() if agent_config.api_key else None,
            api_base=agent_config.api_base,
            temperature=agent_config.temperature,
            max_tokens=agent_config.max_tokens,
            timeout=agent_config.timeout,
            extra_params=agent_config.extra_config
        )
    
    def _attempt_provider_creation(self, agent_type: str, agent_config: AgentProviderConfig, use_cache: bool) -> Optional[BaseLLMProvider]:
        """Attempts to create a provider with the given configuration."""
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
        self, 
        agent_type: str,
        use_cache: bool = True
    ) -> Optional[BaseLLMProvider]:
        """
        Creates a provider instance for a specific agent.
        
        Args:
            agent_type: Type of agent
            use_cache: If True, uses a cached provider if it exists
            
        Returns:
            BaseLLMProvider instance or None if it fails
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
            logger.error(
                f"Error creating provider for {agent_type}: {e}",
                exc_info=True
            )
            
            agent_config = self.get_agent_config(agent_type)
            if agent_config.fallback_provider_type:
                logger.info(
                    f"Attempting fallback provider: {agent_config.fallback_provider_type}"
                )
                return self._create_fallback_provider(agent_type, agent_config)
            
            return None
    
    def _create_fallback_provider(
        self,
        agent_type: str,
        agent_config: AgentProviderConfig
    ) -> Optional[BaseLLMProvider]:
        """
        Creates a fallback provider when the primary one fails.
        
        Args:
            agent_type: Type of agent
            agent_config: Agent configuration
            
        Returns:
            Fallback provider or None
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
                    api_key=agent_config.api_key.get_secret_value() if agent_config.api_key else None,
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
            logger.error(
                f"Error creating fallback provider for {agent_type}: {e}",
                exc_info=True
            )
            return None
    
    def reload_config(self):
        """Reloads the configuration from the file."""
        logger.info("Reloading provider configuration...")
        self._provider_cache.clear()
        self._load_config()
    
    def clear_cache(self):
        """Clears the provider cache."""
        logger.info("Clearing provider cache...")
        self._provider_cache.clear()
    
    @property
    def active_profile(self) -> str:
        """Gets the name of the active profile."""
        return self._active_profile


# Global singleton
_loader_instance: Optional[LLMProviderLoader] = None

@lru_cache(maxsize=1)
def get_provider_loader() -> LLMProviderLoader:
    """
    Gets the singleton instance of the loader.
    
    Returns:
        LLMProviderLoader singleton
    """
    global _loader_instance
    if _loader_instance is None:
        _loader_instance = LLMProviderLoader()
    return _loader_instance

def get_provider_for_agent(agent_type: str) -> Optional[BaseLLMProvider]:
    """
    Convenience function to get a provider for an agent.
    
    Args:
        agent_type: Type of agent
        
    Returns:
        Instantiated provider or None
    """
    loader = get_provider_loader()
    return loader.create_provider_for_agent(agent_type)

def get_agent_provider_config(agent_type: str) -> AgentProviderConfig:
    """
    Convenience function to get the configuration for an agent.
    
    Args:
        agent_type: Type of agent
        
    Returns:
        AgentProviderConfig
    """
    loader = get_provider_loader()
    return loader.get_agent_config(agent_type)


if __name__ == "__main__":
    # Loader test
    logging.basicConfig(level=logging.INFO)
    
    print("=== Testing LLM Provider Loader ===\n")
    
    loader = get_provider_loader()
    print(f"Active profile: {loader.active_profile}")
    
    print("\n=== Agent Configurations ===")
    for agent_type in ['sentiment', 'technical', 'visual', 'qabba', 'decision']:
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
        for agent_type in ['sentiment', 'technical']:
            print(f"\nCreating provider for {agent_type}...")
            provider = loader.create_provider_for_agent(agent_type)
            if provider:
                print(f"  ✅ Success: {provider.__class__.__name__}")
                print(f"  Available: {provider.is_available()}")
            else:
                print(f"  ❌ Failed to create provider")
    else:
        print("⚠️ llm_providers module not available, skipping provider creation test")
