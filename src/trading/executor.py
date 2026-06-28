# src/trading/executor.py
"""
Order executor for the Fenix trading bot.

This module handles:
- Binance market-order execution
- SL/TP placement
- Error handling and retries
- Circuit-breaker protection
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from src.services.binance_service import BinanceService, get_binance_service
from src.trading.order_monitor import get_order_monitor

logger = logging.getLogger("FenixOrderExecutor")


@dataclass
class OrderResult:
    """Result of an order execution."""

    success: bool
    status: str
    position_id: str | None = None
    order_id: int | None = None
    entry_price: float = 0.0
    executed_qty: float = 0.0
    sl_order_id: int | str | None = None
    tp_order_id: int | str | None = None
    message: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status,
            "position_id": self.position_id,
            "order_id": self.order_id,
            "entry_price": self.entry_price,
            "executed_qty": self.executed_qty,
            "sl_order_id": str(self.sl_order_id) if self.sl_order_id else None,
            "tp_order_id": str(self.tp_order_id) if self.tp_order_id else None,
            "message": self.message,
            "timestamp": self.timestamp,
        }


class CircuitBreaker:
    """Circuit breaker that protects against cascading execution failures."""

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout_seconds: int = 60,
        enabled: bool = True,
    ):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout_seconds
        self.failures = 0
        self.last_failure_time: datetime | None = None
        self.is_open = False
        self.enabled = enabled

    def record_failure(self) -> None:
        """Record one failure."""
        if not self.enabled:
            return
        self.failures += 1
        self.last_failure_time = datetime.now(timezone.utc)

        if self.failures >= self.failure_threshold:
            self.is_open = True
            logger.warning(f"🚫 Circuit breaker OPEN after {self.failures} failures")

    def record_success(self) -> None:
        """Record success and reset counters."""
        if not self.enabled:
            return
        self.failures = 0
        self.is_open = False

    def can_execute(self) -> bool:
        """Return whether execution is currently allowed."""
        if not self.enabled:
            return True
        if not self.is_open:
            return True

        # Check whether the reset timeout has elapsed.
        if self.last_failure_time:
            elapsed = (datetime.now(timezone.utc) - self.last_failure_time).total_seconds()
            if elapsed >= self.reset_timeout:
                logger.info("Circuit breaker reset after timeout")
                self.is_open = False
                self.failures = 0
                return True

        return False


class OrderExecutor:
    """
    Trading order executor.

    Responsibilities:
    - Execute market orders
    - Place SL/TP orders
    - Handle retries and errors
    - Format prices and quantities
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        price_precision: int = 2,
        qty_precision: int = 3,
        min_notional: float = 5.0,
        timeframe: str | None = None,
        testnet: bool = True,
    ):
        self.symbol = symbol.upper()
        self.timeframe = timeframe or "15m"
        self.price_precision = price_precision
        self.qty_precision = qty_precision
        self.min_notional = min_notional
        self.testnet = testnet

        self._service: BinanceService | None = None
        disable_cb_env = os.getenv("DISABLE_CIRCUIT_BREAKER", "").lower() in ("1", "true", "yes")
        cb_enabled = not (self.testnet or disable_cb_env)
        self.circuit_breaker = CircuitBreaker(enabled=cb_enabled)

        logger.info(f"OrderExecutor initialized for {symbol} (testnet={testnet})")

    @property
    def service(self) -> BinanceService:
        """Lazily load the Binance service."""
        if self._service is None:
            self._service = get_binance_service(testnet=self.testnet)
        return self._service

    def format_price(self, price: float) -> str:
        """Format a price using the symbol precision."""
        return f"{price:.{self.price_precision}f}"

    def format_quantity(self, qty: float) -> str:
        """Format quantity using the symbol precision (truncated, never rounded up)."""
        factor = 10**self.qty_precision
        truncated = math.floor(qty * factor + 1e-12) / factor
        return f"{truncated:.{self.qty_precision}f}"

    def _resolve_min_protective_pct(self, kind: Literal["sl", "tp"]) -> float:
        """Resolve minimum protective distance as a fraction of entry price."""
        short_tf = self.timeframe in {"1m", "3m", "5m"}
        defaults = {
            "sl": 0.0030 if short_tf else 0.0015,
            "tp": 0.0050 if short_tf else 0.0030,
        }
        env_key = f"FENIX_MIN_{kind.upper()}_PCT"
        raw = os.getenv(env_key)
        if raw is not None:
            try:
                return max(0.0, float(raw))
            except Exception:
                pass
        return defaults[kind]

    def _quantize_protective_price(
        self,
        price: float,
        *,
        tick_size: float | None,
        price_precision: int,
        direction: Literal["up", "down"],
    ) -> float:
        if price <= 0:
            return 0.0

        if tick_size and tick_size > 0:
            steps = price / tick_size
            adjusted = (
                math.ceil(steps) * tick_size if direction == "up" else math.floor(steps) * tick_size
            )
            return round(adjusted, price_precision)

        return round(price, price_precision)

    def _normalize_protective_price(
        self,
        *,
        entry_side: Literal["BUY", "SELL"],
        kind: Literal["sl", "tp"],
        proposed_price: float | None,
        entry_price: float | None,
        tick_size: float | None,
        price_precision: int,
    ) -> float | None:
        if proposed_price is None or entry_price is None or entry_price <= 0:
            return proposed_price

        try:
            fee_pct = float(os.getenv("FENIX_ESTIMATED_FEE_PCT", "0.0004") or 0.0)
        except Exception:
            fee_pct = 0.0004
        min_pct = max(self._resolve_min_protective_pct(kind), max(0.0, fee_pct) * 2.0)

        if entry_side == "BUY":
            target = (
                entry_price * (1.0 - min_pct) if kind == "sl" else entry_price * (1.0 + min_pct)
            )
            clamped = min(proposed_price, target) if kind == "sl" else max(proposed_price, target)
            direction = "down" if kind == "sl" else "up"
        else:
            target = (
                entry_price * (1.0 + min_pct) if kind == "sl" else entry_price * (1.0 - min_pct)
            )
            clamped = max(proposed_price, target) if kind == "sl" else min(proposed_price, target)
            direction = "up" if kind == "sl" else "down"

        return self._quantize_protective_price(
            clamped,
            tick_size=tick_size,
            price_precision=price_precision,
            direction=direction,
        )

    async def execute_market_order(
        self,
        side: Literal["BUY", "SELL"],
        quantity: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        reduce_only: bool = False,
    ) -> OrderResult:
        """
        Execute a market order with optional SL/TP protection.

        Args:
            side: BUY or SELL.
            quantity: Quantity to trade.
            stop_loss: Stop-loss price.
            take_profit: Take-profit price.
            reduce_only: Whether this order only reduces an existing position.

        Returns:
            OrderResult with execution details.
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        # Check circuit breaker. Always allow reduce-only (close) orders so an
        # open circuit breaker never traps an exposed position.
        if not self.circuit_breaker.can_execute() and not reduce_only:
            return OrderResult(
                success=False,
                status="CIRCUIT_BREAKER_OPEN",
                message="Circuit breaker is open due to recent failures",
                timestamp=timestamp,
            )

        try:
            # Format quantity.
            formatted_qty = self.format_quantity(quantity)
            if float(formatted_qty) <= 0:
                return OrderResult(
                    success=False,
                    status="INVALID_QUANTITY",
                    message=f"Quantity {quantity} formats to zero",
                    timestamp=timestamp,
                )

            # Execute order: prefer limit (maker fee 0%) with fallback to market.
            use_limit = (
                not reduce_only
                and os.getenv("FENIX_USE_LIMIT_ENTRY", "0") == "1"
            )
            limit_price: float | None = None

            if use_limit:
                try:
                    # Place a post-only limit at the current best price on our
                    # side of the book — if the book moves before we post, GTX
                    # rejects and we fall back to market.
                    ticker = await asyncio.to_thread(
                        self.service.get_ticker_price, self.symbol
                    )
                    if ticker and ticker > 0:
                        # BUY: bid slightly below mid; SELL: ask slightly above.
                        offset = float(os.getenv("FENIX_LIMIT_OFFSET_TICKS", "1"))
                        tick = getattr(self, "_tick_size", 0.01) or 0.01
                        if side == "BUY":
                            limit_price = ticker - (offset * tick)
                        else:
                            limit_price = ticker + (offset * tick)
                        limit_price = round(limit_price, self.price_precision)
                except Exception as e:
                    logger.debug("Limit price calc failed, using market: %s", e)
                    use_limit = False

            if use_limit and limit_price:
                logger.info(
                    f"Executing LIMIT {side} {formatted_qty} {self.symbol} @ {limit_price}"
                )
                try:
                    response = await asyncio.to_thread(
                        self.service.place_limit_order,
                        symbol=self.symbol,
                        side=side,
                        quantity=float(formatted_qty),
                        price=limit_price,
                        reduce_only=reduce_only,
                    )
                    # GTX limit: if not filled immediately, cancel and use market.
                    limit_status = (response or {}).get("status", "")
                    if limit_status != "FILLED":
                        limit_oid = (response or {}).get("orderId")
                        if limit_oid:
                            try:
                                await asyncio.to_thread(
                                    self.service.cancel_order, self.symbol, limit_oid
                                )
                            except Exception:
                                pass
                        logger.info(
                            "Limit order status=%s (not filled), falling back to market",
                            limit_status,
                        )
                        use_limit = False
                        response = None
                except Exception as e:
                    # GTX rejection or other limit failure -> fall back to market
                    logger.info("Limit order rejected (%s), falling back to market", e)
                    use_limit = False

            if not use_limit or not limit_price:
                logger.info(f"Executing MARKET {side} {formatted_qty} {self.symbol}")
                response = await asyncio.to_thread(
                    self.service.place_market_order,
                    symbol=self.symbol,
                    side=side,
                    quantity=float(formatted_qty),
                    reduce_only=reduce_only,
                )

            order_id = response.get("orderId")
            if not order_id:
                self.circuit_breaker.record_failure()
                return OrderResult(
                    success=False,
                    status="NO_ORDER_ID",
                    message="Market order failed to return order ID",
                    timestamp=timestamp,
                )

            # Fetch order status.
            filled_order = await self._wait_for_fill(order_id)

            if not filled_order or filled_order.get("status") != "FILLED":
                self.circuit_breaker.record_failure()
                return OrderResult(
                    success=False,
                    status="NOT_FILLED",
                    order_id=order_id,
                    message=f"Order status: {filled_order.get('status') if filled_order else 'Unknown'}",
                    timestamp=timestamp,
                )

            entry_price = float(filled_order.get("avgPrice", 0))
            executed_qty = float(filled_order.get("executedQty", 0))

            logger.info(f"✅ MARKET order FILLED: {side} {executed_qty} @ {entry_price}")

            # Place SL/TP when provided.
            sl_order_id = None
            tp_order_id = None
            protection_requested = (
                stop_loss is not None or take_profit is not None
            ) and not reduce_only

            if protection_requested:
                sl_order_id, tp_order_id = await self._place_protective_orders(
                    entry_side=side,
                    quantity=executed_qty,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    entry_price=entry_price,
                )
                protection_verified = await self._verify_protective_orders(
                    sl_order_id=sl_order_id,
                    tp_order_id=tp_order_id,
                    require_sl=stop_loss is not None,
                    require_tp=take_profit is not None,
                )
                if not protection_verified:
                    close_status = await self._close_unprotected_position(
                        entry_side=side,
                        quantity=executed_qty,
                    )
                    self.circuit_breaker.record_failure()
                    logger.critical(
                        "Protective orders were not verified for %s order %s; fail-safe close status=%s",
                        self.symbol,
                        order_id,
                        close_status,
                    )
                    return OrderResult(
                        success=False,
                        status="PROTECTION_NOT_VERIFIED",
                        order_id=order_id,
                        entry_price=entry_price,
                        executed_qty=executed_qty,
                        sl_order_id=sl_order_id,
                        tp_order_id=tp_order_id,
                        message=(
                            "Protective orders were not visible after placement; "
                            f"fail-safe close status: {close_status}"
                        ),
                        timestamp=timestamp,
                    )

            self.circuit_breaker.record_success()

            return OrderResult(
                success=True,
                status="FILLED_WITH_PROTECTION" if sl_order_id else "FILLED",
                order_id=order_id,
                entry_price=entry_price,
                executed_qty=executed_qty,
                sl_order_id=sl_order_id,
                tp_order_id=tp_order_id,
                message="Order executed successfully",
                timestamp=timestamp,
            )

        except Exception as e:
            self.circuit_breaker.record_failure()
            logger.error(f"Order execution failed: {e}", exc_info=True)
            return OrderResult(
                success=False,
                status="ERROR",
                message=str(e),
                timestamp=timestamp,
            )

    async def _wait_for_position_confirmation(
        self,
        *,
        min_abs_position_amt: float = 1e-12,
        retries: int = 6,
        delay: float = 0.35,
    ) -> dict[str, Any] | None:
        """Wait briefly until Binance exposes the filled position for closePosition orders."""
        for _ in range(max(1, retries)):
            try:
                position = (await asyncio.to_thread(self.get_position)) or {}
                amt = float(position.get("positionAmt", 0) or 0)
                if abs(amt) > min_abs_position_amt:
                    return position
            except Exception as e:
                logger.debug("Position confirmation retry failed: %s", e)
            await asyncio.sleep(delay)
        return None

    async def _wait_for_fill(
        self,
        order_id: int,
        max_retries: int = 10,
        delay: float = 0.5,
    ) -> dict[str, Any] | None:
        """Wait for an order to fill."""
        for i in range(max_retries):
            try:
                order = await asyncio.to_thread(self.service.get_order, self.symbol, order_id)
                status = order.get("status")

                if status == "FILLED":
                    return order
                elif status in ["CANCELED", "EXPIRED", "REJECTED"]:
                    logger.warning(f"Order {order_id} terminated with status: {status}")
                    return order

                await asyncio.sleep(delay)

            except Exception as e:
                logger.error(f"Error checking order {order_id}: {e}")
                await asyncio.sleep(delay)

        return None

    def _extract_order_identity_values(self, order: dict[str, Any]) -> set[str]:
        values: set[str] = set()
        for key in ("orderId", "algoId", "clientOrderId", "origClientOrderId"):
            value = order.get(key)
            if value is not None:
                values.add(str(value))
        return values

    async def _verify_protective_orders(
        self,
        *,
        sl_order_id: int | str | None,
        tp_order_id: int | str | None,
        require_sl: bool,
        require_tp: bool,
        retries: int = 4,
        delay: float = 0.35,
    ) -> bool:
        """Confirm that requested protective orders are visible on Binance."""
        required_ids: set[str] = set()
        if require_sl:
            if sl_order_id is None:
                return False
            required_ids.add(str(sl_order_id))
        if require_tp:
            if tp_order_id is None:
                return False
            required_ids.add(str(tp_order_id))

        if not required_ids:
            return True

        for _ in range(max(1, retries)):
            try:
                open_orders = await asyncio.to_thread(self.service.get_open_orders, self.symbol)
                if hasattr(self.service, "get_open_algo_orders"):
                    open_orders = list(open_orders or []) + list(
                        (await asyncio.to_thread(self.service.get_open_algo_orders, self.symbol))
                        or []
                    )
                visible_ids: set[str] = set()
                for order in open_orders or []:
                    visible_ids.update(self._extract_order_identity_values(order))
                if required_ids.issubset(visible_ids):
                    return True
                logger.warning(
                    "Protective order verification pending for %s: required=%s visible=%s",
                    self.symbol,
                    sorted(required_ids),
                    sorted(visible_ids),
                )
            except Exception as e:
                logger.warning("Protective order verification failed for %s: %s", self.symbol, e)
            await asyncio.sleep(delay)

        return False

    async def _close_unprotected_position(
        self,
        *,
        entry_side: Literal["BUY", "SELL"],
        quantity: float,
    ) -> str:
        """Fail-safe close for a filled entry whose SL/TP could not be verified."""
        close_side = "SELL" if entry_side == "BUY" else "BUY"
        formatted_qty = self.format_quantity(quantity)
        if float(formatted_qty) <= 0:
            return "INVALID_CLOSE_QUANTITY"

        try:
            response = await asyncio.to_thread(
                self.service.place_market_order,
                symbol=self.symbol,
                side=close_side,
                quantity=float(formatted_qty),
                reduce_only=True,
            )
            close_order_id = response.get("orderId")
            if not close_order_id:
                return "CLOSE_NO_ORDER_ID"

            filled_order = await self._wait_for_fill(close_order_id)
            if filled_order and filled_order.get("status") == "FILLED":
                return "CLOSED"
            return f"CLOSE_NOT_FILLED:{filled_order.get('status') if filled_order else 'UNKNOWN'}"
        except Exception as e:
            logger.error("Fail-safe close failed for %s: %s", self.symbol, e, exc_info=True)
            return f"CLOSE_ERROR:{e}"

    async def _place_protective_orders(
        self,
        entry_side: Literal["BUY", "SELL"],
        quantity: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        entry_price: float | None = None,
    ) -> tuple[int | None, int | None]:
        """Place SL and TP orders."""
        # SL/TP side is opposite to the entry side.
        sltp_side = "SELL" if entry_side == "BUY" else "BUY"
        formatted_qty = self.format_quantity(quantity)
        symbol_config = None
        try:
            symbol_config = await asyncio.to_thread(self.service.get_symbol_config, self.symbol)
        except Exception:
            symbol_config = None
        price_precision = int(
            getattr(symbol_config, "price_precision", self.price_precision) or self.price_precision
        )
        tick_size = getattr(symbol_config, "tick_size", None)

        if entry_price is not None:
            normalized_sl = self._normalize_protective_price(
                entry_side=entry_side,
                kind="sl",
                proposed_price=stop_loss,
                entry_price=entry_price,
                tick_size=tick_size,
                price_precision=price_precision,
            )
            normalized_tp = self._normalize_protective_price(
                entry_side=entry_side,
                kind="tp",
                proposed_price=take_profit,
                entry_price=entry_price,
                tick_size=tick_size,
                price_precision=price_precision,
            )
            if normalized_sl is not None and stop_loss is not None and normalized_sl != stop_loss:
                logger.info(
                    "Protective SL clamped for %s: %.8f -> %.8f (entry=%.8f, tf=%s)",
                    self.symbol,
                    stop_loss,
                    normalized_sl,
                    entry_price,
                    self.timeframe,
                )
            if (
                normalized_tp is not None
                and take_profit is not None
                and normalized_tp != take_profit
            ):
                logger.info(
                    "Protective TP clamped for %s: %.8f -> %.8f (entry=%.8f, tf=%s)",
                    self.symbol,
                    take_profit,
                    normalized_tp,
                    entry_price,
                    self.timeframe,
                )
            stop_loss = normalized_sl
            take_profit = normalized_tp

        sl_order_id = None
        tp_order_id = None

        if stop_loss is None and take_profit is None:
            return sl_order_id, tp_order_id

        confirmed_position = await self._wait_for_position_confirmation()
        if confirmed_position is None:
            logger.warning(
                "Position not yet visible after fill; protective closePosition orders may fail for %s",
                self.symbol,
            )

        try:
            await asyncio.to_thread(self.service.cancel_all_open_orders, self.symbol)
        except Exception as e:
            logger.debug("Could not clear stale open orders before SL/TP placement: %s", e)

        try:
            # Stop Loss
            if stop_loss is not None:
                sl_response = await asyncio.to_thread(
                    self.service.place_stop_loss_market,
                    symbol=self.symbol,
                    side=sltp_side,
                    quantity=float(formatted_qty),
                    stop_price=stop_loss,
                )
                sl_order_id = sl_response.get("algoId") or sl_response.get("orderId")
                logger.info(f"SL placed: {sl_order_id} @ {stop_loss}")

        except Exception as e:
            logger.error(f"Failed to place SL: {e}")

        try:
            # Take Profit
            if take_profit is not None:
                tp_response = await asyncio.to_thread(
                    self.service.place_take_profit_market,
                    symbol=self.symbol,
                    side=sltp_side,
                    quantity=float(formatted_qty),
                    stop_price=take_profit,
                )
                tp_order_id = tp_response.get("algoId") or tp_response.get("orderId")
                logger.info(f"TP placed: {tp_order_id} @ {take_profit}")

        except Exception as e:
            logger.error(f"Failed to place TP: {e}")

        return sl_order_id, tp_order_id

    async def cancel_order(self, order_id: int) -> bool:
        """Cancel one order."""
        try:
            await asyncio.to_thread(self.service.cancel_order, self.symbol, order_id)
            logger.info(f"Order {order_id} cancelled")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def cancel_all_orders(self) -> bool:
        """Cancel all open and monitored orders for the current symbol."""
        success = True

        try:
            monitor = get_order_monitor()
            if monitor is not None and hasattr(monitor, "cancel_all_for_symbol"):
                monitor_success = await monitor.cancel_all_for_symbol(self.symbol)
                success = success and bool(monitor_success)
        except Exception as e:
            success = False
            logger.error("Failed to cancel monitored orders for %s: %s", self.symbol, e)

        try:
            await asyncio.to_thread(self.service.cancel_all_open_orders, self.symbol)
            logger.info(f"All orders cancelled for {self.symbol}")
        except Exception as e:
            success = False
            logger.error(f"Failed to cancel all orders: {e}")

        return success

    def list_monitored_positions(self) -> list[dict[str, Any]]:
        """Return active positions tracked by the order monitor."""
        try:
            monitor = get_order_monitor()
            if monitor is None or not hasattr(monitor, "list_active_positions"):
                return []
            positions = monitor.list_active_positions()
            return [pos.to_dict() if hasattr(pos, "to_dict") else dict(pos) for pos in positions]
        except Exception as e:
            logger.error("Failed to list monitored positions: %s", e)
            return []

    def get_monitor_stats(self) -> dict[str, Any]:
        """Return order-monitor stats when the monitor is active."""
        try:
            monitor = get_order_monitor()
            if monitor is None or not hasattr(monitor, "get_stats"):
                return {"monitoring_active": False}
            return dict(monitor.get_stats() or {})
        except Exception as e:
            logger.error("Failed to get order monitor stats: %s", e)
            return {"monitoring_active": False, "error": str(e)}

    def get_protection_status(self) -> dict[str, Any]:
        """Return protection-monitor status for this executor symbol."""
        positions = [
            pos
            for pos in self.list_monitored_positions()
            if str(pos.get("symbol", "")).upper() == self.symbol
        ]
        stats = self.get_monitor_stats()
        return {
            "symbol": self.symbol,
            "active_protections": len(positions),
            "monitoring_active": bool(stats.get("monitoring_active", False)),
            "positions": positions,
            "monitor_stats": stats,
        }

    async def refresh_position_protection(
        self,
        *,
        position_id: str,
        entry_order_id: int | None = None,
        entry_side: Literal["BUY", "SELL"] | None = None,
        quantity: float | None = None,
        entry_price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> OrderResult:
        """Replace monitored SL/TP orders for an existing protected position."""
        timestamp = datetime.now(timezone.utc).isoformat()

        try:
            monitor = get_order_monitor()
            if monitor is None:
                return OrderResult(
                    success=False,
                    status="MONITOR_NOT_AVAILABLE",
                    message="Order monitor is not available",
                    timestamp=timestamp,
                )

            existing_position = (
                monitor.get_position(position_id) if hasattr(monitor, "get_position") else None
            )
            if existing_position is None:
                return OrderResult(
                    success=False,
                    status="POSITION_NOT_MONITORED",
                    message=f"Position {position_id} is not monitored",
                    timestamp=timestamp,
                )

            resolved_entry_order_id = entry_order_id
            if resolved_entry_order_id is None:
                resolved_entry_order_id = int(getattr(existing_position, "entry_order_id", 0) or 0)

            resolved_entry_side = str(
                entry_side or getattr(existing_position, "entry_side", "")
            ).upper()
            if resolved_entry_side not in {"BUY", "SELL"}:
                return OrderResult(
                    success=False,
                    status="INVALID_ENTRY_SIDE",
                    message=f"invalid entry_side for {position_id}: {resolved_entry_side}",
                    timestamp=timestamp,
                )

            resolved_quantity = float(
                quantity if quantity is not None else getattr(existing_position, "quantity", 0.0)
            )
            if resolved_quantity <= 0:
                return OrderResult(
                    success=False,
                    status="INVALID_QUANTITY",
                    message=f"invalid quantity for {position_id}: {resolved_quantity}",
                    timestamp=timestamp,
                )

            resolved_entry_price = float(
                entry_price
                if entry_price is not None and float(entry_price) > 0
                else getattr(existing_position, "entry_price", 0.0)
            )
            if resolved_entry_price <= 0:
                return OrderResult(
                    success=False,
                    status="INVALID_ENTRY_PRICE",
                    message=f"invalid entry_price for {position_id}: {resolved_entry_price}",
                    timestamp=timestamp,
                )

            cancelled = await monitor.cancel_all_for_position(position_id)
            if not cancelled:
                return OrderResult(
                    success=False,
                    status="CANCEL_PROTECTION_FAILED",
                    message=f"failed to cancel existing protective orders for {position_id}",
                    timestamp=timestamp,
                )

            if hasattr(monitor, "unregister_position"):
                monitor.unregister_position(position_id)

            sl_order_id, tp_order_id = await self._place_protective_orders(
                entry_side=resolved_entry_side,
                quantity=resolved_quantity,
                stop_loss=stop_loss,
                take_profit=take_profit,
                entry_price=resolved_entry_price,
            )

            if hasattr(monitor, "register_position"):
                monitor.register_position(
                    position_id=position_id,
                    symbol=self.symbol,
                    entry_order_id=resolved_entry_order_id,
                    entry_side=resolved_entry_side,
                    quantity=resolved_quantity,
                    entry_price=resolved_entry_price,
                    sl_order_id=sl_order_id,
                    tp_order_id=tp_order_id,
                    sl_price=stop_loss,
                    tp_price=take_profit,
                )

            return OrderResult(
                success=True,
                status="PROTECTION_REFRESHED",
                position_id=position_id,
                order_id=resolved_entry_order_id,
                entry_price=resolved_entry_price,
                executed_qty=resolved_quantity,
                sl_order_id=sl_order_id,
                tp_order_id=tp_order_id,
                message=f"Protection refreshed for {position_id}",
                timestamp=timestamp,
            )

        except Exception as e:
            logger.error("Failed to refresh protection for %s: %s", position_id, e, exc_info=True)
            return OrderResult(
                success=False,
                status="PROTECTION_REFRESH_ERROR",
                message=str(e),
                timestamp=timestamp,
            )

    def get_position(self) -> dict[str, Any]:
        """Return the current position."""
        try:
            return self.service.get_position(self.symbol)
        except Exception as e:
            logger.error(f"Failed to get position: {e}")
            return {}

    def get_recent_trades(
        self,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent account trades for the current symbol."""
        try:
            return self.service.get_account_trades(
                self.symbol,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )
        except Exception as e:
            logger.error(f"Failed to get recent trades for {self.symbol}: {e}")
            return []

    def get_recent_income(
        self,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent income history for the current symbol."""
        try:
            return self.service.get_income_history(
                self.symbol,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )
        except Exception as e:
            logger.error(f"Failed to get recent income for {self.symbol}: {e}")
            return []

    def get_balance(self) -> float | None:
        """Return the USDT balance."""
        try:
            return self.service.get_balance_usdt()
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return None
