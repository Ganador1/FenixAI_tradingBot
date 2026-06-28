from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config.database import Base
from src.models.db_models import Order, Position, Trade


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    db_path = tmp_path / "fenix_test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_persist_open_position_upserts_one_open_position(session_factory):
    from src.trading.persistence import persist_open_position

    await persist_open_position(
        symbol="ETHUSDC",
        side="SHORT",
        quantity=0.05,
        entry_price=1729.81,
        current_price=1707.79,
        position_id="live:ETHUSDC",
        session_factory=session_factory,
    )
    await persist_open_position(
        symbol="ETHUSDC",
        side="SHORT",
        quantity=0.07,
        entry_price=1715.50,
        current_price=1710.25,
        position_id="live:ETHUSDC",
        session_factory=session_factory,
    )

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Position).where(Position.symbol == "ETHUSDC", Position.is_open.is_(True))
            )
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].id == "live:ETHUSDC"
    assert rows[0].side == "SHORT"
    assert rows[0].quantity == pytest.approx(0.07)
    assert rows[0].entry_price == pytest.approx(1715.50)
    assert rows[0].current_price == pytest.approx(1710.25)
    assert rows[0].unrealized_pnl == pytest.approx((1715.50 - 1710.25) * 0.07)


@pytest.mark.asyncio
async def test_persist_order_fill_is_idempotent(session_factory):
    from src.trading.persistence import persist_order_fill

    await persist_order_fill(
        symbol="ETHUSDC",
        side="SELL",
        quantity=0.05,
        price=1729.81,
        order_id="entry-123",
        realized_pnl=0.0,
        session_factory=session_factory,
    )
    await persist_order_fill(
        symbol="ETHUSDC",
        side="SELL",
        quantity=0.05,
        price=1729.81,
        order_id="entry-123",
        realized_pnl=0.0,
        session_factory=session_factory,
    )

    async with session_factory() as session:
        orders = (await session.execute(select(Order))).scalars().all()
        trades = (await session.execute(select(Trade))).scalars().all()

    assert len(orders) == 1
    assert orders[0].id == "entry-123"
    assert orders[0].status == "filled"
    assert orders[0].filled_quantity == pytest.approx(0.05)
    assert len(trades) == 1
    assert trades[0].id == "fill:entry-123"
    assert trades[0].order_id == "entry-123"


@pytest.mark.asyncio
async def test_expire_stale_pending_orders_only_expires_old_pending(session_factory):
    from src.trading.persistence import expire_stale_pending_orders

    now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    async with session_factory() as session:
        session.add_all(
            [
                Order(
                    id="old-pending",
                    symbol="ETHUSDC",
                    type="market",
                    side="buy",
                    quantity=1.0,
                    status="pending",
                    created_at=now - timedelta(hours=30),
                    updated_at=now - timedelta(hours=30),
                ),
                Order(
                    id="recent-pending",
                    symbol="ETHUSDC",
                    type="market",
                    side="buy",
                    quantity=1.0,
                    status="pending",
                    created_at=now - timedelta(hours=3),
                    updated_at=now - timedelta(hours=3),
                ),
                Order(
                    id="old-filled",
                    symbol="ETHUSDC",
                    type="market",
                    side="buy",
                    quantity=1.0,
                    status="filled",
                    created_at=now - timedelta(hours=30),
                    updated_at=now - timedelta(hours=30),
                ),
            ]
        )
        await session.commit()

    expired_count = await expire_stale_pending_orders(
        max_age_hours=24.0,
        now=now,
        session_factory=session_factory,
    )

    async with session_factory() as session:
        rows = {
            row.id: row
            for row in (await session.execute(select(Order))).scalars().all()
        }

    assert expired_count == 1
    assert rows["old-pending"].status == "expired"
    assert rows["old-pending"].updated_at == now
    assert rows["recent-pending"].status == "pending"
    assert rows["old-filled"].status == "filled"


@pytest.mark.asyncio
async def test_persist_position_close_marks_open_position_closed(session_factory):
    from src.trading.persistence import persist_open_position, persist_position_close

    await persist_open_position(
        symbol="ETHUSDC",
        side="SHORT",
        quantity=0.05,
        entry_price=1729.81,
        current_price=1710.0,
        position_id="live:ETHUSDC",
        session_factory=session_factory,
    )

    await persist_position_close(
        symbol="ETHUSDC",
        close_result={
            "trade_id": "entry-123",
            "side": "SHORT",
            "quantity": 0.05,
            "exit_price": 1707.79,
            "pnl": 1.101,
            "exit_time": datetime.now(timezone.utc).isoformat(),
        },
        tracked_position=SimpleNamespace(side="SHORT", quantity=0.05),
        session_factory=session_factory,
    )

    async with session_factory() as session:
        positions = (await session.execute(select(Position))).scalars().all()
        trades = (await session.execute(select(Trade))).scalars().all()

    assert len(positions) == 1
    assert positions[0].is_open is False
    assert positions[0].closed_at is not None
    assert positions[0].realized_pnl == pytest.approx(1.101)
    assert len(trades) == 1
    assert trades[0].id == "close:entry-123"
    assert trades[0].side == "buy"
    assert trades[0].realized_pnl == pytest.approx(1.101)
