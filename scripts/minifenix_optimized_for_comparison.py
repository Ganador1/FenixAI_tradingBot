"""
MiniFenix Optimised - Based on lessons learned from NanoFenix

Tweaks to match NanoFenix:
1. Brain interval: 15s -> 30s (less churn)
2. SL/TP: 0.5%/1.2% -> 0.4%/0.8% (more conservative)
3. Min Confidence: 0.52 -> 0.55 (more selective)
4. ML horizon: 200 -> 500 ticks (more signal)
5. Cooldown: 5s -> 3s (more active)
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from dataclasses import dataclass

from minifenix.brain import SlowBrain
from minifenix.multi_paper_trader import MultiPaperTrader
from minifenix.simple_trigger import SimpleTrigger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("MiniFenix.Optimized")


@dataclass
class SymbolTrader:
    symbol: str
    trigger: SimpleTrigger
    task: asyncio.Task | None = None


class OptimizedMultiTrader:
    """
    MiniFenix optimised based on the NanoFenix lessons.
    """

    def __init__(self, symbols: list[str], brain_interval: int = 30):
        self.symbols = [s.upper() for s in symbols]
        self.brain_interval = brain_interval
        self.traders: dict[str, SymbolTrader] = {}
        self.brain: SlowBrain | None = None
        self._shutdown_event = asyncio.Event()
        self.start_time = time.time()

    async def start(self):
        """Start every trader."""
        print("=" * 80)
        print("[START] MiniFenix OPTIMIZED (vs NanoFenix Benchmark)")
        print("=" * 80)
        print(f"Symbols: {', '.join(self.symbols)}")
        print(f"Brain interval: {self.brain_interval}s")
        print("Position size: 3% per symbol")
        print("SL/TP: 0.4% / 0.8% (conservative)")
        print("ML horizon: 500 ticks (more signal)")
        print("=" * 80)

        self.brain = SlowBrain()

        for symbol in self.symbols:
            logger.info(f"[INIT] Initialising trader for {symbol}...")

            trigger = self._create_trigger(symbol)
            self.traders[symbol] = SymbolTrader(symbol=symbol, trigger=trigger)

        tasks = []
        tasks.append(asyncio.create_task(self._brain_loop()))

        for symbol, trader in self.traders.items():
            task = asyncio.create_task(self._run_trigger(symbol, trader))
            trader.task = task
            tasks.append(task)

        await self._shutdown_event.wait()

        for task in tasks:
            task.cancel()

        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        finally:
            if self.brain is not None:
                await self.brain.close()

        await self._print_summaries()

    def _create_trigger(self, symbol: str) -> SimpleTrigger:
        """Build an optimised trigger."""

        # Optimised paper trader
        paper = MultiPaperTrader(
            symbol=symbol,
            initial_balance=10_000.0,
            position_size_pct=0.03,  # 3% (balance between 2% and 5%)
            sl_pct=0.004,  # 0.4% SL (more conservative)
            tp_pct=0.008,  # 0.8% TP (1:2 ratio)
            cooldown_seconds=3.0,  # 3s (more active)
        )

        # Override predictor params
        trigger = SimpleTrigger(
            brain=self.brain,
            symbol=symbol,
            paper_trader=paper,
            log_every=500,  # Less verbose
        )

        # Tweak the predictor
        trigger.predictor.horizon = 500  # More signal, less noise
        trigger.predictor.min_confidence = 0.55  # More selective
        trigger.predictor.move_threshold = 1.0 / 10000  # 1.0 bps

        return trigger

    async def _brain_loop(self):
        """Brain loop."""
        while not self._shutdown_event.is_set():
            try:
                await self.brain.analyze_market_with_llm()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Brain error: {e}")

            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=self.brain_interval)
            except asyncio.TimeoutError:
                pass

    async def _run_trigger(self, symbol: str, trader: SymbolTrader):
        """Run the trigger with reconnect."""
        reconnect_delay = 2
        max_reconnect_delay = 30

        while not self._shutdown_event.is_set():
            try:
                await trader.trigger.run_loop()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in {symbol}: {e}. Reconnecting in {reconnect_delay}s...")
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=reconnect_delay)
                    break
                except asyncio.TimeoutError:
                    pass
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
            else:
                reconnect_delay = 2

    async def _print_summaries(self):
        """Print summaries."""
        duration = time.time() - self.start_time

        print("\n" + "=" * 80)
        print("[STATS] MINIFENIX OPTIMIZED SUMMARY")
        print("=" * 80)
        print(f"Duration: {duration / 60:.1f} minutes")
        print(f"Symbols: {len(self.symbols)}")
        print("=" * 80)

        total_pnl = 0
        total_trades = 0

        for symbol, trader in self.traders.items():
            trigger = trader.trigger
            current_price = trigger._price_history[-1] if trigger._price_history else 0

            stats = trigger.paper.get_stats(current_price)
            total_pnl += stats["total_pnl"]
            total_trades += stats["trades"]

            print(f"\n* {symbol}:")
            print(f"   Ticks: {trigger.tick_count:,}")
            print(f"   Price: ${current_price:,.2f}")
            print(f"   Balance: ${stats['balance']:,.2f} / ${stats['equity']:,.2f}")
            print(f"   P&L: ${stats['total_pnl']:+.4f} ({stats['total_return_pct']:+.2f}%)")
            print(f"   Trades: {stats['trades']} ({stats['wins']}W/{stats['losses']}L)")
            print(f"   Win Rate: {stats['win_rate']:.0%}")

            if stats["has_position"]:
                print(f"   Position: {stats['position_side']} @ ${stats['position_entry']:,.2f}")
                print(f"   uPnL: ${stats['unrealized_pnl']:+.4f}")

        print("\n" + "=" * 80)
        print("[TOTAL] TOTALS:")
        print(f"   Combined P&L: ${total_pnl:+.4f}")
        print(f"   Total trades: {total_trades}")
        print("=" * 80)

        print("\n[LIST] DETAILED SUMMARIES:")
        for symbol, trader in self.traders.items():
            trigger = trader.trigger
            current_price = trigger._price_history[-1] if trigger._price_history else 0
            trigger.paper.print_summary(current_price)

    def shutdown(self):
        """Signal shutdown."""
        logger.info("[STOP] Shutdown...")
        self._shutdown_event.set()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="MiniFenix Optimized Benchmark")
    parser.add_argument("--symbols", default="BTCUSDT", help="Comma-separated symbols")
    parser.add_argument(
        "--brain-interval", type=int, default=30, help="Brain interval (default: 30)"
    )
    parser.add_argument(
        "--duration", type=int, default=5400, help="Duration in seconds (default: 5400 = 90 min)"
    )

    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]

    trader = OptimizedMultiTrader(symbols, brain_interval=args.brain_interval)

    def signal_handler(sig, frame):
        trader.shutdown()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    async def auto_shutdown():
        await asyncio.sleep(args.duration)
        logger.info(f"[TIME] Time limit ({args.duration}s)")
        trader.shutdown()

    async def run_with_timeout():
        await asyncio.gather(trader.start(), auto_shutdown(), return_exceptions=True)

    try:
        asyncio.run(run_with_timeout())
    except KeyboardInterrupt:
        pass

    print("\n[OK] Test finished!")


if __name__ == "__main__":
    main()
