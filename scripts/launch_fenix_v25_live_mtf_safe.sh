#!/bin/bash
# Fenix v2.5 live MTF safe launcher for small mainnet accounts.
# Starts NanoFenix v3.5 as companion and Fenix live 15m with deterministic 30m bias veto.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if ! command -v screen >/dev/null 2>&1; then
  echo "Missing required command: screen" >&2
  exit 1
fi

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

: "${OLLAMA_CLOUD_API_KEY_1:?Missing OLLAMA_CLOUD_API_KEY_1 in .env}"
: "${BINANCE_API_KEY:?Missing BINANCE_API_KEY in .env}"
: "${BINANCE_API_SECRET:?Missing BINANCE_API_SECRET in .env}"

SYMBOL="${1:-SOLUSDT}"
RUN_MINUTES="${2:-360}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LAUNCH_EPOCH="$(date -u +%s)"
SYMBOL_LC="$(printf '%s' "$SYMBOL" | tr '[:upper:]' '[:lower:]')"
PYTHON="$PROJECT_ROOT/.venv/bin/python"

COMPANION_SIGNAL_PATH="logs/nanofenixv3_companion_${SYMBOL_LC}_live_mtf_safe.json"
COMPANION_RUNTIME_PATH="nanofenixv3/runtime_${SYMBOL_LC}_live_mtf_safe.pkl"
TEAM_MODELS="${FENIX_TEAM_MODELS:-technical=gemma4:31b-cloud,qabba=ministral-3:14b-cloud}"

export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
export OLLAMA_CLOUD_URL="${OLLAMA_CLOUD_URL:-https://api.ollama.com}"
export LLM_PROFILE="${LLM_PROFILE:-OLLAMA_CLOUD}"

export FENIX_ENABLE_JUDGE="${FENIX_ENABLE_JUDGE:-0}"
export FENIX_ENABLE_REASONING_BANK="${FENIX_ENABLE_REASONING_BANK:-0}"
export FENIX_LITE_PIPELINE="${FENIX_LITE_PIPELINE:-1}"
export FENIX_LITE_CONSENSUS_MODE="${FENIX_LITE_CONSENSUS_MODE:-technical_mtf_qabba_guard}"
export FENIX_LITE_MTF_CONFIRM_CONF="${FENIX_LITE_MTF_CONFIRM_CONF:-0.55}"
export FENIX_LITE_QABBA_OPPOSE_CONF="${FENIX_LITE_QABBA_OPPOSE_CONF:-0.72}"
export FENIX_LITE_NODE_TIMEOUT_SEC="${FENIX_LITE_NODE_TIMEOUT_SEC:-35}"
export FENIX_STRICT_MTF_BIAS_TIMEFRAME="${FENIX_STRICT_MTF_BIAS_TIMEFRAME:-30m}"
export FENIX_STRICT_MTF_OPPOSING_VETO_CONF="${FENIX_STRICT_MTF_OPPOSING_VETO_CONF:-0.75}"
export FENIX_STRICT_MTF_BIAS_CACHE_SEC="${FENIX_STRICT_MTF_BIAS_CACHE_SEC:-120}"

