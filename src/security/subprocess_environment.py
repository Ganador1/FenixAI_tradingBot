"""Least-privilege environment construction for trusted experiment children."""

from __future__ import annotations

import os
from collections.abc import Mapping

_BLOCKED_EXACT = {
    "JWT_SECRET",
    "FENIX_MASTER_PASSWORD",
    "FENIX_METRICS_TOKEN",
    "CREATE_DEMO_USERS",
    "DEMO_ADMIN_PASSWORD",
    "DEMO_TRADER_PASSWORD",
}
_BLOCKED_PREFIXES = (
    "SMTP_",
    "TWILIO_",
    "SMS_",
    "SENDGRID_",
    "GRAFANA_",
    "SUPABASE_",
    "VITE_",
)
_INDEXED_SECRET_BASES = {
    "OLLAMA_CLOUD_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "HUGGINGFACE_API_KEY",
    "BINANCE_TESTNET_API_KEY",
    "BINANCE_TESTNET_API_SECRET",
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
}


def experiment_child_environment(
    source: Mapping[str, str] | None = None,
    *,
    api_key_index: int | None = None,
) -> dict[str, str]:
    """Copy runtime configuration while removing unrelated high-value secrets."""
    source = os.environ if source is None else source
    env: dict[str, str] = {}
    for name, value in source.items():
        if name in _BLOCKED_EXACT or name.startswith(_BLOCKED_PREFIXES):
            continue
        if api_key_index in {1, 2}:
            other_index = 2 if api_key_index == 1 else 1
            if any(name == f"{base}_{other_index}" for base in _INDEXED_SECRET_BASES):
                continue
        env[name] = value

    env["FENIX_SKIP_DOTENV"] = "1"
    return env
