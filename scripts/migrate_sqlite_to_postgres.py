#!/usr/bin/env python3
"""Migrate Fenix trading data from SQLite to PostgreSQL.

Usage:
    DATABASE_URL=postgresql+asyncpg://fenix:password@localhost:5432/fenix_trading \
        python scripts/migrate_sqlite_to_postgres.py

The source SQLite file defaults to ``./fenix_trading.db`` and can be
overridden with ``--sqlite-path``.  The destination PostgreSQL database
is read from ``DATABASE_URL`` (or ``--pg-url``).

The script:
1. Creates tables in PostgreSQL via ``Base.metadata.create_all``.
2. Copies all rows from each SQLite table preserving primary keys.
3. Verifies row counts match.
4. Runs idempotently — existing rows are skipped.

No live process should be writing to either database during migration.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import aiosqlite
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config.database import Base
from src.models.db_models import AgentOutput, Order, Position, Trade
from src.models.user import User

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("migrate")

# Table list in dependency order (parents before children).
TABLES = [User, Order, Trade, Position, AgentOutput]

COLUMN_MAP = {
    "users": ["id", "email", "hashed_password", "full_name", "role", "is_active", "created_at"],
    "orders": [
        "id", "symbol", "type", "side", "quantity", "price", "stop_price",
        "status", "filled_quantity", "created_at", "updated_at",
    ],
    "trades": ["id", "order_id", "symbol", "side", "quantity", "price", "realized_pnl", "executed_at"],
    "positions": [
        "id", "symbol", "side", "quantity", "entry_price", "current_price",
        "unrealized_pnl", "realized_pnl", "opened_at", "closed_at", "is_open",
    ],
    "agent_outputs": ["id", "agent_id", "agent_name", "timestamp", "reasoning", "decision", "confidence", "input_summary"],
}


async def _count_sqlite(db: aiosqlite.Connection, table: str) -> int:
    async with db.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
        row = await cursor.fetchone()
        return row[0] if row else 0


async def _read_sqlite_batch(
    db: aiosqlite.Connection,
    table: str,
    columns: list[str],
    offset: int,
    batch_size: int,
) -> list[dict[str, Any]]:
    placeholders = ",".join(columns)
    async with db.execute(
        f"SELECT {placeholders} FROM {table} LIMIT {batch_size} OFFSET {offset}"
    ) as cursor:
        rows = await cursor.fetchall()
    return [dict(zip(columns, row, strict=False)) for row in rows]


async def _count_pg(pg_engine, table: str) -> int:
    async with pg_engine.connect() as conn:
        result = await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
        return result.scalar() or 0


async def _insert_batch(pg_engine, table: str, columns: list[str], rows: list[dict]) -> int:
    if not rows:
        return 0
    col_list = ",".join(columns)
    param_list = ",".join(f":{c}" for c in columns)
    stmt = text(f"INSERT INTO {table} ({col_list}) VALUES ({param_list}) ON CONFLICT DO NOTHING")
    async with pg_engine.begin() as conn:
        result = await conn.execute(stmt, rows)
        return result.rowcount or 0


async def migrate_table(
    sqlite_db: aiosqlite.Connection,
    pg_engine,
    model_cls,
    table_name: str,
    columns: list[str],
    batch_size: int = 500,
) -> None:
    src_count = await _count_sqlite(sqlite_db, table_name)
    if src_count == 0:
        logger.info("  %s: source empty, skipping", table_name)
        return

    dst_count_before = await _count_pg(pg_engine, table_name)
    logger.info("  %s: source=%d, dest_existing=%d", table_name, src_count, dst_count_before)

    offset = 0
    total_inserted = 0
    while True:
        batch = await _read_sqlite_batch(sqlite_db, table_name, columns, offset, batch_size)
        if not batch:
            break
        inserted = await _insert_batch(pg_engine, table_name, columns, batch)
        total_inserted += inserted
        offset += len(batch)
        if offset % (batch_size * 10) == 0:
            logger.info("    %s: %d/%d rows processed", table_name, offset, src_count)

    dst_count_after = await _count_pg(pg_engine, table_name)
    expected = src_count + dst_count_before
    if dst_count_after != expected:
        logger.warning(
            "  ⚠️ %s: count mismatch after migration: dest=%d expected=%d (inserted=%d)",
            table_name, dst_count_after, expected, total_inserted,
        )
    else:
        logger.info("  ✅ %s: %d rows migrated (total dest=%d)", table_name, total_inserted, dst_count_after)


async def run_migration(sqlite_path: str, pg_url: str) -> dict[str, int]:
    logger.info("Source: SQLite %s", sqlite_path)
    # Mask credentials in the logged URL.
    try:
        safe_url = pg_url.split("://")[0] + "://***@" + pg_url.split("@")[1]
    except Exception:
        safe_url = "***"
    logger.info("Destination: PostgreSQL %s", safe_url)

    pg_engine = create_async_engine(pg_url, echo=False, future=True, pool_pre_ping=True)

    # Create schema in PostgreSQL.
    logger.info("Creating schema in PostgreSQL...")
    async with pg_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Schema created.")

    results: dict[str, int] = {}
    async with aiosqlite.connect(sqlite_path) as sqlite_db:
        sqlite_db.row_factory = aiosqlite.Row
        for model_cls in TABLES:
            table_name = model_cls.__tablename__
            columns = COLUMN_MAP.get(table_name)
            if not columns:
                logger.warning("No column map for %s, skipping", table_name)
                continue
            await migrate_table(sqlite_db, pg_engine, model_cls, table_name, columns)
            results[table_name] = await _count_pg(pg_engine, table_name)

    await pg_engine.dispose()
    logger.info("Migration complete: %s", results)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-path", default=str(PROJECT_ROOT / "fenix_trading.db"))
    parser.add_argument(
        "--pg-url",
        default=None,
        help="PostgreSQL async URL. Defaults to DATABASE_URL env var.",
    )
    args = parser.parse_args()

    pg_url = args.pg_url or __import__("os").getenv("DATABASE_URL", "")
    if "postgresql" not in pg_url:
        logger.error(
            "Destination must be PostgreSQL. Set DATABASE_URL or pass --pg-url.\n"
            "Got: %s", pg_url,
        )
        return 1

    if not Path(args.sqlite_path).exists():
        logger.error("SQLite file not found: %s", args.sqlite_path)
        return 1

    results = asyncio.run(run_migration(args.sqlite_path, pg_url))
    logger.info("✅ Migration finished. Row counts: %s", results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())