export FENIX_ALLOW_ADD_TO_POSITION="${FENIX_ALLOW_ADD_TO_POSITION:-0}"
export FENIX_MAX_RISK_PER_TRADE="${FENIX_MAX_RISK_PER_TRADE:-0.003}"
export FENIX_LEVERAGE="${FENIX_LEVERAGE:-3}"
export FENIX_MAX_EXPOSURE_PCT="${FENIX_MAX_EXPOSURE_PCT:-0.15}"
export FENIX_EXPOSURE_LEVERAGE_MULTIPLIER="${FENIX_EXPOSURE_LEVERAGE_MULTIPLIER:-3}"
export FENIX_MAX_NOTIONAL_USD="${FENIX_MAX_NOTIONAL_USD:-30}"
export FENIX_CLEANUP_ON_STOP="${FENIX_CLEANUP_ON_STOP:-1}"
export FENIX_CLEANUP_ON_START="${FENIX_CLEANUP_ON_START:-1}"
export FENIX_CLEANUP_CANCEL_SYMBOL_ORDERS_WHEN_FLAT="${FENIX_CLEANUP_CANCEL_SYMBOL_ORDERS_WHEN_FLAT:-1}"
export FENIX_MIN_KLINES_TO_START="${FENIX_MIN_KLINES_TO_START:-5}"
export FENIX_KLINE_WATCHDOG_INTERVAL_SEC="${FENIX_KLINE_WATCHDOG_INTERVAL_SEC:-60}"
export FENIX_KLINE_WATCHDOG_GRACE_SEC="${FENIX_KLINE_WATCHDOG_GRACE_SEC:-930}"
export FENIX_RISK_DETERMINISTIC="${FENIX_RISK_DETERMINISTIC:-1}"
export FENIX_ALLOW_EXCHANGE_MIN_QTY_FLOOR="${FENIX_ALLOW_EXCHANGE_MIN_QTY_FLOOR:-1}"
export FENIX_MIN_QTY_FLOOR_MAX_MARGIN_USD="${FENIX_MIN_QTY_FLOOR_MAX_MARGIN_USD:-5}"
export FENIX_MIN_QTY_FLOOR_MAX_LOSS_USD="${FENIX_MIN_QTY_FLOOR_MAX_LOSS_USD:-0.45}"
export FENIX_MIN_QTY_FLOOR_MAX_FEES_USD="${FENIX_MIN_QTY_FLOOR_MAX_FEES_USD:-0.08}"
export FENIX_ESTIMATED_ROUND_TRIP_FEE_PCT="${FENIX_ESTIMATED_ROUND_TRIP_FEE_PCT:-0.0008}"
export FENIX_MIN_TRADE_COOLDOWN_SECONDS="${FENIX_MIN_TRADE_COOLDOWN_SECONDS:-900}"
export FENIX_ENTRY_COOLDOWN_SEC="${FENIX_ENTRY_COOLDOWN_SEC:-900}"
export FENIX_MAX_CONSECUTIVE_HOLDS="${FENIX_MAX_CONSECUTIVE_HOLDS:-1}"
export FENIX_MAX_HOLD_HOURS="${FENIX_MAX_HOLD_HOURS:-2}"

export FENIX_CAUTION_DRAWDOWN_PCT="${FENIX_CAUTION_DRAWDOWN_PCT:-3.0}"
export FENIX_SEVERE_DRAWDOWN_PCT="${FENIX_SEVERE_DRAWDOWN_PCT:-5.0}"
export FENIX_CAUTION_COOLDOWN_SECONDS="${FENIX_CAUTION_COOLDOWN_SECONDS:-1800}"
export FENIX_SEVERE_COOLDOWN_SECONDS="${FENIX_SEVERE_COOLDOWN_SECONDS:-3600}"

export FENIX_ENABLE_NANOFENIX_COMPANION="${FENIX_ENABLE_NANOFENIX_COMPANION:-1}"
export FENIX_NANOFENIX_SIGNAL_PATH="${FENIX_NANOFENIX_SIGNAL_PATH:-$COMPANION_SIGNAL_PATH}"
export FENIX_NANOFENIX_MAX_SIGNAL_AGE_SEC="${FENIX_NANOFENIX_MAX_SIGNAL_AGE_SEC:-90}"
export FENIX_NANOFENIX_MIN_CONF="${FENIX_NANOFENIX_MIN_CONF:-0.50}"
export FENIX_NANOFENIX_MIN_PRED_BPS="${FENIX_NANOFENIX_MIN_PRED_BPS:-1.5}"
export FENIX_NANOFENIX_MIN_DIRECTION_ACCURACY="${FENIX_NANOFENIX_MIN_DIRECTION_ACCURACY:-0.46}"
export FENIX_NANOFENIX_MIN_ACTIONABLE_EDGE_BPS="${FENIX_NANOFENIX_MIN_ACTIONABLE_EDGE_BPS:-0.25}"
export FENIX_NANOFENIX_MAX_UNCERTAINTY_BPS="${FENIX_NANOFENIX_MAX_UNCERTAINTY_BPS:-5.75}"
export FENIX_NANOFENIX_MIN_CALIBRATION_HEALTH="${FENIX_NANOFENIX_MIN_CALIBRATION_HEALTH:-0.45}"
export FENIX_NANOFENIX_REQUIRE_ALLOW_EXECUTE="${FENIX_NANOFENIX_REQUIRE_ALLOW_EXECUTE:-1}"
export FENIX_NANOFENIX_HARD_VETO_REASONS="${FENIX_NANOFENIX_HARD_VETO_REASONS:-companion_not_ready,direction_mismatch,high_uncertainty,stale_signal,symbol_mismatch,signal_file_missing,signal_file_empty,signal_parse_error,missing_or_invalid_timestamp}"
export FENIX_NANOFENIX_REQUIRE_FOR_OPPOSITE_EXIT="${FENIX_NANOFENIX_REQUIRE_FOR_OPPOSITE_EXIT:-0}"
export FENIX_NANOFENIX_FORCE_REVERSAL_EXIT="${FENIX_NANOFENIX_FORCE_REVERSAL_EXIT:-0}"

