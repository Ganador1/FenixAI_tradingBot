#!/usr/bin/env bash
#
# Launch isolated Fenix paper candidates against either Binance Futures
# Testnet data or public Mainnet data. Orders are always simulated.
#
# Configuration:
#   FENIX_EXPERIMENT_VENUE=testnet|mainnet-data   (default: testnet)
#   FENIX_EXPERIMENT_SYMBOL=BTCUSDT               (default: BTCUSDT)
#   FENIX_EXPERIMENT_TIMEFRAMES=5m,1h             (default: 5m,1h)
#   FENIX_EXPERIMENT_ROOT=logs/my_experiment      (optional)
#   FENIX_EXPERIMENT_TEAM_MODELS=agent=model,...  (optional)
#   FENIX_EXPERIMENT_WITH_NANO=0|1                (default: 0)
#
# Usage:
#   bash scripts/paper_experiment_harness.sh start
#   bash scripts/paper_experiment_harness.sh status
#   bash scripts/paper_experiment_harness.sh stop

set -euo pipefail
umask 077

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON="${FENIX_EXPERIMENT_PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
VENUE="${FENIX_EXPERIMENT_VENUE:-testnet}"
SYMBOL="${FENIX_EXPERIMENT_SYMBOL:-BTCUSDT}"
TIMEFRAMES_CSV="${FENIX_EXPERIMENT_TIMEFRAMES:-5m,1h}"
TEAM_MODELS="${FENIX_EXPERIMENT_TEAM_MODELS:-}"
WITH_NANO="${FENIX_EXPERIMENT_WITH_NANO:-0}"
FLOW_WINDOW="${FENIX_EXPERIMENT_FLOW_WINDOW_SEC:-15}"
INITIAL_BALANCE="${FENIX_EXPERIMENT_INITIAL_BALANCE_USDT:-10000}"
ANALYSIS_INTERVAL="${FENIX_EXPERIMENT_INTERVAL_SEC:-300}"
TAKER_FEE_RATE="${FENIX_EXPERIMENT_TAKER_FEE_RATE:-0.0004}"
SLIPPAGE_BPS="${FENIX_EXPERIMENT_SLIPPAGE_BPS:-1.0}"

if [[ ! -x "$PYTHON" ]]; then
    echo "Python executable not found: $PYTHON" >&2
    exit 2
fi

if [[ "${FENIX_EXPERIMENT_LOAD_DOTENV:-0}" == "1" &&
    -f ".env" &&
    "${FENIX_SECURE_DOTENV_LOADED:-0}" != "1" ]]; then
    exec "$PYTHON" scripts/secure_dotenv_exec.py --env "$PROJECT_ROOT/.env" -- \
        bash "$0" "$@"
fi

is_provider_variable() {
    case "$1" in
        LLM_* | OLLAMA_* | GROQ_* | OPENROUTER_* | OPENAI_* | ANTHROPIC_* | HF_* | HUGGINGFACE_*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

if [[ "$VENUE" != "testnet" && "$VENUE" != "mainnet-data" ]]; then
    echo "FENIX_EXPERIMENT_VENUE must be testnet or mainnet-data" >&2
    exit 2
fi
if [[ ! "$SYMBOL" =~ ^[A-Z0-9]{5,20}$ ]]; then
    echo "FENIX_EXPERIMENT_SYMBOL must be an uppercase Binance symbol" >&2
    exit 2
fi
if [[ "$WITH_NANO" != "0" && "$WITH_NANO" != "1" ]]; then
    echo "FENIX_EXPERIMENT_WITH_NANO must be 0 or 1" >&2
    exit 2
fi
if [[ "${#TEAM_MODELS}" -gt 4096 || "$TEAM_MODELS" == *$'\n'* || "$TEAM_MODELS" == *$'\r'* ]]; then
    echo "FENIX_EXPERIMENT_TEAM_MODELS is too long or contains control characters" >&2
    exit 2
fi

SYMBOL_LC="$(printf '%s' "$SYMBOL" | tr '[:upper:]' '[:lower:]')"
DEFAULT_ROOT="logs/paper_experiment_${VENUE}_${SYMBOL_LC}"
RUN_ROOT="${FENIX_EXPERIMENT_ROOT:-$DEFAULT_ROOT}"
EXPERIMENT_BASE="${FENIX_EXPERIMENT_BASE_DIR:-$PROJECT_ROOT/logs}"
EXPERIMENT_BASE_ABS="$(
    "$PYTHON" - "$EXPERIMENT_BASE" <<'PY'
import sys
from pathlib import Path

print(Path(sys.argv[1]).expanduser().resolve(strict=False))
PY
)"
RUN_ROOT_ABS="$(
    "$PYTHON" - "$RUN_ROOT" <<'PY'
