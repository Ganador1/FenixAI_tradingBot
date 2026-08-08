import logging
import os

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger(__name__)


def _normalize_async_database_url(database_url: str) -> str:
    """Ensure database URLs use an async SQLAlchemy driver."""
    if database_url.startswith("sqlite://") and not database_url.startswith("sqlite+"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    # Accept plain postgresql:// and upgrade to the asyncpg driver.
    if database_url.startswith("postgresql://") and not database_url.startswith("postgresql+"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return database_url


# Database URL - SQLite for local dev, PostgreSQL for multi-process production.
DATABASE_URL = _normalize_async_database_url(
    os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./fenix_trading.db")
)


def _sqlite_busy_timeout_ms() -> int:
    try:
        return max(1_000, int(os.getenv("FENIX_SQLITE_BUSY_TIMEOUT_MS", "30000")))
    except (TypeError, ValueError):
        return 30_000


SQLITE_BUSY_TIMEOUT_MS = _sqlite_busy_timeout_ms()
_is_sqlite = "sqlite" in DATABASE_URL
_is_postgres = "postgresql" in DATABASE_URL

# PostgreSQL connection pool tuning — sized for the dual-process topology
# (CLI engine + API observer) with headroom for concurrent migrations.
_pg_pool_size = max(5, int(os.getenv("FENIX_PG_POOL_SIZE", "10")))
_pg_max_overflow = max(2, int(os.getenv("FENIX_PG_MAX_OVERFLOW", "5")))
_pg_pool_timeout_sec = max(5, int(os.getenv("FENIX_PG_POOL_TIMEOUT_SEC", "30")))

engine_kwargs: dict = {
    "echo": False,
    "future": True,
}

if _is_sqlite:
    engine_kwargs["connect_args"] = {
        "check_same_thread": False,
        "timeout": SQLITE_BUSY_TIMEOUT_MS / 1000,
    }
elif _is_postgres:
    engine_kwargs.update(
        pool_size=_pg_pool_size,
        max_overflow=_pg_max_overflow,
        pool_timeout=_pg_pool_timeout_sec,
        pool_pre_ping=True,
    )

engine = create_async_engine(DATABASE_URL, **engine_kwargs)


if _is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
        """Make the local live ledger safe for concurrent CLI/API writers."""
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
            cursor.execute("PRAGMA foreign_keys = ON")
        finally:
            cursor.close()


if _is_postgres:

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_postgres_connection(dbapi_connection, _connection_record) -> None:
        """Set statement timeout and application_name for PostgreSQL connections."""
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(
                f"SET statement_timeout = "
                f"{int(os.getenv('FENIX_PG_STATEMENT_TIMEOUT_MS', '30000'))}"
            )
            cursor.execute("SET application_name = 'fenix_trading'")
        finally:
            cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

Base = declarative_base()


async def get_db():
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    # Import model modules before create_all so CLI-only processes register the
    # same metadata as the API process.
    from src.models import db_models as _db_models  # noqa: F401
    from src.models import user as _user  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
