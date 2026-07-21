from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.redis_bridge import RedisBridge


def _bridge(monkeypatch) -> tuple[RedisBridge, MagicMock, MagicMock]:
    monkeypatch.setenv("FENIX_REDIS_HEALTH_TIMEOUT_SEC", "0.1")
    monkeypatch.setenv("FENIX_REDIS_HEALTH_INTERVAL_SEC", "10")
    monkeypatch.setenv("FENIX_REDIS_RETRY_INTERVAL_SEC", "30")
    manager = MagicMock()
    manager.emit = AsyncMock()
    health = MagicMock()
    health.ping = AsyncMock(return_value=True)
    with (
        patch("socketio.AsyncRedisManager", return_value=manager),
        patch("redis.asyncio.from_url", return_value=health),
    ):
        bridge = RedisBridge("redis://localhost:6379/0")
    return bridge, manager, health


@pytest.mark.asyncio
async def test_unhealthy_redis_skips_socketio_emit_and_throttles_retries(monkeypatch):
    bridge, manager, health = _bridge(monkeypatch)
    health.ping.side_effect = ConnectionError("redis unavailable")

    await bridge.emit("agent:update", {"safe": True})
    await bridge.emit("agent:update", {"safe": True})

    assert health.ping.await_count == 1
    manager.emit.assert_not_awaited()
    assert bridge.available is True


@pytest.mark.asyncio
async def test_redis_recovery_resumes_socketio_emit(monkeypatch):
    bridge, manager, health = _bridge(monkeypatch)
    health.ping.side_effect = [ConnectionError("redis unavailable"), True]

    await bridge.emit("agent:update", {})
    bridge._next_health_check_at = 0.0
    await bridge.emit("agent:update", {"cycle": 2})

    assert health.ping.await_count == 2
    manager.emit.assert_awaited_once_with("agent:update", {"cycle": 2})


@pytest.mark.asyncio
async def test_healthy_redis_uses_cached_health_between_events(monkeypatch):
    bridge, manager, health = _bridge(monkeypatch)

    await bridge.emit("one", {})
    await bridge.emit("two", {})

    assert health.ping.await_count == 1
    assert manager.emit.await_count == 2