import sys
from pathlib import Path

print(Path(sys.argv[1]).expanduser().resolve(strict=False))
PY
)"
if [[ "${FENIX_EXPERIMENT_ALLOW_EXTERNAL_ROOT:-0}" != "1" ]]; then
    case "$RUN_ROOT_ABS/" in
        "$EXPERIMENT_BASE_ABS/"*)
            ;;
        *)
            echo "Experiment root must stay beneath $EXPERIMENT_BASE_ABS" >&2
            exit 2
            ;;
    esac
fi
if [[ "$RUN_ROOT_ABS" == "$EXPERIMENT_BASE_ABS" || -L "$RUN_ROOT" ]]; then
    echo "Experiment root must be a non-symlink child directory" >&2
    exit 2
fi
mkdir -p "$EXPERIMENT_BASE_ABS" "$RUN_ROOT_ABS"
chmod 700 "$EXPERIMENT_BASE_ABS" "$RUN_ROOT_ABS"
PIDFILE="$RUN_ROOT_ABS/pids.txt"
NANO_SIGNAL="$RUN_ROOT_ABS/nanofenix_${SYMBOL_LC}.json"
RUN_MARKER="$RUN_ROOT_ABS/.fenix-paper-experiment"
RUN_MARKER_CONTENT="fenix-paper-experiment-v1"
MODEL_SIGNING_KEY="$RUN_ROOT_ABS/.security/model-signing.key"

if ! awk \
    -v flow="$FLOW_WINDOW" \
    -v balance="$INITIAL_BALANCE" \
    -v interval="$ANALYSIS_INTERVAL" \
    -v fee="$TAKER_FEE_RATE" \
    -v slippage="$SLIPPAGE_BPS" \
    'BEGIN {
        number = "^[0-9]+([.][0-9]+)?$"
        valid = flow ~ number && balance ~ number && interval ~ number &&
                fee ~ number && slippage ~ number &&
                flow >= 1 && flow <= 120 &&
                balance > 0 && balance <= 1000000000 &&
                interval >= 1 && interval <= 86400 &&
                fee >= 0 && fee <= 0.1 &&
                slippage >= 0 && slippage <= 1000
        exit(valid ? 0 : 1)
    }'
then
    echo "Experiment numeric configuration is outside its safe range" >&2
    exit 2
fi

IFS=',' read -r -a TIMEFRAMES <<< "$TIMEFRAMES_CSV"
if [[ "${#TIMEFRAMES[@]}" -eq 0 ]]; then
    echo "At least one timeframe is required" >&2
    exit 2
fi
if [[ "${#TIMEFRAMES[@]}" -gt 16 ]]; then
    echo "At most 16 timeframes may be launched" >&2
    exit 2
fi
for timeframe in "${TIMEFRAMES[@]}"; do
    if [[ ! "$timeframe" =~ ^[1-9][0-9]*(m|h|d|w)$ ]]; then
        echo "Invalid timeframe: $timeframe" >&2
        exit 2
    fi
done

provider_environment=()
while IFS= read -r name; do
    if is_provider_variable "$name"; then
        provider_environment+=("$name=${!name}")
    fi
done < <(compgen -e)

