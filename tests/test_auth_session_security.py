from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request


def _database_returning(user):
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    database = AsyncMock()
    database.execute.return_value = result
    return database


@pytest.mark.asyncio
async def test_password_change_immediately_revokes_existing_access_token(monkeypatch):
    from src.api import auth
    from src.models.user import User

    monkeypatch.setattr(auth, "SECRET_KEY", "a" * 64)
    user = User(
        id="security-user",
        email="security@example.test",
        hashed_password="password-hash-v1",
        role="admin",
        is_active=True,
    )
    token = auth.create_access_token(
        {
            "sub": user.email,
            "auth_state": auth._authentication_state(user),
        }
    )

    assert await auth.get_current_user(token=token, db=_database_returning(user)) is user

    user.hashed_password = "password-hash-v2"
    with pytest.raises(HTTPException) as exc_info:
        await auth.get_current_user(token=token, db=_database_returning(user))
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_access_token_without_authentication_state_is_rejected(monkeypatch):
    from src.api import auth
    from src.models.user import User

    monkeypatch.setattr(auth, "SECRET_KEY", "b" * 64)
    user = User(
        id="legacy-user",
        email="legacy@example.test",
        hashed_password="password-hash",
        role="viewer",
        is_active=True,
    )
    legacy_token = auth.create_access_token({"sub": user.email})

    with pytest.raises(HTTPException) as exc_info:
        await auth.get_current_user(token=legacy_token, db=_database_returning(user))
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_stolen_admin_token_is_not_enough_for_account_mutations(monkeypatch):
    from src.api import auth
    from src.models.user import User

    admin = User(
        id="protected-admin",
        email="admin@example.test",
        hashed_password=auth.get_password_hash("correct-admin-password"),
        role="admin",
        is_active=True,
    )
    monkeypatch.setattr(auth, "_check_login_rate_limit", AsyncMock())
    record_failure = AsyncMock()
    monkeypatch.setattr(auth, "_record_shared_failed_login", record_failure)

    with pytest.raises(HTTPException) as exc_info:
        await auth._require_admin_reauthentication(
            "attacker-only-has-the-jwt",
            admin,
            Request({"type": "http", "client": ("127.0.0.1", 12345)}),
        )

    assert exc_info.value.status_code == 403
    record_failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_last_admin_check_requests_transactional_row_locking():
    from src.api import auth

    scalar_result = MagicMock()
    scalar_result.scalars.return_value.all.return_value = ["admin-a", "admin-b"]
    database = AsyncMock()
    database.execute.return_value = scalar_result

    assert await auth._lock_active_admin_ids(database) == {"admin-a", "admin-b"}
    statement = database.execute.await_args.args[0]
    assert "FOR UPDATE" in str(statement)
