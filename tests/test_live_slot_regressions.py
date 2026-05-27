from __future__ import annotations

from pathlib import Path

from src.analysis.live_slot_regressions import (
    compare_summary_vs_closed_trades,
    detect_flip_blocked_then_trade,
    detect_nanofenix_blocked_then_trade,
)


def test_detect_flip_blocked_then_trade_from_20260305_artifact():
    regressions = detect_flip_blocked_then_trade(Path("logs/fenix_20260305_192946.log"))
    assert regressions
    assert regressions[0]["blocked_decision"] in {"BUY", "SELL"}


def test_detect_nanofenix_policy_block_then_trade_from_20260305_artifact():
    regressions = detect_nanofenix_blocked_then_trade(Path("logs/fenix_20260305_192946.log"))
    assert regressions
    assert regressions[0]["reason"].startswith("nanofenix_policy")


def test_detect_summary_and_closed_trade_divergence_from_nanov3_artifacts():
    comparison = compare_summary_vs_closed_trades(
        Path("logs/live_slot_summary_nanov3.json"),
        Path("logs/live_slot_events_nanov3.jsonl"),
    )
    assert comparison["actual_total_trades"] == 10
    assert comparison["actual_wins"] == 3
    assert comparison["actual_losses"] == 7
    assert set(comparison["mismatch_fields"]) >= {"total_trades", "wins", "losses", "total_pnl"}
