"""Tests for the exit-quality gate and the minimum agent-consensus entry filter.

Motivated by 2026-07-06: a vetoed QABBA-only SELL (1/3 agents, MEDIUM) closed a
winning SOL LONG below its TP, and the Decision Agent opened an ETH SHORT with
Technical=BUY + Visual=BUY (1/3 consensus) that lost -7.60 at the SL.
"""

from __future__ import annotations

import pytest

from src.trading.engine import TradingEngine


def _engine():
    return TradingEngine.__new__(TradingEngine)


def _data(tech="HOLD", qabba="HOLD", visual="HOLD"):
    return {
        "_execution_technical_signal": tech,
        "_execution_qabba_signal": qabba,
        "_execution_visual_signal": visual,
    }


class TestDirectionalAgentsAgreeing:
    def test_counts_matching_agents(self):
        e = _engine()
        assert e._directional_agents_agreeing("SELL", _data("SELL", "SELL_QABBA", "BUY")) == 2

    def test_qabba_suffix_normalized(self):
        e = _engine()
        assert e._directional_agents_agreeing("BUY", _data("HOLD", "BUY_QABBA", "HOLD")) == 1

    def test_zero_when_all_hold(self):
        e = _engine()
        assert e._directional_agents_agreeing("SELL", _data()) == 0

    def test_eth_20260706_1030_case(self):
        # Technical BUY, QABBA SELL, Visual BUY -> SELL had 1/3
        e = _engine()
        assert e._directional_agents_agreeing("SELL", _data("BUY", "SELL_QABBA", "BUY")) == 1


class TestOppositeExitQuality:
    def test_high_confidence_passes(self, monkeypatch):
        monkeypatch.delenv("FENIX_OPPOSITE_EXIT_MIN_QUALITY", raising=False)
        e = _engine()
        assert e._opposite_exit_quality_ok("SELL", "HIGH", _data()) is True

    def test_two_agents_pass(self, monkeypatch):
        monkeypatch.delenv("FENIX_OPPOSITE_EXIT_MIN_QUALITY", raising=False)
        e = _engine()
        assert (
            e._opposite_exit_quality_ok("SELL", "MEDIUM", _data("SELL", "SELL_QABBA", "BUY"))
            is True
        )

    def test_one_agent_medium_fails(self, monkeypatch):
        # The SOL 10:46 case: QABBA-only SELL, MEDIUM
        monkeypatch.delenv("FENIX_OPPOSITE_EXIT_MIN_QUALITY", raising=False)
        e = _engine()
        assert (
            e._opposite_exit_quality_ok("SELL", "MEDIUM", _data("HOLD", "SELL_QABBA", "BUY"))
            is False
        )

    def test_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("FENIX_OPPOSITE_EXIT_MIN_QUALITY", "0")
        e = _engine()
        assert e._opposite_exit_quality_ok("SELL", "LOW", _data()) is True


class TestExitSignalForBlockedEntry:
    def _engine_with_position(self, side="LONG"):
        e = _engine()

        class Pos:
            pass

        pos = Pos()
        pos.side = side
        e._get_tracked_position = lambda: pos
        e._is_opposite_side = TradingEngine._is_opposite_side.__get__(e)
        return e

    def test_low_quality_opposite_keeps_position(self, monkeypatch):
        monkeypatch.delenv("FENIX_OPPOSITE_EXIT_MIN_QUALITY", raising=False)
        monkeypatch.delenv("FENIX_BLOCK_EXIT_ON_ENTRY_FILTER", raising=False)
        e = self._engine_with_position("LONG")
        out = e._exit_signal_for_blocked_entry(
            "SELL", confidence="MEDIUM", decision_data=_data("HOLD", "SELL_QABBA", "BUY")
        )
        assert out == "HOLD"

    def test_high_quality_opposite_allows_exit(self, monkeypatch):
        monkeypatch.delenv("FENIX_OPPOSITE_EXIT_MIN_QUALITY", raising=False)
        monkeypatch.delenv("FENIX_BLOCK_EXIT_ON_ENTRY_FILTER", raising=False)
        e = self._engine_with_position("LONG")
        out = e._exit_signal_for_blocked_entry(
            "SELL", confidence="HIGH", decision_data=_data("SELL", "SELL_QABBA", "HOLD")
        )
        assert out == "SELL"

    def test_same_side_signal_is_noop(self, monkeypatch):
        e = self._engine_with_position("LONG")
        assert e._exit_signal_for_blocked_entry("BUY", confidence="HIGH", decision_data=_data()) == "HOLD"

    def test_no_position_is_noop(self):
        e = _engine()
        e._get_tracked_position = lambda: None
        assert e._exit_signal_for_blocked_entry("SELL", confidence="HIGH", decision_data=_data()) == "HOLD"


class TestHardVetoDefaultList:
    def test_no_directional_signal_in_default(self):
        import argparse
        import run_fenix

        parser_src = open("run_fenix.py").read()
        assert "no_directional_signal" in parser_src


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
