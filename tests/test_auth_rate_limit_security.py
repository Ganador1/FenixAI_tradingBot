from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api import auth


def _request(ip: str = "203.0.113.9") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "headers": [],
            "client": (ip, 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


@pytest.fixture(autouse=True)
def _reset_limiter_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(auth, "SECRET_KEY", "test-only-jwt-secret-" + ("x" * 32))
    monkeypatch.setattr(auth, "_login_rate_redis", None)
    auth._failed_login_attempts.clear()
    yield
    auth._failed_login_attempts.clear()


@pytest.mark.asyncio
async def test_exposed_api_fails_closed_without_shared_limiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOW_EXPOSE_API", "true")
    monkeypatch.delenv("FENIX_REQUIRE_SHARED_LOGIN_RATE_LIMIT", raising=False)

    async def no_shared_client() -> None:
        return None

    monkeypatch.setattr(auth, "_shared_rate_limit_client", no_shared_client)

    with pytest.raises(HTTPException) as exc_info:
        await auth._check_login_rate_limit(_request(), "user@example.test")

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_shared_limiter_uses_non_identifying_keys_and_blocks_either_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_keys: tuple[str, str] | None = None

    class FakeRedis:
        async def mget(self, keys: tuple[str, str]) -> list[str]:
            nonlocal captured_keys
            captured_keys = keys
            return ["0", str(auth.LOGIN_RATE_LIMIT_ATTEMPTS)]

    async def shared_client() -> FakeRedis:
        return FakeRedis()

    monkeypatch.setattr(auth, "_shared_rate_limit_client", shared_client)
    email = "user@example.test"
    ip = "203.0.113.9"

    with pytest.raises(HTTPException) as exc_info:
        await auth._check_login_rate_limit(_request(ip), email)

    assert exc_info.value.status_code == 429
    assert captured_keys is not None
    assert all(ip not in key and email not in key for key in captured_keys)
    assert captured_keys[0] != captured_keys[1]


@pytest.mark.asyncio
async def test_failed_login_updates_both_shared_scopes_with_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations: list[tuple[str, str, int | None]] = []

    class FakePipeline:
        async def __aenter__(self) -> FakePipeline:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def incr(self, key: str) -> FakePipeline:
            operations.append(("incr", key, None))
            return self

        def expire(self, key: str, seconds: int) -> FakePipeline:
            operations.append(("expire", key, seconds))
            return self

        async def execute(self) -> list[int]:
            return [1, 1, 1, 1]

    class FakeRedis:
        def pipeline(self, *, transaction: bool) -> FakePipeline:
            assert transaction is True
            return FakePipeline()

    async def shared_client() -> FakeRedis:
        return FakeRedis()

    monkeypatch.setattr(auth, "_shared_rate_limit_client", shared_client)

    await auth._record_shared_failed_login(_request(), "user@example.test")

    increments = [operation for operation in operations if operation[0] == "incr"]
    expiries = [operation for operation in operations if operation[0] == "expire"]
    assert len(increments) == 2
    assert len(expiries) == 2
    assert all(
        operation[2] == int(auth.LOGIN_RATE_LIMIT_WINDOW_SECONDS) for operation in expiries
    )
