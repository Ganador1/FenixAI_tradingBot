"""
Inference package for Fenix Trading Bot
Exposes provider registry and optional clients without forcing heavy imports.
"""

# Exponer registry y setup_default_providers siempre
from .provider_registry import registry as ProviderRegistry
from .provider_registry import setup_default_providers

# Proveedores opcionales: importar de forma segura
try:
    from .providers.hf_inference import HFInferenceProvider  # type: ignore
except Exception:
    HFInferenceProvider = None  # type: ignore

try:
    from .providers.mlx_provider import MLXProvider  # type: ignore
except Exception:
    MLXProvider = None  # type: ignore

try:
    from .providers.ollama_provider import OllamaProvider  # type: ignore
except Exception:
    OllamaProvider = None  # type: ignore

# Cliente HF opcional: evitar fallo si falta huggingface_hub
try:
    from .huggingface_client import HuggingFaceInferenceClient  # type: ignore
except Exception:
    HuggingFaceInferenceClient = None  # type: ignore

# Evitar importar UnifiedInferenceClient aquí para prevenir ciclos
# Importar UnifiedInferenceClient directamente desde su módulo cuando se necesite.

# Construir __all__ solo con símbolos disponibles
__all__ = []
if "ProviderRegistry" in globals():
    __all__.append("ProviderRegistry")
if "setup_default_providers" in globals():
    __all__.append("setup_default_providers")
if HFInferenceProvider is not None:
    __all__.append("HFInferenceProvider")
if MLXProvider is not None:
    __all__.append("MLXProvider")
if OllamaProvider is not None:
    __all__.append("OllamaProvider")
if HuggingFaceInferenceClient is not None:
    __all__.append("HuggingFaceInferenceClient")
