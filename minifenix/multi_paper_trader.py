"""
MiniFenix Multi-Symbol Paper Trader.

Corrected version for multi-symbol trading with no interference between
instances. Each symbol has its own isolated state.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Dict

logger = logging.getLogger("MiniFenix.MultiPaper")


@dataclass
class Trade:
    id: int
    symbol: str
    side: str  # BUY | SELL
    entry_price: float
    exit_price: float = 0.0
    quantity: float = 0.0
    size_usdt: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    reason: str = ""  # "TP" | "SL" | "SIGNAL" | "REGIME_CHANGE"
    entry_ts: float = 0.0
    exit_ts: float = 0.0
    duration_s: float = 0.0


@dataclass
class Position:
    symbol: str
    side: str  # LONG | SHORT
    entry_price: float
    quantity: float
    size_usdt: float
    stop_loss: float
    take_profit: float
    entry_ts: float = field(default_factory=time.time)
    unrealized_pnl: float = 0.0
    entry_fee: float = 0.0

    def update_pnl(self, current_price: float) -> float:
        if self.side == "LONG":
            self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.quantity
        return self.unrealized_pnl

    def hit_stop(self, price: float) -> bool:
        return price <= self.stop_loss if self.side == "LONG" else price >= self.stop_loss

    def hit_tp(self, price: float) -> bool:
        return price >= self.take_profit if self.side == "LONG" else price <= self.take_profit


class MultiPaperTrader:
    """
    Per-symbol isolated paper trader for multi-symbol trading.

    Fixes vs TestnetTrader:
    - Each symbol has its own isolated balance
    - No interference between instances
    - No Binance connection required
    - P&L calculation is independent per symbol
    """

    def __init__(
        self,
        symbol: str,
        initial_balance: float = 10_000.0,
        position_size_pct: float = 0.02,  # REDUCED: 2% for multi-symbol
        sl_pct: float = 0.005,
        tp_pct: float = 0.012,
        commission_pct: float = 0.0004,
        cooldown_seconds: float = 10.0,
    ):
        self.symbol = symbol
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.position_size_pct = position_size_pct
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct
        self.commission_pct = commission_pct
        self.cooldown_seconds = cooldown_seconds

        # Per-symbol isolated state
        self.position: Optional[Position] = None
        self.trades: list[Trade] = []
        self._trade_id = 0
        self._last_trade_ts = 0.0
        self._pending_order = False

        # Metrics
        self.total_fees = 0.0
        self.peak_balance = initial_balance
        self.max_drawdown = 0.0

        logger.info(
            f"[PAPER] [MULTI-PAPER] {symbol} | Balance: ${initial_balance:,.0f} | "
            f"Size: {position_size_pct:.0%} | SL: {sl_pct:.2%} | TP: {tp_pct:.2%}"
        )

    def can_trade(self) -> bool:
        if self._pending_order:
            return False
        if self.position is not None:
            return False
        if time.time() - self._last_trade_ts < self.cooldown_seconds:
            return False
        return True

    def open_long(self, price: float, **kwargs) -> None:
        if not self.can_trade():
            return
        self._pending_order = True

        size_usdt = self.balance * self.position_size_pct
        quantity = size_usdt / price
        sl = round(price * (1 - self.sl_pct), 2)
        tp = round(price * (1 + self.tp_pct), 2)

        logger.info(
            f"[LONG] [{self.symbol}] ORDER LONG | qty={quantity:.5f} | "
            f"entry~${price:,.2f} | SL=${sl:,.2f} | TP=${tp:,.2f}"
        )

        # Execute immediately (paper trading)
        self._execute_entry("LONG", quantity, price, sl, tp)
        self._pending_order = False

    def open_short(self, price: float, **kwargs) -> None:
        if not self.can_trade():
            return
        self._pending_order = True

        size_usdt = self.balance * self.position_size_pct
        quantity = size_usdt / price
        sl = round(price * (1 + self.sl_pct), 2)
        tp = round(price * (1 - self.tp_pct), 2)

        logger.info(
            f"[SHORT] [{self.symbol}] ORDER SHORT | qty={quantity:.5f} | "
            f"entry~${price:,.2f} | SL=${sl:,.2f} | TP=${tp:,.2f}"
        )

        # Execute immediately (paper trading)
        self._execute_entry("SHORT", quantity, price, sl, tp)
        self._pending_order = False

    def _execute_entry(self, side: str, quantity: float, price: float, sl: float, tp: float):
        """Execute entry immediately (no async)."""
        entry_price = price
        size_usdt = entry_price * quantity
        entry_fee = size_usdt * self.commission_pct
        self.balance -= entry_fee
        self.total_fees += entry_fee

        self.position = Position(
            symbol=self.symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            size_usdt=size_usdt,
            stop_loss=sl,
            take_profit=tp,
            entry_fee=entry_fee,
        )
        self._last_trade_ts = time.time()

        logger.info(
            f"[OK] [{self.symbol}] FILLED {side} {quantity:.5f} @ ${entry_price:,.2f} | "
            f"SL=${sl:,.2f} TP=${tp:,.2f} | Fee=${entry_fee:.4f}"
        )

    def update(self, current_price: float) -> Optional[Trade]:
        """Update position and check SL/TP."""
        if self.position is None:
            return None

        self.position.update_pnl(current_price)

        # Check SL
        if self.position.hit_stop(current_price):
            return self._close_position(current_price, "SL")

        # Check TP
        if self.position.hit_tp(current_price):
            return self._close_position(current_price, "TP")

        return None

    def _close_position(self, price: float, reason: str) -> Trade:
        """Close position and record the trade."""
        pos = self.position
        self.position = None

        exit_price = price
        fee_exit = pos.quantity * exit_price * self.commission_pct
        self.total_fees += fee_exit

        # Gross PnL from the price movement
        if pos.side == "LONG":
            pnl_gross = (exit_price - pos.entry_price) * pos.quantity
        else:
            pnl_gross = (pos.entry_price - exit_price) * pos.quantity

        # Net round-trip PnL (includes entry + exit fee)
        pnl_net = pnl_gross - pos.entry_fee - fee_exit
        pnl_pct = pnl_net / pos.size_usdt if pos.size_usdt > 0 else 0.0

        # On close we only credit the price-move PnL minus the exit fee.
        # The entry fee was already debited when opening.
        self.balance += pnl_gross - fee_exit

        self._trade_id += 1
        now = time.time()

        trade = Trade(
            id=self._trade_id,
            symbol=self.symbol,
            side="BUY" if pos.side == "LONG" else "SELL",
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=pos.quantity,
            size_usdt=pos.size_usdt,
            pnl=pnl_net,
            pnl_pct=pnl_pct,
            reason=reason,
            entry_ts=pos.entry_ts,
            exit_ts=now,
            duration_s=now - pos.entry_ts,
        )

        self.trades.append(trade)
        self._update_metrics(pnl_net)

        emoji = "[OK]" if pnl_net >= 0 else "[FAIL]"
        logger.info(
            f"{emoji} [{self.symbol}] {reason} | {pos.side} | "
            f"${pos.entry_price:,.2f}->${exit_price:,.2f} | "
            f"P&L=${pnl_net:+.4f} ({pnl_pct:+.2%}) | Dur: {trade.duration_s:.0f}s"
        )

        return trade

    def force_close(self, price: float, reason: str = "SIGNAL") -> Optional[Trade]:
        if self.position is None:
            return None
        return self._close_position(price, reason)

    def _update_metrics(self, pnl: float) -> None:
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        dd = (self.peak_balance - self.balance) / self.peak_balance * 100
        if dd > self.max_drawdown:
            self.max_drawdown = dd

    def get_stats(self, current_price: float = 0.0) -> dict:
        """Return per-symbol isolated stats."""
        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl <= 0]
        total_pnl = self.balance - self.initial_balance

        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        win_rate = len(wins) / len(self.trades) if self.trades else 0.0
        total_return_pct = (self.balance - self.initial_balance) / self.initial_balance * 100

        equity = self.balance
        has_position = self.position is not None
        unrealized_pnl = 0.0
        if has_position and current_price > 0:
            unrealized_pnl = self.position.update_pnl(current_price)
            equity += unrealized_pnl

        return {
            "symbol": self.symbol,
            "equity": equity,
            "balance": self.balance,
            "total_pnl": total_pnl,
            "total_return_pct": total_return_pct,
            "trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown_pct": self.max_drawdown,
            "total_fees": self.total_fees,
            "has_position": has_position,
            "position_side": self.position.side if has_position else "",
            "position_entry": self.position.entry_price if has_position else 0.0,
            "unrealized_pnl": unrealized_pnl,
        }

    def print_summary(self, current_price: float = 0.0) -> None:
        s = self.get_stats(current_price)
        line = "-" * 60
        print(f"\n{line}")
        print(f"  [PAPER] {self.symbol} - PAPER TRADING summary")
        print(line)
        print(f"  Initial balance : ${self.initial_balance:>10,.2f} USDT")
        print(f"  Final balance   : ${s['balance']:>10,.2f} USDT")
        print(f"  Total P&L       : ${s['total_pnl']:>+10,.4f} USDT ({s['total_return_pct']:+.2f}%)")
        print(f"  Max Drawdown    : {s['max_drawdown_pct']:>10.2f}%")
        print(f"  Trades          : {s['trades']:>10d}")
        print(f"  Win Rate        : {s['win_rate']:>10.0%}  ({s['wins']}W / {s['losses']}L)")
        print(f"  Profit Factor   : {s['profit_factor']:>10.2f}")
        print(f"  Fees            : ${s['total_fees']:>10.4f} USDT")
        print(line)

        if self.trades:
            print(f"\n  Last 3 trades for {self.symbol}:")
            for t in self.trades[-3:]:
                emoji = "[OK]" if t.pnl >= 0 else "[FAIL]"
                dur = f"{t.duration_s:.0f}s"
                print(
                    f"  {emoji} #{t.id:3d} {t.side:<4s} "
                    f"${t.entry_price:,.2f}->${t.exit_price:,.2f} "
                    f"P&L=${t.pnl:+.4f} [{t.reason}] {dur}"
                )
        print(line)
