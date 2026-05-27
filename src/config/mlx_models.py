# config/mlx_models.py
"""
MLX Model Configuration for FenixAI Trading Bot
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Optimized model configurations for specific agent types
MODEL_CONFIGS: dict[str, dict[str, Any]] = {
    # Mathematical/Technical Analysis Models
    "lmstudio-community/gemma-3n-E4B-it-MLX-4bit": {
        "path": "lmstudio-community/gemma-3n-E4B-it-MLX-4bit",
        "model_type": "language",
        "temperature": 0.1,
        "top_p": 0.85,
        "repetition_penalty": 1.05,
        "repetition_context_size": 30,  # Low temperature for precise mathematical analysis
        "top_p": 0.85,  # Nucleus sampling
        "repetition_penalty": 1.05,  # Evitar repeticiones
        "repetition_context_size": 30,
        "max_tokens": 2048,
        "description": "Specialized in mathematical reasoning and technical analysis",
    },
    "mlx-community/gemma-3-4b-it-4bit": {
        "path": "mlx-community/gemma-3-4b-it-4bit",
        "model_type": "language",
        "temperature": 0.1,
        "top_p": 0.85,
        "repetition_penalty": 1.05,
        "repetition_context_size": 30,
        "max_tokens": 2048,
        "description": "Gemma 3 4B MLX model for technical analysis",
    },
    "mlx-community/gemma-2-9b-it-4bit": {
        "path": "mlx-community/gemma-2-9b-it-4bit",
        "model_type": "language",
        "temperature": 0.1,
        "top_p": 0.85,
        "repetition_penalty": 1.05,
        "repetition_context_size": 30,
        "max_tokens": 2048,
        "description": "Stable MLX Gemma 2 model for technical analysis",
    },
    "google/gemma-2-9b-it": {
        "path": "google/gemma-2-9b-it",
        "model_type": "language",
        "temperature": 0.1,
        "top_p": 0.85,
        "repetition_penalty": 1.05,
        "repetition_context_size": 30,
        "max_tokens": 2048,
        "description": "Official Google Gemma 2 model for technical analysis",
    },
    "mlx-community/gemma-3n-E4B-it-bf16": {
        "path": "mlx-community/gemma-3n-E4B-it-bf16",
        "model_type": "language",
        "temperature": 0.1,
        "top_p": 0.85,
        "repetition_penalty": 1.05,
        "repetition_context_size": 30,
        "max_tokens": 2048,
        "description": "Alternative bf16 version for technical analysis",
    },
    # Visual Analysis Models - Qwen2-VL model (backup option)
    "mlx-community/Qwen2-VL-2B-Instruct-4bit": {
        "path": "mlx-community/Qwen2-VL-2B-Instruct-4bit",
        "model_type": "multimodal",
        "temperature": 0.2,  # Slightly higher for creative visual interpretation
        "top_p": 0.9,  # Nucleus sampling
        "repetition_penalty": 1.1,  # Evitar repeticiones
        "repetition_context_size": 25,
        "max_tokens": 1536,
        "description": "Qwen2-VL model optimized for visual pattern recognition and chart analysis",
    },
    # Primary Visual Analysis Model - Gemma 3 model
    "mlx-community/gemma-3-4b-it-8bit": {
        "path": "mlx-community/gemma-3-4b-it-8bit",
        "model_type": "multimodal",
        "temperature": 0.1,
        "top_p": 0.85,
        "repetition_penalty": 1.05,
        "repetition_context_size": 30,
        "max_tokens": 3536,
        "description": "Gemma 3 model for visual analysis and chart interpretation",
    },
    # Sentiment Analysis Models (using stable working model)
    "mlx-community/Qwen2.5-3B-Instruct-4bit": {
        "path": "mlx-community/Qwen2.5-3B-Instruct-4bit",
        "model_type": "language",
        "temperature": 0.3,  # Higher temperature for nuanced sentiment interpretation
        "top_p": 0.95,  # Más diversidad para análisis de sentimiento
        "repetition_penalty": 1.15,  # Evitar repeticiones
        "repetition_context_size": 20,
        "max_tokens": 1024,
        "description": "Stable model for sentiment analysis and text interpretation",
    },
    # Decision Making Models
    "lmstudio-community/DeepSeek-R1-0528-Qwen3-8B-MLX-4bit": {
        "path": "lmstudio-community/DeepSeek-R1-0528-Qwen3-8B-MLX-4bit",
        "model_type": "language",
        "temperature": 0.1,
        "top_p": 0.85,
        "repetition_penalty": 1.05,
        "repetition_context_size": 30,  # Low temperature for consistent decision making
        "max_tokens": 2048,
        "description": "Advanced reasoning for complex decision making",
    },
    # Decision Making Models (Advanced)
    "ssweens/Kimi-VL-A3B-Thinking-2506-mlx-4bit": {
        "path": "ssweens/Kimi-VL-A3B-Thinking-2506-mlx-4bit",
        "model_type": "multimodal",
        "temperature": 0.1,
        "top_p": 0.85,
        "repetition_penalty": 1.05,
        "repetition_context_size": 30,
        "max_tokens": 2048,
        "description": "Advanced thinking model for complex decision making and visual analysis",
    },
    # Legacy/Backup Models
    "mlx-community/gemma-3n-E4B-it-lm-4bit": {
        "path": "mlx-community/gemma-3n-E4B-it-lm-4bit",
        "model_type": "multimodal",
        "temperature": 0.3,
        "max_tokens": 1024,
        "description": "Legacy multimodal model for backup",
    },
}

# Optimized agent-specific model mappings for latest models
AGENT_MODEL_MAPPING = {
    # Technical Analysis Agents - Use latest math model
    "technical": "mlx-community/gemma-3-4b-it-4bit",
    "qabba": "mlx-community/gemma-3-4b-it-4bit",
    # Visual Analysis Agent - Use Qwen2-VL model for better compatibility
    "visual": "mlx-community/Qwen2-VL-2B-Instruct-4bit",
    # Sentiment Analysis Agent - Use latest small model
    "sentiment": "mlx-community/Qwen2.5-3B-Instruct-4bit",
    # Decision Making Agents - Use latest thinking model
    "decision": "ssweens/Kimi-VL-A3B-Thinking-2506-mlx-4bit",
    "consensus": "ssweens/Kimi-VL-A3B-Thinking-2506-mlx-4bit",
    "risk": "ssweens/Kimi-VL-A3B-Thinking-2506-mlx-4bit",
    "guardian": "ssweens/Kimi-VL-A3B-Thinking-2506-mlx-4bit",
}


def get_agent_model(agent_type: str) -> str:
    """Get the optimal model for an agent type"""
    return AGENT_MODEL_MAPPING.get(agent_type, "mlx-community/gemma-3n-E4B-it-lm-4bit")


def get_model_info(agent_type: str) -> dict[str, Any]:
    """Get complete model information for an agent"""
    model_name = get_agent_model(agent_type)
    config = MODEL_CONFIGS.get(model_name, {})

    # Si el agente es visual, prepara el path local y prompt para integración directa con MLX-VLM
    if agent_type == "visual":
        return {
            "model_name": model_name,
            "model_type": config.get("model_type", "multimodal"),
            "temperature": config.get("temperature", 0.2),
            "max_tokens": config.get("max_tokens", 1536),
            "image_path": "screenshots/latest_screenshot.png",
        }
    return {
        "model_name": model_name,
        "model_type": config.get("model_type", "unknown"),
        "temperature": config.get("temperature", 0.3),
        "max_tokens": config.get("max_tokens", 2048),
    }
