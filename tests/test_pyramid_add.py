"""Tests for the generic same-side pyramid ("scale-in on re-consensus") gate.

Feature: after a position is open and IN PROFIT, if the core agents
(technical + qabba + visual) re-agree on the same direction, the engine may
add to the position with decreasing size and move the combined stop-loss to
breakeven. Everything is OFF unless FENIX_PYRAMID_ENABLE=1.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.trading.engine import TradingEngine


def _engine() -> TradingEngine:
    return TradingEngine(symbol="ETHUSDC", timeframe="15m", paper_trading=True)


def _position(
    *,
    side: str = "LONG",
    entry_price: float = 1700.0,
    entry_count: int = 1,
    minutes_since_entry: float = 60.0,
) -> SimpleNamespace:
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_since_entry)
    return SimpleNamespace(
        side=side,
        entry_price=entry_price,
        entry_count=entry_count,
        entry_signal_ts=ts.isoformat(),
        quantity=0.08,
        stop_loss=1688.0,
        take_profit=1780.0,
    )


def _aligned_decision_data(direction: str = "BUY") -> dict:
    return {
        "_execution_technical_signal": direction,
        "_execution_technical_confidence": 0.75,
        "_execution_qabba_signal": direction,
        "_execution_qabba_confidence": 0.70,
        "_execution_visual_signal": direction,
        "_execution_visual_confidence": 0.80,
        "_directional_score": 0.85,
        "_execution_market_condition": "TRENDING",
        "_execution_chop_regime": "TREND",
    }


def _enable_pyramid(monkeypatch):
    monkeypatch.setenv("FENIX_PYRAMID_ENABLE", "1")


@pytest.mark.unit
def test_pyramid_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FENIX_PYRAMID_ENABLE", raising=False)
    engine = _engine()
    allowed, reason = engine._allow_pyramid_add(
        decision="BUY",
        decision_data=_aligned_decision_data(),
        tracked_position=_position(),
        companion_policy=None,
        current_price=1720.0,
    )
    assert not allowed
    assert reason == "pyramid_disabled"


@pytest.mark.unit
def test_pyramid_allows_profitable_aligned_add(monkeypatch):
    _enable_pyramid(monkeypatch)
    engine = _engine()
    allowed, reason = engine._allow_pyramid_add(
        decision="BUY",
        decision_data=_aligned_decision_data(),
        tracked_position=_position(entry_price=1700.0),
        companion_policy=None,
        current_price=1720.0,  # +1.18% > 0.4% mínimo
    )
    assert allowed, reason
    assert "pyramid_add_allowed" in reason


@pytest.mark.unit
def test_pyramid_never_adds_to_loser(monkeypatch):
    """Anti-martingala: sin ganancia mínima no hay add."""
    _enable_pyramid(monkeypatch)
    engine = _engine()
    allowed, reason = engine._allow_pyramid_add(
        decision="BUY",
        decision_data=_aligned_decision_data(),
        tracked_position=_position(entry_price=1700.0),
        companion_policy=None,
        current_price=1698.0,  # en pérdida
    )
    assert not allowed
    assert "pyramid_gain_below_min" in reason


@pytest.mark.unit
def test_pyramid_respects_cooldown(monkeypatch):
    _enable_pyramid(monkeypatch)
    engine = _engine()
    allowed, reason = engine._allow_pyramid_add(
        decision="BUY",
        decision_data=_aligned_decision_data(),
        tracked_position=_position(minutes_since_entry=10.0),  # < 3 velas de 15m
        companion_policy=None,
        current_price=1720.0,
    )
    assert not allowed
    assert "pyramid_cooldown" in reason


@pytest.mark.unit
def test_pyramid_entry_cap(monkeypatch):
    _enable_pyramid(monkeypatch)
    monkeypatch.setenv("FENIX_PYRAMID_MAX_ENTRIES", "3")
    engine = _engine()
    allowed, reason = engine._allow_pyramid_add(
        decision="BUY",
        decision_data=_aligned_decision_data(),
        tracked_position=_position(entry_count=3),
        companion_policy=None,
        current_price=1720.0,
    )
    assert not allowed
    assert reason == "pyramid_entry_cap_reached"


@pytest.mark.unit
def test_pyramid_requires_all_agents_by_default(monkeypatch):
    _enable_pyramid(monkeypatch)
    engine = _engine()
    data = _aligned_decision_data()
    data["_execution_qabba_signal"] = "HOLD"  # 2/3
    allowed, reason = engine._allow_pyramid_add(
        decision="BUY",
        decision_data=data,
        tracked_position=_position(),
        companion_policy=None,
        current_price=1720.0,
    )
    assert not allowed
    assert "pyramid_agents_not_aligned" in reason


@pytest.mark.unit
def test_pyramid_two_of_three_mode(monkeypatch):
    _enable_pyramid(monkeypatch)
    monkeypatch.setenv("FENIX_PYRAMID_REQUIRE_ALL_AGENTS", "0")
    engine = _engine()
    data = _aligned_decision_data()
    data["_execution_qabba_signal"] = "HOLD"  # 2/3 alineados
    allowed, reason = engine._allow_pyramid_add(
        decision="BUY",
        decision_data=data,
        tracked_position=_position(),
        companion_policy=None,
        current_price=1720.0,
    )
    assert allowed, reason


@pytest.mark.unit
def test_pyramid_blocks_low_directional_score(monkeypatch):
    _enable_pyramid(monkeypatch)
    engine = _engine()
    data = _aligned_decision_data()
    data["_directional_score"] = 0.40
    allowed, reason = engine._allow_pyramid_add(
        decision="BUY",
        decision_data=data,
        tracked_position=_position(),
        companion_policy=None,
        current_price=1720.0,
    )
    assert not allowed
    assert "pyramid_directional_score_low" in reason


@pytest.mark.unit
def test_pyramid_blocks_low_vol_transition(monkeypatch):
    _enable_pyramid(monkeypatch)
    engine = _engine()
    data = _aligned_decision_data()
    data["_execution_market_condition"] = "LOW_VOLATILITY"
    data["_execution_chop_regime"] = "TRANSITION"
    allowed, reason = engine._allow_pyramid_add(
        decision="BUY",
        decision_data=data,
        tracked_position=_position(),
        companion_policy=None,
        current_price=1720.0,
    )
    assert not allowed
    assert reason == "pyramid_blocked_low_vol_transition"


@pytest.mark.unit
def test_pyramid_blocked_by_nanofenix_hard_veto(monkeypatch):
    _enable_pyramid(monkeypatch)
    engine = _engine()
    engine._nanofenix_hard_veto_reasons = set()  # sin filtro → cualquier no-execute veta
    allowed, reason = engine._allow_pyramid_add(
        decision="BUY",
        decision_data=_aligned_decision_data(),
        tracked_position=_position(),
        companion_policy={"allow_execute": False},
        current_price=1720.0,
    )
    assert not allowed
    assert reason == "pyramid_blocked_by_nanofenix_hard_veto"


@pytest.mark.unit
def test_pyramid_short_side_symmetry(monkeypatch):
    _enable_pyramid(monkeypatch)
    engine = _engine()
    allowed, reason = engine._allow_pyramid_add(
        decision="SELL",
        decision_data=_aligned_decision_data("SELL"),
        tracked_position=_position(side="SHORT", entry_price=1700.0),
        companion_policy=None,
        current_price=1680.0,  # short en ganancia
    )
    assert allowed, reason


@pytest.mark.unit
def test_pyramid_side_mismatch_rejected(monkeypatch):
    _enable_pyramid(monkeypatch)
    engine = _engine()
    allowed, reason = engine._allow_pyramid_add(
        decision="SELL",
        decision_data=_aligned_decision_data("SELL"),
        tracked_position=_position(side="LONG"),
        companion_policy=None,
        current_price=1680.0,
    )
    assert not allowed
    assert reason == "pyramid_not_same_side"


@pytest.mark.unit
def test_pyramid_size_factors(monkeypatch):
    engine = _engine()
    monkeypatch.delenv("FENIX_PYRAMID_SIZE_FACTORS", raising=False)
    assert engine._pyramid_size_factor(1) == pytest.approx(0.5)   # 2ª entrada
    assert engine._pyramid_size_factor(2) == pytest.approx(0.25)  # 3ª entrada
    assert engine._pyramid_size_factor(9) == pytest.approx(0.25)  # clamp al último
    monkeypatch.setenv("FENIX_PYRAMID_SIZE_FACTORS", "0.75")
    assert engine._pyramid_size_factor(1) == pytest.approx(0.75)
    assert engine._pyramid_size_factor(2) == pytest.approx(0.75)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pyramid_protection_moves_sl_to_breakeven(monkeypatch):
    """Tras un add, el SL combinado sube a breakeven + fees (LONG)."""
    monkeypatch.setenv("FENIX_ESTIMATED_ROUND_TRIP_FEE_PCT", "0.0008")
    monkeypatch.setenv("FENIX_PYRAMID_SL_EXTRA_BUFFER_PCT", "0.0004")
    engine = _engine()

    refreshed = {}

    async def fake_refresh(position):
        refreshed["called"] = True
        position.protection_refresh_pending = False

    engine._refresh_exchange_protection_if_needed = fake_refresh

    position = _position(entry_price=1710.0, entry_count=2)
    position.stop_loss = 1688.0
    position.protection_refresh_pending = False
    position.mark_protection_synced = lambda **kw: None

    await engine._apply_pyramid_protection(position)

    expected_be = 1710.0 * (1.0 + 0.0008 + 0.0004)
    assert position.stop_loss == pytest.approx(expected_be)
    assert refreshed.get("called") is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pyramid_protection_never_loosens_better_sl(monkeypatch):
    """Si el trailing ya subió el SL por encima del breakeven, se mantiene."""
    engine = _engine()

    async def fake_refresh(position):
        position.protection_refresh_pending = False

    engine._refresh_exchange_protection_if_needed = fake_refresh

    position = _position(entry_price=1710.0, entry_count=2)
    position.stop_loss = 1725.0  # trailing ya mejor que breakeven
    position.protection_refresh_pending = False
    position.mark_protection_synced = lambda **kw: None

    await engine._apply_pyramid_protection(position)

    assert position.stop_loss == pytest.approx(1725.0)
