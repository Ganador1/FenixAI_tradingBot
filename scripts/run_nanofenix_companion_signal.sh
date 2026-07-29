#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
PYTHON="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Python executable not found: $PYTHON" >&2
  exit 2
fi

if [[ -f ".env" && "${FENIX_SECURE_DOTENV_LOADED:-0}" != "1" ]]; then
  exec "$PYTHON" scripts/secure_dotenv_exec.py --env "$ROOT_DIR/.env" -- \
    bash "$0" "$@"
fi

SYMBOL="${1:-ETHUSDT}"
if [[ ! "$SYMBOL" =~ ^[A-Z0-9]{5,20}$ ]]; then
  echo "Symbol must be an uppercase Binance symbol" >&2
  exit 2
fi
SYMBOL_LC="$(echo "$SYMBOL" | tr '[:upper:]' '[:lower:]')"
STATE_PATH="${NANOFENIX_SIGNAL_STATE_PATH:-logs/nanofenix_companion_${SYMBOL_LC}.json}"

export NANOFENIX_SIGNAL_STATE_PATH="$STATE_PATH"

echo "Starting NanoFenix companion for ${SYMBOL}"
echo "Signal state: ${NANOFENIX_SIGNAL_STATE_PATH}"

exec "$PYTHON" run_nanofenixv2.py --symbol "$SYMBOL"
