"""Tests for the NanoFenix hard-veto confluence override.

Regression coverage for the 2026-07-06 xray finding: the NanoFenix hard-veto
blocked correct trend-following SELLs (ETH −1.68%) that Technical + QABBA +
Decision all called right. The veto now stands down when both primary agents
agree with a HIGH-conviction decision.
"""

import pytest

from src.trading.engine import TradingEngine


def _engine():
    return TradingEngine.__new__(TradingEngine)


def _dd(tech, tech_c, qabba, qabba_c):
    return {
        "_execution_technical_signal": tech,
        "_execution_technical_confidence": tech_c,
        "_execution_qabba_signal": qabba,
        "_execution_qabba_confidence": qabba_c,
    }


def test_both_primaries_agree_confirms():
    eng = _engine()
    dd = _dd("SELL", 0.70, "SELL_QABBA", 0.75)
    assert eng._agents_confirm_direction("SELL", dd) is True


def test_buy_qabba_maps_to_buy():
    eng = _engine()
    dd = _dd("BUY", 0.65, "BUY_QABBA", 0.80)
    assert eng._agents_confirm_direction("BUY", dd) is True


def test_only_one_primary_agrees_does_not_confirm():
    eng = _engine()
    dd = _dd("SELL", 0.70, "HOLD_QABBA", 0.50)
    assert eng._agents_confirm_direction("SELL", dd) is False


def test_low_confidence_does_not_confirm():
    eng = _engine()
    dd = _dd("SELL", 0.55, "SELL_QABBA", 0.58)  # both below 0.60 default
    assert eng._agents_confirm_direction("SELL", dd) is False


def test_disagreement_does_not_confirm():
    eng = _engine()
    dd = _dd("BUY", 0.80, "SELL_QABBA", 0.80)
    assert eng._agents_confirm_direction("BUY", dd) is False


def test_direction_mismatch_does_not_confirm():
    eng = _engine()
    # both say SELL but decision is BUY
    dd = _dd("SELL", 0.80, "SELL_QABBA", 0.80)
    assert eng._agents_confirm_direction("BUY", dd) is False


def test_custom_min_conf_env(monkeypatch):
    monkeypatch.setenv("FENIX_NANOFENIX_OVERRIDE_MIN_AGENT_CONF", "0.80")
    eng = _engine()
    dd = _dd("SELL", 0.70, "SELL_QABBA", 0.75)  # below 0.80 now
    assert eng._agents_confirm_direction("SELL", dd) is False


if __name__ == "__main__":
    import os

    raise SystemExit(pytest.main([os.path.abspath(__file__), "-v"]))
