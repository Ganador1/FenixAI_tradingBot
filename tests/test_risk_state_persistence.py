"""Tests that an active CAUTION/SEVERE cooldown survives a process restart.

Regression coverage for the 2026-07-05 streak: 4 restarts each reset the risk
mode to NORMAL and dropped the loss streak, so 1800s CAUTION cooldowns were
silently discarded mid-cooldown and the bot re-entered right after losses.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.risk.runtime_risk_manager import RuntimeRiskManager


def _write_state(path, *, mode, cooldown_start, risk_bias=0.7, extra=None):
    line = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trading_day": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "daily_pnl": -5.0,
        "daily_start_balance": 500.0,
        "peak_balance": 520.0,
        "all_time_peak": 520.0,
        "current_balance": 500.0,
        "current_mode": mode,
        "risk_bias": risk_bias,
        "cooldown_start": cooldown_start.isoformat() if cooldown_start else None,
    }
    if extra:
        line.update(extra)
    path.write_text(json.dumps(line) + "\n")


def test_active_caution_is_restored_after_restart(tmp_path):
    state = tmp_path / "risk_manager.jsonl"
    rm0 = RuntimeRiskManager(storage_path=str(state))
    cooldown = rm0.config.caution_cooldown_seconds
    # Cooldown started 10s ago -> still well within the (>=300s) window.
    _write_state(state, mode="CAUTION", cooldown_start=datetime.now(timezone.utc) - timedelta(seconds=10))

    rm = RuntimeRiskManager(storage_path=str(state))
    assert rm.current_status.mode == "CAUTION"
    assert rm._cooldown_start is not None
    # It must still be considered active on load.
    assert rm._current_status_active(datetime.now(timezone.utc)) is True
    assert cooldown >= 300  # sanity: cooldown scaled to timeframe (post-mortem fix)


def test_expired_caution_is_not_restored(tmp_path):
    state = tmp_path / "risk_manager.jsonl"
    rm0 = RuntimeRiskManager(storage_path=str(state))
    stale = rm0.config.caution_cooldown_seconds + 60
    _write_state(state, mode="CAUTION", cooldown_start=datetime.now(timezone.utc) - timedelta(seconds=stale))

    rm = RuntimeRiskManager(storage_path=str(state))
    assert rm.current_status.mode == "NORMAL"


def test_normal_mode_is_not_restored(tmp_path):
    state = tmp_path / "risk_manager.jsonl"
    _write_state(state, mode="NORMAL", cooldown_start=None)
    rm = RuntimeRiskManager(storage_path=str(state))
    assert rm.current_status.mode == "NORMAL"


def test_missing_cooldown_start_does_not_restore(tmp_path):
    state = tmp_path / "risk_manager.jsonl"
    _write_state(state, mode="CAUTION", cooldown_start=None)
    rm = RuntimeRiskManager(storage_path=str(state))
    assert rm.current_status.mode == "NORMAL"


def test_severe_cooldown_restored_and_blocks(tmp_path):
    state = tmp_path / "risk_manager.jsonl"
    rm0 = RuntimeRiskManager(storage_path=str(state))
    _write_state(
        state,
        mode="SEVERE",
        cooldown_start=datetime.now(timezone.utc) - timedelta(seconds=5),
        risk_bias=0.3,
    )
    rm = RuntimeRiskManager(storage_path=str(state))
    assert rm.current_status.mode == "SEVERE"
    # SEVERE restore should block trading while the cooldown is live.
    allowed, _status = rm.check_trade_allowed("ETHUSDC", 50.0)
    assert allowed is False


def test_save_then_reload_roundtrip_preserves_caution(tmp_path):
    """Full round-trip: arm CAUTION via _save_state, reload in a fresh manager."""
    state = tmp_path / "risk_manager.jsonl"
    rm0 = RuntimeRiskManager(storage_path=str(state))
    rm0._cooldown_start = datetime.now(timezone.utc)
    rm0.current_status.mode = "CAUTION"
    rm0.current_status.risk_bias = 0.7
    rm0._last_trading_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rm0._save_state()

    rm1 = RuntimeRiskManager(storage_path=str(state))
    assert rm1.current_status.mode == "CAUTION"


def test_update_balance_anchors_clean_state_before_trades(tmp_path):
    """A fresh risk state (no prior baseline) must anchor current/peak to the
    real balance on the first update_balance, so drawdown is not computed against
    a zero baseline. Regression for the SOL current_balance=-0.33 seen 2026-07-05
    when an instance only closed an inherited position and never sized a new one.
    """
    state = tmp_path / "risk_manager.jsonl"
    rm = RuntimeRiskManager(storage_path=str(state))
    assert rm._current_balance == 0.0  # clean start
    rm.update_balance(540.0)
    assert rm._current_balance == pytest.approx(540.0)
    assert rm._peak_balance == pytest.approx(540.0)


def test_update_balance_then_trade_keeps_realistic_balance(tmp_path):
    state = tmp_path / "risk_manager.jsonl"
    rm = RuntimeRiskManager(storage_path=str(state))
    rm.update_balance(540.0)
    from src.risk.runtime_risk_manager import TradeRecord
    rm.record_trade(
        TradeRecord(
            trade_id="t1",
            timestamp=datetime.now(timezone.utc),
            symbol="SOLUSDT",
            decision="SELL",
            entry_price=80.95,
            exit_price=81.0,
            pnl=-0.33,
            success=False,
        )
    )
    # Balance must be ~ real balance + pnl, NOT a tiny negative number.
    assert rm._current_balance == pytest.approx(539.67, abs=0.01)


if __name__ == "__main__":
    import os

    raise SystemExit(pytest.main([os.path.abspath(__file__), "-v"]))
