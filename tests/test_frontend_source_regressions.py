from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_recent_trades_cleanup_does_not_remove_global_connect_handlers():
    source = _read("frontend/components/RecentTrades.tsx")

    assert "socket.off('connect');" not in source
    assert 'socket.off("connect");' not in source
    assert "socket.on('connect', () =>" not in source
    assert 'socket.on("connect", () =>' not in source


def test_api_server_redis_channel_is_configurable():
    source = _read("src/api/server.py")

    assert 'os.getenv("FENIX_REDIS_CHANNEL"' in source
    assert 'channel="fenix_socketio"' not in source


def test_compose_redis_password_has_development_fallback():
    source = _read("docker-compose.yml")

    assert "${REDIS_PASSWORD:-" in source
    assert "${REDIS_PASSWORD:?" not in source


def test_frontend_auth_reads_only_the_zustand_store():
    auth_helper = _read("frontend/lib/auth.ts")
    assert "useAuthStore.getState().token" in auth_helper

    direct_auth_storage_reads = []
    for path in (REPO_ROOT / "frontend").rglob("*.ts*"):
        if "node_modules" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if (
            "localStorage.getItem('token')" in source
            or "localStorage.getItem('auth_token')" in source
        ):
            direct_auth_storage_reads.append(str(path.relative_to(REPO_ROOT)))

    assert direct_auth_storage_reads == []


def test_tabs_are_stateful_and_hide_inactive_content():
    source = _read("frontend/components/ui/Tabs.tsx")

    assert "onClick={() => setActiveTab(value)}" in source
    assert "aria-selected={active}" in source
    assert "hidden={!active}" in source
