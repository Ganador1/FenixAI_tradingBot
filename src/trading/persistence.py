"""Async persistence helpers for live trading state."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc, select

from src.config.database import SessionLocal
from src.models.db_models import Order, Position, Trade


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _coerce_datetime(value: Any, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return fallback or _utcnow_naive()
    else:
        return fallback or _utcnow_naive()

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _position_unrealized_pnl(
    *,
    side: str,
    quantity: float,
    entry_price: float,
    current_price: float,
) -> float:
    if side == "SHORT":
        return (entry_price - current_price) * quantity
    return (current_price - entry_price) * quantity


async def persist_open_position(
    *,
    symbol: str,
    side: str,
    quantity: float,
    entry_price: float,
    current_price: float | None = None,
    position_id: str | None = None,
    opened_at: datetime | str | None = None,
    session_factory: Any = SessionLocal,
) -> None:
    """Upsert the current open position for one symbol."""
    normalized_symbol = str(symbol).upper()
    normalized_side = str(side).upper()
    qty = abs(float(quantity))
    entry = float(entry_price)
    current = float(current_price if current_price is not None else entry)
    now = _utcnow_naive()
    opened = _coerce_datetime(opened_at, fallback=now)
    row_id = position_id or f"position:{normalized_symbol}:{uuid.uuid4()}"

    async with session_factory() as session:
        chosen = await session.get(Position, row_id)
        result = await session.execute(
            select(Position)
            .where(Position.symbol == normalized_symbol, Position.is_open.is_(True))
            .order_by(desc(Position.opened_at))
        )
        open_rows = list(result.scalars().all())
        if chosen is None:
            chosen = open_rows[0] if open_rows else None

        for row in open_rows:
            if row.id != getattr(chosen, "id", None):
                row.is_open = False
                row.closed_at = now
                row.current_price = current
                row.unrealized_pnl = 0.0

        if chosen is None:
            chosen = Position(id=row_id, symbol=normalized_symbol, opened_at=opened)
            session.add(chosen)

        chosen.symbol = normalized_symbol
        chosen.side = normalized_side
        chosen.quantity = qty
        chosen.entry_price = entry
        chosen.current_price = current
        chosen.unrealized_pnl = _position_unrealized_pnl(
            side=normalized_side,
            quantity=qty,
            entry_price=entry,
            current_price=current,
        )
        chosen.realized_pnl = float(chosen.realized_pnl or 0.0)
        chosen.opened_at = chosen.opened_at or opened
        chosen.closed_at = None
        chosen.is_open = True
        await session.commit()


async def persist_order_fill(
    *,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    order_id: str | int | None,
    realized_pnl: float = 0.0,
    order_type: str = "market",
    status: str = "filled",
    trade_id: str | None = None,
    executed_at: datetime | str | None = None,
    session_factory: Any = SessionLocal,
) -> None:
    """Persist one filled order and its trade row idempotently."""
    normalized_symbol = str(symbol).upper()
    normalized_side = str(side).lower()
    oid = str(order_id) if order_id is not None else f"generated:{uuid.uuid4()}"
    tid = trade_id or f"fill:{oid}"
    qty = abs(float(quantity))
    fill_price = float(price)
    ts = _coerce_datetime(executed_at)

    async with session_factory() as session:
        order = await session.get(Order, oid)
        if order is None:
            order = Order(id=oid, created_at=ts)
            session.add(order)
        order.symbol = normalized_symbol
        order.type = order_type
        order.side = normalized_side
        order.quantity = qty
        order.price = fill_price
        order.status = status
        order.filled_quantity = qty
        order.updated_at = ts

        trade = await session.get(Trade, tid)
        if trade is None:
            trade = Trade(id=tid, executed_at=ts)
            session.add(trade)
        trade.order_id = oid
        trade.symbol = normalized_symbol
        trade.side = normalized_side
        trade.quantity = qty
        trade.price = fill_price
        trade.realized_pnl = float(realized_pnl)
        trade.executed_at = ts
        await session.commit()


async def expire_stale_pending_orders(
    *,
    max_age_hours: float = 24.0,
    now: datetime | str | None = None,
    session_factory: Any = SessionLocal,
) -> int:
    """Mark old pending DB orders as expired so stale UI rows do not stay actionable."""
    age_hours = max(0.0, float(max_age_hours))
    reference_time = _coerce_datetime(now)
    cutoff = reference_time - timedelta(hours=age_hours)

    async with session_factory() as session:
        result = await session.execute(
            select(Order).where(Order.status == "pending", Order.created_at < cutoff)
        )
        stale_orders = list(result.scalars().all())
        for order in stale_orders:
            order.status = "expired"
            order.updated_at = reference_time

        if stale_orders:
            await session.commit()
        return len(stale_orders)


async def persist_position_close(
    *,
    symbol: str,
    close_result: dict[str, Any],
    tracked_position: Any | None = None,
    session_factory: Any = SessionLocal,
) -> None:
    """Mark open DB positions closed and persist a close trade row."""
    normalized_symbol = str(symbol).upper()
    closed_at = _coerce_datetime(close_result.get("exit_time"))
    exit_price = _coerce_float(close_result.get("exit_price"))
    realized_pnl = float(close_result.get("pnl", 0.0) or 0.0)
    position_side = str(
        close_result.get("side") or getattr(tracked_position, "side", "") or ""
    ).upper()
    close_side = "buy" if position_side == "SHORT" else "sell"
    quantity = (
        _coerce_float(close_result.get("quantity"))
        or _coerce_float(getattr(tracked_position, "quantity", None))
        or 0.0
    )

    async with session_factory() as session:
        result = await session.execute(
            select(Position).where(Position.symbol == normalized_symbol, Position.is_open.is_(True))
        )
        for row in result.scalars().all():
            row.is_open = False
            row.closed_at = closed_at
            if exit_price is not None:
                row.current_price = exit_price
            row.unrealized_pnl = 0.0
            row.realized_pnl = realized_pnl

        if quantity > 0 and exit_price is not None and exit_price > 0:
            source_trade_id = close_result.get("trade_id") or f"{normalized_symbol}:{closed_at.isoformat()}"
            trade_id = f"close:{source_trade_id}"
            trade = await session.get(Trade, trade_id)
            if trade is None:
                trade = Trade(id=trade_id, executed_at=closed_at)
                session.add(trade)
            trade.order_id = str(close_result.get("close_order_id") or close_result.get("order_id") or "")
            trade.symbol = normalized_symbol
            trade.side = close_side
            trade.quantity = abs(float(quantity))
            trade.price = float(exit_price)
            trade.realized_pnl = realized_pnl
            trade.executed_at = closed_at

        await session.commit()
