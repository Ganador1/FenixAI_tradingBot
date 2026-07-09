"""Tests for the counter-trend entry gate WITH price confirmation.

Regression coverage for two things:
1. The original 2026-07-05 streak: SHORT entries fading a TRENDING/BULL regime.
2. The 2026-07-05 xray finding: the raw companion `trend` label lagged/inverted
   the real price (SOL 17-19h labelled BEAR while price rose), so a label-only
   gate blocked 6 winning BUYs. The gate now requires real price structure to
   CONFIRM the companion label before vetoing.
"""

from types import SimpleNamespace

import pytest

from src.trading.engine import TradingEngine


# EMA sets that clearly confirm each trend (fast vs slow separated > 1bps).
BULL_IND = {"ema_9": 1805.0, "ema_20": 1780.0, "last_price": 1806.0}
BEAR_IND = {"ema_9": 1770.0, "ema_20": 1795.0, "last_price": 1769.0}
# Conflicting: price/EMAs bullish even though the label may say BEAR.
BULLISH_PRICE = {"ema_9": 81.7, "ema_20": 81.0, "last_price": 81.75}


def _engine(*, enabled=True, companion=True, signal=None, status="ok", price=100.0):
    engine = TradingEngine.__new__(TradingEngine)
    engine._trend_gate_enabled = enabled
    engine._trend_gate_regimes = {"TRENDING"}
    engine._trend_gate_max_signal_age_sec = 90.0
    engine._nanofenix_companion_enabled = companion
    engine._last_companion_ema_trend_bps = None
    engine.market_data = SimpleNamespace(current_price=price)
    engine._read_nanofenix_companion_signal = lambda: (signal, status)
    return engine


# --- price CONFIRMS the label -> veto fires -------------------------------

def test_sell_into_confirmed_bull_is_blocked():
    eng = _engine(signal={"regime": "TRENDING", "trend": "BULL", "_signal_age_sec": 5.0})
    reason = eng._trend_gate_blocks_entry("SELL", BULL_IND)
    assert reason and "SELL fades" in reason and "price-confirmed" in reason


def test_buy_into_confirmed_bear_is_blocked():
    eng = _engine(signal={"regime": "TRENDING", "trend": "BEAR", "_signal_age_sec": 5.0})
    reason = eng._trend_gate_blocks_entry("BUY", BEAR_IND)
    assert reason and "BUY fades" in reason


# --- the xray failure mode: label says BEAR but price is bullish -----------

def test_buy_not_blocked_when_price_contradicts_bear_label():
    """SOL 17-19h case: companion label BEAR but price/EMAs rising -> allow BUY."""
    eng = _engine(signal={"regime": "TRENDING", "trend": "BEAR", "_signal_age_sec": 5.0})
    assert eng._trend_gate_blocks_entry("BUY", BULLISH_PRICE) is None


def test_sell_not_blocked_when_price_contradicts_bull_label():
    eng = _engine(signal={"regime": "TRENDING", "trend": "BULL", "_signal_age_sec": 5.0})
    # bearish EMAs despite BULL label
    assert eng._trend_gate_blocks_entry("SELL", BEAR_IND) is None


# --- trades WITH the trend are always allowed ------------------------------

def test_buy_with_confirmed_bull_is_allowed():
    eng = _engine(signal={"regime": "TRENDING", "trend": "BULL", "_signal_age_sec": 5.0})
    assert eng._trend_gate_blocks_entry("BUY", BULL_IND) is None


def test_sell_with_confirmed_bear_is_allowed():
    eng = _engine(signal={"regime": "TRENDING", "trend": "BEAR", "_signal_age_sec": 5.0})
    assert eng._trend_gate_blocks_entry("SELL", BEAR_IND) is None


# --- fallback to companion ema_trend_bps when engine EMAs absent -----------

def test_confirmation_falls_back_to_companion_ema_bps():
    eng = _engine(signal={"regime": "TRENDING", "trend": "BULL", "ema_trend_bps": 5.0,
                          "_signal_age_sec": 5.0})
    # no engine EMAs -> uses companion ema_trend_bps (+5 confirms BULL)
    assert eng._trend_gate_blocks_entry("SELL", {}) is not None


def test_fallback_allows_when_companion_ema_contradicts():
    eng = _engine(signal={"regime": "TRENDING", "trend": "BEAR", "ema_trend_bps": 4.0,
                          "_signal_age_sec": 5.0})
    # label BEAR but companion slope +4 (bullish) -> not confirmed -> allow BUY
    assert eng._trend_gate_blocks_entry("BUY", {}) is None


# --- kill switch restores old label-only behaviour -------------------------

def test_require_confirm_off_reverts_to_label_only(monkeypatch):
    monkeypatch.setenv("FENIX_TREND_GATE_REQUIRE_PRICE_CONFIRM", "0")
    eng = _engine(signal={"regime": "TRENDING", "trend": "BEAR", "_signal_age_sec": 5.0})
    # price bullish but confirmation disabled -> label-only veto still fires
    assert eng._trend_gate_blocks_entry("BUY", BULLISH_PRICE) is not None


# --- unchanged guards ------------------------------------------------------

def test_dead_regime_does_not_gate():
    eng = _engine(signal={"regime": "DEAD", "trend": "BULL", "_signal_age_sec": 5.0})
    assert eng._trend_gate_blocks_entry("SELL", BULL_IND) is None


def test_stale_signal_does_not_gate():
    eng = _engine(signal={"regime": "TRENDING", "trend": "BULL", "_signal_age_sec": 120.0})
    assert eng._trend_gate_blocks_entry("SELL", BULL_IND) is None


def test_disabled_gate_allows_everything():
    eng = _engine(enabled=False, signal={"regime": "TRENDING", "trend": "BULL", "_signal_age_sec": 5.0})
    assert eng._trend_gate_blocks_entry("SELL", BULL_IND) is None


def test_companion_disabled_allows_everything():
    eng = _engine(companion=False, signal={"regime": "TRENDING", "trend": "BULL", "_signal_age_sec": 5.0})
    assert eng._trend_gate_blocks_entry("SELL", BULL_IND) is None


def test_bad_signal_status_allows():
    eng = _engine(signal=None, status="signal_file_missing")
    assert eng._trend_gate_blocks_entry("SELL", BULL_IND) is None


def test_hold_signal_is_not_gated():
    eng = _engine(signal={"regime": "TRENDING", "trend": "BULL", "_signal_age_sec": 5.0})
    assert eng._trend_gate_blocks_entry("HOLD", BULL_IND) is None


if __name__ == "__main__":
    import os

    raise SystemExit(pytest.main([os.path.abspath(__file__), "-v"]))
