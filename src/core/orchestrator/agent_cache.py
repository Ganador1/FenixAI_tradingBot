"""
Lightweight in-memory cache for agent reports.

Used to reuse the last *valid* report when an agent LLM call times out on short
timeframes. This is intentionally small and process-local.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CachedAgentReport:
    report: dict[str, Any]
    cached_at: float


class AgentReportCache:
    def __init__(self, *, max_entries: int = 128):
        self._max_entries = int(max_entries)
        self._cache: dict[str, CachedAgentReport] = {}
        self._inflight: dict[str, asyncio.Task] = {}
        self._refresh_started_at: dict[str, float] = {}

    @staticmethod
    def _key(agent: str, symbol: str, timeframe: str) -> str:
        return f"{agent}:{symbol.upper()}:{timeframe}"

    def get(
        self,
        *,
        agent: str,
        symbol: str,
        timeframe: str,
        ttl_sec: float,
    ) -> tuple[dict[str, Any], float] | None:
        key = self._key(agent, symbol, timeframe)
        entry = self._cache.get(key)
        if entry is None:
            return None

        now = time.time()
        age = now - float(entry.cached_at)
        if age > float(ttl_sec):
            self._cache.pop(key, None)
            return None

        # Return a shallow copy to avoid callers mutating the stored report.
        return dict(entry.report), age

    def set(self, *, agent: str, symbol: str, timeframe: str, report: dict[str, Any]) -> None:
        if self._max_entries > 0 and len(self._cache) >= self._max_entries:
            # Evict oldest.
            oldest_key = min(self._cache, key=lambda k: self._cache[k].cached_at)
            self._cache.pop(oldest_key, None)

        key = self._key(agent, symbol, timeframe)
        self._cache[key] = CachedAgentReport(report=dict(report), cached_at=time.time())

    def get_inflight(self, *, agent: str, symbol: str, timeframe: str) -> asyncio.Task | None:
        key = self._key(agent, symbol, timeframe)
        task = self._inflight.get(key)
        if task is None:
            return None
        if task.done():
            self._inflight.pop(key, None)
            return None
        return task

    def set_inflight(self, *, agent: str, symbol: str, timeframe: str, task: asyncio.Task) -> None:
        key = self._key(agent, symbol, timeframe)
        self._inflight[key] = task

        def _cleanup(_t: asyncio.Task) -> None:
            self._inflight.pop(key, None)

        task.add_done_callback(_cleanup)

    def can_start_refresh(
        self,
        *,
        agent: str,
        symbol: str,
        timeframe: str,
        min_interval_sec: float,
    ) -> bool:
        """Return True if a refresh can start now (rate-limited per key)."""
        key = self._key(agent, symbol, timeframe)
        now = time.time()
        last = float(self._refresh_started_at.get(key, 0.0) or 0.0)
        if (now - last) < float(min_interval_sec):
            return False
        if self.get_inflight(agent=agent, symbol=symbol, timeframe=timeframe) is not None:
            return False
        self._refresh_started_at[key] = now
        return True
