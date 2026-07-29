"""Log retention: recursive coverage with protected audit directories.

Regression for the broken retention found on 2026-07-10: the cleanup utility
existed but nothing ever invoked it, and its top-level-only glob patterns
missed the llm_responses* trees where ~43k of the 47k files actually lived.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from src.utils.log_cleanup import PROTECTED_DIRS, clean_old_logs, run_retention_pass


def _age(path: Path, days: float) -> None:
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def _build_log_tree(root: Path) -> dict[str, Path]:
    (root / "llm_responses" / "qabba_enhanced").mkdir(parents=True)
    (root / "live_ledger").mkdir()
    (root / "runtime_instances").mkdir()

    files = {
        "old_root_log": root / "fenix_20260601_000000.log",
        "old_root_jsonl": root / "hybrid_signals_ETHUSDT_old.jsonl",
        "old_agent_json": root / "llm_responses" / "qabba_enhanced" / "resp_old.json",
        "old_agent_txt": root / "llm_responses" / "qabba_enhanced" / "prompt_old.txt",
        "old_ledger": root / "live_ledger" / "ethusdc-live.jsonl",
        "heartbeat": root / "runtime_instances" / "ethusdc-live.json",
        "fresh_log": root / "fenix_today.log",
    }
    for path in files.values():
        path.write_text("x", encoding="utf-8")
    for key in ("old_root_log", "old_root_jsonl", "old_agent_json", "old_agent_txt", "old_ledger"):
        _age(files[key], days=45)
    return files


def test_retention_pass_recursive_and_protects_audit_dirs(tmp_path):
    files = _build_log_tree(tmp_path)

    stats = run_retention_pass(log_dir=str(tmp_path), days_old=30)

    assert not files["old_root_log"].exists()
    assert not files["old_root_jsonl"].exists()
    assert not files["old_agent_json"].exists()
    assert not files["old_agent_txt"].exists()
    # The durable trade audit must survive retention even when stale.
    assert files["old_ledger"].exists()
    assert files["heartbeat"].exists()
    assert files["fresh_log"].exists()
    assert stats["deleted"] == 4


def test_retention_pass_reads_days_from_env(tmp_path, monkeypatch):
    files = _build_log_tree(tmp_path)
    monkeypatch.setenv("FENIX_LOG_RETENTION_DAYS", "60")

    stats = run_retention_pass(log_dir=str(tmp_path))

    # Nothing is 60 days old yet.
    assert stats["deleted"] == 0
    assert files["old_root_log"].exists()


def test_retention_pass_disabled_with_nonpositive_days(tmp_path, monkeypatch):
    monkeypatch.setenv("FENIX_LOG_RETENTION_DAYS", "0")

    stats = run_retention_pass(log_dir=str(tmp_path))

    assert stats.get("disabled") is True
    assert stats["deleted"] == 0


def test_clean_old_logs_counts_overlapping_patterns_once(tmp_path):
    stale = tmp_path / "fenix_stale.log"
    stale.write_text("x", encoding="utf-8")
    _age(stale, days=45)

    # "*.log" and "**/*.log" both match the same file; it must be counted once.
    stats = clean_old_logs(
        log_dir=str(tmp_path),
        days_old=30,
        patterns=["*.log", "**/*.log"],
    )

    assert stats["deleted"] == 1


def test_protected_dirs_cover_audit_and_runtime_state():
    assert {"live_ledger", "runtime_locks", "runtime_instances"} <= PROTECTED_DIRS


def test_cleanup_never_follows_symlinked_files_or_directories(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    outside_log = outside / "valuable.log"
    outside_log.write_text("keep", encoding="utf-8")
    _age(outside_log, days=45)

    (tmp_path / "linked-file.log").symlink_to(outside_log)
    (tmp_path / "linked-directory").symlink_to(outside, target_is_directory=True)

    stats = clean_old_logs(
        log_dir=str(tmp_path),
        days_old=30,
        patterns=["*.log", "**/*.log"],
    )

    assert stats["deleted"] == 0
    assert outside_log.read_text(encoding="utf-8") == "keep"


def test_cleanup_rejects_symlinked_root(tmp_path):
    real_logs = tmp_path / "real"
    real_logs.mkdir()
    linked_logs = tmp_path / "linked"
    linked_logs.symlink_to(real_logs, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link"):
        clean_old_logs(log_dir=str(linked_logs), days_old=30)


@pytest.mark.parametrize("days_old", [0, -1, float("inf"), float("nan"), 36_501])
def test_direct_cleanup_rejects_dangerous_retention_windows(tmp_path, days_old):
    with pytest.raises(ValueError, match="days_old"):
        clean_old_logs(log_dir=str(tmp_path), days_old=days_old)


@pytest.mark.parametrize("pattern", ["../*.log", "/tmp/*.log", "x\x00.log"])
def test_cleanup_rejects_patterns_that_can_escape_root(tmp_path, pattern):
    with pytest.raises(ValueError, match="Unsafe"):
        clean_old_logs(log_dir=str(tmp_path), days_old=30, patterns=[pattern])
