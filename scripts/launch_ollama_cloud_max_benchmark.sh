#!/usr/bin/env bash
# ============================================================================
# Ollama Cloud Max Benchmark — FenixAI
# ============================================================================
# Compara el plan Max (10 agentes potentes simultaneos) vs el plan anterior.
# Activa Visual + Sentiment en 5m, timing detallado, y concurrencia alta.
#
# Uso:
#   bash scripts/launch_ollama_cloud_max_benchmark.sh [SLOT_MINUTES] [MAX_SLOTS] [PLAN_JSON] [SYMBOL] [TAG_SUFFIX]
#
# Por defecto: 15 min/slot, 5 slots, plan max_benchmark, BTCUSDT
# Se pueden lanzar varios en paralelo con planes/simbolos distintos para
# aprovechar los 10 requests concurrentes del plan Max.
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

SLOT_MINUTES="${1:-15}"
MAX_SLOTS="${2:-5}"
PLAN_JSON="${3:-plans/ollama_cloud_max_benchmark.json}"
SYMBOL="${4:-BTCUSDT}"
RUN_TAG_SUFFIX="${5:-_max_cloud}"
PYTHON_BIN="fenix_env/bin/python"

# Fall back to .venv if fenix_env doesn't exist
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN=".venv/bin/python"
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "❌ No se encontro Python. Probe fenix_env/bin/python y .venv/bin/python"
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

# Concurrencia alta (plan Max = hasta 10 agentes simultaneos)
export FENIX_LLM_MAX_CONCURRENT_REQUESTS="10"

# Forzar Visual y Sentiment en corto timeframe (5m)
export FENIX_DISABLE_VISUAL_SHORT_TF="0"
export FENIX_DISABLE_SENTIMENT_SHORT_TF="0"
export FENIX_DISABLE_VISUAL_ALL_TF="0"

# Desactivar ReasoningBank y Judge para comparacion pura de latencia
export FENIX_ENABLE_REASONING_BANK="0"
export FENIX_DISABLE_REASONING_BANK="1"
export FENIX_ENABLE_JUDGE="0"
export FENIX_SHORT_TF_NONBLOCKING="1"

# Estado de riesgo limpio por run: sin esto, el drawdown anchor de sesiones
# live anteriores (peak 703 vs balance paper 100) bloquea trades del benchmark
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

# Reintentos conservadores
export FENIX_TECH_MAX_RETRIES="1"
export FENIX_TECHNICAL_MAX_RETRIES="1"
export FENIX_QABBA_MAX_RETRIES="1"
export FENIX_DECISION_MAX_RETRIES="1"
export FENIX_SENTIMENT_MAX_RETRIES="1"
export FENIX_RETRY_429_WAIT_SEC="8"
export FENIX_RETRY_429_WAIT_JITTER_SEC="3"
export FENIX_RETRY_503_WAIT_SEC="15"
export FENIX_RETRY_503_WAIT_JITTER_SEC="8"

# Token budgets amplios para modelos grandes
export FENIX_MAX_TOKENS_MULTIPLIER="1.5"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  🚀 Ollama Cloud Max Benchmark — FenixAI                     ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Plan:      ${PLAN_JSON}"
echo "║  Symbol:    ${SYMBOL}"
echo "║  Slots:     ${MAX_SLOTS}                                            ║"
echo "║  Duracion:  ${SLOT_MINUTES} min/slot = ~$(( MAX_SLOTS * SLOT_MINUTES )) min total               ║"
echo "║  TF:        5m (single-timeframe)                             ║"
echo "║  Visual:    FORZADO ON en 5m                                   ║"
echo "║  Sentiment: FORZADO ON en 5m                                   ║"
echo "║  Concurrencia: 10 agentes simultaneos                         ║"
echo "║  Timing:    FENIX_LLM_TIMING_LOG=1                             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

"$PYTHON_BIN" scripts/run_benchmark_suite.py \
    --plan-json "$PLAN_JSON" \
    --symbol "$SYMBOL" \
    --timeframes 5m \
    --bias-tf 5m \
    --entry-tf 5m \
    --scout-tf 5m \
    --slot-minutes "$SLOT_MINUTES" \
    --position-usd 1000 \
    --python-bin "$PYTHON_BIN" \
    --max-slots "$MAX_SLOTS" \
    --llm-max-concurrent-requests 10 \
    --tech-timeout-short-sec 120 \
    --qabba-timeout-short-sec 120 \
    --decision-timeout-short-sec 90 \
    --visual-timeout-short-sec 60 \
    --sentiment-agent-timeout-short-sec 60 \
    --sentiment-agent-timeout-sec 90 \
    --tech-max-retries 1 \
    --qabba-max-retries 1 \
    --decision-max-retries 1 \
    --sentiment-max-retries 1 \
    --retry-429-wait-sec 8 \
    --retry-429-wait-jitter-sec 3 \
    --max-tokens-multiplier 1.5 \
    --run-tag-suffix "$RUN_TAG_SUFFIX"
