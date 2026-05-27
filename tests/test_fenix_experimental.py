from __future__ import annotations

import json
import time

from fenix_experimental import ExperimentalPaperRunner, FastTrigger, SlowBrain


def test_slow_brain_publishes_directional_regime_when_technical_and_qabba_align():
    brain = SlowBrain(ttl_seconds=15)
    regime = brain.publish_regime(
        technical_report={"signal": "BUY", "confidence": 0.78, "adx": 34},
        qabba_report={"signal": "BUY", "confidence": 0.72, "chop": 38},
        timestamp=1_740_000_000.0,
    )

    assert regime.bias == "LONG"
    assert regime.ttl_seconds == 15
    assert regime.max_spread_bps >= 1.2
    assert regime.min_ofi_required <= 0.08


def test_fast_trigger_requires_fresh_regime_and_ofi_alignment():
    trigger = FastTrigger(use_nanofenix=False)

    trigger.update_regime(
        SlowBrain(ttl_seconds=15).publish_regime(
            technical_report={"signal": "BUY", "confidence": 0.8, "adx": 30},
            qabba_report={"signal": "BUY", "confidence": 0.75, "chop": 40},
            timestamp=time.time() - 30,
        )
    )
    trigger.on_book_ticker(bid=100.0, ask=100.002, bid_qty=80.0, ask_qty=20.0, timestamp=10.1)
    stale_decision = trigger.on_book_ticker(bid=100.1, ask=100.102, bid_qty=90.0, ask_qty=10.0, timestamp=11.2)
    assert stale_decision is None

    trigger = FastTrigger(use_nanofenix=False)
    fresh_brain = SlowBrain(ttl_seconds=15)
    trigger.update_regime(
        fresh_brain.publish_regime(
            technical_report={"signal": "BUY", "confidence": 0.8, "adx": 30},
            qabba_report={"signal": "BUY", "confidence": 0.74, "chop": 41},
            timestamp=20.0,
        )
    )
    trigger.on_agg_trade(price=100.05, qty=4.0, is_buyer_maker=False, timestamp=20.4)
    trigger.on_book_ticker(bid=100.0, ask=100.002, bid_qty=85.0, ask_qty=30.0, timestamp=20.1)
    trigger.on_book_ticker(bid=100.12, ask=100.122, bid_qty=92.0, ask_qty=18.0, timestamp=21.2)
    decision = trigger.on_book_ticker(bid=100.22, ask=100.222, bid_qty=94.0, ask_qty=16.0, timestamp=22.2)

    assert decision is not None
    assert decision.action == "LONG"
    assert decision.to_engine_decision() == "BUY"


def test_experimental_runner_writes_compatible_logs(tmp_path):
    runner = ExperimentalPaperRunner(
        symbol="ETHUSDT",
        timeframe="3m",
        run_tag="unit_test",
        output_dir=tmp_path,
        use_nanofenix=False,
    )

    runner.on_agent_snapshot(
        technical_report={"signal": "BUY", "confidence": 0.8, "adx": 33},
        qabba_report={"signal": "BUY", "confidence": 0.72, "chop": 39},
        timestamp=100.0,
    )
    runner.on_agg_trade(price=100.01, qty=3.5, is_buyer_maker=False, timestamp=100.2)
    runner.on_book_ticker(bid=100.0, ask=100.02, bid_qty=88.0, ask_qty=26.0, timestamp=100.1)
    runner.on_book_ticker(bid=100.12, ask=100.14, bid_qty=95.0, ask_qty=19.0, timestamp=101.2)
    runner.update_market_price(price=100.8, timestamp=102.0)

    summary = runner.finalize()

    assert runner.event_log_path.exists()
    assert runner.summary_path.exists()
    assert summary["event"] == "slot_summary"
    assert "events" in summary
    assert "risk_status" in summary
    assert "engine_status" in summary

    first_event = json.loads(runner.event_log_path.read_text().splitlines()[0])
    assert {"timestamp", "event", "payload", "run_tag", "slot_name"} <= set(first_event)
