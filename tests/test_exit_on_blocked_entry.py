"""Tests for the exit-on-blocked-entry behaviour.

Regression coverage for the 2026-07-05 losing streak: entry filters
(RESISTANCE, MTF_VETO, POST_STOPOUT, ...) used to also suppress the
opposite-signal EXIT of an already-open position, so a BUY that was vetoed as
an entry could not close an open SHORT that the market had turned against.
"""

import os
from types import SimpleNamespace

import pytest

from src.trading.engine import TradingEngine


def _engine_with_position(side: str | None):
    engine = TradingEngine.__new__(TradingEngine)
    tracked = SimpleNamespace(side=side) if side is not None else None
    engine._get_tracked_position = lambda: tracked
    return engine


def test_blocked_buy_exits_open_short():
    """A vetoed BUY still closes an open SHORT (opposite side)."""
    engine = _engine_with_position("SHORT")
    assert engine._exit_signal_for_blocked_entry("BUY") == "BUY"


def test_blocked_sell_exits_open_long():
    """A vetoed SELL still closes an open LONG (opposite side)."""
    engine = _engine_with_position("LONG")
    assert engine._exit_signal_for_blocked_entry("SELL") == "SELL"


def test_blocked_buy_does_not_touch_open_long():
    """A vetoed BUY on an open LONG is a same-side add -> stays HOLD (no exit)."""
    engine = _engine_with_position("LONG")
    assert engine._exit_signal_for_blocked_entry("BUY") == "HOLD"


def test_blocked_sell_does_not_touch_open_short():
    engine = _engine_with_position("SHORT")
    assert engine._exit_signal_for_blocked_entry("SELL") == "HOLD"


def test_no_open_position_returns_hold():
    engine = _engine_with_position(None)
    assert engine._exit_signal_for_blocked_entry("BUY") == "HOLD"


def test_hold_signal_is_noop():
    engine = _engine_with_position("SHORT")
    assert engine._exit_signal_for_blocked_entry("HOLD") == "HOLD"
    assert engine._exit_signal_for_blocked_entry("") == "HOLD"


def test_kill_switch_restores_old_behaviour(monkeypatch):
    """FENIX_BLOCK_EXIT_ON_ENTRY_FILTER=1 keeps entry blocks suppressing exits."""
    monkeypatch.setenv("FENIX_BLOCK_EXIT_ON_ENTRY_FILTER", "1")
    engine = _engine_with_position("SHORT")
    assert engine._exit_signal_for_blocked_entry("BUY") == "HOLD"


def test_default_env_allows_exit(monkeypatch):
    monkeypatch.delenv("FENIX_BLOCK_EXIT_ON_ENTRY_FILTER", raising=False)
    engine = _engine_with_position("SHORT")
    assert engine._exit_signal_for_blocked_entry("BUY") == "BUY"


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-v"]))
