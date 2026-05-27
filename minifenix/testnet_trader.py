"""
MiniFenix Testnet Trader.

Executes REAL orders on Binance Futures Testnet using FenixAI's
OrderExecutor. Same interface as PaperTrader for drop-in replacement.

Flow:
  open_long / open_short  -> asyncio.create_task -> execute_market_order (real)
  update(price)           -> Local SL/TP tracking (same as PaperTrader)
  force_close             -> reduce_only market order on Binance
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("MiniFenix.Testnet")

# --- Env helpers --------------------------------------------------------------

def _load_testnet_keys() -> None:
    """
    Load .env and map BINANCE_TESTNET_API_KEY_1 -> BINANCE_TESTNET_API_KEY
    (FenixAI's get_binance_service expects the name without the _1 suffix).
    """
    try:
        from dotenv import load_dotenv
        # Look for .env in several locations
        possible_paths = [
            Path(__file__).parent.parent / ".env",  # FenixAI/.env
            Path.cwd() / ".env",                      # Current directory
            Path.home() / ".env",                     # Home directory
        ]
        for env_path in possible_paths:
            if env_path.exists():
                load_dotenv(dotenv_path=env_path, override=True)
                logger.info(f"[FILE] Loading .env from: {env_path}")
                break
    except ImportError:
        logger.warning("[WARN] python-dotenv not installed, using existing environment variables")

    # Try the _1 suffix first, fall back to the unsuffixed name
    key = os.getenv("BINANCE_TESTNET_API_KEY_1") or os.getenv("BINANCE_TESTNET_API_KEY")
    secret = os.getenv("BINANCE_TESTNET_API_SECRET_1") or os.getenv("BINANCE_TESTNET_API_SECRET")

    if key:
        os.environ["BINANCE_TESTNET_API_KEY"] = key
        logger.info(f"[OK] BINANCE_TESTNET_API_KEY loaded (len={len(key)})")
    else:
        logger.warning("[WARN] BINANCE_TESTNET_API_KEY not found in .env")

    if secret:
        os.environ["BINANCE_TESTNET_API_SECRET"] = secret
        logger.info(f"[OK] BINANCE_TESTNET_API_SECRET loaded (len={len(secret)})")
    else:
        logger.warning("[WARN] BINANCE_TESTNET_API_SECRET not found in .env")


# --- Dataclasses (identical to PaperTrader) -----------------------------------

@dataclass
class Trade:
    id: int
    side: str                   # BUY | SELL (position direction)
    entry_price: float
    exit_price: float = 0.0
    quantity: float = 0.0       # in BTC
    size_usdt: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    reason: str = ""            # "TP" | "SL" | "SIGNAL" | "REGIME_CHANGE"
    entry_ts: float = 0.0
    exit_ts: float = 0.0
    duration_s: float = 0.0
    ml_signal: str = ""
    ml_confidence: float = 0.0
    brain_bias: str = ""
    brain_confidence: float = 0.0
    order_id: Optional[int] = None   # Real Binance order ID


@dataclass
class Position:
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
    order_id: Optional[int] = None
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


# --- TestnetTrader ------------------------------------------------------------

class TestnetTrader:
    """
    Drop-in replacement for PaperTrader that executes on Binance Futures Testnet.

    - Same public interface: can_trade(), open_long(), open_short(),
      update(), force_close(), get_stats(), print_summary()
    - Orders are dispatched via asyncio.create_task() so they do not
      block the fast loop (microseconds).
    - SL/TP are monitored locally AND sent as protective orders to
      Binance (backstop).
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        initial_balance: float = 10_000.0,
        position_size_pct: float = 0.05,
        sl_pct: float = 0.003,
        tp_pct: float = 0.006,
        commission_pct: float = 0.0004,
        cooldown_seconds: float = 10.0,
    ):
        _load_testnet_keys()

        self.symbol = symbol.upper()
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.position_size_pct = position_size_pct
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct
        self.commission_pct = commission_pct
        self.cooldown_seconds = cooldown_seconds

        # Position state
        self.position: Optional[Position] = None
        self.trades: list[Trade] = []
        self._trade_id = 0
        self._last_trade_ts = 0.0
        self._pending_order = False   # anti double-entry lock

        # Metrics
        self.total_fees = 0.0
        self.peak_balance = initial_balance
        self.max_drawdown = 0.0

        # Real executor (imported from FenixAI)
        self._executor = None   # lazy init to avoid circular import in tests

        logger.info(
            f"[LIVE] [TESTNET] Starting live trader | {symbol} | "
            f"Balance: ${initial_balance:,.0f} | "
            f"Size: {position_size_pct:.0%} | SL: {sl_pct:.2%} | TP: {tp_pct:.2%}"
        )

    # -- Lazy executor ---------------------------------------------------------

    def _get_executor(self):
        if self._executor is None:
            try:
                from src.trading.executor import OrderExecutor
                self._executor = OrderExecutor(symbol=self.symbol, testnet=True)
                logger.info(f"[OK] [TESTNET] OrderExecutor created for {self.symbol} (testnet=True)")
            except Exception as e:
                logger.error(f"[FAIL] [TESTNET] Could not create OrderExecutor: {e}")
                raise
        return self._executor

    # -- Public interface ------------------------------------------------------

    def can_trade(self) -> bool:
        if self._pending_order:
            return False
        if self.position is not None:
            return False
        if time.time() - self._last_trade_ts < self.cooldown_seconds:
            return False
        return True

    def open_long(
        self,
        price: float,
        ml_signal: str = "",
        ml_confidence: float = 0.0,
        brain_bias: str = "",
        brain_confidence: float = 0.0,
    ) -> None:
        if not self.can_trade():
            return
        self._pending_order = True
        size_usdt = self.balance * self.position_size_pct
        quantity = size_usdt / price
        sl = round(price * (1 - self.sl_pct), 2)
        tp = round(price * (1 + self.tp_pct), 2)
        logger.info(
            f"[LONG] [TESTNET] ORDER LONG | qty={quantity:.5f} BTC | "
            f"entry~${price:,.2f} | SL=${sl:,.2f} | TP=${tp:,.2f}"
        )
        asyncio.create_task(
            self._execute_entry(
                side="BUY",
                quantity=quantity,
                sl=sl,
                tp=tp,
                expected_price=price,
                ml_signal=ml_signal,
                ml_confidence=ml_confidence,
                brain_bias=brain_bias,
                brain_confidence=brain_confidence,
            )
        )

    def open_short(
        self,
        price: float,
        ml_signal: str = "",
        ml_confidence: float = 0.0,
        brain_bias: str = "",
        brain_confidence: float = 0.0,
    ) -> None:
        if not self.can_trade():
            return
        self._pending_order = True
        size_usdt = self.balance * self.position_size_pct
        quantity = size_usdt / price
        sl = round(price * (1 + self.sl_pct), 2)
        tp = round(price * (1 - self.tp_pct), 2)
        logger.info(
            f"[SHORT] [TESTNET] ORDER SHORT | qty={quantity:.5f} BTC | "
            f"entry~${price:,.2f} | SL=${sl:,.2f} | TP=${tp:,.2f}"
        )
        asyncio.create_task(
            self._execute_entry(
                side="SELL",
                quantity=quantity,
                sl=sl,
                tp=tp,
                expected_price=price,
                ml_signal=ml_signal,
                ml_confidence=ml_confidence,
                brain_bias=brain_bias,
                brain_confidence=brain_confidence,
            )
        )

    def update(self, current_price: float) -> Optional[Trade]:
        """
        Monitor SL/TP locally using the WebSocket price.
        When triggered, send a reduce_only close to Binance.
        """
        if self.position is None:
            return None

        self.position.update_pnl(current_price)

        # Check SL
        if self.position.hit_stop(current_price):
            logger.warning(
                f"[STOP] [TESTNET] STOP LOSS hit @ ${current_price:,.2f} | "
                f"uPnL=${self.position.unrealized_pnl:+.2f}"
            )
            return self.force_close(current_price, reason="SL")

        # Check TP
        if self.position.hit_tp(current_price):
            logger.info(
                f"[TARGET] [TESTNET] TAKE PROFIT hit @ ${current_price:,.2f} | "
                f"uPnL=${self.position.unrealized_pnl:+.2f}"
            )
            return self.force_close(current_price, reason="TP")

        return None

    def force_close(self, price: float, reason: str = "SIGNAL") -> Optional[Trade]:
        """
        Close the current position by sending a reduce_only order to Binance.
        Returns a preview of the trade (the final record happens once the close is confirmed).
        """
        if self.position is None:
            return None

        pos = self.position
        close_side = "SELL" if pos.side == "LONG" else "BUY"
        self.position = None   # optimistic: assume it will close
        self._pending_order = True

        asyncio.create_task(
            self._execute_close(close_side=close_side, pos=pos, price=price, reason=reason)
        )

        # Preview for immediate caller logging; does not mutate balance or metrics.
        return self._build_trade_preview(pos=pos, exit_price=price, reason=reason)

    # -- Real async execution --------------------------------------------------

    async def _execute_entry(
        self,
        side: str,
        quantity: float,
        sl: float,
        tp: float,
        expected_price: float,
        ml_signal: str,
        ml_confidence: float,
        brain_bias: str,
        brain_confidence: float,
    ) -> None:
        try:
            executor = self._get_executor()
            result = await executor.execute_market_order(
                side=side,
                quantity=quantity,
                stop_loss=sl,
                take_profit=tp,
            )

            if result.success:
                entry_price = result.entry_price if result.entry_price else expected_price
                executed_qty = result.executed_qty if result.executed_qty else quantity
                size_usdt = entry_price * executed_qty
                entry_fee = size_usdt * self.commission_pct
                self.balance -= entry_fee
                self.total_fees += entry_fee

                pos_side = "LONG" if side == "BUY" else "SHORT"
                # Recompute SL/TP using the real fill price
                real_sl = round(entry_price * (1 - self.sl_pct), 2) if pos_side == "LONG" \
                          else round(entry_price * (1 + self.sl_pct), 2)
                real_tp = round(entry_price * (1 + self.tp_pct), 2) if pos_side == "LONG" \
                          else round(entry_price * (1 - self.tp_pct), 2)

                self.position = Position(
                    side=pos_side,
                    entry_price=entry_price,
                    quantity=executed_qty,
                    size_usdt=size_usdt,
                    stop_loss=real_sl,
                    take_profit=real_tp,
                    ml_signal=ml_signal,
                    ml_confidence=ml_confidence,
                    brain_bias=brain_bias,
                    brain_confidence=brain_confidence,
                    order_id=result.order_id,
                    entry_fee=entry_fee,
                )
                self._last_trade_ts = time.time()
                logger.info(
                    f"[OK] [TESTNET] FILLED {pos_side} {executed_qty:.5f} BTC @ "
                    f"${entry_price:,.2f} | SL=${real_sl:,.2f} TP=${real_tp:,.2f} | "
                    f"Fee=${entry_fee:.4f} | OrderID={result.order_id}"
                )
            else:
                logger.error(
                    f"[FAIL] [TESTNET] Order failed ({side}): {result.status} - {result.message}"
                )

        except Exception as e:
            logger.error(f"[FAIL] [TESTNET] Exception in _execute_entry: {e}", exc_info=True)
        finally:
            self._pending_order = False

    async def _execute_close(
        self,
        close_side: str,
        pos: Position,
        price: float,
        reason: str,
    ) -> None:
        try:
            executor = self._get_executor()
            result = await executor.execute_market_order(
                side=close_side,
                quantity=pos.quantity,
                reduce_only=True,
            )

            exit_price = result.entry_price if (result.success and result.entry_price) else price

            if result.success:
                logger.info(
                    f"[OK] [TESTNET] CLOSED {pos.side} @ ${exit_price:,.2f} | "
                    f"Reason={reason} | OrderID={result.order_id}"
                )
            else:
                # Binance may have closed it already via SL/TP - not a critical error
                logger.warning(
                    f"[WARN] [TESTNET] Close returned error ({result.status}): {result.message}. "
                    f"Possibly already closed by a Binance stop."
                )

            trade = self._record_trade(pos=pos, exit_price=exit_price, reason=reason)

            pnl_emoji = "[OK]" if trade.pnl >= 0 else "[FAIL]"
            logger.warning(
                f"{pnl_emoji} [TESTNET] TRADE #{trade.id} closed | "
                f"{trade.side} {trade.quantity:.5f} BTC | "
                f"${trade.entry_price:,.2f}->${trade.exit_price:,.2f} | "
                f"P&L=${trade.pnl:+.4f} ({trade.pnl_pct:+.2%}) | "
                f"Reason={trade.reason}"
            )

        except Exception as e:
            logger.error(f"[FAIL] [TESTNET] Exception in _execute_close: {e}", exc_info=True)
        finally:
            self._pending_order = False

    # -- Record helpers --------------------------------------------------------

    def _build_trade_preview(self, pos: Position, exit_price: float, reason: str) -> Trade:
        """Estimated trade for immediate logging (no persistence)."""
        if pos.side == "LONG":
            pnl_preview = (exit_price - pos.entry_price) * pos.quantity
        else:
            pnl_preview = (pos.entry_price - exit_price) * pos.quantity

        pnl_pct = pnl_preview / pos.size_usdt if pos.size_usdt > 0 else 0.0

        return Trade(
            id=self._trade_id + 1,
            side="BUY" if pos.side == "LONG" else "SELL",
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=pos.quantity,
            size_usdt=pos.size_usdt,
            pnl=pnl_preview,
            pnl_pct=pnl_pct,
            reason=reason,
            entry_ts=pos.entry_ts,
            exit_ts=time.time(),
            duration_s=time.time() - pos.entry_ts,
            ml_signal=pos.ml_signal,
            ml_confidence=pos.ml_confidence,
            brain_bias=pos.brain_bias,
            brain_confidence=pos.brain_confidence,
            order_id=pos.order_id,
        )

    def _record_trade(self, pos: Position, exit_price: float, reason: str) -> Trade:
        self._trade_id += 1
        fee_exit = (pos.quantity * exit_price) * self.commission_pct
        self.total_fees += fee_exit

        if pos.side == "LONG":
            pnl_gross = (exit_price - pos.entry_price) * pos.quantity
        else:
            pnl_gross = (pos.entry_price - exit_price) * pos.quantity

        # Net round-trip PnL (includes entry + exit fee)
        pnl = pnl_gross - pos.entry_fee - fee_exit

        # On close we only credit gross PnL minus the exit fee.
        # The entry fee was already debited when the position opened.
        self.balance += pnl_gross - fee_exit

        pnl_pct = pnl / pos.size_usdt if pos.size_usdt > 0 else 0.0
        if pos.side == "LONG":
            side = "BUY"
        else:
            side = "SELL"
        now = time.time()

        trade = Trade(
            id=self._trade_id,
            side=side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=pos.quantity,
            size_usdt=pos.size_usdt,
            pnl=pnl,
            pnl_pct=pnl_pct,
            reason=reason,
            entry_ts=pos.entry_ts,
            exit_ts=now,
            duration_s=now - pos.entry_ts,
            ml_signal=pos.ml_signal,
            ml_confidence=pos.ml_confidence,
            brain_bias=pos.brain_bias,
            brain_confidence=pos.brain_confidence,
            order_id=pos.order_id,
        )
        self.trades.append(trade)
        self._update_metrics(pnl)
        return trade

    def _update_metrics(self, pnl: float) -> None:
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        dd = (self.peak_balance - self.balance) / self.peak_balance * 100
        if dd > self.max_drawdown:
            self.max_drawdown = dd

    # -- Stats -----------------------------------------------------------------

    def get_stats(self, current_price: float = 0.0) -> dict:
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
        line = "-" * 56
        print(f"\n{line}")
        print(f"  [LIVE] TESTNET TRADER - Final summary")
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
            print("\n  Last 5 trades:")
            for t in self.trades[-5:]:
                emoji = "[OK]" if t.pnl >= 0 else "[FAIL]"
                dur = f"{t.duration_s:.0f}s"
                print(
                    f"  {emoji} #{t.id:3d} {t.side:<4s} "
                    f"${t.entry_price:,.2f}->${t.exit_price:,.2f} "
                    f"P&L=${t.pnl:+.4f} ({t.pnl_pct:+.2%}) "
                    f"[{t.reason}] {dur}"
                )
        print(line)
