"""Tests for the sentiment-labeling fix and the risk_manager bank throttle.

Bug 1 (sentiment 0% win): the AutoEvaluator only understood BUY/SELL/HOLD, so
sentiment vocabulary (NEUTRAL/POSITIVE/NEGATIVE) fell through every branch and
was hard-labeled success=False on every entry.

Bug 2 (risk noise): risk_manager stored an entry on every invocation (unique
price in the prompt → unique digest), flooding the bank with ~10k unlabeled
entries. Now storage is throttled to verdict transitions / periodic snapshots.
"""

import pytest

from src.analysis.auto_evaluator import AutoEvaluator
from src.core.orchestrator import bank_helper
from src.core.orchestrator.bank_helper import should_store_risk_entry


# ---------------------------------------------------------------------------
# Sentiment vocabulary normalization
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("POSITIVE", "BUY"),
        ("VERY_POSITIVE", "BUY"),
        ("BULLISH", "BUY"),
        ("BUY_QABBA", "BUY"),
        ("LONG", "BUY"),
        ("NEGATIVE", "SELL"),
        ("BEARISH", "SELL"),
        ("SELL_QABBA", "SELL"),
        ("SHORT", "SELL"),
        ("NEUTRAL", "HOLD"),
        ("HOLD", "HOLD"),
        ("WAIT", "HOLD"),
        ("", "UNKNOWN"),
        (None, "UNKNOWN"),
        ("GIBBERISH", "UNKNOWN"),
    ],
)
def test_normalize_direction(raw, expected):
    assert AutoEvaluator._normalize_direction(raw) == expected


@pytest.mark.unit
def test_hold_band_default_is_saner_than_cost(monkeypatch):
    """With a tiny cost threshold, the HOLD band keeps a usable floor."""
    monkeypatch.setenv("FENIX_EVAL_ROUNDTRIP_COST_PCT", "0.05")
    monkeypatch.delenv("FENIX_EVAL_HOLD_BAND_PCT", raising=False)
    ev = AutoEvaluator(symbol="ETHUSDC", timeframe="15m")
    assert ev.hold_band_pct == pytest.approx(0.15)

    monkeypatch.setenv("FENIX_EVAL_HOLD_BAND_PCT", "0.25")
    ev2 = AutoEvaluator(symbol="ETHUSDC", timeframe="15m")
    assert ev2.hold_band_pct == pytest.approx(0.25)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_neutral_sentiment_labeled_as_hold(monkeypatch):
    """A NEUTRAL call with the market inside the band must be success=True."""
    monkeypatch.setenv("FENIX_EVAL_ROUNDTRIP_COST_PCT", "0.05")
    monkeypatch.setenv("FENIX_EVAL_HOLD_BAND_PCT", "0.15")
    ev = AutoEvaluator(symbol="ETHUSDC", timeframe="15m")

    # Market moved +0.07% — inside the 0.15% neutral band.
    async def fake_get_klines(**kwargs):
        return [
            {"open": "1728.95", "close": "1729.5"},
            {"open": "1729.5", "close": "1730.24"},
        ]

    ev.client.get_klines = lambda **kw: fake_get_klines(**kw)

    captured = {}

    def fake_update(agent_name, prompt_digest, success, reward, reward_notes):
        captured.update(success=success, reward=reward, notes=reward_notes)

    ev.bank.update_entry_outcome = fake_update

    from src.memory.reasoning_bank import ReasoningEntry
    from datetime import datetime, timezone

    entry = ReasoningEntry(
        agent="sentiment_agent",
        prompt_digest="deadbeef00000000",
        prompt="p",
        reasoning='{"overall_sentiment": "NEUTRAL"}',
        action="NEUTRAL",
        confidence=0.65,
        backend="test",
        latency_ms=1.0,
        metadata={},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    now = datetime.now(timezone.utc)
    await ev.evaluate_entry(entry, now, now)

    assert captured.get("success") is True
    assert captured.get("reward") == 0.0
    assert "neutral band" in captured.get("notes", "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_negative_sentiment_labeled_as_sell(monkeypatch):
    """NEGATIVE must be evaluated as SELL, not fall through to False."""
    monkeypatch.setenv("FENIX_EVAL_ROUNDTRIP_COST_PCT", "0.05")
    ev = AutoEvaluator(symbol="ETHUSDC", timeframe="15m")

    # Market dropped -0.50% — a NEGATIVE (SELL) call is a win.
    async def fake_get_klines(**kwargs):
        return [
            {"open": "1730.00", "close": "1725.0"},
            {"open": "1725.0", "close": "1721.35"},
        ]

    ev.client.get_klines = lambda **kw: fake_get_klines(**kw)

    captured = {}

    def fake_update(agent_name, prompt_digest, success, reward, reward_notes):
        captured.update(success=success, reward=reward)

    ev.bank.update_entry_outcome = fake_update

    from src.memory.reasoning_bank import ReasoningEntry
    from datetime import datetime, timezone

    entry = ReasoningEntry(
        agent="sentiment_agent",
        prompt_digest="deadbeef00000001",
        prompt="p",
        reasoning='{"overall_sentiment": "NEGATIVE"}',
        action="NEGATIVE",
        confidence=0.8,
        backend="test",
        latency_ms=1.0,
        metadata={},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    now = datetime.now(timezone.utc)
    await ev.evaluate_entry(entry, now, now)

    assert captured.get("success") is True
    assert captured.get("reward") > 0


# ---------------------------------------------------------------------------
# risk_manager storage throttle
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_risk_throttle():
    bank_helper._RISK_STORE_STATE.update(key=None, ts=0.0)
    yield
    bank_helper._RISK_STORE_STATE.update(key=None, ts=0.0)


@pytest.mark.unit
def test_risk_throttle_stores_first_and_transitions(monkeypatch):
    monkeypatch.setenv("FENIX_RISK_BANK_MIN_INTERVAL_SEC", "900")
    assert should_store_risk_entry("BUY", "APPROVE") is True       # primera
    assert should_store_risk_entry("BUY", "APPROVE") is False      # repetida
    assert should_store_risk_entry("BUY", "VETO") is True          # transición
    assert should_store_risk_entry("BUY", "VETO") is False         # repetida
    assert should_store_risk_entry("SELL", "VETO") is True         # cambia acción


@pytest.mark.unit
def test_risk_throttle_periodic_snapshot(monkeypatch):
    monkeypatch.setenv("FENIX_RISK_BANK_MIN_INTERVAL_SEC", "900")
    assert should_store_risk_entry("BUY", "APPROVE") is True
    # Simula que pasaron >15 min desde el último almacenamiento.
    bank_helper._RISK_STORE_STATE["ts"] -= 901
    assert should_store_risk_entry("BUY", "APPROVE") is True


@pytest.mark.unit
def test_risk_throttle_disabled(monkeypatch):
    monkeypatch.setenv("FENIX_RISK_BANK_MIN_INTERVAL_SEC", "0")
    assert should_store_risk_entry("BUY", "APPROVE") is True
    assert should_store_risk_entry("BUY", "APPROVE") is True
