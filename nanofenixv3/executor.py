"""
NanoFenix v3 — Paper Trading Executor.

Enhanced version of V2's PatientExecutor with:
- Dynamic position sizing (Kelly-inspired)
- Better trailing stop (activation at +8 bps, tighter after +20 bps)
- Regime-aware exit strategy
- Fee accounting with realistic Binance rates
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger("NanoFenixV3.Executor")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


# ── Configuration ──
POSITION_PCT = _env_float("NANOFENIXV3_POSITION_PCT", 0.06)
TAKER_FEE = 0.00035  # 0.035% per side (BNB discount)
MAX_HOLD_BARS = 1800  # 30 min max hold (safety net only — trailing is primary exit)
COOLDOWN_BARS = 30  # 30 seconds between trades (V3.5: was 60)
COOLDOWN_AFTER_LOSS = 60  # 60 seconds after a loss (V3.5: was 120)
STOP_LOSS_PCT = 0.0040  # -40 bps stop loss
TAKE_PROFIT_PCT = 0.0060  # +60 bps take profit
TRAILING_ACTIVATION = (
    0.0005  # Activate trailing after +5 bps profit (V3.5.2: immediate was too tight)
)
TRAILING_DISTANCE = 0.0010  # 10 bps trail (V3.5.2: wider for LOW vol, was 6bps)
TIGHT_TRAILING_AFTER = 0.0020  # Tighter trailing after +20 bps
TIGHT_TRAILING_DIST = 0.0005  # 5 bps tight trail (V3.5.2: was 3bps)
EARLY_EXIT_BARS = 300  # Check early exit after 5 min (V3.5.4: was 120=2min, too early)
EARLY_EXIT_THRESHOLD = -0.0025  # Cut at -25 bps after 5 min (V3.5.4: was -15bps)
MIN_DIR_ACC = 0.50  # Minimum rolling direction accuracy (V3.5.2: raised back to 0.50, was 0.45)
MIN_TRAILING_NET_PCT = _env_float("NANOFENIXV3_MIN_TRAILING_NET_PCT", 0.0002)


@dataclass
class Position:
    direction: str  # "LONG" | "SHORT"
    entry_price: float
    quantity: float
    entry_bar: int
    peak_price: float = field(init=False)
    trailing_active: bool = field(init=False, default=False)
    trailing_tight: bool = field(init=False, default=False)

    def __post_init__(self):
        self.peak_price = self.entry_price

    def unrealized_pnl(self, price: float) -> float:
        if self.direction == "LONG":
            return (price - self.entry_price) * self.quantity
        return (self.entry_price - price) * self.quantity

    def unrealized_pct(self, price: float) -> float:
        if self.entry_price <= 0:
            return 0.0
        if self.direction == "LONG":
            return (price - self.entry_price) / self.entry_price
        return (self.entry_price - price) / self.entry_price


@dataclass
class TradeRecord:
    direction: str
    entry_price: float
    exit_price: float
    quantity: float
    entry_bar: int
    exit_bar: int
    pnl_gross: float
    pnl_net: float
    close_reason: str
    win: bool


class PaperExecutor:
    """Paper trading executor with position management."""

    def __init__(self, balance: float = 10_000.0):
        self._balance = balance
        self._initial_balance = balance
        self._position: Position | None = None
        self._trades: list[TradeRecord] = []
        self._wins = 0
        self._losses = 0
        self._bar_idx = 0
        self._cooldown_remaining = 0
        self._consecutive_losses = 0
        self._daily_pnl = 0.0

    @property
    def balance(self) -> float:
        return self._balance

    @property
    def position(self) -> Position | None:
        return self._position

    @property
    def n_trades(self) -> int:
        return len(self._trades)

    @property
    def win_rate(self) -> float:
        total = self._wins + self._losses
        return self._wins / total * 100 if total > 0 else 0.0

    @property
    def total_pnl(self) -> float:
        return self._balance - self._initial_balance

    @property
    def total_pnl_pct(self) -> float:
        return self.total_pnl / self._initial_balance * 100

    def on_bar(
        self,
        bar_idx: int,
        close: float,
        signal: str,
        pred_bps: float,
        ema_trend: float,
        direction_accuracy: float,
        direction_samples: int = 0,
        calibration_samples: int = 0,
        companion_ready: bool = False,
        val_accuracy: float = 0.0,
        range_bps: float = 0.0,
        buy_vol_ratio: float = 0.5,
        volatility_state: str = "MEDIUM",
    ) -> str | None:
        """Process one bar. Returns close reason if trade closed, None otherwise."""
        self._bar_idx = bar_idx
        close_reason = None

        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1

        # Manage existing position
        if self._position is not None:
            close_reason = self._manage(close, signal, direction_accuracy, volatility_state)

        # Attempt new trade
        if self._position is None and close_reason is None:
            self._maybe_open(
                close,
                signal,
                pred_bps,
                ema_trend,
                direction_accuracy,
                direction_samples,
                calibration_samples,
                companion_ready,
                val_accuracy,
                range_bps,
                buy_vol_ratio,
                volatility_state,
            )

        return close_reason

    def unrealized_pnl(self, price: float) -> float:
        if self._position is None:
            return 0.0
        return self._position.unrealized_pnl(price)

    @staticmethod
    def _estimated_net_pct(pos: Position, exit_price: float) -> float:
        notional = pos.entry_price * pos.quantity
        if notional <= 0:
            return 0.0
        if pos.direction == "LONG":
            pnl_gross = (exit_price - pos.entry_price) * pos.quantity
        else:
            pnl_gross = (pos.entry_price - exit_price) * pos.quantity
        fees = notional * TAKER_FEE + pos.quantity * exit_price * TAKER_FEE
        return (pnl_gross - fees) / notional

    def _manage(
        self, close: float, signal: str, dir_acc: float, volatility_state: str = "MEDIUM"
    ) -> str | None:
        pos = self._position
        assert pos is not None
        bars_held = self._bar_idx - pos.entry_bar
        pct = pos.unrealized_pct(close)

        # Update peak price
        if pos.direction == "LONG":
            pos.peak_price = max(pos.peak_price, close)
        else:
            pos.peak_price = min(pos.peak_price, close)

        # ① Stop loss
        if pct <= -STOP_LOSS_PCT:
            return self._close(close, "STOP_LOSS")

        # ② Take profit
        if pct >= TAKE_PROFIT_PCT:
            return self._close(close, "TAKE_PROFIT")

        # ③ Early exit: after 60s still losing
        if bars_held >= EARLY_EXIT_BARS and pct <= EARLY_EXIT_THRESHOLD:
            return self._close(close, "EARLY_EXIT")

        # ④ Dynamic reversal exit: losing significantly + ML strongly disagrees
        if (
            pct < -0.0010  # -10 bps threshold (V3.5.2: was -3bps, too aggressive)
            and (
                (pos.direction == "LONG" and signal == "SHORT")
                or (pos.direction == "SHORT" and signal == "LONG")
            )
            and dir_acc >= MIN_DIR_ACC
            and bars_held >= 60
        ):  # Only after 60s (V3.5.2: avoid premature reversal)
            return self._close(close, "REVERSAL_EXIT")

        # ⑤ Trailing stop (dynamic based on volatility)
        if pos.direction == "LONG":
            peak_pct = (pos.peak_price - pos.entry_price) / pos.entry_price
        else:
            peak_pct = (pos.entry_price - pos.peak_price) / pos.entry_price

        # Dynamic trailing: wider in LOW vol, tighter in HIGH vol
        if volatility_state == "LOW":
            effective_trail_dist = TRAILING_DISTANCE * 2.0  # 20bps in LOW vol
            effective_tight_dist = TIGHT_TRAILING_DIST * 2.0  # 10bps in LOW vol
            effective_activation = TRAILING_ACTIVATION * 2.0  # 10bps activation in LOW vol
            effective_tight_after = TIGHT_TRAILING_AFTER * 1.5  # 30bps tight after in LOW vol
        elif volatility_state == "HIGH":
            effective_trail_dist = TRAILING_DISTANCE  # 10bps in HIGH vol
            effective_tight_dist = TIGHT_TRAILING_DIST  # 5bps in HIGH vol
            effective_activation = TRAILING_ACTIVATION  # 5bps activation in HIGH vol
            effective_tight_after = TIGHT_TRAILING_AFTER  # 20bps tight after in HIGH vol
        else:  # MEDIUM
            effective_trail_dist = TRAILING_DISTANCE * 1.5  # 15bps in MEDIUM vol
            effective_tight_dist = TIGHT_TRAILING_DIST * 1.5  # 7.5bps in MEDIUM vol
            effective_activation = TRAILING_ACTIVATION * 1.5  # 7.5bps activation in MEDIUM vol
            effective_tight_after = TIGHT_TRAILING_AFTER * 1.25  # 25bps tight after in MEDIUM vol

        # Activate trailing
        fee_aware_activation = max(
            effective_activation,
            (TAKER_FEE * 2.0) + max(0.0, MIN_TRAILING_NET_PCT),
        )
        if peak_pct >= fee_aware_activation and not pos.trailing_active:
            pos.trailing_active = True

        # Tighten trailing after bigger profit
        if peak_pct >= effective_tight_after and not pos.trailing_tight:
            pos.trailing_tight = True

        if pos.trailing_active:
            trail_dist = effective_tight_dist if pos.trailing_tight else effective_trail_dist
            if pos.direction == "LONG":
                trail_exit = pos.peak_price * (1 - trail_dist)
                if (
                    close <= trail_exit
                    and self._estimated_net_pct(pos, close) >= MIN_TRAILING_NET_PCT
                ):
                    return self._close(close, "TRAILING_STOP")
            else:
                trail_exit = pos.peak_price * (1 + trail_dist)
                if (
                    close >= trail_exit
                    and self._estimated_net_pct(pos, close) >= MIN_TRAILING_NET_PCT
                ):
                    return self._close(close, "TRAILING_STOP")

        # ⑥ Time limit
        if bars_held >= MAX_HOLD_BARS:
            return self._close(close, "TIME_EXIT")

        return None

    def _maybe_open(
        self,
        close: float,
        signal: str,
        pred_bps: float,
        ema_trend: float,
        dir_acc: float,
        dir_samples: int,
        calibration_samples: int,
        companion_ready: bool,
        val_acc: float,
        range_bps: float,
        buy_vol_ratio: float,
        volatility_state: str,
    ):
        if self._cooldown_remaining > 0:
            return
        if signal == "HOLD":
            return
        if not companion_ready:
            return

        # Regime filter: as companion, we trust Fenix's regime assessment
        # Only skip truly dead markets (range < 3bps)
        if range_bps > 0 and range_bps < 3.0 and volatility_state == "LOW":
            return

        # Order flow confirmation (V3.5: more permissive)
        if signal == "LONG" and buy_vol_ratio < 0.35:
            return
        if signal == "SHORT" and buy_vol_ratio > 0.65:
            return

        # Trend alignment (don't fight strong trend)
        if signal == "LONG" and ema_trend < -8.0:
            return
        if signal == "SHORT" and ema_trend > 8.0:
            return

        # Model quality gate
        if val_acc > 0 and val_acc < 0.48:
            return

        # Direction accuracy gate (after warmup)
        if dir_samples >= 20 and dir_acc < MIN_DIR_ACC:
            return
        if calibration_samples >= 20 and dir_acc < MIN_DIR_ACC:
            return

        # Daily loss limit: -3%
        if self._daily_pnl / self._initial_balance < -0.03:
            return

        # Position size — Kelly-inspired
        # f* = (p*b - q) / b, where: p=win prob, q=1-p, b=win/loss ratio
        if len(self._trades) >= 5:
            p = self.win_rate / 100
            avg_win = np.mean([t.pnl_net for t in self._trades if t.win]) if self._wins > 0 else 1.0
            avg_loss = (
                abs(np.mean([t.pnl_net for t in self._trades if not t.win]))
                if self._losses > 0
                else 1.0
            )
            b = avg_win / max(avg_loss, 1e-8)
            kelly = max(0, (p * b - (1 - p)) / b)
            kelly = min(kelly, 0.25)  # Cap at 25%
            pos_pct = min(POSITION_PCT * (1 + kelly), POSITION_PCT * 1.5)
        else:
            pos_pct = POSITION_PCT

        notional = self._balance * pos_pct
        qty = notional / close

        self._position = Position(
            direction=signal,
            entry_price=close,
            quantity=qty,
            entry_bar=self._bar_idx,
        )
        logger.info(
            f"📈 OPEN {signal} @ ${close:,.2f} | "
            f"Pred: {pred_bps:+.1f}bps | EMA: {ema_trend:+.1f} | "
            f"BuyR: {buy_vol_ratio:.2f} | Range: {range_bps:.0f}bps | "
            f"Notional: ${notional:.2f}"
        )

    def _close(self, price: float, reason: str) -> str:
        pos = self._position
        assert pos is not None

        if pos.direction == "LONG":
            pnl_gross = (price - pos.entry_price) * pos.quantity
        else:
            pnl_gross = (pos.entry_price - price) * pos.quantity

        notional = pos.entry_price * pos.quantity
        fees = notional * TAKER_FEE + pos.quantity * price * TAKER_FEE
        pnl_net = pnl_gross - fees
        self._balance += pnl_net
        self._daily_pnl += pnl_net
        win = pnl_net > 0

        if win:
            self._wins += 1
            self._consecutive_losses = 0
            self._cooldown_remaining = COOLDOWN_BARS
        else:
            self._losses += 1
            self._consecutive_losses += 1
            multiplier = min(3, self._consecutive_losses)
            self._cooldown_remaining = COOLDOWN_AFTER_LOSS * multiplier

        bars_held = self._bar_idx - pos.entry_bar
        pnl_pct = pnl_net / (pos.entry_price * pos.quantity) * 100

        record = TradeRecord(
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=price,
            quantity=pos.quantity,
            entry_bar=pos.entry_bar,
            exit_bar=self._bar_idx,
            pnl_gross=pnl_gross,
            pnl_net=pnl_net,
            close_reason=reason,
            win=win,
        )
        self._trades.append(record)
        self._position = None

        emoji = "✅" if win else "❌"
        logger.info(
            f"{emoji} CLOSE {pos.direction} @ ${price:,.2f} | "
            f"Reason: {reason} | P&L: ${pnl_net:+.4f} ({pnl_pct:+.3f}%) | "
            f"Held: {bars_held}s | Balance: ${self._balance:,.2f} | "
            f"WR: {self.win_rate:.0f}% ({self._wins}W/{self._losses}L)"
        )
        return reason
