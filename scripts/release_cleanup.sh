#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${FENIX_RELEASE_ROOT:-$(pwd)}"
APPLY=0

for arg in "$@"; do
  case "$arg" in
    --apply)
      APPLY=1
      ;;
    --help|-h)
      cat <<'USAGE'
Usage: scripts/release_cleanup.sh [--apply]

Dry-run by default. Reports local/generated artifacts that should not be
included in a public release. With --apply, moves artifacts to a local
.release_quarantine directory, or to trash when the trash command is available.
No git commands are used.
USAGE
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

cd "$ROOT_DIR"

echo "FenixAI release cleanup preflight"
echo "root: $ROOT_DIR"
if [[ "$APPLY" -eq 1 ]]; then
  echo "mode: apply"
else
  echo "mode: dry-run"
fi

volatile_paths=(
  ".DS_Store"
  "debug_visual_raw.log"
  "logs"
  "src/logs"
  "cache"
  ".pytest_cache"
  ".mypy_cache"
  ".ruff_cache"
  "htmlcov"
  "dist"
  "build"
  "frontend/dist"
  "frontend/playwright-report"
  "frontend/test-results"
  "src/fenix_trading_bot.egg-info"
)

move_to_quarantine() {
  local target="$1"
  local stamp="$2"
  if command -v trash >/dev/null 2>&1; then
    trash "$target"
    return
  fi
  local safe_name="${target//\//__}"
  mkdir -p .release_quarantine
  mv "$target" ".release_quarantine/${safe_name}.${stamp}"
}

echo
echo "Volatile artifacts:"
stamp="$(date +%Y%m%d_%H%M%S)"
found=0
for path in "${volatile_paths[@]}"; do
  if [[ -e "$path" ]]; then
    found=1
    echo "  - $path"
    if [[ "$APPLY" -eq 1 ]]; then
      move_to_quarantine "$path" "$stamp"
    fi
  fi
done
if [[ "$found" -eq 0 ]]; then
  echo "  none found"
fi

echo
echo "Sensitive-pattern scan over release surface:"
if command -v rg >/dev/null 2>&1; then
  rg -n --hidden \
    --glob '!.git/**' \
    --glob '!.venv/**' \
    --glob '!fenix_env/**' \
    --glob '!frontend/node_modules/**' \
    --glob '!logs/**' \
    --glob '!src/logs/**' \
    --glob '!cache/**' \
    --glob '!backups/**' \
    --glob '!*.egg-info/**' \
    'BINANCE_API_KEY|BINANCE_API_SECRET|OPENAI_API_KEY|HUGGINGFACE_API_KEY|GROQ_API_KEY|_API_KEY|_SECRET|sk-|-----BEGIN PRIVATE KEY-----|BEGIN RSA PRIVATE KEY' \
    . || true
else
  grep -RInE \
    --exclude-dir=.git \
    --exclude-dir=.venv \
    --exclude-dir=fenix_env \
    --exclude-dir=node_modules \
    --exclude-dir=logs \
    --exclude-dir=cache \
    --exclude-dir=backups \
    'BINANCE_API_KEY|BINANCE_API_SECRET|OPENAI_API_KEY|HUGGINGFACE_API_KEY|GROQ_API_KEY|_API_KEY|_SECRET|sk-|-----BEGIN PRIVATE KEY-----|BEGIN RSA PRIVATE KEY' \
    . || true
fi

echo
echo "Review any scan hits before publishing. This script does not alter history."
