#!/usr/bin/env bash
set -euo pipefail

# Usage: update_doc_references.sh [dry-run|apply]
MODE=${1:-dry-run}

MAPPINGS=(
  "MEJORAS_IMPLEMENTADAS.md|docs/archives/reports/MEJORAS_IMPLEMENTADAS.md"
  "GUIA_MEJORAS.md|docs/archives/reports/GUIA_MEJORAS.md"
  "README_MEJORAS.md|docs/archives/reports/README_MEJORAS.md"
  "RESUMEN_CAMBIOS.md|docs/archives/reports/RESUMEN_CAMBIOS.md"
  "RESUMEN_VISUAL.md|docs/archives/reports/RESUMEN_VISUAL.md"
  "PROYECTO_FINAL.md|docs/archives/reports/PROYECTO_FINAL.md"
  "INICIO_RAPIDO_v2.md|docs/archives/reports/INICIO_RAPIDO_v2.md"
  "CHECKLIST_MEJORAS.md|docs/archives/reports/CHECKLIST_MEJORAS.md"
)

echo "Mode: $MODE"

for mapping in "${MAPPINGS[@]}"; do
  old="${mapping%%|*}"
  new="${mapping#*|}"
  echo "\n---\nProcessing mapping: $old -> $new"
  echo "Searching for occurrences..."
  # list files where the old filename appears
  files=$(git grep -I --line-number --heading --break -- "${old}" || true)
  if [[ -z "${files}" ]]; then
    echo "No occurrences found for ${old}."
    continue
  fi
  echo "Found occurrences:\n${files}"
  if [[ "${MODE}" == "apply" ]]; then
    echo "Applying replacements in files..."
    # On macOS, sed -i requires an argument; use inline perl for portability
    git grep -l "${old}" | xargs -I{} perl -0777 -pe "s#${old}#${new}#g" -i {}
    echo "Replacements applied for ${old}."
  else
    echo "Dry run: nothing changed. Run with 'apply' to make changes."
  fi
done

echo "\nDone."
