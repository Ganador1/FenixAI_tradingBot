import pytest

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


@pytest.mark.asyncio
async def test_sqlite_engine_enables_wal_and_busy_timeout():
    from src.config.database import SQLITE_BUSY_TIMEOUT_MS, engine

    async with engine.connect() as connection:
        journal_mode = (await connection.exec_driver_sql("PRAGMA journal_mode")).scalar_one()
        busy_timeout = (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar_one()

    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) >= SQLITE_BUSY_TIMEOUT_MS
