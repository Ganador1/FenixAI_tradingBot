#!/usr/bin/env python3
"""Safely repair historical ReasoningBank JSONL files.

This is an operator-only migration. It is dry-run by default and never targets
a hard-coded production directory. Use ``--apply`` after reviewing the preview.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from src.security.private_files import (
    ensure_private_directory,
    read_private_text,
    write_private_text,
)

DEFAULT_KEEP_RISK = 500
MAX_INPUT_BYTES = 64 * 1024 * 1024
_SAFE_SUFFIX = re.compile(r"\.bak-[A-Za-z0-9_.-]{1,40}")


def _regular_file(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError(f"{path} cannot be a symbolic link")
    if not path.is_file():
        raise ValueError(f"{path} must be an existing regular file")
    return path


def _backup_path(source: Path, suffix: str) -> Path:
    if not _SAFE_SUFFIX.fullmatch(suffix):
        raise ValueError("backup suffix must look like .bak-YYYYMMDD")
    backup = source.with_name(source.name + suffix)
    if backup.is_symlink():
        raise ValueError(f"{backup} cannot be a symbolic link")
    return backup


def _load_lines(path: Path) -> list[str]:
    return [
        line
        for line in read_private_text(_regular_file(path), max_bytes=MAX_INPUT_BYTES).splitlines()
        if line.strip()
    ]


def build_repairs(bank: Path, keep_risk: int) -> tuple[dict[Path, str], dict[str, int]]:
    """Build bounded replacement content without modifying the filesystem."""
    if not 1 <= keep_risk <= 10_000:
        raise ValueError("keep-risk must be between 1 and 10000")
    if bank.is_symlink() or not bank.is_dir():
        raise ValueError("bank must be a real existing directory")

    risk_file = bank / "risk_manager.jsonl"
    sentiment_file = bank / "sentiment_agent.jsonl"
    risk_lines = _load_lines(risk_file)
    sentiment_lines = _load_lines(sentiment_file)

    kept_risk = risk_lines[-keep_risk:]
    reset = 0
    repaired_sentiment: list[str] = []
    for line in sentiment_lines:
        entry = json.loads(line)
        if not isinstance(entry, dict):
            raise ValueError("sentiment entries must be JSON objects")
        action = str(entry.get("action", "")).upper()
        if entry.get("success") is not None and action in {
            "NEUTRAL",
            "NEGATIVE",
            "POSITIVE",
            "UNKNOWN",
        }:
            entry["success"] = None
            entry["reward"] = None
            entry["reward_notes"] = None
            entry["evaluated_at"] = None
            reset += 1
        repaired_sentiment.append(
            json.dumps(entry, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        )

    replacements = {
        risk_file: "\n".join(kept_risk) + "\n",
        sentiment_file: "\n".join(repaired_sentiment) + "\n",
    }
    return replacements, {
        "risk_before": len(risk_lines),
        "risk_after": len(kept_risk),
        "sentiment_reset": reset,
    }


def apply_repairs(replacements: dict[Path, str], suffix: str) -> None:
    """Back up originals and atomically install the reviewed replacements."""
    for source, replacement in replacements.items():
        backup = _backup_path(source, suffix)
        if backup.exists():
            raise FileExistsError(f"backup already exists: {backup}")
        write_private_text(backup, read_private_text(source, max_bytes=MAX_INPUT_BYTES))
        write_private_text(source, replacement)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bank",
        type=Path,
        default=Path("logs/reasoning_bank"),
        help="ReasoningBank directory (default: logs/reasoning_bank)",
    )
    parser.add_argument("--keep-risk", type=int, default=DEFAULT_KEEP_RISK)
    parser.add_argument("--backup-suffix", default=".bak-20260703")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the migration. Without this flag only a preview is printed.",
    )
    args = parser.parse_args()

    bank = args.bank.expanduser().resolve(strict=True)
    replacements, summary = build_repairs(bank, args.keep_risk)
    print(json.dumps({**summary, "bank": str(bank), "apply": args.apply}, indent=2))
    if not args.apply:
        print("Dry run only; pass --apply after reviewing this preview.")
        return 0

    ensure_private_directory(bank)
    apply_repairs(replacements, args.backup_suffix)
    print("Maintenance completed with private backups and atomic replacements.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
