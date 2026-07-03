#!/usr/bin/env bash
# API server de desarrollo para el dashboard (paper BTCUSDT embebido).
# No toca la sesión live: proceso, símbolo y modo distintos.
set -euo pipefail
cd "$(dirname "$0")/.."

export LLM_PROFILE="OLLAMA_CLOUD"
export PYTHONUNBUFFERED=1
export CREATE_DEMO_USERS="true"
export ENVIRONMENT="development"
export FENIX_RISK_ALLOW_REANCHOR=1
export FENIX_BALANCE_FALLBACK_USDT=1000
export FENIX_LLM_MAX_CONCURRENT_REQUESTS=10
export FENIX_VISUAL_MAX_TOKENS=1200
# Mismo equipo ganador que la sesión live, vía proxy local :cloud
export FENIX_ROTATE_MODELS_TECHNICAL="deepseek-v4-flash:cloud"
export FENIX_ROTATE_MODELS_QABBA="deepseek-v4-flash:cloud"
export FENIX_ROTATE_MODELS_SENTIMENT="deepseek-v4-flash:cloud"
export FENIX_ROTATE_MODELS_VISUAL="gemini-3-flash-preview:cloud"
export FENIX_ROTATE_MODELS_DECISION="deepseek-v4-pro:cloud"
export FENIX_ROTATE_MODELS_RISK_MANAGER="deepseek-v4-pro:cloud"

exec .venv/bin/python run_fenix.py --api
