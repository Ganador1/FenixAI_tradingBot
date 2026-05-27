#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

SYMBOL="${1:-ETHUSDT}"
SYMBOL_LC="$(echo "$SYMBOL" | tr '[:upper:]' '[:lower:]')"
STATE_PATH="${NANOFENIX_SIGNAL_STATE_PATH:-logs/nanofenix_companion_${SYMBOL_LC}.json}"

export NANOFENIX_SIGNAL_STATE_PATH="$STATE_PATH"

echo "Starting NanoFenix companion for ${SYMBOL}"
echo "Signal state: ${NANOFENIX_SIGNAL_STATE_PATH}"

exec ./fenix_env/bin/python run_nanofenixv2.py --symbol "$SYMBOL"
