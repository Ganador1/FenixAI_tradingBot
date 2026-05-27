"""
Sistema de monitoreo de órdenes SL/TP con lógica OCO (One-Cancels-Other).

Cuando se ejecuta el TP o SL, automáticamente cancela la orden contraria
para evitar que quede huérfana.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger("FenixOrderMonitor")


class OrderStatus(Enum):
    """Estados de una orden protegida."""

    PENDING = "pending"  # Órdenes colocadas, esperando ejecución
    TP_FILLED = "tp_filled"  # TP ejecutado, SL debe cancelarse
    SL_FILLED = "sl_filled"  # SL ejecutado, TP debe cancelarse
    BOTH_FILLED = "both_filled"  # Ambos ejecutados (raro, pero posible)
    CANCELLED = "cancelled"  # Orden contraria cancelada
    EXPIRED = "expired"  # Tiempo máximo alcanzado


@dataclass
class ProtectedPosition:
    """Representa una posición con SL/TP protegidos."""

    position_id: str  # ID único de la posición
    symbol: str
    entry_order_id: int  # Orden de entrada
    entry_side: str  # BUY o SELL
    quantity: float
    entry_price: float

    # Órdenes de protección
    sl_order_id: int | str | None = None
    tp_order_id: int | str | None = None
    sl_price: float | None = None
    tp_price: float | None = None

    # Estado
    status: OrderStatus = field(default=OrderStatus.PENDING)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Callbacks
    on_tp_filled: Callable | None = None
    on_sl_filled: Callable | None = None
    on_cancelled: Callable | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "entry_order_id": self.entry_order_id,
            "entry_side": self.entry_side,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "sl_order_id": self.sl_order_id,
            "tp_order_id": self.tp_order_id,
            "sl_price": self.sl_price,
            "tp_price": self.tp_price,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class OrderMonitor:
    """
    Monitorea órdenes SL/TP y ejecuta lógica OCO.

    Cuando una orden (TP o SL) se ejecuta, automáticamente cancela
    la contraparte para evitar órdenes huérfanas.
    """

    def __init__(self, binance_service, check_interval: float = 2.0):
        """
        Args:
            binance_service: Instancia del servicio de Binance
            check_interval: Segundos entre chequeos de estado
        """
        self.service = binance_service
        self.check_interval = check_interval

        # Posiciones protegidas: {position_id: ProtectedPosition}
        self._positions: dict[str, ProtectedPosition] = {}
        self._positions_lock = threading.RLock()

        # IDs de órdenes a posiciones: {order_id: position_id}
        self._order_to_position: dict[str, str] = {}

        # Control del loop de monitoreo
        self._monitoring = False
        self._monitor_task: asyncio.Task | None = None

        logger.info(f"OrderMonitor initialized (check_interval={check_interval}s)")

    def register_position(
        self,
        position_id: str,
        symbol: str,
        entry_order_id: int,
        entry_side: str,
        quantity: float,
        entry_price: float,
        sl_order_id: int | str | None = None,
        tp_order_id: int | str | None = None,
        sl_price: float | None = None,
        tp_price: float | None = None,
        on_tp_filled: Callable | None = None,
        on_sl_filled: Callable | None = None,
    ) -> ProtectedPosition:
        """
        Registra una nueva posición con órdenes de protección.

        Args:
            position_id: ID único (ej: f"{symbol}_{entry_order_id}_{timestamp}")
            symbol: Símbolo del par
            entry_order_id: ID de la orden de entrada
            entry_side: BUY o SELL
            quantity: Cantidad de la posición
            entry_price: Precio de entrada
            sl_order_id: ID de la orden SL (opcional)
            tp_order_id: ID de la orden TP (opcional)
            sl_price: Precio del SL (opcional)
            tp_price: Precio del TP (opcional)
            on_tp_filled: Callback cuando TP se ejecuta
            on_sl_filled: Callback cuando SL se ejecuta

        Returns:
            ProtectedPosition creada
        """
        position = ProtectedPosition(
            position_id=position_id,
            symbol=symbol,
            entry_order_id=entry_order_id,
            entry_side=entry_side,
            quantity=quantity,
            entry_price=entry_price,
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
            sl_price=sl_price,
            tp_price=tp_price,
            on_tp_filled=on_tp_filled,
            on_sl_filled=on_sl_filled,
        )

        with self._positions_lock:
            self._positions[position_id] = position

            # Mapear IDs de órdenes a posición
            if sl_order_id:
                self._order_to_position[str(sl_order_id)] = position_id
            if tp_order_id:
                self._order_to_position[str(tp_order_id)] = position_id

        logger.info(
            f"📋 Registered protected position: {position_id} | "
            f"SL: {sl_order_id} @ {sl_price} | TP: {tp_order_id} @ {tp_price}"
        )

        # Asegurar que el monitoreo esté activo
        self._ensure_monitoring()

        return position

    def unregister_position(self, position_id: str) -> ProtectedPosition | None:
        """Elimina una posición del monitoreo."""
        with self._positions_lock:
            position = self._positions.pop(position_id, None)

            if position:
                # Limpiar mapeo de órdenes
                if position.sl_order_id:
                    self._order_to_position.pop(str(position.sl_order_id), None)
                if position.tp_order_id:
                    self._order_to_position.pop(str(position.tp_order_id), None)

                logger.info(f"🗑️ Unregistered position: {position_id}")

            return position

    def get_position(self, position_id: str) -> ProtectedPosition | None:
        """Obtiene una posición por ID."""
        with self._positions_lock:
            return self._positions.get(position_id)

    def get_position_by_order(self, order_id: int | str) -> ProtectedPosition | None:
        """Obtiene una posición por ID de orden SL/TP."""
        position_id = self._order_to_position.get(str(order_id))
        if position_id:
            return self.get_position(position_id)
        return None

    def list_active_positions(self) -> list[ProtectedPosition]:
        """Lista todas las posiciones activas (PENDING)."""
        with self._positions_lock:
            return [pos for pos in self._positions.values() if pos.status == OrderStatus.PENDING]

    def _ensure_monitoring(self):
        """Asegura que el loop de monitoreo esté corriendo."""
        if not self._monitoring:
            self._monitoring = True
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            logger.info("🔍 Order monitoring started")

    async def _monitor_loop(self):
        """Loop principal de monitoreo."""
        while self._monitoring:
            try:
                await self._check_all_positions()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}", exc_info=True)
                await asyncio.sleep(self.check_interval * 2)  # Backoff

    async def _check_all_positions(self):
        """Chequea el estado de todas las posiciones pendientes."""
        active_positions = self.list_active_positions()

        if not active_positions:
            return

        # Chequear cada posición
        for position in active_positions:
            try:
                await self._check_position_status(position)
            except Exception as e:
                logger.error(f"Error checking position {position.position_id}: {e}")

    async def _check_position_status(self, position: ProtectedPosition):
        """Chequea el estado de una posición específica y ejecuta OCO si es necesario."""

        # Verificar SL
        sl_filled = False
        if position.sl_order_id:
            sl_filled = await self._is_order_filled(position.symbol, position.sl_order_id)

        # Verificar TP
        tp_filled = False
        if position.tp_order_id:
            tp_filled = await self._is_order_filled(position.symbol, position.tp_order_id)

        # Lógica OCO
        if sl_filled and tp_filled:
            # Ambos ejecutados (raro, pero posible en volatilidad extrema)
            position.status = OrderStatus.BOTH_FILLED
            position.updated_at = datetime.now(timezone.utc)
            logger.warning(
                f"⚠️ Both SL and TP filled for {position.position_id} | "
                f"This indicates extreme volatility"
            )

        elif sl_filled and not tp_filled:
            # SL ejecutado → Cancelar TP
            position.status = OrderStatus.SL_FILLED
            position.updated_at = datetime.now(timezone.utc)

            logger.info(f"🛑 SL filled for {position.position_id}, cancelling TP...")

            if position.tp_order_id:
                cancelled = await self._cancel_order(position.symbol, position.tp_order_id)
                if cancelled:
                    position.status = OrderStatus.CANCELLED
                    logger.info(f"✅ TP {position.tp_order_id} cancelled successfully")
                else:
                    logger.warning(f"⚠️ Failed to cancel TP {position.tp_order_id}")

            # Ejecutar callback
            if position.on_sl_filled:
                try:
                    callback_result = position.on_sl_filled(position)
                    if inspect.isawaitable(callback_result):
                        await callback_result
                except Exception as e:
                    logger.error(f"Error in on_sl_filled callback: {e}")

        elif tp_filled and not sl_filled:
            # TP ejecutado → Cancelar SL
            position.status = OrderStatus.TP_FILLED
            position.updated_at = datetime.now(timezone.utc)

            logger.info(f"🎯 TP filled for {position.position_id}, cancelling SL...")

            if position.sl_order_id:
                cancelled = await self._cancel_order(position.symbol, position.sl_order_id)
                if cancelled:
                    position.status = OrderStatus.CANCELLED
                    logger.info(f"✅ SL {position.sl_order_id} cancelled successfully")
                else:
                    logger.warning(f"⚠️ Failed to cancel SL {position.sl_order_id}")

            # Ejecutar callback
            if position.on_tp_filled:
                try:
                    callback_result = position.on_tp_filled(position)
                    if inspect.isawaitable(callback_result):
                        await callback_result
                except Exception as e:
                    logger.error(f"Error in on_tp_filled callback: {e}")

    async def _is_order_filled(self, symbol: str, order_id: int | str) -> bool:
        """Verifica si una orden está llena."""
        try:
            order = self.service.get_order(symbol, order_id)
            status = order.get("status", "")

            # Para órdenes de algoritmo (TP/SL), también verificar algoId
            if status in ["FILLED", "EXECUTED", "TRIGGERED"]:
                return True

            # Para órdenes condicionales, verificar si se ejecutó
            if order.get("algoId") and order.get("executedQty", 0) > 0:
                return True

            return False

        except Exception as e:
            # Si la orden no existe, intentar como ALGO order
            if self._is_unknown_order_error(e):
                try:
                    order = self.service.get_algo_order(symbol, order_id)
                    status = (order.get("algoStatus") or order.get("status") or "").upper()

                    if status in {"FILLED", "EXECUTED", "TRIGGERED", "COMPLETED", "SUCCESS"}:
                        return True

                    executed_qty = order.get("executedQty") or order.get("executedQty", 0)
                    if executed_qty and float(executed_qty) > 0:
                        return True

                    return False
                except Exception as algo_error:
                    logger.debug(f"Error checking algo order {order_id}: {algo_error}")
                    return False

            logger.debug(f"Error checking order {order_id}: {e}")
            return False

    async def _cancel_order(self, symbol: str, order_id: int | str) -> bool:
        """Cancela una orden."""
        try:
            self.service.cancel_order(symbol, order_id)
            return True
        except Exception as e:
            # Si la orden ya no existe o ya se ejecutó, intentar como ALGO
            if self._is_unknown_order_error(e):
                try:
                    self.service.cancel_algo_order(symbol, order_id)
                    return True
                except Exception as algo_error:
                    if self._is_unknown_order_error(algo_error):
                        logger.info(f"Order {order_id} already cancelled or filled")
                        return True
                    logger.error(f"Failed to cancel algo order {order_id}: {algo_error}")
                    return False

            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    @staticmethod
    def _is_unknown_order_error(error: Exception) -> bool:
        msg = str(error)
        return "Unknown order" in msg or "Order does not exist" in msg or "-2013" in msg

    async def cancel_all_for_position(self, position_id: str) -> bool:
        """Cancela todas las órdenes pendientes de una posición."""
        position = self.get_position(position_id)
        if not position:
            return False

        success = True

        with self._positions_lock:
            if position.sl_order_id:
                if not await self._cancel_order(position.symbol, position.sl_order_id):
                    success = False
                self._order_to_position.pop(str(position.sl_order_id), None)

            if position.tp_order_id:
                if not await self._cancel_order(position.symbol, position.tp_order_id):
                    success = False
                self._order_to_position.pop(str(position.tp_order_id), None)

            position.status = OrderStatus.CANCELLED
            position.updated_at = datetime.now(timezone.utc)

        return success

    async def cancel_all_for_symbol(self, symbol: str) -> bool:
        """
        Cancela todas las posiciones y órdenes de un símbolo específico.

        Útil cuando se cancelan todas las órdenes de un símbolo (ej: al cerrar posición opuesta).
        """
        success = True

        with self._positions_lock:
            # Encontrar todas las posiciones del símbolo
            positions_to_cancel = [
                pos_id
                for pos_id, pos in self._positions.items()
                if pos.symbol == symbol and pos.status == OrderStatus.PENDING
            ]

            # Cancelar cada posición
            for pos_id in positions_to_cancel:
                position = self._positions[pos_id]

                # Cancelar órdenes SL/TP
                if position.sl_order_id:
                    if not await self._cancel_order(position.symbol, position.sl_order_id):
                        success = False

                if position.tp_order_id:
                    if not await self._cancel_order(position.symbol, position.tp_order_id):
                        success = False

                # Marcar como cancelada
                position.status = OrderStatus.CANCELLED
                position.updated_at = datetime.now(timezone.utc)

                # Remover del mapeo de órdenes
                if position.sl_order_id:
                    self._order_to_position.pop(str(position.sl_order_id), None)

                if position.tp_order_id:
                    self._order_to_position.pop(str(position.tp_order_id), None)

                logger.info(f"🗑️ Cancelled position {pos_id} for symbol {symbol}")

        return success

    def stop_monitoring(self):
        """Detiene el loop de monitoreo."""
        self._monitoring = False
        if self._monitor_task:
            self._monitor_task.cancel()
            logger.info("🛑 Order monitoring stopped")

    def get_stats(self) -> dict[str, Any]:
        """Obtiene estadísticas del monitoreo."""
        with self._positions_lock:
            all_positions = list(self._positions.values())

            return {
                "total_positions": len(all_positions),
                "active": len([p for p in all_positions if p.status == OrderStatus.PENDING]),
                "tp_filled": len([p for p in all_positions if p.status == OrderStatus.TP_FILLED]),
                "sl_filled": len([p for p in all_positions if p.status == OrderStatus.SL_FILLED]),
                "both_filled": len(
                    [p for p in all_positions if p.status == OrderStatus.BOTH_FILLED]
                ),
                "cancelled": len([p for p in all_positions if p.status == OrderStatus.CANCELLED]),
                "monitoring_active": self._monitoring,
            }


# Singleton para uso global
_order_monitor_instance: OrderMonitor | None = None


def get_order_monitor(binance_service=None, check_interval: float = 2.0) -> OrderMonitor:
    """Obtiene o crea el monitor global de órdenes."""
    global _order_monitor_instance

    if _order_monitor_instance is None and binance_service is not None:
        _order_monitor_instance = OrderMonitor(binance_service, check_interval)

    return _order_monitor_instance


def reset_order_monitor():
    """Resetea el monitor global (útil para tests)."""
    global _order_monitor_instance

    if _order_monitor_instance:
        _order_monitor_instance.stop_monitoring()
        _order_monitor_instance = None
