#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

pkill -f "run_fenix.py --mode live --allow-live --symbol ETHUSDC --timeframe 15m --with-nanofenix-companion" || true
pkill -f "run_nanofenixv3.py --symbol ETHUSDC --companion" || true
sleep 2

FENIX_LEVERAGE=10 \
FENIX_NANOFENIX_COMPANION_SINGLETON=1 \
FENIX_MAX_RISK_PER_TRADE=0.0125 \
FENIX_MAX_AVAILABLE_NOTIONAL_USD=253 \
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
FENIX_BALANCE_FALLBACK_USDT=700 \
FENIX_MIN_TRADE_COOLDOWN_SECONDS=900 \
FENIX_MIN_RR_FOR_ENTRY=2.0 \
FENIX_USE_LIMIT_ENTRY=1 \
nohup python run_fenix.py \
  --mode live \
  --allow-live \
  --symbol ETHUSDC \
  --timeframe 15m \
  --with-nanofenix-companion \
  > logs/live_ethusdc_session18.log 2>&1 < /dev/null &

ps -ax -o pid=,command= | grep -E "run_fenix.py --mode live --allow-live --symbol ETHUSDC --timeframe 15m --with-nanofenix-companion" | grep -v grep || true
