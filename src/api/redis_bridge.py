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

import logging
import os
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
        try:
            import socketio

            self._mgr = socketio.AsyncRedisManager(
                redis_url, channel=channel, write_only=True
            )
            self._url = redis_url
            self._channel = channel
            logger.info(
                "RedisBridge connected: %s (channel=%s)",
                _redact_url_password(redis_url),
                channel,
            )
        except Exception as e:
            self._mgr = None
            logger.warning(f"RedisBridge init failed: {e}")

    async def emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Emit a Socket.IO event to all connected frontend clients via Redis."""
        if not self._mgr:
            return
        try:
            await self._mgr.emit(event, data or {})
        except Exception as e:
            logger.debug(f"RedisBridge emit error: {e}")

    @property
    def available(self) -> bool:
        return self._mgr is not None


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
