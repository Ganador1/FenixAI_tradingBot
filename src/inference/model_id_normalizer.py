"""
Utilities to normalize model IDs across providers.
Converts between Ollama-style ids (like 'qwen3-vl:8b') and forms expected by MLX/HF providers.

This is intentionally simple and extensible via mappings in src/config/model_id_mappings.json.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_MAPPING_PATH = Path(__file__).resolve().parents[1] / "config" / "model_id_mappings.json"


def _load_mappings(path: Path | None = None) -> dict:
    p = path or DEFAULT_MAPPING_PATH
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def normalize_model_id_for_provider(model_id: str, target_provider: str) -> str:
    """Normalize model id for the `target_provider`.

    Basic rules applied:
    - If a mapping exists in model_id_mappings.json, use it.
    - For HF/MLX: replace ':' with '-' as a heuristic.
    - For Ollama: keep as-is.
    """
    if not model_id:
        return model_id

    mappings = _load_mappings()
    key = f"{model_id}|{target_provider}"
    if key in mappings:
        return mappings[key]

    # If there is a model config in MLX that matches a sanitized name, return it
    try:
        from src.config.mlx_models import MODEL_CONFIGS

        # Normalize a simple gemma3:4b -> gemma-3-4b variations
        sanitized = model_id.replace(":", "-")
        # try exact match first
        if sanitized in MODEL_CONFIGS:
            return sanitized
        # fallback: check keys for substring match
        for k in MODEL_CONFIGS.keys():
            if sanitized in k or model_id in k:
                return k
    except Exception:
        pass

    if target_provider in ("huggingface", "hf_inference"):
        return model_id.replace(":", "-")

    if target_provider in ("mlx", "mlx_local"):
        # MLX often accepts the same repo-style string; but a ':' may be invalid
        return model_id.replace(":", "-")

    if target_provider in ("ollama", "ollama_local", "ollama_cloud"):
        # Ollama uses the colon-style model id (ollama:tag format)
        return model_id

    # Ollama expected id form
    return model_id
