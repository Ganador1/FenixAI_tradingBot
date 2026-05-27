"""
MiniFenix Paper Trader.

Self-contained paper trading engine: simulates order execution using real
WebSocket prices, manages positions, computes P&L in real time and records
full performance metrics.

No Binance API keys required. Everything in memory.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("MiniFenix.Paper")


@dataclass
class Trade:
    """Record of a completed (closed) trade."""
    id: int
    side: str                   # BUY | SELL
    entry_price: float
    exit_price: float = 0.0
    quantity: float = 0.0       # in BTC
    size_usdt: float = 0.0      # notional in USDT
    pnl: float = 0.0            # Realised P&L (USDT)
    pnl_pct: float = 0.0
    reason: str = ""            # "TP" | "SL" | "SIGNAL"
    entry_ts: float = 0.0
    exit_ts: float = 0.0
    duration_s: float = 0.0
    ml_signal: str = ""
    ml_confidence: float = 0.0
    brain_bias: str = ""
    brain_confidence: float = 0.0


@dataclass
class Position:
    """Current open position."""
    side: str          # LONG | SHORT
    entry_price: float
    quantity: float    # in BTC
    size_usdt: float
    stop_loss: float
    take_profit: float
    entry_ts: float = field(default_factory=time.time)
    unrealized_pnl: float = 0.0
    ml_signal: str = ""
    ml_confidence: float = 0.0
    brain_bias: str = ""
    brain_confidence: float = 0.0

    def update_pnl(self, current_price: float) -> float:
        """Update unrealised P&L with the current price."""
        if self.side == "LONG":
            self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
        else:  # SHORT
            self.unrealized_pnl = (self.entry_price - current_price) * self.quantity
        return self.unrealized_pnl

    def hit_stop(self, price: float) -> bool:
        if self.side == "LONG":
            return price <= self.stop_loss
        return price >= self.stop_loss

    def hit_tp(self, price: float) -> bool:
        if self.side == "LONG":
            return price >= self.take_profit
        return price <= self.take_profit


class PaperTrader:
    """
    Paper trading engine using live prices.

    Features:
    - Virtual USDT balance
    - Single position (closed before opening a new one)
    - Configurable automatic SL/TP
    - Real-time P&L
    - Stats: win rate, max drawdown, approximate Sharpe
    - Structured log of each trade
    """

    def __init__(
        self,
        initial_balance: float = 10_000.0,   # Initial USDT
        position_size_pct: float = 0.05,      # 5% of balance per trade
        sl_pct: float = 0.003,                # Stop Loss: -0.3%
        tp_pct: float = 0.006,                # Take Profit: +0.6% (1:2 ratio)
        commission_pct: float = 0.0004,       # 0.04% Binance Futures maker fee
        cooldown_seconds: float = 10.0,       # Minimum between trades
    ):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.position_size_pct = position_size_pct
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct
        self.commission_pct = commission_pct
        self.cooldown_seconds = cooldown_seconds

        self.position: Optional[Position] = None
        self.trades: list[Trade] = []
        self._trade_id = 0
        self._last_trade_ts = 0.0

        # Metrics
        self.total_fees = 0.0
        self.peak_balance = initial_balance
        self.max_drawdown = 0.0

        logger.info(
            f"[PAPER] [PAPER] Started | Balance: ${initial_balance:,.2f} USDT | "
            f"Position size: {position_size_pct:.0%} | "
            f"SL: {sl_pct:.2%} | TP: {tp_pct:.2%}"
        )

    # ------------------------------------------
    # Public interface
    # ------------------------------------------

    def can_trade(self) -> bool:
        """Can we open a new position?"""
        if self.position is not None:
            return False
        if time.time() - self._last_trade_ts < self.cooldown_seconds:
            return False
        if self.balance < self.initial_balance * 0.1:  # Stop if loss >90%
            return False
        return True

    def open_long(
        self,
        price: float,
        ml_signal: str = "",
        ml_confidence: float = 0.0,
        brain_bias: str = "",
        brain_confidence: float = 0.0,
    ) -> bool:
        """Open LONG position."""
        return self._open("LONG", price, ml_signal, ml_confidence, brain_bias, brain_confidence)

    def open_short(
        self,
        price: float,
        ml_signal: str = "",
        ml_confidence: float = 0.0,
        brain_bias: str = "",
        brain_confidence: float = 0.0,
    ) -> bool:
        """Open SHORT position."""
        return self._open("SHORT", price, ml_signal, ml_confidence, brain_bias, brain_confidence)

    def update(self, current_price: float) -> Optional[Trade]:
        """
        Update the position with the current price.
        Close if SL or TP is hit.
        Returns the closed trade or None.
        """
        if self.position is None:
            return None

        self.position.update_pnl(current_price)

        if self.position.hit_tp(current_price):
            return self._close(current_price, reason="TP")
        if self.position.hit_stop(current_price):
            return self._close(current_price, reason="SL")
        return None

    def force_close(self, price: float, reason: str = "SIGNAL") -> Optional[Trade]:
        """Close the position based on the opposite model signal."""
        if self.position is None:
            return None
        return self._close(price, reason=reason)

    def get_unrealized_pnl(self) -> float:
        return self.position.unrealized_pnl if self.position else 0.0

    def get_equity(self, current_price: float = 0.0) -> float:
        """Balance + unrealised P&L."""
        if self.position and current_price:
            self.position.update_pnl(current_price)
        return self.balance + (self.position.unrealized_pnl if self.position else 0.0)

    def get_stats(self, current_price: float = 0.0) -> dict:
        """Return full paper-trading stats."""
        equity = self.get_equity(current_price)
        total_pnl = equity - self.initial_balance
        total_return_pct = (equity / self.initial_balance - 1) * 100

        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl <= 0]
        win_rate = len(wins) / len(self.trades) if self.trades else 0.0

        avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0.0
        profit_factor = (
            sum(t.pnl for t in wins) / abs(sum(t.pnl for t in losses))
            if losses and sum(t.pnl for t in losses) != 0
            else float("inf") if wins else 0.0
        )

        return {
            "equity": round(equity, 2),
            "balance": round(self.balance, 2),
            "total_pnl": round(total_pnl, 2),
            "total_return_pct": round(total_return_pct, 3),
            "total_trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 4),
            "avg_win_usdt": round(avg_win, 2),
            "avg_loss_usdt": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_pct": round(self.max_drawdown * 100, 2),
            "total_fees": round(self.total_fees, 2),
            "unrealized_pnl": round(self.get_unrealized_pnl(), 2),
            "has_position": self.position is not None,
            "position_side": self.position.side if self.position else None,
            "position_entry": self.position.entry_price if self.position else None,
        }

    # ------------------------------------------
    # Internal implementation
    # ------------------------------------------

    def _open(
        self,
        side: str,
        price: float,
        ml_signal: str,
        ml_confidence: float,
        brain_bias: str,
        brain_confidence: float,
    ) -> bool:
        if not self.can_trade():
            return False
        if price <= 0:
            return False

        size_usdt = self.balance * self.position_size_pct
        quantity = size_usdt / price

        # Entry commission
        fee = size_usdt * self.commission_pct
        self.balance -= fee
        self.total_fees += fee

        # Compute SL/TP depending on side
        if side == "LONG":
            sl = price * (1 - self.sl_pct)
            tp = price * (1 + self.tp_pct)
        else:
            sl = price * (1 + self.sl_pct)
            tp = price * (1 - self.tp_pct)

        self.position = Position(
            side=side,
            entry_price=price,
            quantity=quantity,
            size_usdt=size_usdt,
            stop_loss=sl,
            take_profit=tp,
            entry_ts=time.time(),
            ml_signal=ml_signal,
            ml_confidence=ml_confidence,
            brain_bias=brain_bias,
            brain_confidence=brain_confidence,
        )
        self._last_trade_ts = time.time()

        sl_dist = abs(price - sl) / price * 100
        tp_dist = abs(tp - price) / price * 100
        logger.warning(
            f"[UP] [PAPER] OPEN {side} | ${price:,.2f} | "
            f"${size_usdt:.0f} ({self.position_size_pct:.0%}) | "
            f"SL=${sl:,.2f}(-{sl_dist:.2f}%) TP=${tp:,.2f}(+{tp_dist:.2f}%) | "
            f"Brain:{brain_bias}({brain_confidence:.0%}) ML:{ml_signal}({ml_confidence:.0%})"
        )
        return True

    def _close(self, price: float, reason: str) -> Trade:
        pos = self.position
        assert pos is not None

        self._trade_id += 1

        # Gross P&L
        if pos.side == "LONG":
            pnl_gross = (price - pos.entry_price) * pos.quantity
        else:
            pnl_gross = (pos.entry_price - price) * pos.quantity

        # Exit commission
        fee = pos.size_usdt * self.commission_pct
        self.total_fees += fee
        pnl_net = pnl_gross - fee

        self.balance += pnl_net
        pnl_pct = pnl_net / pos.size_usdt * 100
        duration = time.time() - pos.entry_ts

        # Update max drawdown
        self.peak_balance = max(self.peak_balance, self.balance)
        dd = (self.peak_balance - self.balance) / self.peak_balance
        self.max_drawdown = max(self.max_drawdown, dd)

        trade = Trade(
            id=self._trade_id,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=price,
            quantity=pos.quantity,
            size_usdt=pos.size_usdt,
            pnl=round(pnl_net, 4),
            pnl_pct=round(pnl_pct, 4),
            reason=reason,
            entry_ts=pos.entry_ts,
            exit_ts=time.time(),
            duration_s=round(duration, 1),
            ml_signal=pos.ml_signal,
            ml_confidence=pos.ml_confidence,
            brain_bias=pos.brain_bias,
            brain_confidence=pos.brain_confidence,
        )
        self.trades.append(trade)
        self.position = None

        emoji = "[OK]" if pnl_net > 0 else "[FAIL]"
        logger.warning(
            f"{emoji} [PAPER] CLOSE {pos.side} | {reason} | "
            f"${pos.entry_price:,.2f} -> ${price:,.2f} | "
            f"PnL: ${pnl_net:+.2f} ({pnl_pct:+.2f}%) | "
            f"Balance: ${self.balance:,.2f} | "
            f"T={duration:.1f}s | Trades={len(self.trades)}"
        )
        return trade

    def print_summary(self, current_price: float = 0.0) -> None:
        """Print a full summary at the end of the session."""
        s = self.get_stats(current_price)
        trades = self.trades

        print("\n" + "=" * 65)
        print("[PAPER] MINIFENIX PAPER TRADING - Final summary")
        print("=" * 65)
        print(f"  Initial balance:   ${self.initial_balance:>10,.2f} USDT")
        print(f"  Final equity:      ${s['equity']:>10,.2f} USDT")
        print(f"  Total P&L:         ${s['total_pnl']:>+10.2f} USDT  ({s['total_return_pct']:+.2f}%)")
        print(f"  Fees paid:         ${s['total_fees']:>10.2f} USDT")
        print("-" * 65)
        print(f"  Trades:            {s['total_trades']:>10}")
        print(f"  Wins:              {s['wins']:>10}  ({s['win_rate']:.1%})")
        print(f"  Losses:            {s['losses']:>10}")
        print(f"  Avg win:           ${s['avg_win_usdt']:>+10.2f} USDT")
        print(f"  Avg loss:          ${s['avg_loss_usdt']:>+10.2f} USDT")
        print(f"  Profit Factor:     {s['profit_factor']:>10.2f}x")
        print(f"  Max Drawdown:      {s['max_drawdown_pct']:>10.2f}%")
        print("-" * 65)
        if trades:
            print("  Last 5 trades:")
            for t in trades[-5:]:
                emoji = "[OK]" if t.pnl > 0 else "[FAIL]"
                print(
                    f"   {emoji} #{t.id} {t.side:<5} "
                    f"${t.entry_price:,.0f}->${t.exit_price:,.0f} "
                    f"{t.pnl:+.2f}$ ({t.pnl_pct:+.2f}%) [{t.reason}] {t.duration_s:.0f}s"
                )
        print("=" * 65 + "\n")