base_child_environment=(
    "PATH=$(dirname "$PYTHON"):/usr/bin:/bin"
    "HOME=${HOME:-$PROJECT_ROOT}"
    "TMPDIR=${TMPDIR:-/tmp}"
    "LANG=${LANG:-C.UTF-8}"
)
for optional_name in SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE HTTPS_PROXY HTTP_PROXY NO_PROXY; do
    if [[ -n "${!optional_name:-}" ]]; then
        base_child_environment+=("$optional_name=${!optional_name}")
    fi
done

process_command() {
    ps -p "$1" -o command= 2>/dev/null || true
}

process_identity() {
    ps -p "$1" -o lstart= -o command= 2>/dev/null |
        "$PYTHON" -c \
            'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
}

valid_pid_entry() {
    local pid="${1:-}"
    local role="${2:-}"
    local identity="${3:-}"
    local extra="${4:-}"
    [[ "$pid" =~ ^[1-9][0-9]{0,9}$ ]] &&
        [[ "$role" == "nano" || "$role" =~ ^[1-9][0-9]*(m|h|d|w)$ ]] &&
        [[ "$identity" =~ ^[0-9a-f]{64}$ ]] &&
        [[ -z "$extra" ]]
}

validate_pidfile() {
    if [[ -L "$PIDFILE" || ( -e "$PIDFILE" && ! -f "$PIDFILE" ) ]]; then
        echo "Refusing unsafe experiment pidfile: $PIDFILE" >&2
        return 1
    fi
    [[ -f "$PIDFILE" ]] || return 0

    local pid role identity extra
    while read -r pid role identity extra; do
        [[ -z "${pid:-}" ]] && continue
        if ! valid_pid_entry "$pid" "$role" "$identity" "$extra"; then
            echo "Refusing malformed experiment pidfile: $PIDFILE" >&2
            return 1
        fi
    done < "$PIDFILE"
}

record_process() {
    local pid="$1"
    local role="$2"
    local identity
    identity="$(process_identity "$pid")"
    if [[ ! "$identity" =~ ^[0-9a-f]{64}$ ]]; then
        echo "Unable to record process identity: role=$role pid=$pid" >&2
        return 1
    fi
    printf '%s %s %s\n' "$pid" "$role" "$identity" >> "$PIDFILE"
}

is_expected_process() {
    local pid="$1"
    local role="$2"
    local expected_identity="$3"
    local command
    if [[ "$(process_identity "$pid")" != "$expected_identity" ]]; then
        return 1
    fi
    command="$(process_command "$pid")"
    if [[ "$role" == "nano" ]]; then
        [[ "$command" == *"run_nanofenixv3.py"*"--symbol $SYMBOL"* ]]
        return
    fi

    [[ "$command" == *"run_fenix.py"*"--mode paper"*"--symbol $SYMBOL"*"--timeframe $role"* ]] &&
        [[ "$command" != *"--allow-live"* ]] &&
        {
            [[ "$VENUE" == "testnet" && "$command" == *"--testnet"* ]] ||
                [[ "$VENUE" == "mainnet-data" && "$command" == *"--mainnet-data"* ]]
        }
}

status_all() {
    validate_pidfile || return 1
    if [[ ! -f "$PIDFILE" ]]; then
        echo "No experiment pidfile found at $PIDFILE"
        return 1
    fi
    local unhealthy=0
    while read -r pid role identity; do
        [[ -z "${pid:-}" ]] && continue
        if kill -0 "$pid" 2>/dev/null && is_expected_process "$pid" "$role" "$identity"; then
            echo "RUNNING_SAFE role=$role pid=$pid"
        else
            echo "STOPPED_OR_UNSAFE role=$role pid=$pid"
            unhealthy=1
        fi
    done < "$PIDFILE"
    return "$unhealthy"
}

