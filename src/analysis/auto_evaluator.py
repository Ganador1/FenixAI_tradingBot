import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from src.memory.reasoning_bank import ReasoningEntry, get_reasoning_bank
from src.trading.binance_client import BinanceClient

logger = logging.getLogger(__name__)

# Horizon per trading timeframe: predictions need room to play out, but not so
# much that unrelated moves dominate the label (3x the bar size is a common rule).
_TIMEFRAME_HORIZON_MINUTES = {
    "1m": 5,
    "3m": 9,
    "5m": 15,
    "15m": 45,
    "30m": 90,
    "1h": 240,
    "4h": 720,
}


def horizon_for_timeframe(timeframe: str | None) -> int:
    """Return an evaluation horizon (minutes) appropriate for the timeframe."""
    if not timeframe:
        return 45
    return _TIMEFRAME_HORIZON_MINUTES.get(str(timeframe).lower(), 45)


class AutoEvaluator:
    """
    Evaluates agent predictions against actual market movements.
    Updates ReasoningBank entries with success/failure status.

    Success thresholds are fee-aware: a directional call only counts as a
    success if the move would have covered round-trip costs (fees + slippage).
    Otherwise the memory gets polluted with "wins" that lose money net.
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        evaluation_horizon_minutes: int | None = None,
        timeframe: str | None = None,
    ):
        self.symbol = symbol
        if evaluation_horizon_minutes is not None:
            self.horizon = evaluation_horizon_minutes
        else:
            self.horizon = horizon_for_timeframe(timeframe)
        # Round-trip cost estimate in pct (taker fee ~0.04% x2 + slippage).
        try:
            self.cost_pct = float(os.getenv("FENIX_EVAL_ROUNDTRIP_COST_PCT", "0.12"))
        except ValueError:
            self.cost_pct = 0.12
        self.bank = get_reasoning_bank()
        self.client = BinanceClient(
            testnet=False
        )  # Use public data, so testnet doesn't matter much for read-only
        self._running = False

    async def start(self, interval_seconds: int = 60):
        """Start the evaluation loop."""
        if self._running:
            return
        self._running = True
        logger.info(f"Starting AutoEvaluator for {self.symbol} (Horizon: {self.horizon}m)")

        # Connect the async client; without this every evaluation fails with
        # "Cliente no conectado" and spams the log.
        try:
            await self.client.connect()
        except Exception as e:
            logger.error(f"AutoEvaluator could not connect Binance client: {e}")

        while self._running:
            try:
                if self.client._session is None:
                    await self.client.connect()
                await self.evaluate_pending_entries()
            except Exception as e:
                logger.error(f"Error in AutoEvaluator loop: {e}")
            await asyncio.sleep(interval_seconds)

    async def stop(self):
        self._running = False
        try:
            await self.client.close()
        except Exception:
            pass

    @staticmethod
    def _resolve_sentiment_action(entry: ReasoningEntry) -> str:
        """Map sentiment verdicts (POSITIVE/NEGATIVE/NEUTRAL) to directions."""
        try:
            import json as _json

            data = _json.loads(entry.reasoning or "{}")
            sentiment = str(data.get("overall_sentiment", "")).upper()
        except (ValueError, TypeError):
            sentiment = ""
        if "POSITIVE" in sentiment or "BULLISH" in sentiment:
            return "BUY"
        if "NEGATIVE" in sentiment or "BEARISH" in sentiment:
            return "SELL"
        if "NEUTRAL" in sentiment:
            return "HOLD"
        return "UNKNOWN"

    async def evaluate_pending_entries(self):
        """Check pending entries and evaluate them if horizon has passed."""
        # Accessing internal cache - thread safe copy might be needed if iterating
        # ReasoningBank exposes get_recent, but we want ALL pending.
        # We'll iterate over all agents.

        agents = [
            "decision_agent",
            "technical_agent",
            "qabba_agent",
            "sentiment_agent",
            "visual_agent",
        ]
        now = datetime.now(timezone.utc)

        for agent_name in agents:
            entries = self.bank.get_recent(agent_name, limit=100)  # Check last 100 entries

            for entry in entries:
                if entry.success is not None:
                    continue  # Already evaluated

                try:
                    # Handle timezone aware/naive
                    created_at = datetime.fromisoformat(entry.created_at)
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)

                    # Check if horizon passed
                    eval_time = created_at + timedelta(minutes=self.horizon)
                    if now < eval_time:
                        continue

                    # Ready to evaluate
                    await self.evaluate_entry(entry, created_at, eval_time)

                except Exception as e:
                    logger.warning(f"Failed to evaluate entry {entry.prompt_digest}: {e}")

    async def evaluate_entry(self, entry: ReasoningEntry, start_time: datetime, end_time: datetime):
        """Compare prediction with actual price movement."""

        # Fetch price at start and end
        # We use get_klines to find the closest candles
        # Binance API uses timestamps in ms
        start_ts = int(start_time.timestamp() * 1000)
        end_ts = int(end_time.timestamp() * 1000)

        # Fetch 1m candles around the times
        # We fetch a range to be safe
        klines = await self.client.get_klines(
            symbol=self.symbol,
            interval="1m",
            limit=1000,  # Should cover the range if it's recent
            start_time=start_ts,
            end_time=end_ts + 60000,  # +1 min buffer
        )

        if not klines:
            logger.warning(f"No klines found for evaluation of {entry.prompt_digest}")
            return

        # Find start price (closest to start_time)
        start_price = float(klines[0]["open"])  # Approximation
        # Find end price (closest to end_time)
        end_price = float(klines[-1]["close"])

        price_change_pct = ((end_price - start_price) / start_price) * 100

        # Determine success based on agent type and action.
        # Directional calls must beat round-trip costs to count as success;
        # otherwise they would have lost money net of fees.
        success = False
        reward = price_change_pct
        notes = (
            f"Price moved {price_change_pct:.2f}% ({start_price} -> {end_price}) "
            f"[cost threshold {self.cost_pct:.2f}%]"
        )

        action = entry.action.upper()

        # Sentiment entries store action=UNKNOWN; derive direction from the
        # overall_sentiment field inside the raw reasoning JSON instead.
        if action == "UNKNOWN":
            action = self._resolve_sentiment_action(entry)
            if action == "UNKNOWN":
                return  # Not directionally evaluable; leave unlabeled.

        if "BUY" in action or "LONG" in action:
            success = price_change_pct > self.cost_pct
            reward = price_change_pct - self.cost_pct
        elif "SELL" in action or "SHORT" in action:
            success = price_change_pct < -self.cost_pct
            reward = -price_change_pct - self.cost_pct
        elif "HOLD" in action:
            # HOLD avoided paying costs; it is "correct" when no profitable
            # move was available in either direction (|move| below costs).
            success = abs(price_change_pct) <= self.cost_pct
            reward = 0.0

        # Update ReasoningBank
        self.bank.update_entry_outcome(
            agent_name=entry.agent,
            prompt_digest=entry.prompt_digest,
            success=success,
            reward=reward,
            reward_notes=notes,
        )
        logger.info(
            f"Evaluated {entry.agent} ({entry.prompt_digest[:8]}): {action} -> Success={success}, Reward={reward:.2f}%"
        )
