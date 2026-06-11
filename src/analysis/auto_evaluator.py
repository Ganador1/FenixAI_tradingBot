import asyncio
import logging
from datetime import datetime, timedelta, timezone

from src.memory.reasoning_bank import ReasoningEntry, get_reasoning_bank
from src.trading.binance_client import BinanceClient

logger = logging.getLogger(__name__)


class AutoEvaluator:
    """
    Evaluates agent predictions against actual market movements.
    Updates ReasoningBank entries with success/failure status.
    """

    def __init__(self, symbol: str = "BTCUSDT", evaluation_horizon_minutes: int = 15):
        self.symbol = symbol
        self.horizon = evaluation_horizon_minutes
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

    async def evaluate_pending_entries(self):
        """Check pending entries and evaluate them if horizon has passed."""
        # Accessing internal cache - thread safe copy might be needed if iterating
        # ReasoningBank exposes get_recent, but we want ALL pending.
        # We'll iterate over all agents.

        agents = ["decision_agent", "technical_agent", "sentiment_agent", "visual_agent"]
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

        # Determine success based on agent type and action
        success = False
        reward = price_change_pct
        notes = f"Price moved {price_change_pct:.2f}% ({start_price} -> {end_price})"

        action = entry.action.upper()

        if "BUY" in action or "LONG" in action:
            success = price_change_pct > 0.05  # Threshold for success (e.g. fees)
        elif "SELL" in action or "SHORT" in action:
            success = price_change_pct < -0.05
            reward = -price_change_pct
        elif "HOLD" in action:
            # Success if price didn't move much? Or if we avoided a loss?
            # For now, let's say HOLD is successful if volatility was low or if we avoided a drop (if we were long)
            # This is subjective. Let's say HOLD is neutral/success if change is small.
            success = abs(price_change_pct) < 0.2
            reward = 0

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
