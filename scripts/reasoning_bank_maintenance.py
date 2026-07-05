"""One-time maintenance for logs/reasoning_bank (2026-07-03).

1. Compact risk_manager.jsonl (~10k unlabeled entries) to the last 500.
2. Reset sentiment labels created under the buggy evaluator (NEUTRAL/NEGATIVE/
   UNKNOWN actions were hard-labeled success=False) so the fixed AutoEvaluator
   can relabel the recent ones correctly.

Originals are backed up as *.jsonl.bak-20260703 before any rewrite.
"""

import json
import shutil
from pathlib import Path

BANK = Path("/Volumes/Ganador disk/Fenix unic agent/FenixAI/logs/reasoning_bank")
SUFFIX = ".bak-20260703"
KEEP_RISK = 500

# --- 1. Compact risk_manager ------------------------------------------------
risk_file = BANK / "risk_manager.jsonl"
backup = risk_file.with_name(risk_file.name + SUFFIX)
if not backup.exists():
    shutil.copy2(risk_file, backup)
lines = risk_file.read_text(encoding="utf-8").strip().split("\n")
kept = lines[-KEEP_RISK:]
risk_file.write_text("\n".join(kept) + "\n", encoding="utf-8")
print(f"risk_manager.jsonl: {len(lines)} -> {len(kept)} entradas (backup: {backup.name})")

# --- 2. Reset buggy sentiment labels -----------------------------------------
sent_file = BANK / "sentiment_agent.jsonl"
backup_s = sent_file.with_name(sent_file.name + SUFFIX)
if not backup_s.exists():
    shutil.copy2(sent_file, backup_s)

reset = 0
out_lines = []
for line in sent_file.read_text(encoding="utf-8").strip().split("\n"):
    try:
        e = json.loads(line)
    except json.JSONDecodeError:
        out_lines.append(line)
        continue
    action = str(e.get("action", "")).upper()
    # Labels produced by the old evaluator for non-BUY/SELL/HOLD vocab were
    # always success=False regardless of the market — invalid, reset them.
    if e.get("success") is not None and action in ("NEUTRAL", "NEGATIVE", "POSITIVE", "UNKNOWN"):
        e["success"] = None
        e["reward"] = None
        e["reward_notes"] = None
        e["evaluated_at"] = None
        reset += 1
    out_lines.append(json.dumps(e))
sent_file.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
print(f"sentiment_agent.jsonl: {reset} etiquetas inválidas reseteadas (backup: {backup_s.name})")
