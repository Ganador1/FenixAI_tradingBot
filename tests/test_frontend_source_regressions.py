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
