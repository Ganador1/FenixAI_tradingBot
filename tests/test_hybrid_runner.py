from __future__ import annotations

from scripts.run_hybrid_live_paper import HybridController, _extract_event_price


def test_update_signal_preserves_indicators():
    controller = HybridController(
        symbol="ETHUSDT",
        bias_tf="5m",
        entry_tf="3m",
        scout_tf=None,
    )

    controller.update_signal(
        timeframe="3m",
        decision="BUY",
        confidence=0.7,
        price=100.0,
        timestamp="2026-03-05T00:00:00+00:00",
        judge_verdict=None,
        indicators={"atr": 1.2, "chop": 65.0},
    )

    assert controller.latest["3m"].indicators["atr"] == 1.2
    assert controller.latest["3m"].indicators["chop"] == 65.0


def test_update_signal_ignores_invalid_zero_price():
    controller = HybridController(
        symbol="ETHUSDT",
        bias_tf="5m",
        entry_tf="3m",
        scout_tf=None,
    )

    controller.update_signal(
        timeframe="3m",
        decision="BUY",
        confidence=0.9,
        price=0.0,
        timestamp="2026-03-05T00:00:00+00:00",
        judge_verdict=None,
        indicators={"atr": 1.2},
    )

    assert "3m" not in controller.latest
    assert controller.position is None


def test_extract_event_price_uses_risk_entry_when_engine_price_is_zero():
    payload = {
        "full_data": {
            "risk_assessment": {
                "entry_price": 86.42,
                "order_details": {"entry_price": 86.11},
            }
        }
    }

    assert _extract_event_price(payload, 0.0) == 86.42


def test_close_position_ignores_invalid_entry_without_dividing_by_zero():
    controller = HybridController(
        symbol="ETHUSDT",
        bias_tf="5m",
        entry_tf="3m",
        scout_tf=None,
    )
    controller.position = {
        "side": "LONG",
        "entry_price": 0.0,
        "timestamp": "2026-03-05T00:00:00+00:00",
        "reason": "test",
    }

    controller._close_position(
        price=100.0,
        timestamp="2026-03-05T00:05:00+00:00",
        reason="hybrid_flip",
    )

    assert controller.position is not None


def test_range_helpers_can_open_position_from_indicator_context():
    controller = HybridController(
        symbol="ETHUSDT",
        bias_tf="5m",
        entry_tf="3m",
        scout_tf=None,
    )

    controller.update_signal(
        timeframe="3m",
        decision="HOLD",
        confidence=0.2,
        price=100.0,
        timestamp="2026-03-05T00:00:00+00:00",
        judge_verdict=None,
        indicators={
            "atr": 0.8,
            "chop": 68.0,
            "bandwidth_pct": 1.5,
            "percent_b": 0.03,
            "bollinger_upper": 102.0,
            "bollinger_lower": 99.8,
            "bollinger_middle": 100.7,
        },
    )

    assert controller.position is not None
    assert controller.position["side"] == "LONG"
