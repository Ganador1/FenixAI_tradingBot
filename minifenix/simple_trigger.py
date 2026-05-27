"""
MiniFenix Simple Trigger (Multi-Symbol Compatible).

Simplified version of FastTrigger that works with any trader
(PaperTrader, TestnetTrader, or MultiPaperTrader).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import websockets

from minifenix.sota_predictor import SOTAPredictor
from minifenix.feature_engine import LOBSnapshot, FeatureEngine
from minifenix.brain import MarketContext

logger = logging.getLogger("MiniFenix.SimpleTrigger")


class SimpleTrigger:
    """
    Simplified trigger compatible with multiple symbols.

    Differences vs FastTrigger:
    - Not tied to a specific trader class
    - Compatible with MultiPaperTrader
    - Improved error handling
    """

    def __init__(self, brain, symbol: str, paper_trader, log_every: int = 200):
        self.brain = brain
        self.symbol = symbol
        self.paper = paper_trader

        # Each symbol gets its own predictor
        self.predictor = SOTAPredictor(
            retrain_every_n=500,
            min_training_samples=1000,
            prediction_horizon=200,
            move_threshold_bps=2.0,
            min_confidence=0.55,
        )

        self.tick_count = 0
        self.log_every = log_every
        self._price_history: list[float] = []

    async def run_loop(self) -> None:
        """Main WebSocket loop."""
        url = f"wss://stream.binance.com:9443/ws/{self.symbol.lower()}@bookTicker"
        logger.info(f"[FAST] [{self.symbol.upper()}] Connecting: {url}")

        while True:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    logger.info(f"[FAST] [{self.symbol.upper()}] Connected. Listening for ticks...")
                    async for raw in ws:
                        await self._process_tick(json.loads(raw))
            except Exception as e:
                logger.error(f"[FAST] [{self.symbol.upper()}] WS error: {e}. Reconnecting in 2s...")
                await asyncio.sleep(2)

    async def _process_tick(self, data: dict) -> None:
        """Process a WebSocket tick."""
        try:
            snap = LOBSnapshot(
                bid=float(data["b"]),
                bid_qty=float(data["B"]),
                ask=float(data["a"]),
                ask_qty=float(data["A"]),
                timestamp_ms=int(data.get("T", time.time() * 1000)),
            )
        except (KeyError, ValueError):
            return

        self.tick_count += 1
        mid_price = (snap.bid + snap.ask) / 2
        spread_bps = ((snap.ask - snap.bid) / mid_price * 10000) if mid_price > 0 else 0

        # Track price history
        self._price_history.append(mid_price)
        if len(self._price_history) > 250:
            self._price_history.pop(0)

        # 1. SOTA prediction
        t0 = time.perf_counter_ns()
        result = self.predictor.process_tick(snap)
        elapsed_us = (time.perf_counter_ns() - t0) / 1000

        ml_signal = result["signal"]
        ml_confidence = result["confidence"]
        stats = self.predictor.get_stats()

        # 2. Update paper trader position
        closed_trade = self.paper.update(mid_price)
        if closed_trade:
            ps = self.paper.get_stats()
            logger.warning(
                f"[STATS] [{self.symbol.upper()}] trade closed | "
                f"Equity=${ps['equity']:,.2f} | "
                f"P&L=${ps['total_pnl']:+.2f} ({ps['total_return_pct']:+.2f}%) | "
                f"WinRate={ps['win_rate']:.0%} ({ps['wins']}W/{ps['losses']}L)"
            )

        # 3. Update Brain context
        obi = (snap.bid_qty - snap.ask_qty) / (snap.bid_qty + snap.ask_qty + 1e-8)
        price_200_ago = self._price_history[0] if len(self._price_history) >= 200 else mid_price
        price_change_pct = (mid_price / price_200_ago - 1) * 100 if price_200_ago > 0 else 0.0

        if len(self._price_history) >= 20:
            import numpy as np
            prices_20 = self._price_history[-20:]
            rets = [prices_20[i] / prices_20[i-1] - 1 for i in range(1, len(prices_20))]
            vol_20 = float(np.std(rets))
        else:
            vol_20 = 0.0

        self.brain.update_market_context(MarketContext(
            price=mid_price,
            price_change_pct_1m=price_change_pct,
            obi=obi,
            spread_bps=spread_bps,
            realized_vol=vol_20,
            ml_signal=ml_signal,
            ml_confidence=ml_confidence,
            ml_accuracy=stats["running_accuracy"],
            tick_count=self.tick_count,
        ))

        # 4. Periodic log
        if self.tick_count % self.log_every == 0:
            ps = self.paper.get_stats(mid_price)
            pos_str = (
                f"POS={ps['position_side']}@${ps['position_entry']:,.0f} "
                f"uPnL=${ps['unrealized_pnl']:+.2f}"
                if ps["has_position"] else "no position"
            )
            logger.info(
                f"[STATS] [{self.symbol.upper()}] Tick #{self.tick_count} | "
                f"${mid_price:,.2f} | "
                f"ML={ml_signal}({ml_confidence:.0%}) Acc={stats['running_accuracy']:.1%} | "
                f"Brain={self.brain.current_regime.bias}({self.brain.current_regime.confidence:.0%}) | "
                f"Equity=${ps['equity']:,.2f} | {pos_str} | {elapsed_us:.0f}us"
            )

        # 5. Trading logic
        if not result["model_ready"]:
            return

        regime = self.brain.current_regime
        if regime.is_stale(max_age_seconds=120):
            return

        brain_bias = regime.bias
        brain_conf = regime.confidence

        # Entry signal
        if (
            self.paper.can_trade()
            and spread_bps <= regime.max_spread_bps
            and brain_conf >= 0.6
            and ml_confidence >= self.predictor.min_confidence
        ):
            if brain_bias == "LONG" and ml_signal == "UP":
                self.paper.open_long(
                    price=mid_price,
                    ml_signal=ml_signal,
                    ml_confidence=ml_confidence,
                    brain_bias=brain_bias,
                    brain_confidence=brain_conf,
                )

            elif brain_bias == "SHORT" and ml_signal == "DOWN":
                self.paper.open_short(
                    price=mid_price,
                    ml_signal=ml_signal,
                    ml_confidence=ml_confidence,
                    brain_bias=brain_bias,
                    brain_confidence=brain_conf,
                )

        # Early close
        elif self.paper.position is not None:
            pos = self.paper.position
            if (brain_bias == "SHORT" and pos.side == "LONG" and brain_conf >= 0.75) or \
               (brain_bias == "LONG" and pos.side == "SHORT" and brain_conf >= 0.75):
                logger.info(f"[CYCLE] [{self.symbol.upper()}] close on regime change -> {brain_bias}")
                self.paper.force_close(mid_price, reason="REGIME_CHANGE")