stop_all() {
    validate_pidfile || return 1
    if [[ ! -f "$PIDFILE" ]]; then
        echo "No experiment pidfile found at $PIDFILE"
        return 0
    fi

    while read -r pid role identity; do
        [[ -z "${pid:-}" ]] && continue
        if kill -0 "$pid" 2>/dev/null && is_expected_process "$pid" "$role" "$identity"; then
            echo "SIGTERM role=$role pid=$pid"
            kill -TERM "$pid"
        fi
    done < "$PIDFILE"

    local deadline=$((SECONDS + 20))
    while [[ "$SECONDS" -lt "$deadline" ]]; do
        local running=0
        while read -r pid role identity; do
            [[ -z "${pid:-}" ]] && continue
            if kill -0 "$pid" 2>/dev/null &&
                is_expected_process "$pid" "$role" "$identity"; then
                running=1
            fi
        done < "$PIDFILE"
        [[ "$running" -eq 0 ]] && break
        sleep 1
    done

    while read -r pid role identity; do
        [[ -z "${pid:-}" ]] && continue
        if kill -0 "$pid" 2>/dev/null && is_expected_process "$pid" "$role" "$identity"; then
            echo "SIGKILL role=$role pid=$pid"
            kill -KILL "$pid"
        fi
    done < "$PIDFILE"
}

