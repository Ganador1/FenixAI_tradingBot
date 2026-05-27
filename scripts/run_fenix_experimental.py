#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fenix_experimental import ExperimentalPaperRunner


def run_demo(
    *, symbol: str, timeframe: str, run_tag: str, demo_seconds: int, output_dir: Path
) -> None:
    runner = ExperimentalPaperRunner(
        symbol=symbol,
        timeframe=timeframe,
        run_tag=run_tag,
        output_dir=output_dir,
        use_nanofenix=False,
    )

    base_price = 2100.0
    current_price = base_price
    for second in range(demo_seconds):
        ts = 1_740_000_000.0 + second
        phase = math.sin(second / 18.0)
        drift = 0.12 if second < demo_seconds / 2 else -0.10
        current_price += drift + phase * 0.08

        if second % 15 == 0:
            if second < demo_seconds / 2:
                tech = {"signal": "BUY", "confidence": 0.78, "adx": 31}
                qabba = {"signal": "BUY", "confidence": 0.72, "chop": 39}
            else:
                tech = {"signal": "SELL", "confidence": 0.80, "adx": 29}
                qabba = {"signal": "SELL", "confidence": 0.74, "chop": 42}
            runner.on_agent_snapshot(
                technical_report=tech,
                qabba_report=qabba,
                timestamp=ts,
            )

        bid = current_price - 0.02
        ask = current_price + 0.02
        if second < demo_seconds / 2:
            bid_qty, ask_qty = 90.0, 45.0
            is_buyer_maker = False
        else:
            bid_qty, ask_qty = 38.0, 84.0
            is_buyer_maker = True

        runner.on_agg_trade(
            price=current_price,
            qty=3.0 + abs(phase),
            is_buyer_maker=is_buyer_maker,
            timestamp=ts + 0.25,
        )
        runner.on_book_ticker(
            bid=bid,
            ask=ask,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            timestamp=ts + 0.9,
        )

    summary = runner.finalize()
    print(f"event_log={runner.event_log_path}")
    print(f"summary={runner.summary_path}")
    print(f"closed_trades={summary['risk_status']['total_trades']}")
    print(
        f"wins={summary['risk_status']['wins']} losses={summary['risk_status']['losses']} total_pnl={summary['risk_status']['total_pnl']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the isolated Fenix experimental paper line.")
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--timeframe", default="3m")
    parser.add_argument("--run-tag", default="experimental_demo")
    parser.add_argument("--demo-seconds", type=int, default=180)
    parser.add_argument("--output-dir", default="logs")
    args = parser.parse_args()

    run_demo(
        symbol=args.symbol,
        timeframe=args.timeframe,
        run_tag=args.run_tag,
        demo_seconds=args.demo_seconds,
        output_dir=Path(args.output_dir),
    )


if __name__ == "__main__":
    main()
