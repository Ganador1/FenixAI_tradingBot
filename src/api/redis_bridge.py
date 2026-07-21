"""
Redis Bridge — allows the live trading process to emit WebSocket events
to the API server (which broadcasts to frontend clients) via Redis pub/sub.

Usage in the live trading engine:
    from src.api.redis_bridge import get_redis_bridge

    bridge = get_redis_bridge()
    if bridge:
        await bridge.emit("trade:executed", {"symbol": "ETHUSDC", ...})

The API server must be started with REDIS_URL=redis://localhost:6379 and
both processes must use the same Redis channel (default: "fenix_socketio").
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger("RedisBridge")

_redis_bridge: RedisBridge | None = None


def _redact_url_password(url: str) -> str:
    """Return a URL safe for logs by removing embedded credentials."""
    try:
        parsed = urlsplit(url)
        if not parsed.password:
            return url
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        username = parsed.username or ""
        netloc = f"{username}:***@{host}" if username else f"***@{host}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    except Exception:
        return "<redacted-url>"


class RedisBridge:
    """Write-only Redis client that emits Socket.IO events to the API server."""

    def __init__(self, redis_url: str, channel: str = "fenix_socketio"):
        self._mgr = None
        self._health_client = None
        self._url = redis_url
        self._channel = channel
        self._healthy = False
        self._outage_logged = False
        self._next_health_check_at = 0.0
        self._health_timeout_sec = max(
            0.1, float(os.getenv("FENIX_REDIS_HEALTH_TIMEOUT_SEC", "0.5"))
        )
        self._health_interval_sec = max(
            1.0, float(os.getenv("FENIX_REDIS_HEALTH_INTERVAL_SEC", "10"))
        )
        self._retry_interval_sec = max(
            1.0, float(os.getenv("FENIX_REDIS_RETRY_INTERVAL_SEC", "30"))
        )
        try:
            import socketio
            from redis import asyncio as redis_async

            self._mgr = socketio.AsyncRedisManager(
                redis_url, channel=channel, write_only=True
            )
            self._health_client = redis_async.from_url(
                redis_url,
                socket_connect_timeout=self._health_timeout_sec,
                socket_timeout=self._health_timeout_sec,
            )
            logger.info(
                "RedisBridge configured: %s (channel=%s); awaiting health check",
                _redact_url_password(redis_url),
                channel,
            )
        except Exception as e:
            self._mgr = None
            self._health_client = None
            logger.warning("RedisBridge init failed: %s", e)

    async def _check_health(self) -> bool:
        """Return Redis health without invoking Socket.IO while Redis is down."""
        if self._mgr is None or self._health_client is None:
            return False

        now = time.monotonic()
        if now < self._next_health_check_at:
            return self._healthy

        try:
            healthy = bool(
                await asyncio.wait_for(
                    self._health_client.ping(),
                    timeout=self._health_timeout_sec,
                )
            )
        except Exception as exc:
            self._healthy = False
            self._next_health_check_at = now + self._retry_interval_sec
            if not self._outage_logged:
                self._outage_logged = True
                logger.warning(
                    "Redis unavailable; Socket.IO relay paused for %.0fs while local "
                    "event persistence continues: %s",
                    self._retry_interval_sec,
                    type(exc).__name__,
                )
            return False

        self._healthy = healthy
        self._next_health_check_at = now + (
            self._health_interval_sec if healthy else self._retry_interval_sec
        )
        if healthy and self._outage_logged:
            logger.info("RedisBridge recovered; Socket.IO relay resumed")
        self._outage_logged = not healthy
        return healthy

    async def emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Emit a Socket.IO event to all connected frontend clients via Redis."""
        if not await self._check_health():
            return
        try:
            await self._mgr.emit(event, data or {})
        except Exception as e:
            self._healthy = False
            self._next_health_check_at = time.monotonic() + self._retry_interval_sec
            if not self._outage_logged:
                self._outage_logged = True
                logger.warning(
                    "Redis Socket.IO relay failed; pausing retries for %.0fs: %s",
                    self._retry_interval_sec,
                    type(e).__name__,
                )

    @property
    def available(self) -> bool:
        return self._mgr is not None and self._health_client is not None


def get_redis_bridge() -> RedisBridge | None:
    """Get or create the singleton Redis bridge instance."""
    global _redis_bridge
    if _redis_bridge is not None:
        return _redis_bridge if _redis_bridge.available else None

    redis_url = os.getenv("REDIS_URL", os.getenv("FENIX_REDIS_URL", ""))
    if not redis_url:
        return None

    _redis_bridge = RedisBridge(
        redis_url,
        channel=os.getenv("FENIX_REDIS_CHANNEL", "fenix_socketio"),
    )
    return _redis_bridge if _redis_bridge.available else None
