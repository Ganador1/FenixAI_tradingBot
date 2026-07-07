"""Tests for the graduated NanoFenix abstention.

2026-07-06 xray: the ``no_directional_signal`` hard-veto killed the one quality
setup of the session (SOL 14:45 BUY, Technical+Visual+Decision aligned, +2.02%
to TP) while every useful block that day came from AGENT_CONSENSUS. A companion
with NO opinion now ABSTAINS when >=2/3 directional agents back the decision;
disagreement (direction_mismatch), warnings (high_uncertainty) and operational
failures (stale/missing/mismatch) keep the veto absolute.
"""

from __future__ import annotations

import pytest

from src.trading.engine import TradingEngine

DEFAULT_HARD_VETO = {
    "direction_mismatch",
    "no_directional_signal",
    "high_uncertainty",
    "stale_signal",
    "symbol_mismatch",
    "run_id_mismatch",
    "signal_file_missing",
    "signal_file_empty",
    "signal_parse_error",
    "missing_or_invalid_timestamp",
}


def _engine(configured=None):
    e = TradingEngine.__new__(TradingEngine)
    e._nanofenix_hard_veto_reasons = DEFAULT_HARD_VETO if configured is None else configured
    return e


def _policy(*reasons):
    return {"allow_execute": False, "reason": ",".join(reasons), "reasons": list(reasons)}


class TestPureAbstention:
    def test_sol_1445_case_is_abstention(self):
        """The exact reason set that cost +2.02%: only no_directional_signal
        triggers among the configured hard-veto reasons."""
        e = _engine()
        policy = _policy(
            "companion_not_ready",
            "low_actionable_edge",
            "low_calibration_health",
            "low_confidence",
            "low_direction_accuracy",
            "low_pred_bps",
            "no_directional_signal",
        )
        assert e._nanofenix_veto_is_pure_abstention(policy) is True

    def test_no_directional_signal_alone_is_abstention(self):
        e = _engine()
        assert e._nanofenix_veto_is_pure_abstention(_policy("no_directional_signal")) is True

    def test_companion_not_ready_when_configured_is_abstention(self):
        e = _engine(DEFAULT_HARD_VETO | {"companion_not_ready"})
        assert (
            e._nanofenix_veto_is_pure_abstention(
                _policy("companion_not_ready", "no_directional_signal")
            )
            is True
        )

    def test_direction_mismatch_is_not_abstention(self):
        e = _engine()
        assert (
            e._nanofenix_veto_is_pure_abstention(
                _policy("direction_mismatch", "no_directional_signal")
            )
            is False
        )

    def test_high_uncertainty_is_not_abstention(self):
        e = _engine()
        assert (
            e._nanofenix_veto_is_pure_abstention(
                _policy("no_directional_signal", "high_uncertainty")
            )
            is False
        )

    def test_stale_signal_is_not_abstention(self):
        e = _engine()
        assert (
            e._nanofenix_veto_is_pure_abstention(_policy("stale_signal", "no_directional_signal"))
            is False
        )

    def test_soft_reasons_only_is_not_abstention(self):
        """Soft reasons don't intersect the configured set, so the hard-veto
        would not fire via reasons at all — nothing to graduate."""
        e = _engine()
        assert (
            e._nanofenix_veto_is_pure_abstention(_policy("low_actionable_edge", "low_pred_bps"))
            is False
        )

    def test_empty_configured_reasons_is_not_abstention(self):
        e = _engine(set())
        assert e._nanofenix_veto_is_pure_abstention(_policy("no_directional_signal")) is False

    def test_non_dict_policy_is_not_abstention(self):
        e = _engine()
        assert e._nanofenix_veto_is_pure_abstention(None) is False


if __name__ == "__main__":
    import os

    raise SystemExit(pytest.main([os.path.abspath(__file__), "-v"]))
