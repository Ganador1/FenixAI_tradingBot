"""
Legacy import path for the unified LLM client.

Prefer: `from src.inference.llm import UnifiedLLMClient`.
"""

from __future__ import annotations

from src.inference.llm import UnifiedLLMClient

__all__ = ["UnifiedLLMClient"]
