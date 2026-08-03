from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select


@pytest.mark.asyncio
async def test_user_admin_mutations_persist_in_database():
    from src.api.auth import (
        PasswordResetPayload,
        UserAdminPayload,
        UserCreatePayload,
        create_user,
        delete_user,
        reset_password,
        update_user,
        verify_password,
    )
    from src.config.database import SessionLocal, init_db
    from src.models.user import User

    await init_db()
    suffix = uuid.uuid4().hex
    admin = User(
        id=f"admin-{suffix}",
        email=f"admin-{suffix}@example.test",
        hashed_password="unused-in-this-test",
        full_name="Test Admin",
        role="admin",
        is_active=True,
    )

    async with SessionLocal() as session:
        session.add(admin)
        await session.commit()
        created = await create_user(
            UserCreatePayload(
                email=f"trader-{suffix}@example.test",
                password="initial-password-123",
                role="trader",
                status="active",
                profile={"first_name": "Community", "last_name": "Trader"},
            ),
            current_admin=admin,
            db=session,
        )
        user_id = created["id"]

    async with SessionLocal() as session:
        persisted = await session.get(User, user_id)
        assert persisted is not None
        assert persisted.full_name == "Community Trader"
        assert verify_password("initial-password-123", persisted.hashed_password)

        updated = await update_user(
            user_id,
            UserAdminPayload(
                email=f"viewer-{suffix}@example.test",
                role="viewer",
                status="inactive",
                profile={"first_name": "Persistent", "last_name": "Viewer"},
            ),
            current_admin=admin,
            db=session,
        )
        assert updated["status"] == "inactive"
        assert updated["role"] == "viewer"

        await reset_password(
            user_id,
            PasswordResetPayload(new_password="replacement-password-456"),
            current_admin=admin,
            db=session,
        )

    async with SessionLocal() as session:
        persisted = await session.get(User, user_id)
        assert persisted is not None
        assert persisted.email == f"viewer-{suffix}@example.test"
        assert persisted.full_name == "Persistent Viewer"
        assert verify_password("replacement-password-456", persisted.hashed_password)
        await delete_user(user_id, current_admin=admin, db=session)

    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_user_admin_cannot_delete_own_account():
    from src.api.auth import delete_user
    from src.config.database import SessionLocal, init_db
    from src.models.user import User

    await init_db()
    suffix = uuid.uuid4().hex
    admin = User(
        id=f"self-admin-{suffix}",
        email=f"self-admin-{suffix}@example.test",
        hashed_password="unused-in-this-test",
        role="admin",
        is_active=True,
    )
    async with SessionLocal() as session:
        session.add(admin)
        await session.commit()
        with pytest.raises(HTTPException, match="delete your own account") as exc_info:
            await delete_user(str(admin.id), current_admin=admin, db=session)
        assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_position_close_creates_and_persists_dashboard_alert(monkeypatch):
    from src.api import engine_events

    emit = AsyncMock()
    persist = AsyncMock()
    monkeypatch.setattr(engine_events, "_persist_system_alert", persist)
    handler = engine_events.create_engine_event_handler(emit, persist=True)

    await handler(
        "position:closed",
        {
            "symbol": "ETHUSDT",
            "exit_reason": "stop_loss",
            "timestamp": "2026-08-03T12:00:00+00:00",
        },
    )

    alert = next(call.args[1] for call in emit.await_args_list if call.args[0] == "system:alert")
    assert alert["title"] == "Stop loss hit"
    assert alert["severity"] == "high"
    assert "ETHUSDT" in alert["message"]
    persist.assert_awaited_once_with(alert)
    assert any(call.args[0] == "position:update" for call in emit.await_args_list)


@pytest.mark.asyncio
async def test_system_alert_persistence_survives_a_new_database_session():
    from src.api.engine_events import _persist_system_alert
    from src.config.database import SessionLocal, init_db
    from src.models.db_models import SystemAlert

    await init_db()
    alert_id = str(uuid.uuid4())
    await _persist_system_alert(
        {
            "id": alert_id,
            "type": "error",
            "title": "Execution error",
            "message": "An order failed safely.",
            "component": "trading",
            "severity": "critical",
            "created_at": "2026-08-03T12:00:00+00:00",
            "resolved": False,
        }
    )

    async with SessionLocal() as session:
        persisted = await session.get(SystemAlert, alert_id)
        assert persisted is not None
        assert persisted.title == "Execution error"
        assert persisted.severity == "critical"
        await session.delete(persisted)
        await session.commit()


@pytest.mark.asyncio
async def test_metrics_history_filters_real_samples_by_timeframe(monkeypatch):
    from src.api import server

    old_history = list(server._METRICS_HISTORY)
    old_last_sample = server._LAST_METRICS_HISTORY_TS
    try:
        server._METRICS_HISTORY.clear()
        server._METRICS_HISTORY.extend(
            [
                {"timestamp": 1_000.0, "cpu": {"usage": 1.0}},
                {"timestamp": 4_000.0, "cpu": {"usage": 2.0}},
            ]
        )
        monkeypatch.setattr(server.time, "time", lambda: 4_100.0)
        payload = await server.get_metrics_history("15m")
        assert [point["timestamp"] for point in payload["metrics"]] == [4_000.0]
    finally:
        server._METRICS_HISTORY.clear()
        server._METRICS_HISTORY.extend(old_history)
        server._LAST_METRICS_HISTORY_TS = old_last_sample


def test_frontend_community_bugfixes_are_wired_to_real_behavior():
    root = Path(__file__).resolve().parents[1] / "frontend"
    dashboard = (root / "pages" / "Dashboard.tsx").read_text()
    reasoning = (root / "pages" / "ReasoningBank.tsx").read_text()
    sidebar = (root / "components" / "Sidebar.tsx").read_text()
    chart = (root / "components" / "SystemStatusChart.tsx").read_text()
    login = (root / "components" / "ModernLoginPage.tsx").read_text()

    assert "navigate('/trading')" in dashboard
    assert "navigate('/agents')" in dashboard
    assert "navigate('/system')" in dashboard
    assert "fetchAlerts();" in dashboard
    assert "visibleEntries" in reasoning and "setVisibleEntries" in reasoning
    assert "socketConnected ? 'Connected' : 'Offline'" in sidebar
    assert "/api/system/metrics/history?timeframe=1h" in chart
    assert "Remember me" not in login
    assert "Continue with Google" not in login
    assert "Sign up" not in login

    for dead_file in (
        root / "components" / "trading" / "OrderForm.tsx",
        root / "components" / "trading" / "PositionsList.tsx",
        root / "components" / "DashboardComponents.tsx",
        root / "pages" / "Login.tsx",
    ):
        assert not dead_file.exists()
