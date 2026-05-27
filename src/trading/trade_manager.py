"""
Trade Manager - Manejo de posiciones abiertas y lógica de cierre.

Este módulo agrega:
1. Tracking de posiciones abiertas
2. Cierre automático por señal contraria
3. Trailing stop dinámico ESCALONADO (v2.1)
   - 1% después de +1% ganancia
   - 2% después de +2% ganancia
   - 3% después de +3% ganancia
4. Time-based exit (max time in trade)
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger("FenixTradeManager")


class ExitReason(Enum):
    OPPOSITE_SIGNAL = "opposite_signal"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"
    TIME_EXIT = "time_exit"
    MANUAL = "manual"
    EXCHANGE_RECONCILIATION = "exchange_reconciliation"


@dataclass
class OpenPosition:
    """Representa una posición abierta."""

    symbol: str
    side: str  # LONG o SHORT
    entry_price: float
    quantity: float
    entry_time: datetime
    entry_signal_ts: str
    stop_loss: float | None = None
    take_profit: float | None = None
    highest_price: float | None = None  # Para trailing stop en LONG
    lowest_price: float | None = None  # Para trailing stop en SHORT
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    trailing_activated: bool = False
    trailing_history: list[dict[str, Any]] = field(default_factory=list)
    trailing_tp_activated: bool = False
    trailing_tp_history: list[dict[str, Any]] = field(default_factory=list)
    trade_id: str | None = None
    reasoning_digest: str | None = None
    decision_agent_name: str | None = None
    protection_position_id: str | None = None
    sl_order_id: int | str | None = None
    tp_order_id: int | str | None = None
    last_synced_stop_loss: float | None = None
    last_synced_take_profit: float | None = None
    protection_refresh_pending: bool = False
    entry_count: int = 1

    def update_price(
        self,
        current_price: float,
        base_trailing_pct: float = 0.02,
        trailing_tp_enabled: bool = False,
        trailing_tp_pct: float = 0.004,
        trailing_tp_arm_pct: float = 0.0015,
        esc1_gain: float = 1.0,
        esc1_trail: float = 0.01,
        esc2_gain: float = 2.0,
        esc2_trail: float = 0.005,
        esc3_gain: float = 3.0,
        esc3_trail: float = 0.003,
    ):
        """
        Actualiza PnL y trailing stop ESCALONADO.

        Escalación configurable (defaults para timeframes largos):
        - 0 a esc1_gain% ganancia: trailing stop base
        - esc1_gain% a esc2_gain%: esc1_trail
        - esc2_gain% a esc3_gain%: esc2_trail
        - >esc3_gain%: esc3_trail (máximo profit protection)

        Para 3m: usar esc1_gain=0.15, esc1_trail=0.0015, etc.
        """
        if self.side == "LONG":
            self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
            self.unrealized_pnl_pct = (current_price - self.entry_price) / self.entry_price * 100

            # Actualizar highest price
            if self.highest_price is None or current_price > self.highest_price:
                self.highest_price = current_price

            # Calcular trailing stop ESCALONADO basado en ganancia actual
            if self.highest_price and self.stop_loss:
                gain_pct = (self.highest_price - self.entry_price) / self.entry_price * 100

                # Determinar nivel de trailing según ganancia (configurable)
                if gain_pct >= esc3_gain:
                    trailing_pct = esc3_trail
                elif gain_pct >= esc2_gain:
                    trailing_pct = esc2_trail
                elif gain_pct >= esc1_gain:
                    trailing_pct = esc1_trail
                else:
                    trailing_pct = base_trailing_pct

                new_sl = self.highest_price * (1 - trailing_pct)
                if new_sl > self.stop_loss:
                    old_sl = self.stop_loss
                    self.stop_loss = new_sl
                    self._mark_protection_dirty()
                    self.trailing_activated = True
                    self.trailing_history.append(
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "price": current_price,
                            "highest": self.highest_price,
                            "gain_pct": gain_pct,
                            "trailing_pct": trailing_pct,
                            "old_sl": old_sl,
                            "new_sl": new_sl,
                        }
                    )
                    logger.info(
                        f"📈 Trailing stop updated for LONG {self.symbol}: "
                        f"SL {old_sl:.2f} → {new_sl:.2f} (gain: {gain_pct:.2f}%, trailing: {trailing_pct * 100:.1f}%)"
                    )

            # Optional trailing TP: once price approaches TP, extend target upward.
            if trailing_tp_enabled and self.take_profit and self.highest_price:
                arm_price = self.take_profit * (1 - trailing_tp_arm_pct)
                if current_price >= arm_price:
                    new_tp = self.highest_price * (1 + trailing_tp_pct)
                    if new_tp > self.take_profit:
                        old_tp = self.take_profit
                        self.take_profit = new_tp
                        self._mark_protection_dirty()
                        self.trailing_tp_activated = True
                        self.trailing_tp_history.append(
                            {
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "price": current_price,
                                "highest": self.highest_price,
                                "old_tp": old_tp,
                                "new_tp": new_tp,
                                "trailing_tp_pct": trailing_tp_pct,
                                "arm_pct": trailing_tp_arm_pct,
                            }
                        )
                        logger.info(
                            f"🎯 Trailing TP updated for LONG {self.symbol}: "
                            f"TP {old_tp:.2f} → {new_tp:.2f}"
                        )
        else:  # SHORT
            self.unrealized_pnl = (self.entry_price - current_price) * self.quantity
            self.unrealized_pnl_pct = (self.entry_price - current_price) / self.entry_price * 100

            # Actualizar lowest price
            if self.lowest_price is None or current_price < self.lowest_price:
                self.lowest_price = current_price

            # Calcular trailing stop ESCALONADO basado en ganancia actual
            if self.lowest_price and self.stop_loss:
                gain_pct = (self.entry_price - self.lowest_price) / self.entry_price * 100

                # Determinar nivel de trailing según ganancia (configurable)
                if gain_pct >= esc3_gain:
                    trailing_pct = esc3_trail
                elif gain_pct >= esc2_gain:
                    trailing_pct = esc2_trail
                elif gain_pct >= esc1_gain:
                    trailing_pct = esc1_trail
                else:
                    trailing_pct = base_trailing_pct

                new_sl = self.lowest_price * (1 + trailing_pct)
                if new_sl < self.stop_loss:
                    old_sl = self.stop_loss
                    self.stop_loss = new_sl
                    self._mark_protection_dirty()
                    self.trailing_activated = True
                    self.trailing_history.append(
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "price": current_price,
                            "lowest": self.lowest_price,
                            "gain_pct": gain_pct,
                            "trailing_pct": trailing_pct,
                            "old_sl": old_sl,
                            "new_sl": new_sl,
                        }
                    )
                    logger.info(
                        f"📉 Trailing stop updated for SHORT {self.symbol}: "
                        f"SL {old_sl:.2f} → {new_sl:.2f} (gain: {gain_pct:.2f}%, trailing: {trailing_pct * 100:.1f}%)"
                    )

            # Optional trailing TP: once price approaches TP, extend target downward.
            if trailing_tp_enabled and self.take_profit and self.lowest_price:
                arm_price = self.take_profit * (1 + trailing_tp_arm_pct)
                if current_price <= arm_price:
                    new_tp = self.lowest_price * (1 - trailing_tp_pct)
                    if new_tp < self.take_profit:
                        old_tp = self.take_profit
                        self.take_profit = new_tp
                        self._mark_protection_dirty()
                        self.trailing_tp_activated = True
                        self.trailing_tp_history.append(
                            {
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "price": current_price,
                                "lowest": self.lowest_price,
                                "old_tp": old_tp,
                                "new_tp": new_tp,
                                "trailing_tp_pct": trailing_tp_pct,
                                "arm_pct": trailing_tp_arm_pct,
                            }
                        )
                        logger.info(
                            f"🎯 Trailing TP updated for SHORT {self.symbol}: "
                            f"TP {old_tp:.2f} → {new_tp:.2f}"
                        )

    def should_exit_trailing(self, current_price: float) -> bool:
        """Verifica si el trailing stop fue触发"""
        if self.stop_loss is None:
            return False

        if self.side == "LONG":
            return current_price < self.stop_loss
        else:  # SHORT
            return current_price > self.stop_loss

    def _mark_protection_dirty(self) -> None:
        """Flag exchange-side protections for refresh after local trailing updates."""
        if (
            self.stop_loss != self.last_synced_stop_loss
            or self.take_profit != self.last_synced_take_profit
        ):
            self.protection_refresh_pending = True

    def mark_protection_synced(
        self,
        *,
        stop_loss: float | None,
        take_profit: float | None,
        position_id: str | None = None,
        sl_order_id: int | str | None = None,
        tp_order_id: int | str | None = None,
    ) -> None:
        """Persist the latest exchange-side protection snapshot."""
        self.last_synced_stop_loss = stop_loss
        self.last_synced_take_profit = take_profit
        self.protection_refresh_pending = False
        if position_id is not None:
            self.protection_position_id = position_id
        if sl_order_id is not None or self.sl_order_id is None:
            self.sl_order_id = sl_order_id
        if tp_order_id is not None or self.tp_order_id is None:
            self.tp_order_id = tp_order_id

    def to_dict(self) -> dict[str, Any]:
        """Serializa para logging."""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "entry_time": self.entry_time.isoformat(),
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "unrealized_pnl": self.unrealized_pnl,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "highest_price": self.highest_price,
            "lowest_price": self.lowest_price,
            "trailing_activated": self.trailing_activated,
            "trailing_history_count": len(self.trailing_history),
            "trailing_tp_activated": self.trailing_tp_activated,
            "trailing_tp_history_count": len(self.trailing_tp_history),
            "trade_id": self.trade_id,
            "reasoning_digest": self.reasoning_digest,
            "decision_agent_name": self.decision_agent_name,
            "protection_position_id": self.protection_position_id,
            "sl_order_id": str(self.sl_order_id) if self.sl_order_id is not None else None,
            "tp_order_id": str(self.tp_order_id) if self.tp_order_id is not None else None,
            "last_synced_stop_loss": self.last_synced_stop_loss,
            "last_synced_take_profit": self.last_synced_take_profit,
            "protection_refresh_pending": self.protection_refresh_pending,
            "entry_count": self.entry_count,
        }


class TradeManager:
    """
    Gestiona el ciclo de vida de trades.

    Responsabilidades:
    - Trackear posiciones abiertas por símbolo
    - Generar señales CLOSE cuando corresponda
    - Monitorear stops y trailing stops escalonados
    - Prevenir hedging accidental
    """

    def __init__(
        self,
        trailing_stop_pct: float = 0.02,
        max_hold_hours: float | None = None,
        trailing_tp_enabled: bool = False,
        trailing_tp_pct: float = 0.004,
        trailing_tp_arm_pct: float = 0.0015,
        esc1_gain: float = 1.0,
        esc1_trail: float = 0.01,
        esc2_gain: float = 2.0,
        esc2_trail: float = 0.005,
        esc3_gain: float = 3.0,
        esc3_trail: float = 0.003,
        max_consecutive_holds: int = 0,
    ):
        """
        Args:
            trailing_stop_pct: Porcentaje de trailing stop base (0.02 = 2%)
            max_hold_hours: Máximo tiempo en horas antes de forzar cierre (None = sin límite)
            esc{1,2,3}_gain: Ganancia % para activar cada nivel de trailing
            esc{1,2,3}_trail: Trailing % en cada nivel de escalación
            max_consecutive_holds: Cerrar posición perdedora tras N HOLDs seguidos (0 = deshabilitado)
        """
        self.positions: dict[str, OpenPosition] = {}  # symbol -> position
        self.trailing_stop_pct = trailing_stop_pct
        self.max_hold_hours = max_hold_hours
        self.trailing_tp_enabled = trailing_tp_enabled
        self.trailing_tp_pct = trailing_tp_pct
        self.trailing_tp_arm_pct = trailing_tp_arm_pct
        self.esc1_gain = esc1_gain
        self.esc1_trail = esc1_trail
        self.esc2_gain = esc2_gain
        self.esc2_trail = esc2_trail
        self.esc3_gain = esc3_gain
        self.esc3_trail = esc3_trail
        self.max_consecutive_holds = max_consecutive_holds
        self._consecutive_holds: dict[str, int] = {}  # symbol -> count
        self.closed_trades = []  # Historial de trades cerrados

    def open_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        signal_timestamp: str,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        trade_id: str | None = None,
        reasoning_digest: str | None = None,
        decision_agent_name: str | None = None,
        protection_position_id: str | None = None,
        sl_order_id: int | str | None = None,
        tp_order_id: int | str | None = None,
    ) -> OpenPosition:
        """Registra una nueva posición abierta o agrega tamaño a una posición same-side."""
        existing = self.positions.get(symbol)
        if existing and existing.side == side:
            previous_quantity = float(existing.quantity)
            new_quantity = float(quantity)
            total_quantity = previous_quantity + new_quantity
            if total_quantity <= 0:
                return existing

            existing.entry_price = (
                (existing.entry_price * previous_quantity) + (float(entry_price) * new_quantity)
            ) / total_quantity
            existing.quantity = total_quantity
            existing.entry_signal_ts = signal_timestamp
            existing.entry_count += 1
            existing.trade_id = trade_id or existing.trade_id
            existing.reasoning_digest = reasoning_digest or existing.reasoning_digest
            existing.decision_agent_name = decision_agent_name or existing.decision_agent_name
            existing.stop_loss = stop_loss if stop_loss is not None else existing.stop_loss
            existing.take_profit = take_profit if take_profit is not None else existing.take_profit
            existing.protection_position_id = (
                protection_position_id or existing.protection_position_id
            )
            existing.sl_order_id = sl_order_id if sl_order_id is not None else existing.sl_order_id
            existing.tp_order_id = tp_order_id if tp_order_id is not None else existing.tp_order_id
            existing.mark_protection_synced(
                stop_loss=existing.stop_loss,
                take_profit=existing.take_profit,
                position_id=existing.protection_position_id,
                sl_order_id=existing.sl_order_id,
                tp_order_id=existing.tp_order_id,
            )
            logger.info(
                f"📊 Position added: {side} +{quantity} {symbol} @ {entry_price} "
                f"→ total {existing.quantity} @ avg {existing.entry_price:.4f} "
                f"(entries={existing.entry_count})"
            )
            return existing

        pos = OpenPosition(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            entry_time=datetime.now(timezone.utc),
            entry_signal_ts=signal_timestamp,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trade_id=trade_id,
            reasoning_digest=reasoning_digest,
            decision_agent_name=decision_agent_name,
            protection_position_id=protection_position_id,
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
        )
        pos.mark_protection_synced(
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_id=protection_position_id,
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
        )

        self.positions[symbol] = pos

        logger.info(
            f"📊 Position opened: {side} {quantity} {symbol} @ {entry_price} "
            f"(SL={stop_loss}, TP={take_profit})"
        )

        return pos

    def close_position(
        self,
        symbol: str,
        exit_price: float,
        reason: ExitReason,
        extra_notes: str = "",
    ) -> dict[str, Any] | None:
        """
        Cierra una posición abierta.

        Returns:
            Dict con detalles del trade cerrado, o None si no había posición.
        """
        if symbol not in self.positions:
            logger.warning(f"⚠️ No position to close for {symbol}")
            return None

        pos = self.positions[symbol]

        # Calcular PnL final
        if pos.side == "LONG":
            pnl = (exit_price - pos.entry_price) * pos.quantity
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
        else:  # SHORT
            pnl = (pos.entry_price - exit_price) * pos.quantity
            pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100

        trade_record = {
            "symbol": pos.symbol,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "quantity": pos.quantity,
            "entry_time": pos.entry_time.isoformat(),
            "exit_time": datetime.now(timezone.utc).isoformat(),
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "exit_reason": reason.value,
            "exit_notes": extra_notes,
            "duration_minutes": (datetime.now(timezone.utc) - pos.entry_time).total_seconds() / 60,
            "final_stop_loss": pos.stop_loss,
            "final_take_profit": pos.take_profit,
            "trailing_activated": pos.trailing_activated,
            "trailing_adjustments": len(pos.trailing_history),
            "trailing_tp_activated": pos.trailing_tp_activated,
            "trailing_tp_adjustments": len(pos.trailing_tp_history),
            "max_unrealized_pnl_pct": ((pos.highest_price or pos.entry_price) - pos.entry_price)
            / pos.entry_price
            * 100
            if pos.side == "LONG"
            else (pos.entry_price - (pos.lowest_price or pos.entry_price)) / pos.entry_price * 100,
            "trade_id": pos.trade_id,
            "reasoning_digest": pos.reasoning_digest,
            "decision_agent_name": pos.decision_agent_name,
        }

        self.closed_trades.append(trade_record)
        del self.positions[symbol]
        self._consecutive_holds.pop(symbol, None)

        logger.info(
            f"✅ Position closed: {pos.side} {pos.symbol} | "
            f"Entry: {pos.entry_price} → Exit: {exit_price} | "
            f"PnL: {pnl:+.2f} USDT ({pnl_pct:+.2f}%) | "
            f"Reason: {reason.value} {extra_notes}"
        )

        return trade_record

    def check_exit_conditions(
        self,
        symbol: str,
        current_price: float,
        new_signal: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Verifica si hay condiciones de cierre para una posición.

        Args:
            symbol: Símbolo a verificar
            current_price: Precio actual
            new_signal: Nueva señal (BUY/SELL/HOLD) para detectar señal contraria

        Returns:
            Dict de cierre si hay que cerrar, None si no.
        """
        if symbol not in self.positions:
            return None

        pos = self.positions[symbol]

        # Actualizar PnL y trailing stop ESCALONADO
        pos.update_price(
            current_price,
            self.trailing_stop_pct,
            trailing_tp_enabled=self.trailing_tp_enabled,
            trailing_tp_pct=self.trailing_tp_pct,
            trailing_tp_arm_pct=self.trailing_tp_arm_pct,
            esc1_gain=self.esc1_gain,
            esc1_trail=self.esc1_trail,
            esc2_gain=self.esc2_gain,
            esc2_trail=self.esc2_trail,
            esc3_gain=self.esc3_gain,
            esc3_trail=self.esc3_trail,
        )

        # 1. Verificar stop loss
        if pos.stop_loss:
            if pos.side == "LONG" and current_price <= pos.stop_loss:
                return self.close_position(
                    symbol, current_price, ExitReason.STOP_LOSS, f"Hit SL @ {pos.stop_loss}"
                )
            elif pos.side == "SHORT" and current_price >= pos.stop_loss:
                return self.close_position(
                    symbol, current_price, ExitReason.STOP_LOSS, f"Hit SL @ {pos.stop_loss}"
                )

        # 2. Verificar take profit
        if pos.take_profit:
            if pos.side == "LONG" and current_price >= pos.take_profit:
                return self.close_position(
                    symbol, current_price, ExitReason.TAKE_PROFIT, f"Hit TP @ {pos.take_profit}"
                )
            elif pos.side == "SHORT" and current_price <= pos.take_profit:
                return self.close_position(
                    symbol, current_price, ExitReason.TAKE_PROFIT, f"Hit TP @ {pos.take_profit}"
                )

        # 3. Verificar trailing stop
        if pos.should_exit_trailing(current_price):
            return self.close_position(
                symbol,
                current_price,
                ExitReason.TRAILING_STOP,
                f"Trailing SL @ {pos.stop_loss:.2f} (adjustments: {len(pos.trailing_history)})",
            )

        # 4. Verificar señal contraria
        if new_signal:
            if new_signal in ("BUY", "SELL", "HOLD"):
                if new_signal == "HOLD":
                    self._consecutive_holds[symbol] = self._consecutive_holds.get(symbol, 0) + 1
                else:
                    self._consecutive_holds[symbol] = 0

            if pos.side == "LONG" and new_signal == "SELL":
                return self.close_position(
                    symbol,
                    current_price,
                    ExitReason.OPPOSITE_SIGNAL,
                    f"Opposite signal: {new_signal}",
                )
            elif pos.side == "SHORT" and new_signal == "BUY":
                return self.close_position(
                    symbol,
                    current_price,
                    ExitReason.OPPOSITE_SIGNAL,
                    f"Opposite signal: {new_signal}",
                )

        # 5. Verificar consecutive HOLD exit (P5: cierra posición perdedora tras muchos HOLDs)
        if self.max_consecutive_holds > 0:
            hold_count = self._consecutive_holds.get(symbol, 0)
            if hold_count >= self.max_consecutive_holds and pos.unrealized_pnl < 0:
                return self.close_position(
                    symbol,
                    current_price,
                    ExitReason.TIME_EXIT,
                    f"{hold_count} consecutive HOLDs with unrealized PnL={pos.unrealized_pnl:+.2f}",
                )

        # 6. Verificar time exit
        if self.max_hold_hours:
            hold_time = (datetime.now(timezone.utc) - pos.entry_time).total_seconds() / 3600
            if hold_time >= self.max_hold_hours:
                return self.close_position(
                    symbol, current_price, ExitReason.TIME_EXIT, f"Max hold time: {hold_time:.1f}h"
                )

        return None

    def get_position(self, symbol: str) -> OpenPosition | None:
        """Obtiene una posición abierta."""
        return self.positions.get(symbol)

    def has_position(self, symbol: str) -> bool:
        """Verifica si hay posición abierta."""
        return symbol in self.positions

    def get_all_positions(self) -> dict[str, dict[str, Any]]:
        """Obtiene todas las posiciones abiertas serializadas."""
        return {sym: pos.to_dict() for sym, pos in self.positions.items()}

    def get_stats(self) -> dict[str, Any]:
        """Estadísticas de performance."""
        if not self.closed_trades:
            return {"total_trades": 0}

        wins = [t for t in self.closed_trades if t["pnl"] > 0]
        losses = [t for t in self.closed_trades if t["pnl"] <= 0]

        total_pnl = sum(t["pnl"] for t in self.closed_trades)
        win_rate = len(wins) / len(self.closed_trades) * 100 if self.closed_trades else 0

        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

        # Trailing stop stats
        trades_with_trailing = [t for t in self.closed_trades if t.get("trailing_activated", False)]
        avg_trailing_adjustments = (
            sum(t.get("trailing_adjustments", 0) for t in trades_with_trailing)
            / len(trades_with_trailing)
            if trades_with_trailing
            else 0
        )
        trades_with_trailing_tp = [
            t for t in self.closed_trades if t.get("trailing_tp_activated", False)
        ]

        return {
            "total_trades": len(self.closed_trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": abs(avg_win / avg_loss) if avg_loss != 0 else float("inf"),
            "open_positions": len(self.positions),
            "trailing_activated_count": len(trades_with_trailing),
            "avg_trailing_adjustments": avg_trailing_adjustments,
            "trailing_tp_activated_count": len(trades_with_trailing_tp),
        }


# Singleton global
_trade_manager: TradeManager | None = None


def get_trade_manager() -> TradeManager:
    """Obtiene el TradeManager global."""
    global _trade_manager
    if _trade_manager is None:
        trailing_tp_enabled = os.getenv("FENIX_TRAILING_TP_ENABLED", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        _trade_manager = TradeManager(
            trailing_stop_pct=float(os.getenv("FENIX_TRAILING_STOP_PCT", "0.02")),
            max_hold_hours=float(os.getenv("FENIX_MAX_HOLD_HOURS", "0")) or None,
            trailing_tp_enabled=trailing_tp_enabled,
            trailing_tp_pct=float(os.getenv("FENIX_TRAILING_TP_PCT", "0.004")),
            trailing_tp_arm_pct=float(os.getenv("FENIX_TRAILING_TP_ARM_PCT", "0.0015")),
            esc1_gain=float(os.getenv("FENIX_TRAILING_ESC1_GAIN", "1.0")),
            esc1_trail=float(os.getenv("FENIX_TRAILING_ESC1_PCT", "0.01")),
            esc2_gain=float(os.getenv("FENIX_TRAILING_ESC2_GAIN", "2.0")),
            esc2_trail=float(os.getenv("FENIX_TRAILING_ESC2_PCT", "0.005")),
            esc3_gain=float(os.getenv("FENIX_TRAILING_ESC3_GAIN", "3.0")),
            esc3_trail=float(os.getenv("FENIX_TRAILING_ESC3_PCT", "0.003")),
            max_consecutive_holds=int(os.getenv("FENIX_MAX_CONSECUTIVE_HOLDS", "0")),
        )
    return _trade_manager
