from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.routing import APIRoute


def test_system_settings_persist_mask_secrets_and_reset(monkeypatch, tmp_path):
    from src.api.system_settings import (
        DEFAULT_SYSTEM_SETTINGS,
        load_system_settings,
        public_system_settings,
        reset_system_settings,
        update_system_settings,
    )

    path = tmp_path / "settings.json"
    monkeypatch.setenv("FENIX_SYSTEM_SETTINGS_PATH", str(path))

    updated = update_system_settings(
        "notifications",
        {
            "email_host": "smtp.example.test",
            "email_password": "not-returned-to-clients",
        },
    )
    assert updated["email_host"] == "smtp.example.test"
    assert path.exists()

    persisted = load_system_settings()
    assert persisted["notifications"]["email_password"] == "not-returned-to-clients"

    public = public_system_settings(persisted)
    assert public["notifications"]["email_password"] == ""
    assert public["_meta"]["configured_secrets"]["notifications.email_password"] is True

    # Saving the masked representation must preserve the stored secret.
    update_system_settings("notifications", {"email_password": ""})
    assert load_system_settings()["notifications"]["email_password"] == "not-returned-to-clients"

    reset = reset_system_settings("notifications")
    assert reset == DEFAULT_SYSTEM_SETTINGS["notifications"]
    assert load_system_settings()["notifications"] == DEFAULT_SYSTEM_SETTINGS["notifications"]


def test_system_settings_reject_unknown_keys(monkeypatch, tmp_path):
    from src.api.system_settings import SettingsValidationError, update_system_settings

    monkeypatch.setenv("FENIX_SYSTEM_SETTINGS_PATH", str(tmp_path / "settings.json"))
    with pytest.raises(SettingsValidationError, match="Unknown setting"):
        update_system_settings("general", {"unexpected": True})


def test_settings_routes_use_control_access_guard():
    import src.api.server as server

    guarded_paths = {
        "/api/system/settings",
        "/api/system/settings/{section}",
        "/api/system/settings/{section}/reset",
        "/api/system/test-connection/{type}",
    }
    routes = {
        route.path: route
        for route in server.app.routes
        if isinstance(route, APIRoute) and route.path in guarded_paths
    }
    assert routes.keys() == guarded_paths
    for route in routes.values():
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        assert server.require_control_access in dependencies


@pytest.mark.asyncio
async def test_database_connection_check_executes_real_query():
    import src.api.server as server

    db = AsyncMock()
    result = await server.test_system_connection("database", db=db)

    db.execute.assert_awaited_once()
    assert str(db.execute.await_args.args[0]) == "SELECT 1"
    assert result["success"] is True


@pytest.mark.asyncio
async def test_paper_balance_uses_ledger_without_binance(monkeypatch):
    import src.api.server as server

    monkeypatch.setenv("FENIX_BALANCE_FALLBACK_USDT", "1000")
    monkeypatch.setattr(server, "engine", SimpleNamespace(paper_trading=True))
    signed_request = AsyncMock(side_effect=AssertionError("paper mode called Binance"))
    monkeypatch.setattr(server, "_with_binance_client", signed_request)

    realized = MagicMock()
    realized.scalar_one.return_value = 25.0
    unrealized = MagicMock()
    unrealized.scalar_one.return_value = -5.0
    db = AsyncMock()
    db.execute.side_effect = [realized, unrealized]

    result = await server.get_account_balance(db=db)

    assert result["mode"] == "paper"
    assert result["source"] == "paper_ledger"
    assert result["realized_pnl"] == pytest.approx(25.0)
    assert result["total_usdt"] == pytest.approx(1020.0)
    signed_request.assert_not_awaited()


def test_cached_balance_is_io_free():
    from src.trading.engine import TradingEngine

    executor = MagicMock()
    engine = object.__new__(TradingEngine)
    engine.executor = executor
    engine.risk_manager = SimpleNamespace(_current_balance=321.0)

    assert engine._get_cached_balance() == pytest.approx(321.0)
    executor.get_balance.assert_not_called()


def test_api_port_uses_environment_and_cli_override(monkeypatch):
    import run_fenix

    monkeypatch.setenv("FENIX_API_PORT", "8123")
    monkeypatch.setattr("sys.argv", ["run_fenix.py"])
    assert run_fenix.parse_args().port == 8123

    monkeypatch.setattr("sys.argv", ["run_fenix.py", "--port", "8456"])
    assert run_fenix.parse_args().port == 8456
