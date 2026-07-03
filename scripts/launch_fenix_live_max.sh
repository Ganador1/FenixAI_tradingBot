#!/usr/bin/env bash
# ============================================================================
# FenixAI — Run en vivo con Ollama Cloud Max (MTF + Visual + Sentiment)
# ============================================================================
# Lanza FenixAI en paper/testnet con multi-timeframe, todos los agentes activos,
# y modelos potentes del plan Max de Ollama Cloud.
#
# Uso:
#   bash scripts/launch_fenix_live_max.sh [RUN_MINUTES] [SYMBOL]
#
# Por defecto: 30 min, BTCUSDT, timeframes 5m/15m/1h
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

RUN_MINUTES="${1:-30}"
SYMBOL="${2:-BTCUSDT}"
PYTHON_BIN="fenix_env/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN=".venv/bin/python"
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "❌ No se encontro Python"
    exit 1
fi

export PYTHONPATH="."
export LLM_PROFILE="OLLAMA_CLOUD"
export OLLAMA_CLOUD_URL="${OLLAMA_CLOUD_URL:-https://api.ollama.com}"
export DISABLE_MLX="1"
export PYTHONUNBUFFERED="1"
export MPLCONFIGDIR="/tmp/matplotlib"
export XDG_CACHE_HOME="/tmp"

# Timing detallado del LLM
export FENIX_LLM_TIMING_LOG="1"

# Concurrencia alta (plan Max)
export FENIX_LLM_MAX_CONCURRENT_REQUESTS="10"

# Forzar Visual y Sentiment en TODOS los timeframes (incluso cortos)
export FENIX_DISABLE_VISUAL_SHORT_TF="0"
export FENIX_DISABLE_SENTIMENT_SHORT_TF="0"
export FENIX_DISABLE_VISUAL_ALL_TF="0"

# Resetear circuit breaker del RiskManager para run limpio
export FENIX_RISK_ALLOW_REANCHOR="1"

# Timeouts ampliados para modelos pesados
export FENIX_TECH_TIMEOUT_SHORT_SEC="120"
export FENIX_TECHNICAL_TIMEOUT_SHORT_SEC="120"
export FENIX_QABBA_TIMEOUT_SHORT_SEC="120"
export FENIX_DECISION_TIMEOUT_SHORT_SEC="90"
export FENIX_VISUAL_TIMEOUT_SHORT_SEC="60"
export FENIX_RISK_TIMEOUT_SEC="90"
export FENIX_SENTIMENT_AGENT_TIMEOUT_SHORT_SEC="60"
export FENIX_SENTIMENT_AGENT_TIMEOUT_SEC="90"

# Reintentos
export FENIX_TECH_MAX_RETRIES="1"
export FENIX_TECHNICAL_MAX_RETRIES="1"
export FENIX_QABBA_MAX_RETRIES="1"
export FENIX_DECISION_MAX_RETRIES="1"
export FENIX_SENTIMENT_MAX_RETRIES="1"
export FENIX_RETRY_429_WAIT_SEC="8"
export FENIX_RETRY_429_WAIT_JITTER_SEC="3"
export FENIX_RETRY_503_WAIT_SEC="15"
export FENIX_RETRY_503_WAIT_JITTER_SEC="8"

# Token budgets amplios
export FENIX_MAX_TOKENS_MULTIPLIER="1.5"

# Team models — modelos potentes del plan Max
TEAM_MODELS="technical=kimi-k2.7-code,qabba=minimax-m2.7,decision=glm-5.2,sentiment=minimax-m2.7,visual=gemma4:31b:cloud,risk_manager=deepseek-v4-pro"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  🦅 FenixAI Live Run — Ollama Cloud Max                      ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Symbol:     $SYMBOL                                          ║"
echo "║  Duracion:   $RUN_MINUTES min                                  ║"
echo "║  Timeframes: 5m,15m,1h (MTF)                                 ║"
echo "║  Visual:     ON en todos los TF                               ║"
echo "║  Sentiment:  ON en todos los TF                               ║"
echo "║  Concurrencia: 10 agentes                                    ║"
echo "║  Modelos:    kimi/minimax/glm/deepseek (plan Max)            ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

"$PYTHON_BIN" scripts/run_hybrid_live_paper.py \
    --symbol "$SYMBOL" \
    --timeframes "5m,15m,1h" \
    --bias-tf "1h" \
    --entry-tf "5m" \
    --scout-tf "15m" \
    --run-minutes "$RUN_MINUTES" \
    --position-usd 1000 \
    --team-models "$TEAM_MODELS" \
    --team-tag "max_live_mtf" \
    --team-provider "ollama_cloud" \
    --disable-reasoning-bank \
    --disable-judge
