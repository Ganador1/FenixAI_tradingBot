#!/usr/bin/env python3
"""Quarantine duplicated ReasoningBank entries left by the 2026-07 fan-in bug.

The duplicated LangGraph fan-in executed the decision/judge nodes twice per
cycle, appending two ReasoningBank entries with the same prompt_digest within
seconds of each other (81 visual analyses -> 162 decisions on 2026-07-09).
Those ghost entries bias retrieval, scorecards and distilled strategies.

Detection is surgical, not date-based: within each agent's JSONL, entries
sharing a prompt_digest whose created_at timestamps fall within a small
window are duplicates of one cycle — every one after the first is marked
``metadata.quarantined`` so retrieval skips it. Nothing is deleted, so the
operation is reversible by clearing the flag.

Default is a DRY RUN; pass --apply to rewrite the files (atomic replace).

Usage:
    python scripts/quarantine_reasoning_bank_duplicates.py \
        [--dir logs/reasoning_bank ...] [--window-sec 300] [--apply]
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

QUARANTINE_REASON = "fanin-duplicate-2026-07"
DEFAULT_DIRS = [
    "logs/reasoning_bank",
    "logs/reasoning_bank_ethusdc",
    "logs/reasoning_bank_solusdt",
]


def _parse_created_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def find_duplicate_indices(records: list[dict], window_sec: float) -> list[int]:
    """Indices of every entry that repeats an earlier prompt_digest in-window."""
    by_digest: dict[tuple[str, str], list[tuple[int, datetime | None]]] = defaultdict(list)
    for idx, record in enumerate(records):
        digest = record.get("prompt_digest")
        agent = record.get("agent")
        if not digest:
            continue
        by_digest[(str(agent), str(digest))].append(
            (idx, _parse_created_at(record.get("created_at")))
        )

    duplicates: list[int] = []
    for occurrences in by_digest.values():
        if len(occurrences) < 2:
            continue
        first_idx, first_ts = occurrences[0]
        for idx, ts in occurrences[1:]:
            if first_ts is None or ts is None:
                # Same digest but no comparable timestamps: still a repeat of
                # the same prompt in the same file — treat as duplicate.
                duplicates.append(idx)
            elif abs((ts - first_ts).total_seconds()) <= window_sec:
                duplicates.append(idx)
    return duplicates


def process_file(path: Path, window_sec: float, apply: bool) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    records: list[dict] = []
    raw_keep: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
            raw_keep.append(line)
        except json.JSONDecodeError:
            records.append({})
            raw_keep.append(line)

    duplicate_indices = [
        idx
        for idx in find_duplicate_indices(records, window_sec)
        if not (records[idx].get("metadata") or {}).get("quarantined")
    ]

    stats = {
        "file": str(path),
        "entries": len(records),
        "duplicates": len(duplicate_indices),
        "applied": False,
    }
    if not duplicate_indices or not apply:
        return stats

    stamp = datetime.now().astimezone().isoformat()
    for idx in duplicate_indices:
        metadata = records[idx].get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["quarantined"] = QUARANTINE_REASON
        metadata["quarantined_at"] = stamp
        records[idx]["metadata"] = metadata
        raw_keep[idx] = json.dumps(records[idx], ensure_ascii=False)

    descriptor, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name, suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("\n".join(raw_keep) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    stats["applied"] = True
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        action="append",
        dest="dirs",
        help="ReasoningBank directory (repeatable). Default: the three live dirs.",
    )
    parser.add_argument(
        "--window-sec",
        type=float,
        default=300.0,
        help="Max seconds between same-digest entries to call them one cycle",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Rewrite files (default is dry run)",
    )
    args = parser.parse_args()

    directories = args.dirs or DEFAULT_DIRS
    total_duplicates = 0
    for directory in directories:
        base = Path(directory)
        if not base.exists():
            print(f"-- skip (missing): {base}")
            continue
        for jsonl in sorted(base.glob("*.jsonl")):
            stats = process_file(jsonl, args.window_sec, args.apply)
            total_duplicates += stats["duplicates"]
            marker = "APPLIED" if stats["applied"] else "dry-run"
            print(
                f"[{marker}] {stats['file']}: {stats['duplicates']} duplicates "
                f"of {stats['entries']} entries"
            )

    if not args.apply and total_duplicates:
        print(f"\nDry run: {total_duplicates} entries would be quarantined. "
              "Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
