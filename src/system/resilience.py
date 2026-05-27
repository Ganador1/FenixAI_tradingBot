import asyncio
import time
from typing import Any


class RobustErrorHandler:
    def __init__(self):
        self.retry_config = {
            "binance_api": {"max_retries": 3, "base_delay": 1, "max_delay": 30},
            "websocket": {"max_retries": 5, "base_delay": 2, "max_delay": 60},
            "order_placement": {"max_retries": 2, "base_delay": 0.5, "max_delay": 10},
        }

    async def execute_with_retry(self, func, operation_type: str = "binance_api", *args, **kwargs):
        config = self.retry_config.get(operation_type, self.retry_config["binance_api"])
        for attempt in range(config["max_retries"]):
            try:
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                result = func(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    return await result
                return result
            except Exception as e:
                if attempt == config["max_retries"] - 1:
                    raise e
                delay = min(config["base_delay"] * (2**attempt), config["max_delay"])
                await asyncio.sleep(delay)


class DynamicCooldownManager:
    def __init__(self):
        self.base_cooldown = 300
        self.consecutive_losses = 0
        self.last_trade_time = 0
        self.volatility_multiplier = 1.0
        self._forced_cooldown_until: float | None = None
        self._forced_reason: str | None = None

    def update_after_trade(self, trade_result: str, market_volatility: float = 1.0):
        self.last_trade_time = time.time()
        self.volatility_multiplier = max(0.5, min(2.0, market_volatility))
        if trade_result == "LOSS":
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        self._clear_expired_forced_cooldown()

    def update_cooldown(self, is_loss: bool, market_volatility: float = 1.0):
        trade_result = "LOSS" if is_loss else "WIN"
        self.update_after_trade(trade_result, market_volatility)

    def get_current_cooldown(self) -> int:
        loss_multiplier = 1 + (self.consecutive_losses * 0.5)
        volatility_adjustment = self.volatility_multiplier
        dynamic_cooldown = self.base_cooldown * loss_multiplier * volatility_adjustment
        return int(max(60, min(1800, dynamic_cooldown)))

    def is_cooldown_active(self) -> bool:
        if self._forced_cooldown_until and time.time() < self._forced_cooldown_until:
            return True
        self._clear_expired_forced_cooldown()
        if self.last_trade_time == 0:
            return False
        elapsed = time.time() - self.last_trade_time
        required_cooldown = self.get_current_cooldown()
        return elapsed < required_cooldown

    def get_remaining_cooldown(self) -> int:
        if self._forced_cooldown_until:
            remaining = int(max(0, self._forced_cooldown_until - time.time()))
            if remaining > 0:
                return remaining
        self._clear_expired_forced_cooldown()
        if not self.is_cooldown_active():
            return 0
        elapsed = time.time() - self.last_trade_time
        required_cooldown = self.get_current_cooldown()
        return max(0, required_cooldown - int(elapsed))

    def apply_external_cooldown(self, duration_seconds: int, reason: str = ""):
        if duration_seconds <= 0:
            self._forced_cooldown_until = None
            self._forced_reason = None
            return
        self._forced_cooldown_until = time.time() + duration_seconds
        self._forced_reason = reason or "External cooldown"

    def get_cooldown_reason(self) -> str | None:
        return self._forced_reason

    def _clear_expired_forced_cooldown(self):
        if self._forced_cooldown_until and time.time() >= self._forced_cooldown_until:
            self._forced_cooldown_until = None
            self._forced_reason = None

    def describe_state(self) -> dict[str, Any]:
        """Expose a structured snapshot for dashboards/logging."""
        forced_active = bool(
            self._forced_cooldown_until and time.time() < self._forced_cooldown_until
        )
        active = self.is_cooldown_active()
        remaining = self.get_remaining_cooldown() if active else 0
        reason = self.get_cooldown_reason()
        if not reason:
            if self.consecutive_losses > 0:
                reason = f"Loss streak: {self.consecutive_losses} trades"
            else:
                reason = "Adaptive guard idle"
        source = "external" if forced_active else "adaptive"
        return {
            "active": active,
            "seconds_remaining": remaining,
            "reason": reason,
            "source": source,
            "consecutive_losses": self.consecutive_losses,
            "volatility_multiplier": self.volatility_multiplier,
            "forced": forced_active,
        }
