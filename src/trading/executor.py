# src/trading/executor.py
"""
Ejecutor de órdenes para Fenix Trading Bot.

Este módulo se encarga de:
- Ejecutar órdenes de mercado en Binance
- Gestionar SL/TP
- Manejar errores y reintentos
- Circuit breaker para protección
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from src.services.binance_service import BinanceService, get_binance_service

logger = logging.getLogger("FenixOrderExecutor")


@dataclass
class OrderResult:
    """Resultado de una ejecución de orden."""
    success: bool
    status: str
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
            "order_id": self.order_id,
            "entry_price": self.entry_price,
            "executed_qty": self.executed_qty,
            "sl_order_id": str(self.sl_order_id) if self.sl_order_id else None,
            "tp_order_id": str(self.tp_order_id) if self.tp_order_id else None,
            "message": self.message,
            "timestamp": self.timestamp,
        }


class CircuitBreaker:
    """Circuit breaker para proteger contra errores en cascada."""

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
        """Registra un fallo."""
        if not self.enabled:
            return
        self.failures += 1
        self.last_failure_time = datetime.now(timezone.utc)

        if self.failures >= self.failure_threshold:
            self.is_open = True
            logger.warning(
                f"🚫 Circuit breaker OPEN after {self.failures} failures"
            )

    def record_success(self) -> None:
        """Registra éxito y resetea contadores."""
        if not self.enabled:
            return
        self.failures = 0
        self.is_open = False

    def can_execute(self) -> bool:
        """Verifica si se puede ejecutar."""
        if not self.enabled:
            return True
        if not self.is_open:
            return True

        # Verificar si el timeout ha pasado
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
    Ejecutor de órdenes de trading.

    Responsabilidades:
    - Ejecutar órdenes de mercado
    - Colocar SL/TP
    - Manejar reintentos y errores
    - Formatear precios y cantidades
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        price_precision: int = 2,
        qty_precision: int = 3,
        min_notional: float = 5.0,
        testnet: bool = True,
    ):
        self.symbol = symbol.upper()
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
        """Lazy loading del servicio de Binance."""
        if self._service is None:
            self._service = get_binance_service(testnet=self.testnet)
        return self._service

    def format_price(self, price: float) -> str:
        """Formatea precio según precisión del símbolo."""
        return f"{price:.{self.price_precision}f}"

    def format_quantity(self, qty: float) -> str:
        """Formatea cantidad según precisión del símbolo."""
        return f"{qty:.{self.qty_precision}f}"

    async def execute_market_order(
        self,
        side: Literal["BUY", "SELL"],
        quantity: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        reduce_only: bool = False,
    ) -> OrderResult:
        """
        Ejecuta una orden de mercado con SL/TP opcionales.

        Args:
            side: BUY o SELL
            quantity: Cantidad a operar
            stop_loss: Precio de stop loss
            take_profit: Precio de take profit
            reduce_only: Si es orden de reducción de posición

        Returns:
            OrderResult con el resultado de la ejecución
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        # Verificar circuit breaker
        if not self.circuit_breaker.can_execute():
            return OrderResult(
                success=False,
                status="CIRCUIT_BREAKER_OPEN",
                message="Circuit breaker is open due to recent failures",
                timestamp=timestamp,
            )

        try:
            # Formatear cantidad
            formatted_qty = self.format_quantity(quantity)
            if float(formatted_qty) <= 0:
                return OrderResult(
                    success=False,
                    status="INVALID_QUANTITY",
                    message=f"Quantity {quantity} formats to zero",
                    timestamp=timestamp,
                )

            # Ejecutar orden de mercado
            logger.info(f"Executing MARKET {side} {formatted_qty} {self.symbol}")

            response = self.service.place_market_order(
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

            # Obtener status de la orden
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

            logger.info(
                f"✅ MARKET order FILLED: {side} {executed_qty} @ {entry_price}"
            )

            # Colocar SL/TP si se proporcionan
            sl_order_id = None
            tp_order_id = None

            if (stop_loss or take_profit) and not reduce_only:
                sl_order_id, tp_order_id = await self._place_protective_orders(
                    entry_side=side,
                    quantity=executed_qty,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
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

    async def _wait_for_fill(
        self,
        order_id: int,
        max_retries: int = 10,
        delay: float = 0.5,
    ) -> dict[str, Any] | None:
        """Espera a que una orden sea llenada."""
        for i in range(max_retries):
            try:
                order = self.service.get_order(self.symbol, order_id)
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

    async def _place_protective_orders(
        self,
        entry_side: Literal["BUY", "SELL"],
        quantity: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> tuple[int | None, int | None]:
        """Coloca órdenes de SL y TP."""
        # El lado del SL/TP es opuesto al de entrada
        sltp_side = "SELL" if entry_side == "BUY" else "BUY"
        formatted_qty = self.format_quantity(quantity)

        sl_order_id = None
        tp_order_id = None

        try:
            # Stop Loss
            sl_response = self.service.place_stop_loss_market(
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
            tp_response = self.service.place_take_profit_market(
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
        """Cancela una orden."""
        try:
            self.service.cancel_order(self.symbol, order_id)
            logger.info(f"Order {order_id} cancelled")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def cancel_all_orders(self) -> bool:
        """Cancela todas las órdenes del símbolo."""
        try:
            self.service.cancel_all_open_orders(self.symbol)
            logger.info(f"All orders cancelled for {self.symbol}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")
            return False

    def get_position(self) -> dict[str, Any]:
        """Obtiene la posición actual."""
        try:
            return self.service.get_position(self.symbol)
        except Exception as e:
            logger.error(f"Failed to get position: {e}")
            return {}

    def get_balance(self) -> float | None:
        """Obtiene el balance en USDT."""
        try:
            return self.service.get_balance_usdt()
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return None
