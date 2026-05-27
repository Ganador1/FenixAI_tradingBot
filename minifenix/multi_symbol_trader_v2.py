"""
MiniFenix Multi-Symbol Trader v2 (Corrected).

Fixes:
- Uses MultiPaperTrader instead of TestnetTrader (isolated state)
- Position size reduced to 2% to avoid margin issues
- Brain interval raised to 60s to avoid timeouts
- Better WebSocket reconnect handling

Usage:
    python -m minifenix.multi_symbol_trader_v2 --symbols BTCUSDT,ETHUSDT,SOLUSDT
"""
from __future__ import annotations

import asyncio
import argparse
import logging
import sys
import signal
import time
from dataclasses import dataclass
from typing import Dict, List

# Import MiniFenix components
from minifenix.brain import SlowBrain
from minifenix.simple_trigger import SimpleTrigger
from minifenix.multi_paper_trader import MultiPaperTrader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("MiniFenix.MultiV2")


@dataclass
class SymbolTrader:
    """Container for each trading symbol."""
    symbol: str
    trigger: SimpleTrigger
    task: asyncio.Task | None = None


class MultiSymbolTraderV2:
    """
    Manages multiple MiniFenix instances with fixes.

    Changes vs v1:
    - PaperTrader with isolated state (no interference)
    - Position size 2% (vs 5%)
    - Brain interval 60s (vs 30s)
    """

    def __init__(self, symbols: List[str], brain_interval: int = 60):
        self.symbols = [s.upper() for s in symbols]
        self.brain_interval = brain_interval
        self.traders: Dict[str, SymbolTrader] = {}
        self.brain: SlowBrain | None = None
        self._shutdown_event = asyncio.Event()
        self.start_time = time.time()

    async def start(self):
        """Start every trader."""
        print("=" * 80)
        print("[START] MiniFenix MULTI-SYMBOL v2 (FIXED)")
        print("=" * 80)
        print(f"Symbols: {', '.join(self.symbols)}")
        print(f"Shared Brain: {self.brain_interval}s interval")
        print(f"Position size: 2% per symbol")
        print(f"Paper Trading: per-symbol isolated stats")
        print("=" * 80)

        # Create shared Brain
        self.brain = SlowBrain()

        # Create traders for each symbol
        for symbol in self.symbols:
            logger.info(f"[INIT] Initialising trader for {symbol.upper()}...")

            # Create trigger with custom PaperTrader
            from minifenix.sota_predictor import SOTAPredictor
            from minifenix.feature_engine import LOBSnapshot
            from minifenix.brain import MarketContext

            trigger = self._create_trigger(symbol)
            self.traders[symbol] = SymbolTrader(
                symbol=symbol,
                trigger=trigger
            )

        # Start tasks
        tasks = []

        # Brain loop
        tasks.append(asyncio.create_task(self._brain_loop()))

        # Trigger loops for each symbol
        for symbol, trader in self.traders.items():
            task = asyncio.create_task(self._run_trigger(symbol, trader))
            trader.task = task
            tasks.append(task)

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        # Cancel every task
        for task in tasks:
            task.cancel()

        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

        # Print summaries
        await self._print_summaries()

    def _create_trigger(self, symbol: str) -> 'SimpleTrigger':
        """Create a simplified trigger with an isolated PaperTrader."""
        return SimpleTrigger(
            brain=self.brain,
            symbol=symbol,
            paper_trader=MultiPaperTrader(
                symbol=symbol.upper(),
                initial_balance=10_000.0,
                position_size_pct=0.02,  # REDUCED: 2% for multi-symbol
                sl_pct=0.005,
                tp_pct=0.012,
                cooldown_seconds=10.0,
            )
        )

    async def _brain_loop(self):
        """Shared Brain loop."""
        while not self._shutdown_event.is_set():
            try:
                await self.brain.analyze_market_with_llm()
            except Exception as e:
                logger.error(f"Brain error: {e}")

            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self.brain_interval
                )
            except asyncio.TimeoutError:
                pass

    async def _run_trigger(self, symbol: str, trader: SymbolTrader):
        """Run the trigger for a symbol with automatic reconnect."""
        reconnect_delay = 2
        max_reconnect_delay = 30

        while not self._shutdown_event.is_set():
            try:
                await trader.trigger.run_loop()
            except Exception as e:
                logger.error(f"Error in {symbol}: {e}. Reconnecting in {reconnect_delay}s...")
                await asyncio.sleep(reconnect_delay)
                # Exponential backoff (with cap)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
            else:
                # Reset delay after a successful run
                reconnect_delay = 2

    async def _print_summaries(self):
        """Print summaries for every trader."""
        duration = time.time() - self.start_time

        print("\n" + "=" * 80)
        print("[STATS] MULTI-SYMBOL v2 SUMMARY")
        print("=" * 80)
        print(f"Duration: {duration/60:.1f} minutes")
        print(f"Symbols: {len(self.symbols)}")
        print("=" * 80)

        total_pnl = 0
        total_trades = 0

        for symbol, trader in self.traders.items():
            trigger = trader.trigger
            current_price = trigger._price_history[-1] if trigger._price_history else 0

            stats = trigger.paper.get_stats(current_price)
            total_pnl += stats['total_pnl']
            total_trades += stats['trades']

            print(f"\n* {symbol.upper()}:")
            print(f"   Ticks: {trigger.tick_count:,}")
            print(f"   Price: ${current_price:,.2f}")
            print(f"   Balance: ${stats['balance']:,.2f} / ${stats['equity']:,.2f}")
            print(f"   P&L: ${stats['total_pnl']:+.4f} ({stats['total_return_pct']:+.2f}%)")
            print(f"   Trades: {stats['trades']} ({stats['wins']}W/{stats['losses']}L)")
            print(f"   Win Rate: {stats['win_rate']:.0%}")

            if stats['has_position']:
                print(f"   Position: {stats['position_side']} @ ${stats['position_entry']:,.2f}")
                print(f"   uPnL: ${stats['unrealized_pnl']:+.4f}")

        print("\n" + "=" * 80)
        print(f"[TOTAL] TOTALS:")
        print(f"   Combined P&L: ${total_pnl:+.4f}")
        print(f"   Total trades: {total_trades}")
        print("=" * 80)

        # Detailed per-symbol summaries
        print("\n[LIST] DETAILED SUMMARIES:")
        for symbol, trader in self.traders.items():
            trigger = trader.trigger
            current_price = trigger._price_history[-1] if trigger._price_history else 0
            trigger.paper.print_summary(current_price)

    def shutdown(self):
        """Signal graceful shutdown."""
        logger.info("[STOP] shutdown signal received...")
        self._shutdown_event.set()


