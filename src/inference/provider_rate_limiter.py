from __future__ import annotations

import asyncio
import time

DEFAULT_PROVIDER_LIMITS = {
    # provider_name: (requests_per_interval, interval_seconds)
    "groq": (30, 60),  # 30 requests per minute as an example
    "openai": (60, 60),
    "ollama": (60, 60),
    "hf_inference": (120, 60),
    "mlx": (9999, 60),
}


class TokenBucket:
    def __init__(self, capacity: int, refill_interval: int):
        self.capacity = capacity
        self.refill_interval = refill_interval
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1, timeout: float | None = 0) -> bool:
        """Attempt to acquire tokens. If timeout=0, returns immediately with boolean.
        Otherwise, waits up to timeout seconds trying to refill and acquire."""
        end = time.monotonic() + timeout if timeout and timeout > 0 else None

        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
            if end is None:
                return False
            if time.monotonic() >= end:
                return False
            await asyncio.sleep(0.05)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed >= self.refill_interval:
            # refill to full capacity
            self._tokens = self.capacity
            self._last_refill = now

    async def update_tokens(self, remaining: int, reset_in: float | None = None) -> None:
        """Update the bucket state based on external rate limit headers."""
        async with self._lock:
            self._tokens = min(remaining, self.capacity)
            self._last_refill = time.monotonic()


class ProviderRateLimiter:
    def __init__(self, limits: dict[str, tuple[int, int]] | None = None):
        limits = limits or DEFAULT_PROVIDER_LIMITS
        self._buckets: dict[str, TokenBucket] = {}
        for name, (cap, interval) in limits.items():
            self._buckets[name] = TokenBucket(cap, interval)

    async def acquire(self, provider_name: str, tokens: int = 1, timeout: float | None = 0) -> bool:
        bucket = self._buckets.get(provider_name)
        if not bucket:
            # No rate-limiting configured -> allow
            return True
        return await bucket.acquire(tokens=tokens, timeout=timeout or 0)

    async def update_limit(
        self, provider_name: str, remaining: int, reset_in: float | None = None
    ) -> None:
        """Update the rate limit state for a provider based on response headers."""
        bucket = self._buckets.get(provider_name)
        if bucket:
            await bucket.update_tokens(remaining, reset_in)


# Singleton for convenient import
_global_limiter: ProviderRateLimiter | None = None


def get_rate_limiter() -> ProviderRateLimiter:
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = ProviderRateLimiter()
    return _global_limiter
