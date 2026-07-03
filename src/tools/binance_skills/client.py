# src/tools/binance_skills/client.py
"""
HTTP Client for Binance Skills Hub APIs (Web3 public endpoints).

All endpoints are public and require NO authentication.
Base URL: https://web3.binance.com/bapi/defi/

Rate limiting and caching built-in to avoid abuse.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Base URL for Binance Web3 APIs
WEB3_BASE_URL = "https://web3.binance.com/bapi/defi"

# Default headers
DEFAULT_HEADERS = {
    "Accept-Encoding": "identity",
    "Content-Type": "application/json",
    "User-Agent": "FenixAI-TradingBot/2.0",
}

# Rate limiting: max requests per minute
_request_timestamps: list[float] = []
_rate_limit_lock = asyncio.Lock()
MAX_REQUESTS_PER_MINUTE = 30
_RATE_LIMIT_WINDOW = 60.0


class BinanceSkillsError(Exception):
    """Error from Binance Skills Hub API."""

    def __init__(self, message: str, code: str | None = None, status: int = 0):
        self.code = code
        self.status = status
        super().__init__(message)


class BinanceSkillsClient:
    """
    Async HTTP client for Binance Skills Hub public APIs.

    Features:
    - Rate limiting (30 req/min)
    - Response caching (configurable TTL)
    - Automatic retries with backoff
    - Timeout handling
    """

    def __init__(
        self,
        timeout: float = 15.0,
        max_retries: int = 2,
        cache_ttl: int = 300,
    ):
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self.cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, Any]] = {}
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers=DEFAULT_HEADERS,
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _check_cache(self, cache_key: str) -> Any | None:
        if cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if time.time() - ts < self.cache_ttl:
                logger.debug(f"[BinanceSkills] Cache hit: {cache_key[:60]}")
                return data
            del self._cache[cache_key]
        return None

    def _set_cache(self, cache_key: str, data: Any) -> None:
        self._cache[cache_key] = (time.time(), data)
        # Evict old entries if cache grows too large
        if len(self._cache) > 200:
            oldest = sorted(self._cache.items(), key=lambda x: x[1][0])
            for key, _ in oldest[:50]:
                del self._cache[key]

    @staticmethod
    async def _rate_limit() -> None:
        """Simple in-memory async rate limiter."""
        global _request_timestamps
        async with _rate_limit_lock:
            now = time.time()
            _request_timestamps = [
                t for t in _request_timestamps if now - t < _RATE_LIMIT_WINDOW
            ]
            if len(_request_timestamps) >= MAX_REQUESTS_PER_MINUTE:
                wait = _RATE_LIMIT_WINDOW - (now - _request_timestamps[0])
                if wait > 0:
                    logger.warning(
                        f"[BinanceSkills] Rate limit reached, waiting {wait:.1f}s"
                    )
                    await asyncio.sleep(min(wait, 5.0))
            _request_timestamps.append(time.time())

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        GET request to Binance Web3 API.

        Args:
            path: API path (e.g., /v1/public/wallet-direct/...)
            params: Query parameters

        Returns:
            Parsed JSON response data
        """
        cache_key = f"GET:{path}:{params}"
        cached = self._check_cache(cache_key)
        if cached is not None:
            return cached

        await self._rate_limit()
        url = f"{WEB3_BASE_URL}{path}"
        session = await self._get_session()

        for attempt in range(self.max_retries + 1):
            try:
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("success") or data.get("code") == "000000":
                            result = data.get("data", data)
                            self._set_cache(cache_key, result)
                            return result
                        raise BinanceSkillsError(
                            f"API error: {data.get('message', 'Unknown')}",
                            code=data.get("code"),
                        )
                    elif resp.status == 429:
                        wait = 2 ** (attempt + 1)
                        logger.warning(f"[BinanceSkills] 429 rate limited, waiting {wait}s")
                        await asyncio.sleep(wait)
                    else:
                        text = await resp.text()
                        raise BinanceSkillsError(
                            f"HTTP {resp.status}: {text[:200]}",
                            status=resp.status,
                        )
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < self.max_retries:
                    wait = 1.0 * (attempt + 1)
                    logger.warning(
                        f"[BinanceSkills] GET {path} failed (attempt {attempt+1}): {e}, retrying in {wait}s"
                    )
                    await asyncio.sleep(wait)
                else:
                    raise BinanceSkillsError(f"Request failed after {self.max_retries+1} attempts: {e}")

        raise BinanceSkillsError(f"GET {path} failed after all retries")

    async def post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        POST request to Binance Web3 API.

        Args:
            path: API path
            body: JSON body

        Returns:
            Parsed JSON response data
        """
        cache_key = f"POST:{path}:{body}"
        cached = self._check_cache(cache_key)
        if cached is not None:
            return cached

        await self._rate_limit()
        url = f"{WEB3_BASE_URL}{path}"
        session = await self._get_session()

        for attempt in range(self.max_retries + 1):
            try:
                async with session.post(url, json=body, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("success") or data.get("code") == "000000":
                            result = data.get("data", data)
                            self._set_cache(cache_key, result)
                            return result
                        raise BinanceSkillsError(
                            f"API error: {data.get('message', 'Unknown')}",
                            code=data.get("code"),
                        )
                    elif resp.status == 429:
                        wait = 2 ** (attempt + 1)
                        logger.warning(f"[BinanceSkills] 429 rate limited, waiting {wait}s")
                        await asyncio.sleep(wait)
                    else:
                        text = await resp.text()
                        raise BinanceSkillsError(
                            f"HTTP {resp.status}: {text[:200]}",
                            status=resp.status,
                        )
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < self.max_retries:
                    wait = 1.0 * (attempt + 1)
                    logger.warning(
                        f"[BinanceSkills] POST {path} failed (attempt {attempt+1}): {e}, retrying in {wait}s"
                    )
                    await asyncio.sleep(wait)
                else:
                    raise BinanceSkillsError(f"Request failed after {self.max_retries+1} attempts: {e}")

        raise BinanceSkillsError(f"POST {path} failed after all retries")


# Singleton client
_client: BinanceSkillsClient | None = None


def get_skills_client(
    timeout: float = 12.0,
    cache_ttl: int = 300,
) -> BinanceSkillsClient:
    """Get or create the singleton BinanceSkillsClient."""
    global _client
    if _client is None:
        _client = BinanceSkillsClient(timeout=timeout, cache_ttl=cache_ttl)
    return _client


async def close_skills_client() -> None:
    """Close singleton client session (useful for short scripts/tests)."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