export NANOFENIX_SIGNAL_STATE_PATH="${NANOFENIX_SIGNAL_STATE_PATH:-$COMPANION_SIGNAL_PATH}"
export NANOFENIXV3_RUNTIME_STATE_PATH="${NANOFENIXV3_RUNTIME_STATE_PATH:-$COMPANION_RUNTIME_PATH}"
export NANOFENIXV3_COMPANION_OBSERVER_ONLY="${NANOFENIXV3_COMPANION_OBSERVER_ONLY:-1}"
export NANOFENIXV3_ENABLE_ADAPTIVE_FUSION="${NANOFENIXV3_ENABLE_ADAPTIVE_FUSION:-1}"
export NANOFENIXV3_ADAPTIVE_FUSION_BASE_THRESHOLD="${NANOFENIXV3_ADAPTIVE_FUSION_BASE_THRESHOLD:-0.32}"
export NANOFENIXV3_ADAPTIVE_FUSION_MIN_MARGIN="${NANOFENIXV3_ADAPTIVE_FUSION_MIN_MARGIN:-0.06}"
export NANOFENIXV3_COMPANION_MIN_DIRECTION_ACCURACY="${NANOFENIXV3_COMPANION_MIN_DIRECTION_ACCURACY:-0.46}"
export NANOFENIXV3_MIN_ACTIONABLE_EDGE_BPS="${NANOFENIXV3_MIN_ACTIONABLE_EDGE_BPS:-0.25}"
export NANOFENIXV3_MIN_CALIBRATION_HEALTH="${NANOFENIXV3_MIN_CALIBRATION_HEALTH:-0.44}"
export NANOFENIXV3_POLICY_FEE_BUFFER_BPS="${NANOFENIXV3_POLICY_FEE_BUFFER_BPS:-0.25}"
export NANOFENIXV3_BASE_MIN_BPS="${NANOFENIXV3_BASE_MIN_BPS:-1.2}"

NANO_SESSION="fenix-nano-${SYMBOL_LC}-live-mtf-${STAMP}"
LIVE_SESSION="fenix-live-${SYMBOL_LC}-15m-mtf-${STAMP}"
NANO_LOG="logs/screen_nanofenixv3_${SYMBOL_LC}_live_mtf_${STAMP}.log"
LIVE_LOG="logs/screen_fenix_${SYMBOL_LC}_15m_live_mtf_${STAMP}.log"
RUN_TAG="v25_live_mtf_safe_${SYMBOL}_${STAMP}"

start_screen() {
  local session_name="$1"
  local log_path="$2"
  shift 2
  local quoted_root quoted_log quoted_command
  printf -v quoted_root "%q" "$PROJECT_ROOT"
  printf -v quoted_log "%q" "$log_path"
  printf -v quoted_command "%q " "$@"
  screen -dmS "$session_name" bash -lc "cd $quoted_root && exec $quoted_command >>$quoted_log 2>&1"
}

