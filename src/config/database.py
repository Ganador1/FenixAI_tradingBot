import os

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker


def _normalize_async_database_url(database_url: str) -> str:
    """Ensure SQLite URLs use an async SQLAlchemy driver."""
    if database_url.startswith("sqlite://") and not database_url.startswith("sqlite+"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url


# Database URL - using SQLite for local development
DATABASE_URL = _normalize_async_database_url(
    os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./fenix_trading.db")
)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)

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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
