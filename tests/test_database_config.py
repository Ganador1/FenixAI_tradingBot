from src.config.database import _normalize_async_database_url


def test_plain_sqlite_database_url_uses_async_driver():
    assert (
        _normalize_async_database_url("sqlite:///fenix_trading.db")
        == "sqlite+aiosqlite:///fenix_trading.db"
    )


def test_sqlite_async_database_url_is_preserved():
    url = "sqlite+aiosqlite:///./fenix_trading.db"
    assert _normalize_async_database_url(url) == url


def test_non_sqlite_database_url_is_preserved():
    url = "postgresql+asyncpg://user:pass@localhost/fenix"
    assert _normalize_async_database_url(url) == url
