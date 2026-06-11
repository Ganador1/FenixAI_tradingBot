"""Runtime Risk Manager - Circuit Breakers activo.

Implementación completa del sistema de protección de capital.
Evalúa riesgo en tiempo real antes de cada trade.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from src.risk.circuit_breaker_alerts import CircuitBreakerNotifier, get_circuit_breaker_notifier

    NOTIFIER_AVAILABLE = True
except ImportError:
    NOTIFIER_AVAILABLE = False
    get_circuit_breaker_notifier = None

try:
    from src.risk.runtime_feedback import RiskFeedbackLoopConfig, RiskFeedbackStatus
except ImportError:
    # Fallback definitions
    from dataclasses import dataclass as _dc

    @_dc
    class RiskFeedbackLoopConfig:
        max_drawdown_pct: float = 10.0
        max_daily_loss_pct: float = 5.0
        max_consecutive_losses: int = 5
        hot_streak_threshold: int = 3

    @_dc
    class RiskFeedbackStatus:
        mode: str = "NORMAL"
        risk_bias: float = 1.0


logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """Registro de trade para análisis de riesgo."""

    trade_id: str
    timestamp: datetime
    symbol: str
    decision: str
    entry_price: float
    exit_price: float | None = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    success: bool = False
    size: float = 0.0


class RuntimeRiskManager:
    """
    Gestor de riesgo runtime activo.

    Evalúa protecciones antes de cada trade:
    - Drawdown protection
    - Daily loss limit
    - Consecutive losses streak
    - Hot streak detection
    """

    def __init__(
        self, config: RiskFeedbackLoopConfig | None = None, storage_path: str | None = None
    ):
        self.config = config or RiskFeedbackLoopConfig()
        resolved_storage_path = storage_path or os.getenv(
            "FENIX_RISK_MANAGER_STORAGE_PATH",
            "logs/risk_manager.jsonl",
        )
        self.storage_path = Path(resolved_storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        # Notificador
        if NOTIFIER_AVAILABLE and get_circuit_breaker_notifier:
            self.notifier = get_circuit_breaker_notifier()
        else:
            self.notifier = None

        # Estado actual
        self.current_status = RiskFeedbackStatus(mode="NORMAL", risk_bias=1.0)
        self._last_evaluation: datetime | None = None

        # Historial de trades para métricas
        self._trades: deque[TradeRecord] = deque(maxlen=100)

        # Métricas del día
        self._daily_pnl: float = 0.0
        self._daily_start_balance: float | None = None
        self._last_trading_day: str | None = None
        self._max_exposure_pct: float = 0.50
        self._exposure_leverage_multiplier: float = 1.0
        self._open_positions: dict[str, dict[str, Any]] = {}
        self._active_trades: dict[str, TradeRecord] = {}

        # Métricas de drawdown
        self._peak_balance: float = 0.0
        self._current_balance: float = 0.0
        # All-time high-water mark: only ever increases (except explicit re-anchor).
        # Protects against drawdown erasure via daily resets or process restarts.
        self._all_time_peak: float = 0.0

        # Cooldown tracking
        self._cooldown_start: datetime | None = None

        # Cargar estado previo si existe
        self._load_state()

    def _load_state(self) -> None:
        """Carga estado previo del día si existe."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path) as f:
                    # Append-only JSONL: only the last line matters.
                    last_raw = deque(f, maxlen=1)
                if last_raw:
                    last_line = json.loads(last_raw[0])
                    self._peak_balance = last_line.get("peak_balance", 0.0)
                    self._all_time_peak = max(
                        last_line.get("all_time_peak", 0.0), self._peak_balance
                    )
                    self._current_balance = last_line.get("current_balance", self._peak_balance)
                    self._last_trading_day = last_line.get("trading_day")
                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    if self._last_trading_day == today:
                        # Same trading day: restore daily metrics so the daily
                        # loss limit survives a process restart.
                        self._daily_pnl = last_line.get("daily_pnl", 0.0)
                        self._daily_start_balance = last_line.get("daily_start_balance")
                    else:
                        self._daily_pnl = 0.0
                        self._daily_start_balance = None
                    if self._current_balance <= 0 and self._peak_balance > 0:
                        self._current_balance = self._peak_balance
            except Exception as e:
                logger.warning(f"Could not load risk state: {e}")

    def _save_state(self) -> None:
        """Persiste estado actual."""
        trading_day = self._last_trading_day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trading_day": trading_day,
            "daily_pnl": self._daily_pnl,
            "daily_start_balance": self._daily_start_balance,
            "peak_balance": self._peak_balance,
            "all_time_peak": self._all_time_peak,
            "current_balance": self._current_balance,
            "current_mode": self.current_status.mode,
            "risk_bias": self.current_status.risk_bias,
        }
        try:
            with open(self.storage_path, "a") as f:
                f.write(json.dumps(state) + "\n")
        except Exception as e:
            logger.warning(f"Could not save risk state: {e}")

    def update_balance(self, balance: float) -> None:
        """Actualiza balance y recalcula drawdown."""
        balance = float(balance)

        # Re-anchor stale persisted baselines to the first real balance observed for this runtime.
        # This avoids phantom drawdown when a previous session stored a much larger peak balance.
        if (
            balance > 0
            and not self._active_trades
            and not self._trades
            and (
                self._current_balance <= 0
                or self._peak_balance <= 0
                or self._peak_balance > (balance * 1.5)
                or self._current_balance > (balance * 1.5)
            )
        ):
            self._current_balance = balance
            self._peak_balance = balance
            self._daily_pnl = 0.0
            self._last_trading_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._daily_start_balance is None or self._daily_start_balance > (balance * 1.5):
                self._daily_start_balance = balance
            # The all-time peak is NOT cleared automatically: a >33% drop followed
            # by a restart must not erase drawdown protection. Re-anchor it only
            # with explicit operator consent.
            if self._all_time_peak > (balance * 1.5):
                if os.getenv("FENIX_RISK_ALLOW_REANCHOR", "0") == "1":
                    self._all_time_peak = balance
                    logger.warning(
                        "RiskManager all-time peak re-anchored to %.2f by operator request "
                        "(FENIX_RISK_ALLOW_REANCHOR=1)",
                        balance,
                    )
                else:
                    logger.error(
                        "RiskManager: balance %.2f is far below all-time peak %.2f. "
                        "Drawdown protection stays active. Set FENIX_RISK_ALLOW_REANCHOR=1 "
                        "(or delete %s) if this is an intentional balance change.",
                        balance,
                        self._all_time_peak,
                        self.storage_path,
                    )
            logger.info(
                "RiskManager balance baseline re-anchored to %.2f (peak/current reset, daily pnl cleared)",
                balance,
            )

        self._current_balance = balance

        # Reset diario si es nuevo día
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._last_trading_day != today:
            self._daily_pnl = 0.0
            self._daily_start_balance = balance
            self._last_trading_day = today
            # Intraday peak re-anchors daily; the all-time peak never resets here.
            self._peak_balance = balance
            logger.info(f"New trading day: {today}, reset daily PnL")

        # Actualizar peak
        if balance > self._peak_balance:
            self._peak_balance = balance
        if balance > self._all_time_peak:
            self._all_time_peak = balance

    def set_max_exposure_pct(self, max_exposure_pct: float) -> None:
        self._max_exposure_pct = max(0.0, float(max_exposure_pct))

    def set_exposure_leverage_multiplier(self, multiplier: float) -> None:
        self._exposure_leverage_multiplier = max(1.0, float(multiplier))

    def update_open_position(
        self,
        symbol: str,
        size: float = 0.0,
        notional: float = 0.0,
        side: str = "",
    ) -> None:
        symbol = str(symbol or "").upper()
        if not symbol:
            return
        size = max(0.0, float(size))
        notional = max(0.0, float(notional))
        current = self._open_positions.get(symbol)
        if current:
            current["size"] = float(current.get("size", 0.0)) + size
            current["notional"] = float(current.get("notional", 0.0)) + notional
            current["side"] = str(side or current.get("side") or "").lower()
        else:
            self._open_positions[symbol] = {
                "size": size,
                "notional": notional,
                "side": str(side or "").lower(),
            }

    def _reduce_open_position(self, symbol: str, *, notional: float) -> None:
        symbol = str(symbol or "").upper()
        current = self._open_positions.get(symbol)
        if not current:
            return
        current["notional"] = max(
            0.0, float(current.get("notional", 0.0)) - max(0.0, float(notional))
        )
        current["size"] = max(0.0, float(current.get("size", 0.0)) - max(0.0, float(notional)))
        if current["notional"] <= 1e-9:
            self._open_positions.pop(symbol, None)

    def open_trade(self, trade: TradeRecord) -> None:
        self._active_trades[trade.trade_id] = trade
        self.update_open_position(
            trade.symbol,
            size=trade.size,
            notional=trade.size,
            side=trade.decision.lower(),
        )

    def close_trade(
        self,
        trade_id: str,
        exit_price: float | None = None,
        pnl: float = 0.0,
        pnl_pct: float = 0.0,
        success: bool = False,
        symbol: str | None = None,
    ) -> bool:
        trade = self._active_trades.pop(trade_id, None)
        if trade is None and symbol:
            return self.close_trade_by_symbol(
                symbol=symbol,
                exit_price=exit_price,
                pnl=pnl,
                pnl_pct=pnl_pct,
                success=success,
            )
        if trade is None:
            return False

        self._reduce_open_position(trade.symbol, notional=trade.size)
        self.record_trade(
            TradeRecord(
                trade_id=trade.trade_id,
                timestamp=datetime.now(timezone.utc),
                symbol=trade.symbol,
                decision=trade.decision,
                entry_price=trade.entry_price,
                exit_price=exit_price,
                pnl=float(pnl),
                pnl_pct=float(pnl_pct),
                success=bool(success),
                size=trade.size,
            )
        )
        return True

    def close_trade_by_symbol(
        self,
        symbol: str,
        exit_price: float | None = None,
        pnl: float = 0.0,
        pnl_pct: float = 0.0,
        success: bool = False,
    ) -> bool:
        symbol = str(symbol or "").upper()
        trade_id = next(
            (
                trade.trade_id
                for trade in self._active_trades.values()
                if trade.symbol.upper() == symbol
            ),
            None,
        )
        if trade_id is None:
            return False
        return self.close_trade(
            trade_id=trade_id,
            exit_price=exit_price,
            pnl=pnl,
            pnl_pct=pnl_pct,
            success=success,
        )

    def get_total_exposure(self) -> dict[str, Any]:
        total_exposure = sum(
            float(position.get("notional", 0.0)) for position in self._open_positions.values()
        )
        max_exposure_pct = float(getattr(self, "_max_total_exposure_pct", self._max_exposure_pct))
        max_margin_exposure = max(0.0, float(self._current_balance) * max_exposure_pct)
        max_exposure = max_margin_exposure * float(self._exposure_leverage_multiplier)
        return {
            "total_exposure": total_exposure,
            "max_margin_exposure": max_margin_exposure,
            "max_exposure": max_exposure,
            "exposure_leverage_multiplier": float(self._exposure_leverage_multiplier),
            "positions_count": len(self._open_positions),
            "positions": dict(self._open_positions),
        }

    def _check_total_exposure(self, new_notional: float, side: str) -> tuple[bool, str]:
        del side
        exposure = self.get_total_exposure()
        projected = float(exposure["total_exposure"]) + max(0.0, float(new_notional))
        if float(exposure["max_exposure"]) > 0 and projected > float(exposure["max_exposure"]):
            return False, "Total exposure would exceed limit"
        return True, "ok"

    def record_trade(self, trade: TradeRecord) -> None:
        """Registra un trade y actualiza métricas."""
        self._trades.append(trade)
        self._daily_pnl += trade.pnl

        # Recalcular balance y guardar estado
        self._current_balance += trade.pnl
        self._save_state()

        # Auto-reevaluar riesgo después de cada trade
        status = self.evaluate_risk()
        if status.mode != "NORMAL":
            logger.warning(f"Risk mode changed after trade: {status.describe()}")

    def get_metrics(self) -> dict[str, Any]:
        """Obtiene métricas de trading recientes."""
        if not self._trades:
            drawdown_pct = 0.0
            if self._peak_balance > 0:
                drawdown_pct = (
                    (self._peak_balance - self._current_balance) / self._peak_balance * 100
                )
            all_time_drawdown_pct = 0.0
            if self._all_time_peak > 0:
                all_time_drawdown_pct = (
                    (self._all_time_peak - self._current_balance) / self._all_time_peak * 100
                )
            daily_loss_pct = 0.0
            if self._daily_start_balance and self._daily_start_balance > 0:
                daily_loss_pct = -self._daily_pnl / self._daily_start_balance * 100
            return {
                "total_trades": 0,
                "wins": 0,
                "winning_trades": 0,
                "losses": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
                "loss_streak": 0,
                "drawdown_pct": drawdown_pct,
                "all_time_drawdown_pct": all_time_drawdown_pct,
                "daily_pnl": self._daily_pnl,
                "daily_loss_pct": daily_loss_pct,
                "peak_balance": self._peak_balance,
                "all_time_peak": self._all_time_peak,
                "current_balance": self._current_balance,
            }

        recent = list(self._trades)[-self.config.lookback_trades :]
        wins = sum(1 for t in recent if t.success)

        total_pnl = sum(t.pnl for t in recent)
        avg_pnl = total_pnl / len(recent) if recent else 0.0

        # Consecutive losses
        loss_streak = 0
        for t in reversed(recent):
            if not t.success:
                loss_streak += 1
            else:
                break

        # Drawdown
        drawdown_pct = 0.0
        if self._peak_balance > 0:
            drawdown_pct = (self._peak_balance - self._current_balance) / self._peak_balance * 100

        all_time_drawdown_pct = 0.0
        if self._all_time_peak > 0:
            all_time_drawdown_pct = (
                (self._all_time_peak - self._current_balance) / self._all_time_peak * 100
            )

        # Daily loss pct
        daily_loss_pct = 0.0
        if self._daily_start_balance and self._daily_start_balance > 0:
            daily_loss_pct = -self._daily_pnl / self._daily_start_balance * 100

        return {
            "total_trades": len(recent),
            "wins": wins,
            "winning_trades": wins,
            "losses": len(recent) - wins,
            "losing_trades": len(recent) - wins,
            "win_rate": wins / len(recent) if recent else 0.0,
            "total_pnl": total_pnl,
            "avg_pnl": avg_pnl,
            "loss_streak": loss_streak,
            "drawdown_pct": drawdown_pct,
            "all_time_drawdown_pct": all_time_drawdown_pct,
            "daily_pnl": self._daily_pnl,
            "daily_loss_pct": daily_loss_pct,
            "peak_balance": self._peak_balance,
            "all_time_peak": self._all_time_peak,
            "current_balance": self._current_balance,
        }

    def evaluate_risk(self) -> RiskFeedbackStatus:
        """
        Evalúa el riesgo actual y retorna el status.

        Este es el CORE del circuit breaker.
        """
        if not self.config.enabled:
            return RiskFeedbackStatus(mode="NORMAL", risk_bias=1.0, reason="Risk loop disabled")

        metrics = self.get_metrics()

        now = datetime.now(timezone.utc)

        # A still-active hard stop should remain hard until it expires. Soft cooldowns are
        # allowed to escalate below if newer metrics cross a SEVERE threshold.
        if self.current_status.mode == "SEVERE" and self._current_status_active(now):
            return self.current_status

        # 1. Evaluar SEVERE drawdown (6.5%+)
        drawdown_pct = metrics.get("drawdown_pct", 0.0)
        if drawdown_pct >= self.config.severe_drawdown_pct:
            self.current_status = RiskFeedbackStatus(
                mode="SEVERE",
                risk_bias=self.config.drawdown_risk_bias,
                block_trading=True,
                reason=f"Drawdown {drawdown_pct:.1f}% >= {self.config.severe_drawdown_pct}%",
                cooldown_seconds=self.config.severe_cooldown_seconds,
                expires_at=now + timedelta(seconds=self.config.severe_cooldown_seconds),
                metrics_snapshot=metrics,
            )
            self._cooldown_start = now
            self._alert_severe(metrics)
            return self.current_status

        # 1b. Evaluar SEVERE all-time drawdown (acumulado entre días/restarts).
        # El peak intradía se re-ancla a medianoche; este check evita que pérdidas
        # sostenidas (-5% diario) escapen al circuit breaker indefinidamente.
        all_time_drawdown_pct = metrics.get("all_time_drawdown_pct", 0.0)
        try:
            max_alltime_dd = float(
                os.getenv("FENIX_RISK_MAX_ALLTIME_DRAWDOWN_PCT", "")
                or getattr(self.config, "max_alltime_drawdown_pct", 15.0)
            )
        except (TypeError, ValueError):
            max_alltime_dd = 15.0
        if max_alltime_dd > 0 and all_time_drawdown_pct >= max_alltime_dd:
            self.current_status = RiskFeedbackStatus(
                mode="SEVERE",
                risk_bias=self.config.drawdown_risk_bias,
                block_trading=True,
                reason=(
                    f"All-time drawdown {all_time_drawdown_pct:.1f}% >= {max_alltime_dd:.1f}%"
                ),
                cooldown_seconds=self.config.severe_cooldown_seconds,
                expires_at=now + timedelta(seconds=self.config.severe_cooldown_seconds),
                metrics_snapshot=metrics,
            )
            self._cooldown_start = now
            self._alert_severe(metrics)
            return self.current_status

        # 2. Evaluar SEVERE loss streak before softer drawdown/daily-loss modes.
        loss_streak = metrics.get("loss_streak", 0)
        if loss_streak >= self.config.loss_streak_halt:
            self.current_status = RiskFeedbackStatus(
                mode="SEVERE",
                risk_bias=self.config.drawdown_risk_bias,
                block_trading=True,
                reason=f"Loss streak {loss_streak} >= {self.config.loss_streak_halt}",
                cooldown_seconds=self.config.severe_cooldown_seconds,
                expires_at=now + timedelta(seconds=self.config.severe_cooldown_seconds),
                metrics_snapshot=metrics,
            )
            self._cooldown_start = now
            self._alert_severe(metrics)
            return self.current_status

        # 3. Evaluar SEVERE daily loss (3.5%+)
        daily_loss_pct = metrics.get("daily_loss_pct", 0.0)
        if daily_loss_pct >= self.config.severe_daily_loss_pct:
            self.current_status = RiskFeedbackStatus(
                mode="SEVERE",
                risk_bias=self.config.drawdown_risk_bias,
                block_trading=True,
                reason=f"Daily loss {daily_loss_pct:.1f}% >= {self.config.severe_daily_loss_pct}%",
                cooldown_seconds=self.config.severe_cooldown_seconds,
                expires_at=now + timedelta(seconds=self.config.severe_cooldown_seconds),
                metrics_snapshot=metrics,
            )
            self._cooldown_start = now
            self._alert_severe(metrics)
            return self.current_status

        # A still-active soft mode remains soft only after hard-stop checks have passed.
        if self.current_status.mode == "CAUTION" and self._current_status_active(now):
            return self.current_status
        if self._cooldown_start and not self._current_status_active(now):
            self._cooldown_start = None
            self.current_status = RiskFeedbackStatus(mode="NORMAL", risk_bias=1.0)

        # 4. Evaluar CAUTION drawdown (4%+)
        if drawdown_pct >= self.config.caution_drawdown_pct:
            self.current_status = RiskFeedbackStatus(
                mode="CAUTION",
                risk_bias=self.config.cooldown_risk_bias,
                block_trading=False,  # Solo reduce tamaño, no bloquea
                reason=f"Drawdown {drawdown_pct:.1f}% >= {self.config.caution_drawdown_pct}%",
                cooldown_seconds=self.config.caution_cooldown_seconds,
                expires_at=now + timedelta(seconds=self.config.caution_cooldown_seconds),
                metrics_snapshot=metrics,
            )
            self._cooldown_start = now
            logger.warning(f"CAUTION MODE: {self.current_status.describe()}")
            return self.current_status

        # 5. Evaluar CAUTION loss streak
        if loss_streak >= self.config.loss_streak_caution:
            self.current_status = RiskFeedbackStatus(
                mode="CAUTION",
                risk_bias=self.config.cooldown_risk_bias,
                block_trading=False,
                reason=f"Loss streak {loss_streak} >= {self.config.loss_streak_caution}",
                cooldown_seconds=self.config.caution_cooldown_seconds,
                expires_at=now + timedelta(seconds=self.config.caution_cooldown_seconds),
                metrics_snapshot=metrics,
            )
            self._cooldown_start = now
            logger.warning(f"CAUTION MODE: {self.current_status.describe()}")
            return self.current_status

        # 6. Evaluar CAUTION daily loss (2%+)
        if daily_loss_pct >= self.config.caution_daily_loss_pct:
            self.current_status = RiskFeedbackStatus(
                mode="CAUTION",
                risk_bias=self.config.cooldown_risk_bias,
                block_trading=False,
                reason=f"Daily loss {daily_loss_pct:.1f}% >= {self.config.caution_daily_loss_pct}%",
                cooldown_seconds=self.config.caution_cooldown_seconds,
                expires_at=now + timedelta(seconds=self.config.caution_cooldown_seconds),
                metrics_snapshot=metrics,
            )
            self._cooldown_start = now
            logger.warning(f"CAUTION MODE: {self.current_status.describe()}")
            return self.current_status

        # 7. Evaluar hot streak (para aumentar apuestas)
        win_rate = metrics.get("win_rate", 0.0)
        total_trades = metrics.get("total_trades", 0)
        avg_pnl = metrics.get("avg_pnl", 0.0)
        if (
            win_rate >= self.config.hot_streak_win_rate
            and total_trades >= self.config.hot_streak_min_trades
            and avg_pnl >= self.config.hot_streak_min_avg_pnl
        ):
            self.current_status = RiskFeedbackStatus(
                mode="HOT",
                risk_bias=self.config.hot_streak_risk_bias,
                reason=f"Hot streak! Win rate {win_rate:.1%}, Avg PnL ${avg_pnl:.2f}",
                metrics_snapshot=metrics,
            )
            return self.current_status

        # Normal mode
        self.current_status = RiskFeedbackStatus(
            mode="NORMAL", risk_bias=1.0, reason="Performance stable"
        )
        return self.current_status

    def _current_status_active(self, now: datetime) -> bool:
        if self.current_status.expires_at and now < self.current_status.expires_at:
            return True
        if not self._cooldown_start:
            return False
        elapsed = (now - self._cooldown_start).total_seconds()
        if self.current_status.mode == "CAUTION":
            return elapsed < self.config.caution_cooldown_seconds
        if self.current_status.mode == "SEVERE":
            return elapsed < self.config.severe_cooldown_seconds
        return False

    def check_trade_allowed(
        self,
        symbol: str,
        size: float,
        side: str = "",
    ) -> tuple[bool, RiskFeedbackStatus]:
        """
        Verifica si un trade está permitido.

        Returns:
            (allowed: bool, status: RiskFeedbackStatus)
        """
        if self.current_status.block_trading and self._current_status_active(
            datetime.now(timezone.utc)
        ):
            status = self.current_status
        else:
            status = self.evaluate_risk()

        if status.block_trading:
            logger.warning(f"Trade BLOCKED: {status.describe()}")
            return False, status

        exposure_ok, exposure_reason = self._check_total_exposure(size, side)
        if not exposure_ok:
            exposure = self.get_total_exposure()
            return True, RiskFeedbackStatus(
                mode=status.mode,
                risk_bias=status.risk_bias,
                block_trading=False,
                reason=exposure_reason,
                metrics_snapshot={
                    "total_exposure": float(exposure.get("total_exposure", 0.0)),
                    "max_exposure": float(exposure.get("max_exposure", 0.0)),
                    "positions_count": float(exposure.get("positions_count", 0)),
                },
            )

        return True, status

    def get_adjusted_size(self, base_size: float) -> float:
        """Ajusta tamaño de posición según modo de riesgo actual."""
        if self.current_status.mode != "NORMAL":
            status = self.current_status
        else:
            status = self.evaluate_risk()
        adjusted = base_size * status.risk_bias
        exposure = self.get_total_exposure()
        available_exposure = max(
            0.0,
            float(exposure.get("max_exposure", 0.0)) - float(exposure.get("total_exposure", 0.0)),
        )
        if float(exposure.get("max_exposure", 0.0)) > 0:
            adjusted = min(adjusted, available_exposure)

        if status.risk_bias != 1.0:
            logger.info(
                f"Size adjusted: ${base_size:.2f} * {status.risk_bias} = ${adjusted:.2f} [{status.mode}]"
            )

        return adjusted

    def _alert_severe(self, metrics: dict[str, Any]) -> None:
        """Envía alerta cuando se activa modo SEVERE."""
        logger.critical(f"🚨 SEVERE MODE ACTIVATED: {self.current_status.describe()}")

        # Enviar notificación async
        if self.notifier and NOTIFIER_AVAILABLE:
            try:
                metrics = self.get_metrics()
                loop = asyncio.get_running_loop()
                loop.create_task(self.notifier.send_alert(self.current_status, metrics))
            except Exception as e:
                logger.warning(f"Could not schedule alert: {e}")

    def get_status_summary(self) -> dict[str, Any]:
        """Retorna resumen del estado para dashboard."""
        metrics = self.get_metrics()
        return {
            "mode": self.current_status.mode,
            "risk_bias": self.current_status.risk_bias,
            "block_trading": self.current_status.block_trading,
            "reason": self.current_status.reason,
            **metrics,
        }


# Singleton para uso global
_risk_manager: RuntimeRiskManager | None = None


def get_risk_manager() -> RuntimeRiskManager:
    """Obtiene o crea el RiskManager global."""
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = RuntimeRiskManager()
    return _risk_manager
