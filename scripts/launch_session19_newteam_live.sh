#!/usr/bin/env bash
# ============================================================================
# Session 19 — Equipo ganador Ollama Cloud Max en LIVE (ETHUSDC 15m)
# ============================================================================
# Equipo nuevo (benchmark 2026-07-01): deepseek-v4-flash analistas,
# deepseek-v4-pro decision/risk, gemma4:31b:cloud visual. MTF bias 1h,
# visual + sentiment ON, NanoFenix companion con drift-retrain y
# meta-labeling por régimen.
#
# Parámetros de riesgo heredados de session18 (probados en live), con
# balance/notional ajustados al balance real de la cuenta (~397 USDC).
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

pkill -f "run_fenix.py --mode live --allow-live --symbol ETHUSDC" || true
pkill -f "run_nanofenixv3.py --symbol ETHUSDC --companion" || true
sleep 2

# Sufijo :cloud = proxy del daemon local de Ollama hacia Ollama Cloud
# (mismo mecanismo que session18; el perfil OLLAMA_CLOUD usa localhost:11434).
# Visual: gemma4:31b:cloud fue validado con ChatOllama + imagen vía el daemon local.
TEAM_MODELS="technical=deepseek-v4-flash:cloud,qabba=deepseek-v4-flash:cloud,sentiment=deepseek-v4-flash:cloud,visual=gemma4:31b:cloud,decision=deepseek-v4-pro:cloud,risk_manager=deepseek-v4-pro:cloud"

LLM_PROFILE=OLLAMA_CLOUD \
OLLAMA_CLOUD_URL="${OLLAMA_CLOUD_URL:-https://api.ollama.com}" \
FENIX_LLM_TIMING_LOG=1 \
FENIX_LLM_MAX_CONCURRENT_REQUESTS=10 \
FENIX_LEVERAGE=10 \
FENIX_NANOFENIX_COMPANION_SINGLETON=1 \
FENIX_MAX_RISK_PER_TRADE=0.0125 \
FENIX_MAX_AVAILABLE_NOTIONAL_USD=140 \
FENIX_RISK_MAX_ALLTIME_DRAWDOWN_PCT=15 \
FENIX_RISK_ALLOW_REANCHOR=1 \
FENIX_STRICT_MTF_BIAS_TIMEFRAME=1h \
FENIX_STRICT_MTF_OPPOSING_VETO_CONF=0.75 \
FENIX_W_VISUAL=0.05 \
FENIX_MIN_BUY_DIRECTIONAL_SCORE=0.20 \
FENIX_MIN_SELL_DIRECTIONAL_SCORE=0.20 \
FENIX_FILTER_QABBA_OPPOSITE_VETO_CONF=0.85 \
FENIX_ALLOW_CONSENSUS_SAME_SIDE_ADD_SELL=1 \
FENIX_CONSENSUS_ADD_MIN_CONFIDENCE=HIGH \
FENIX_CONSENSUS_ADD_MIN_DIRECTIONAL_SCORE=0.70 \
FENIX_CONSENSUS_ADD_TECH_MIN_CONF=0.60 \
FENIX_CONSENSUS_ADD_QABBA_MIN_CONF=0.70 \
FENIX_CONSENSUS_ADD_MTF_MIN_CONF=0.60 \
FENIX_CONSENSUS_ADD_MAX_ENTRIES=2 \
FENIX_EVAL_ROUNDTRIP_COST_PCT=0.05 \
FENIX_BALANCE_FALLBACK_USDT=397 \
FENIX_MIN_TRADE_COOLDOWN_SECONDS=900 \
FENIX_MIN_RR_FOR_ENTRY=2.0 \
FENIX_USE_LIMIT_ENTRY=1 \
FENIX_DISABLE_VISUAL_SHORT_TF=0 \
FENIX_DISABLE_SENTIMENT_SHORT_TF=0 \
FENIX_VISUAL_MAX_TOKENS=1200 \
FENIX_TECH_TIMEOUT_SHORT_SEC=120 \
FENIX_TECHNICAL_TIMEOUT_SHORT_SEC=120 \
FENIX_QABBA_TIMEOUT_SHORT_SEC=120 \
FENIX_DECISION_TIMEOUT_SHORT_SEC=90 \
FENIX_VISUAL_TIMEOUT_SHORT_SEC=60 \
FENIX_RISK_TIMEOUT_SEC=90 \
FENIX_SENTIMENT_AGENT_TIMEOUT_SHORT_SEC=60 \
FENIX_SENTIMENT_AGENT_TIMEOUT_SEC=90 \
nohup .venv/bin/python run_fenix.py \
  --mode live \
  --allow-live \
  --symbol ETHUSDC \
  --timeframe 15m \
  --team-models "$TEAM_MODELS" \
  --with-nanofenix-companion \
  > logs/live_ethusdc_session19_newteam.log 2>&1 < /dev/null &

sleep 3
ps -ax -o pid=,command= | grep -E "run_fenix.py --mode live --allow-live --symbol ETHUSDC" | grep -v grep || true
