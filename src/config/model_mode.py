"""
Model Mode Configuration
========================

Flag simple para alternar entre modelos cloud (HuggingFace/Novita) y locales (MLX).

Variables de entorno:
- USE_CLOUD_MODELS=true/false (default: false)
- FORCE_MLX_LOCAL=true/false (default: false)

Uso:
    from src.config.model_mode import should_use_cloud_models

    if should_use_cloud_models():
        # Usar HuggingFace API con modelos grandes
    else:
        # Usar MLX local con modelos pequeños
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Variable global para controlar el modo
_USE_CLOUD_MODELS: bool = None


def should_use_cloud_models() -> bool:
    """
    Determina si se deben usar modelos cloud o locales.

    Prioridad:
    1. FORCE_MLX_LOCAL=true → Siempre local (override máximo)
    2. USE_CLOUD_MODELS=true → Cloud si API disponible
    3. Default → Local (más rápido, no requiere API key)

    Returns:
        bool: True para usar cloud, False para local
    """
    global _USE_CLOUD_MODELS

    # Si ya fue determinado, devolver valor cacheado
    if _USE_CLOUD_MODELS is not None:
        return _USE_CLOUD_MODELS

    # Check override forzado a local
    force_local = os.getenv("FORCE_MLX_LOCAL", "false").lower() in ["true", "1", "yes"]
    if force_local:
        logger.info("🔧 FORCE_MLX_LOCAL=true → Usando modelos locales MLX")
        _USE_CLOUD_MODELS = False
        return False

    # Check si se solicita cloud
    use_cloud = os.getenv("USE_CLOUD_MODELS", "false").lower() in ["true", "1", "yes"]

    if use_cloud:
        # Verificar que haya API key disponible
        api_key = (
            os.getenv("HUGGINGFACE_API_KEY") or os.getenv("HF_TOKEN") or os.getenv("HF_API_KEY")
        )
        if api_key and api_key.strip():
            logger.info("☁️  USE_CLOUD_MODELS=true + API key disponible → Usando modelos cloud")
            logger.info("   Modelos grandes: Qwen 2.5-72B, DeepSeek V3, FinBERT")
            _USE_CLOUD_MODELS = True
            return True
        else:
            logger.warning("⚠️  USE_CLOUD_MODELS=true pero no hay API key → Fallback a local")
            _USE_CLOUD_MODELS = False
            return False

    # Default: usar local
    logger.info("🏠 Modo default → Usando modelos locales MLX")
    logger.info("   Modelos pequeños: Qwen 2.5-3B, Gemma-3-4B")
    _USE_CLOUD_MODELS = False
    return False


def get_model_mode_info() -> dict[str, Any]:
    """
    Obtiene información detallada sobre el modo de modelos activo.

    Returns:
        Dict con información del modo activo
    """
    using_cloud = should_use_cloud_models()

    if using_cloud:
        return {
            "mode": "cloud",
            "provider": "HuggingFace/Novita API",
            "models": {
                "technical": "Qwen/Qwen2.5-72B-Instruct",
                "sentiment": "yiyanghkust/finbert-tone",
                "visual": "Qwen/Qwen2.5-VL-72B-Instruct",
                "qabba": "deepseek-ai/DeepSeek-V3",
                "decision": "deepseek-ai/DeepSeek-V3",
            },
            "advantages": [
                "Modelos SOTA (72B parameters)",
                "Mejor reasoning y math",
                "No consume RAM local",
                "Parallelización real",
            ],
            "disadvantages": [
                "Latencia de red (~3-10s)",
                "Requiere API key",
                "Costo por uso",
                "Depende de disponibilidad API",
            ],
        }
    else:
        return {
            "mode": "local",
            "provider": "MLX (Apple Silicon)",
            "models": {
                "technical": "mlx-community/gemma-3-4b-it-4bit",
                "sentiment": "mlx-community/Qwen2.5-3B-Instruct-4bit",
                "visual": "mlx-community/gemma-3-4b-it-8bit",
                "qabba": "mlx-community/gemma-3-4b-it-4bit",
                "decision": "mlx-community/gemma-3-4b-it-4bit",
            },
            "advantages": [
                "Ultra baja latencia (<1s)",
                "Sin costos adicionales",
                "Funciona offline",
                "No requiere API keys",
            ],
            "disadvantages": [
                "Modelos pequeños (3-4B)",
                "Consume RAM local (~4-8GB)",
                "Menor capacidad de reasoning",
                "Respuestas menos sofisticadas",
            ],
        }


def reset_model_mode_cache():
    """Resetea el cache del modo de modelos (útil para testing)"""
    global _USE_CLOUD_MODELS
    _USE_CLOUD_MODELS = None


def print_model_mode_banner():
    """Imprime un banner con información del modo activo"""
    info = get_model_mode_info()

    print("\n" + "=" * 80)
    print(f"🤖 MODELO MODE: {info['mode'].upper()}")
    print("=" * 80)
    print(f"Provider: {info['provider']}")
    print("\nModelos activos:")
    for agent, model in info["models"].items():
        print(f"  • {agent:12s}: {model}")

    print("\n✅ Ventajas:")
    for adv in info["advantages"]:
        print(f"  • {adv}")

    print("\n⚠️  Consideraciones:")
    for dis in info["disadvantages"]:
        print(f"  • {dis}")

    print("=" * 80)

    if info["mode"] == "local":
        print("\n💡 Tip: Para usar modelos cloud grandes, configura:")
        print("   export USE_CLOUD_MODELS=true")
        print("   export HUGGINGFACE_API_KEY='tu_api_key'")
    else:
        print("\n💡 Tip: Para volver a modelos locales rápidos:")
        print("   export FORCE_MLX_LOCAL=true")

    print()


if __name__ == "__main__":
    # Test del sistema
    print("🧪 Testing Model Mode System\n")

    print("Test 1: Modo default")
    print(f"should_use_cloud_models() = {should_use_cloud_models()}")
    print_model_mode_banner()

    print("\nTest 2: Info del modo")
    import json

    print(json.dumps(get_model_mode_info(), indent=2))