def main():
    parser = argparse.ArgumentParser(description="MiniFenix Multi-Symbol Trader v2")
    parser.add_argument(
        "--symbols",
        default="BTCUSDT,ETHUSDT,SOLUSDT",
        help="Comma-separated symbols to trade"
    )
    parser.add_argument(
        "--brain-interval",
        type=int,
        default=60,  # RAISED: 60s vs 30s
        help="Brain analysis interval in seconds (default: 60)"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=1200,  # 20 minutes by default
        help="Test duration in seconds (default: 1200 = 20 min)"
    )

    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]

    trader = MultiSymbolTraderV2(symbols, brain_interval=args.brain_interval)

    # Setup signal handlers
    def signal_handler(sig, frame):
        trader.shutdown()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Auto-shutdown after the requested duration
    async def auto_shutdown():
        await asyncio.sleep(args.duration)
        logger.info(f"[TIME] Time limit reached ({args.duration}s)")
        trader.shutdown()

    async def run_with_timeout():
        await asyncio.gather(
            trader.start(),
            auto_shutdown(),
            return_exceptions=True
        )

    try:
        asyncio.run(run_with_timeout())
    except KeyboardInterrupt:
        pass

    print("\n[OK] multi-symbol v2 test finished!")


if __name__ == "__main__":
    main()