archive_previous_run() {
    validate_pidfile || exit 1
    if [[ -f "$PIDFILE" ]]; then
        while read -r pid role identity; do
            [[ -z "${pid:-}" ]] && continue
            if kill -0 "$pid" 2>/dev/null &&
                is_expected_process "$pid" "$role" "$identity"; then
                echo "Experiment process is still running; stop it before starting a new sample." >&2
                exit 1
            fi
        done < "$PIDFILE"
    fi
    if [[ -z "$(find "$RUN_ROOT_ABS" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
        printf '%s\n' "$RUN_MARKER_CONTENT" > "$RUN_MARKER"
        return
    fi
    if [[ ! -f "$RUN_MARKER" || -L "$RUN_MARKER" ]] ||
        [[ "$(<"$RUN_MARKER")" != "$RUN_MARKER_CONTENT" ]]; then
        echo "Refusing to archive a directory not created by the Fenix paper harness" >&2
        exit 1
    fi
    local stamp archive
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    archive="${RUN_ROOT_ABS}_archive_${stamp}_$$"
    if [[ -e "$archive" || -L "$archive" ]]; then
        echo "Refusing to overwrite existing archive: $archive" >&2
        exit 1
    fi
    mv "$RUN_ROOT_ABS" "$archive"
    mkdir -p "$RUN_ROOT_ABS"
    chmod 700 "$RUN_ROOT_ABS"
    printf '%s\n' "$RUN_MARKER_CONTENT" > "$RUN_MARKER"
    echo "Archived previous sample to $archive"
}

launch_nanofenix() {
    local -a nano_args=(
        "$PYTHON" run_nanofenixv3.py
        --symbol "$SYMBOL"
        --companion
        --adaptive-fusion
        --output-path "$NANO_SIGNAL"
        --runtime-state-path "$RUN_ROOT_ABS/nanofenix_runtime_${SYMBOL_LC}.json"
    )
    if [[ -n "${FENIX_EXPERIMENT_NANO_MODEL:-}" ]]; then
        nano_args+=(--model "$FENIX_EXPERIMENT_NANO_MODEL")
    fi

    nohup env -i \
        "${base_child_environment[@]}" \
        FENIX_SKIP_DOTENV=1 \
        PYTHONPATH="$PROJECT_ROOT" \
        PYTHONUNBUFFERED=1 \
        FENIX_MODEL_SIGNING_KEY_FILE="$MODEL_SIGNING_KEY" \
        NANOFENIXV3_COMPANION_OBSERVER_ONLY=1 \
        NANOFENIX_USE_TESTNET="$([[ "$VENUE" == "testnet" ]] && echo 1 || echo 0)" \
        "${nano_args[@]}" \
        > "$RUN_ROOT_ABS/nanofenix.log" 2>&1 &
    local pid=$!
    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "NanoFenix failed startup validation: pid=$pid" >&2
        exit 1
    fi
    record_process "$pid" nano
    echo "Launched observer-only NanoFenix pid=$pid"
}

launch_candidate() {
    local timeframe="$1"
    local index="$2"
    local slug="${SYMBOL_LC}_${timeframe}"
    local -a venue_args
    if [[ "$VENUE" == "testnet" ]]; then
        venue_args=(--testnet)
    else
        venue_args=(--mainnet-data)
    fi

    local -a nano_env=(FENIX_ENABLE_NANOFENIX_COMPANION=0)
    if [[ "$WITH_NANO" == "1" ]]; then
        nano_env=(
            FENIX_ENABLE_NANOFENIX_COMPANION=1
            FENIX_NANOFENIX_SIGNAL_PATH="$NANO_SIGNAL"
        )
    fi
    local -a candidate_args=(
        "$PYTHON" run_fenix.py
        --mode paper
        "${venue_args[@]}"
        --symbol "$SYMBOL"
        --timeframe "$timeframe"
        --interval "$ANALYSIS_INTERVAL"
        --trade-flow-window-sec "$FLOW_WINDOW"
    )
    if [[ -n "$TEAM_MODELS" ]]; then
        candidate_args+=(--team-models "$TEAM_MODELS")
    fi

    nohup env -i \
        "${base_child_environment[@]}" \
        "${provider_environment[@]}" \
        FENIX_SKIP_DOTENV=1 \
        PYTHONPATH="$PROJECT_ROOT" \
        PYTHONUNBUFFERED=1 \
        DATABASE_URL="sqlite+aiosqlite:///$RUN_ROOT_ABS/fenix_${slug}.db" \
        FENIX_INSTANCE_LOCK_DIR="$RUN_ROOT_ABS/locks_${slug}" \
        FENIX_RISK_MANAGER_STORAGE_PATH="$RUN_ROOT_ABS/risk_${slug}.jsonl" \
        FENIX_REASONING_BANK_DIR="$RUN_ROOT_ABS/reasoning_${slug}" \
        FENIX_LLM_RESPONSE_LOG_DIR="$RUN_ROOT_ABS/llm_${slug}" \
        FENIX_OPERATIONAL_STATE_DIR="$RUN_ROOT_ABS/operational_${slug}" \
        FENIX_MODEL_SIGNING_KEY_FILE="$MODEL_SIGNING_KEY" \
        FENIX_INSTANCE_ID="paper-experiment-${VENUE}-${slug}" \
        FENIX_BALANCE_FALLBACK_USDT="$INITIAL_BALANCE" \
        FENIX_PAPER_TAKER_FEE_RATE="$TAKER_FEE_RATE" \
        FENIX_PAPER_SLIPPAGE_BPS="$SLIPPAGE_BPS" \
        FENIX_TRADE_IMBALANCE_WINDOW_SEC="$FLOW_WINDOW" \
        FENIX_ANALYSIS_STAGGER_OFFSET_SEC="$((index * 15))" \
        FENIX_ANALYZE_ON_START=1 \
        "${nano_env[@]}" \
        "${candidate_args[@]}" \
        > "$RUN_ROOT_ABS/fenix_${slug}.log" 2>&1 &
    local pid=$!
    local identity
    sleep 1
    identity="$(process_identity "$pid")"
    if ! kill -0 "$pid" 2>/dev/null ||
        ! is_expected_process "$pid" "$timeframe" "$identity"; then
        echo "Candidate failed startup safety validation: timeframe=$timeframe pid=$pid" >&2
        process_command "$pid" >&2
        exit 1
    fi
    record_process "$pid" "$timeframe"
    echo "Launched paper candidate timeframe=$timeframe pid=$pid"
}

action="${1:-status}"
case "$action" in
    status)
        status_all
        ;;
    stop)
        stop_all
        ;;
    start)
        archive_previous_run
        validate_pidfile || exit 1
        : > "$PIDFILE"
        if [[ "$WITH_NANO" == "1" ]]; then
            launch_nanofenix
        fi
        for index in "${!TIMEFRAMES[@]}"; do
            launch_candidate "${TIMEFRAMES[$index]}" "$index"
        done
        echo "Paper experiment started: venue=$VENUE symbol=$SYMBOL root=$RUN_ROOT_ABS"
        echo "Inspect: $PYTHON scripts/inspect_paper_experiment.py --root $RUN_ROOT_ABS"
        ;;
    *)
        echo "Usage: $0 [start|status|stop]" >&2
        exit 2
        ;;
esac