wait_for_companion() {
  "$PYTHON" - "$COMPANION_SIGNAL_PATH" "$SYMBOL" "$FENIX_NANOFENIX_MAX_SIGNAL_AGE_SEC" "$1" <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import json
import sys
import time

path = Path(sys.argv[1])
symbol = sys.argv[2]
max_age = float(sys.argv[3])
min_epoch = float(sys.argv[4])
deadline = time.time() + 150
last = None
while time.time() < deadline:
    try:
        payload = json.loads(path.read_text())
        raw_ts = str(payload.get("timestamp_utc") or payload.get("timestamp") or "")
        ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        last = {
            "age_sec": round(age, 2),
            "symbol": payload.get("symbol"),
            "signal": payload.get("signal"),
            "action": payload.get("action"),
            "allow_execute": payload.get("allow_execute"),
            "confidence": payload.get("confidence"),
            "pred_bps": payload.get("pred_bps"),
        }
        if payload.get("symbol") == symbol and age <= max_age and ts.timestamp() >= min_epoch:
            print("companion_fresh=" + json.dumps(last, sort_keys=True))
            raise SystemExit(0)
    except Exception as exc:
        last = {"waiting": str(exc)}
    time.sleep(2)
print("companion_not_fresh=" + json.dumps(last, sort_keys=True))
raise SystemExit(1)
PY
}

echo "======================================================================"
echo "Fenix v2.5 LIVE MTF SAFE"
echo "======================================================================"
echo "symbol        : $SYMBOL"
echo "entry_tf      : 15m"
echo "bias_tf       : $FENIX_STRICT_MTF_BIAS_TIMEFRAME"
echo "run_minutes   : $RUN_MINUTES"
echo "run_tag       : $RUN_TAG"
echo "team_models   : $TEAM_MODELS"
echo "leverage      : ${FENIX_LEVERAGE}x"
echo "max_notional  : ${FENIX_MAX_NOTIONAL_USD} USDT"
echo "risk/trade    : ${FENIX_MAX_RISK_PER_TRADE}"
echo "allow_add     : ${FENIX_ALLOW_ADD_TO_POSITION}"
echo "watchdog      : interval=${FENIX_KLINE_WATCHDOG_INTERVAL_SEC}s grace=${FENIX_KLINE_WATCHDOG_GRACE_SEC}s"
echo "nano_require  : allow_execute=${FENIX_NANOFENIX_REQUIRE_ALLOW_EXECUTE}"
echo "screen nano   : $NANO_SESSION"
echo "screen live   : $LIVE_SESSION"
echo "logs nano     : $NANO_LOG"
echo "logs live     : $LIVE_LOG"
echo "======================================================================"

if [[ "${FENIX_REUSE_NANOFENIX:-0}" == "1" ]]; then
  echo "screen nano   : reusing existing companion signal"
  COMPANION_MIN_EPOCH="0"
else
  start_screen "$NANO_SESSION" "$NANO_LOG" "$PYTHON" run_nanofenixv3.py \
    --symbol "$SYMBOL" \
    --balance 242 \
    --companion \
    --adaptive-fusion \
    --output-path "$COMPANION_SIGNAL_PATH" \
    --runtime-state-path "$COMPANION_RUNTIME_PATH"
  COMPANION_MIN_EPOCH="$LAUNCH_EPOCH"
fi

wait_for_companion "$COMPANION_MIN_EPOCH"

start_screen "$LIVE_SESSION" "$LIVE_LOG" "$PYTHON" scripts/run_fenix_live_slot.py \
  --symbol "$SYMBOL" \
  --timeframe 15m \
  --run-minutes "$RUN_MINUTES" \
  --mode live \
  --allow-live \
  --team-provider ollama_cloud \
  --team-models "$TEAM_MODELS" \
  --no-visual \
  --no-sentiment \
  --lite-pipeline \
  --slot-name "v25-live-mtf-safe" \
  --slot-index 25 \
  --run-tag "$RUN_TAG" \
  --api-key-index 1 \
  --min-klines-to-start "$FENIX_MIN_KLINES_TO_START" \
  --model-timeout-sec 45 \
  --disable-judge

echo "nano_session=$NANO_SESSION"
echo "live_session=$LIVE_SESSION"
echo "nano_log=$NANO_LOG"
echo "live_log=$LIVE_LOG"
echo "run_tag=$RUN_TAG"
screen -list || true
