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

if [[ "${FENIX_EXPERIMENT_LOAD_DOTENV:-1}" == "1" && -f ".env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source ".env"
    set +a
fi

if [[ "$VENUE" != "testnet" && "$VENUE" != "mainnet-data" ]]; then
    echo "FENIX_EXPERIMENT_VENUE must be testnet or mainnet-data" >&2
    exit 2
fi
if [[ ! "$SYMBOL" =~ ^[A-Z0-9]{5,20}$ ]]; then
    echo "FENIX_EXPERIMENT_SYMBOL must be an uppercase Binance symbol" >&2
    exit 2
fi
if [[ ! -x "$PYTHON" ]]; then
    echo "Python executable not found: $PYTHON" >&2
    exit 2
fi
if [[ "$WITH_NANO" != "0" && "$WITH_NANO" != "1" ]]; then
    echo "FENIX_EXPERIMENT_WITH_NANO must be 0 or 1" >&2
    exit 2
fi

SYMBOL_LC="$(printf '%s' "$SYMBOL" | tr '[:upper:]' '[:lower:]')"
DEFAULT_ROOT="logs/paper_experiment_${VENUE}_${SYMBOL_LC}"
RUN_ROOT="${FENIX_EXPERIMENT_ROOT:-$DEFAULT_ROOT}"
mkdir -p "$RUN_ROOT"
RUN_ROOT_ABS="$(cd "$RUN_ROOT" && pwd)"
PIDFILE="$RUN_ROOT_ABS/pids.txt"
NANO_SIGNAL="$RUN_ROOT_ABS/nanofenix_${SYMBOL_LC}.json"

IFS=',' read -r -a TIMEFRAMES <<< "$TIMEFRAMES_CSV"
if [[ "${#TIMEFRAMES[@]}" -eq 0 ]]; then
    echo "At least one timeframe is required" >&2
    exit 2
fi
for timeframe in "${TIMEFRAMES[@]}"; do
    if [[ ! "$timeframe" =~ ^[1-9][0-9]*(m|h|d|w)$ ]]; then
        echo "Invalid timeframe: $timeframe" >&2
        exit 2
    fi
done

process_command() {
    ps -p "$1" -o command= 2>/dev/null || true
}

is_expected_process() {
    local pid="$1"
    local role="$2"
    local command
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
    if [[ ! -f "$PIDFILE" ]]; then
        echo "No experiment pidfile found at $PIDFILE"
        return 1
    fi
    local unhealthy=0
    while read -r pid role; do
        [[ -z "${pid:-}" ]] && continue
        if kill -0 "$pid" 2>/dev/null && is_expected_process "$pid" "$role"; then
            echo "RUNNING_SAFE role=$role pid=$pid"
        else
            echo "STOPPED_OR_UNSAFE role=$role pid=$pid"
            unhealthy=1
        fi
    done < "$PIDFILE"
    return "$unhealthy"
}

stop_all() {
    if [[ ! -f "$PIDFILE" ]]; then
        echo "No experiment pidfile found at $PIDFILE"
        return 0
    fi

    while read -r pid role; do
        [[ -z "${pid:-}" ]] && continue
        if kill -0 "$pid" 2>/dev/null && is_expected_process "$pid" "$role"; then
            echo "SIGTERM role=$role pid=$pid"
            kill -TERM "$pid"
        fi
    done < "$PIDFILE"

    local deadline=$((SECONDS + 20))
    while [[ "$SECONDS" -lt "$deadline" ]]; do
        local running=0
        while read -r pid role; do
            [[ -z "${pid:-}" ]] && continue
            if kill -0 "$pid" 2>/dev/null && is_expected_process "$pid" "$role"; then
                running=1
            fi
        done < "$PIDFILE"
        [[ "$running" -eq 0 ]] && break
        sleep 1
    done

    while read -r pid role; do
        [[ -z "${pid:-}" ]] && continue
        if kill -0 "$pid" 2>/dev/null && is_expected_process "$pid" "$role"; then
            echo "SIGKILL role=$role pid=$pid"
            kill -KILL "$pid"
        fi
    done < "$PIDFILE"
}

archive_previous_run() {
    if [[ -f "$PIDFILE" ]]; then
        while read -r pid role; do
            [[ -z "${pid:-}" ]] && continue
            if kill -0 "$pid" 2>/dev/null && is_expected_process "$pid" "$role"; then
                echo "Experiment process is still running; stop it before starting a new sample." >&2
                exit 1
            fi
        done < "$PIDFILE"
    fi
    if [[ -z "$(find "$RUN_ROOT_ABS" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
        return
    fi
    local stamp archive
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    archive="${RUN_ROOT_ABS}_archive_${stamp}"
    mv "$RUN_ROOT_ABS" "$archive"
    mkdir -p "$RUN_ROOT_ABS"
    echo "Archived previous sample to $archive"
}

launch_nanofenix() {
    local -a nano_args=(
        "$PYTHON" run_nanofenixv3.py
        --symbol "$SYMBOL"
        --companion
        --adaptive-fusion
        --output-path "$NANO_SIGNAL"
        --runtime-state-path "$RUN_ROOT_ABS/nanofenix_runtime_${SYMBOL_LC}.pkl"
    )
    if [[ -n "${FENIX_EXPERIMENT_NANO_MODEL:-}" ]]; then
        nano_args+=(--model "$FENIX_EXPERIMENT_NANO_MODEL")
    fi

    nohup env \
        -u BINANCE_API_KEY -u BINANCE_API_SECRET \
        -u BINANCE_TESTNET_API_KEY -u BINANCE_TESTNET_API_SECRET \
        FENIX_SKIP_DOTENV=1 \
        PYTHONPATH="$PROJECT_ROOT" \
        PYTHONUNBUFFERED=1 \
        NANOFENIXV3_COMPANION_OBSERVER_ONLY=1 \
        NANOFENIX_USE_TESTNET="$([[ "$VENUE" == "testnet" ]] && echo 1 || echo 0)" \
        "${nano_args[@]}" \
        > "$RUN_ROOT_ABS/nanofenix.log" 2>&1 &
    local pid=$!
    echo "$pid nano" >> "$PIDFILE"
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

    nohup env \
        -u BINANCE_API_KEY -u BINANCE_API_SECRET \
        -u BINANCE_TESTNET_API_KEY -u BINANCE_TESTNET_API_SECRET \
        FENIX_SKIP_DOTENV=1 \
        PYTHONPATH="$PROJECT_ROOT" \
        PYTHONUNBUFFERED=1 \
        DATABASE_URL="sqlite+aiosqlite:///$RUN_ROOT_ABS/fenix_${slug}.db" \
        FENIX_INSTANCE_LOCK_DIR="$RUN_ROOT_ABS/locks_${slug}" \
        FENIX_RISK_MANAGER_STORAGE_PATH="$RUN_ROOT_ABS/risk_${slug}.jsonl" \
        FENIX_REASONING_BANK_DIR="$RUN_ROOT_ABS/reasoning_${slug}" \
        FENIX_LLM_RESPONSE_LOG_DIR="$RUN_ROOT_ABS/llm_${slug}" \
        FENIX_OPERATIONAL_STATE_DIR="$RUN_ROOT_ABS/operational_${slug}" \
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
    sleep 1
    if ! kill -0 "$pid" 2>/dev/null || ! is_expected_process "$pid" "$timeframe"; then
        echo "Candidate failed startup safety validation: timeframe=$timeframe pid=$pid" >&2
        process_command "$pid" >&2
        exit 1
    fi
    echo "$pid $timeframe" >> "$PIDFILE"
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
