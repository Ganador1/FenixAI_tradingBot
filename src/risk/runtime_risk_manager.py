"""Runtime Risk Manager - Circuit Breakers activo.

Implementación completa del sistema de protección de capital.
Evalúa riesgo en tiempo real antes de cada trade.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

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
    exit_price: Optional[float]
    pnl: float
    pnl_pct: float
    success: bool
    size: float


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
        self,
        config: Optional[RiskFeedbackLoopConfig] = None,
        storage_path: str = "logs/risk_manager.jsonl"
    ):
        self.config = config or RiskFeedbackLoopConfig()
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Notificador
        if NOTIFIER_AVAILABLE and get_circuit_breaker_notifier:
            self.notifier = get_circuit_breaker_notifier()
        else:
            self.notifier = None
        
        # Estado actual
        self.current_status = RiskFeedbackStatus(mode="NORMAL", risk_bias=1.0)
        self._last_evaluation: Optional[datetime] = None
        
        # Historial de trades para métricas
        self._trades: deque[TradeRecord] = deque(maxlen=100)
        
        # Métricas del día
        self._daily_pnl: float = 0.0
        self._daily_start_balance: Optional[float] = None
        self._last_trading_day: Optional[str] = None
        
        # Métricas de drawdown
        self._peak_balance: float = 0.0
        self._current_balance: float = 0.0
        
        # Cooldown tracking
        self._cooldown_start: Optional[datetime] = None
        
        # Cargar estado previo si existe
        self._load_state()
    
    def _load_state(self) -> None:
        """Carga estado previo del día si existe."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path) as f:
                    lines = f.readlines()
                    if lines:
                        last_line = json.loads(lines[-1])
                        self._daily_pnl = last_line.get("daily_pnl", 0.0)
                        self._peak_balance = last_line.get("peak_balance", 0.0)
                        self._last_trading_day = last_line.get("trading_day")
            except Exception as e:
                logger.warning(f"Could not load risk state: {e}")
    
    def _save_state(self) -> None:
        """Persiste estado actual."""
        state = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trading_day": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "daily_pnl": self._daily_pnl,
            "peak_balance": self._peak_balance,
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
        self._current_balance = balance
        
        # Reset diario si es nuevo día
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._last_trading_day != today:
            self._daily_pnl = 0.0
            self._daily_start_balance = balance
            self._last_trading_day = today
            self._peak_balance = balance
            logger.info(f"New trading day: {today}, reset daily PnL")
        
        # Actualizar peak
        if balance > self._peak_balance:
            self._peak_balance = balance
    
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
    
    def get_metrics(self) -> Dict[str, Any]:
        """Obtiene métricas de trading recientes."""
        if not self._trades:
            return {"total_trades": 0, "win_rate": 0.0}
        
        recent = list(self._trades)[-self.config.lookback_trades:]
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
        
        # Daily loss pct
        daily_loss_pct = 0.0
        if self._daily_start_balance and self._daily_start_balance > 0:
            daily_loss_pct = -self._daily_pnl / self._daily_start_balance * 100
        
        return {
            "total_trades": len(recent),
            "wins": wins,
            "losses": len(recent) - wins,
            "win_rate": wins / len(recent) if recent else 0.0,
            "total_pnl": total_pnl,
            "avg_pnl": avg_pnl,
            "loss_streak": loss_streak,
            "drawdown_pct": drawdown_pct,
            "daily_pnl": self._daily_pnl,
            "daily_loss_pct": daily_loss_pct,
            "peak_balance": self._peak_balance,
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
        
        # Verificar cooldown activo
        if self._cooldown_start:
            elapsed = (datetime.now(timezone.utc) - self._cooldown_start).total_seconds()
            
            if self.current_status.mode == "CAUTION" and elapsed < self.config.caution_cooldown_seconds:
                return self.current_status
            elif self.current_status.mode == "SEVERE" and elapsed < self.config.severe_cooldown_seconds:
                return self.current_status
            else:
                # Cooldown expirado, resetear
                self._cooldown_start = None
                self.current_status = RiskFeedbackStatus(mode="NORMAL", risk_bias=1.0)
        
        # 1. Evaluar SEVERE drawdown (6.5%+)
        drawdown_pct = metrics.get("drawdown_pct", 0.0)
        if drawdown_pct >= self.config.severe_drawdown_pct:
            self.current_status = RiskFeedbackStatus(
                mode="SEVERE",
                risk_bias=self.config.drawdown_risk_bias,
                block_trading=True,
                reason=f"Drawdown {drawdown_pct:.1f}% >= {self.config.severe_drawdown_pct}%",
                cooldown_seconds=self.config.severe_cooldown_seconds,
                expires_at=datetime.now(timezone.utc).replace(
                    second=datetime.now(timezone.utc).second + self.config.severe_cooldown_seconds
                ),
                metrics_snapshot=metrics
            )
            self._cooldown_start = datetime.now(timezone.utc)
            self._alert_severe(metrics)
            return self.current_status
        
        # 2. Evaluar CAUTION drawdown (4%+)
        if drawdown_pct >= self.config.caution_drawdown_pct:
            self.current_status = RiskFeedbackStatus(
                mode="CAUTION",
                risk_bias=self.config.cooldown_risk_bias,
                block_trading=False,  # Solo reduce tamaño, no bloquea
                reason=f"Drawdown {drawdown_pct:.1f}% >= {self.config.caution_drawdown_pct}%",
                cooldown_seconds=self.config.caution_cooldown_seconds,
                expires_at=datetime.now(timezone.utc).replace(
                    second=datetime.now(timezone.utc).second + self.config.caution_cooldown_seconds
                ),
                metrics_snapshot=metrics
            )
            self._cooldown_start = datetime.now(timezone.utc)
            logger.warning(f"CAUTION MODE: {self.current_status.describe()}")
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
                metrics_snapshot=metrics
            )
            self._cooldown_start = datetime.now(timezone.utc)
            self._alert_severe(metrics)
            return self.current_status
        
        # 4. Evaluar CAUTION daily loss (2%+)
        if daily_loss_pct >= self.config.caution_daily_loss_pct:
            self.current_status = RiskFeedbackStatus(
                mode="CAUTION",
                risk_bias=self.config.cooldown_risk_bias,
                block_trading=False,
                reason=f"Daily loss {daily_loss_pct:.1f}% >= {self.config.caution_daily_loss_pct}%",
                cooldown_seconds=self.config.caution_cooldown_seconds,
                metrics_snapshot=metrics
            )
            self._cooldown_start = datetime.now(timezone.utc)
            logger.warning(f"CAUTION MODE: {self.current_status.describe()}")
            return self.current_status
        
        # 5. Evaluar loss streak
        loss_streak = metrics.get("loss_streak", 0)
        if loss_streak >= self.config.loss_streak_halt:
            self.current_status = RiskFeedbackStatus(
                mode="SEVERE",
                risk_bias=self.config.drawdown_risk_bias,
                block_trading=True,
                reason=f"Loss streak {loss_streak} >= {self.config.loss_streak_halt}",
                cooldown_seconds=self.config.severe_cooldown_seconds,
                metrics_snapshot=metrics
            )
            self._cooldown_start = datetime.now(timezone.utc)
            self._alert_severe(metrics)
            return self.current_status
        
        if loss_streak >= self.config.loss_streak_caution:
            self.current_status = RiskFeedbackStatus(
                mode="CAUTION",
                risk_bias=self.config.cooldown_risk_bias,
                block_trading=False,
                reason=f"Loss streak {loss_streak} >= {self.config.loss_streak_caution}",
                cooldown_seconds=self.config.caution_cooldown_seconds,
                metrics_snapshot=metrics
            )
            self._cooldown_start = datetime.now(timezone.utc)
            logger.warning(f"CAUTION MODE: {self.current_status.describe()}")
            return self.current_status
        
        # 6. Evaluar hot streak (para aumentar apuestas)
        win_rate = metrics.get("win_rate", 0.0)
        total_trades = metrics.get("total_trades", 0)
        avg_pnl = metrics.get("avg_pnl", 0.0)
        if (win_rate >= self.config.hot_streak_win_rate and
            total_trades >= self.config.hot_streak_min_trades and
            avg_pnl >= self.config.hot_streak_min_avg_pnl):
            self.current_status = RiskFeedbackStatus(
                mode="HOT",
                risk_bias=self.config.hot_streak_risk_bias,
                reason=f"Hot streak! Win rate {win_rate:.1%}, Avg PnL ${avg_pnl:.2f}",
                metrics_snapshot=metrics
            )
            return self.current_status
        
        # Normal mode
        self.current_status = RiskFeedbackStatus(
            mode="NORMAL",
            risk_bias=1.0,
            reason="Performance stable"
        )
        return self.current_status
    
    def check_trade_allowed(self, symbol: str, size: float) -> tuple[bool, RiskFeedbackStatus]:
        """
        Verifica si un trade está permitido.
        
        Returns:
            (allowed: bool, status: RiskFeedbackStatus)
        """
        status = self.evaluate_risk()
        
        if status.block_trading:
            logger.warning(f"Trade BLOCKED: {status.describe()}")
            return False, status
        
        # Verificar exposure máxima
        # TODO: Implementar límite de exposición total
        
        return True, status
    
    def get_adjusted_size(self, base_size: float) -> float:
        """Ajusta tamaño de posición según modo de riesgo actual."""
        status = self.evaluate_risk()
        adjusted = base_size * status.risk_bias
        
        if status.risk_bias != 1.0:
            logger.info(f"Size adjusted: ${base_size:.2f} * {status.risk_bias} = ${adjusted:.2f} [{status.mode}]")
        
        return adjusted
    
    def _alert_severe(self, metrics: Dict[str, Any]) -> None:
        """Envía alerta cuando se activa modo SEVERE."""
        logger.critical(f"🚨 SEVERE MODE ACTIVATED: {self.current_status.describe()}")
        
        # Enviar notificación async
        if self.notifier and NOTIFIER_AVAILABLE:
            try:
                import asyncio
                metrics = self.get_metrics()
                # Schedule async task
                asyncio.create_task(
                    self.notifier.send_alert(self.current_status, metrics)
                )
            except Exception as e:
                logger.warning(f"Could not schedule alert: {e}")
    
    def get_status_summary(self) -> Dict[str, Any]:
        """Retorna resumen del estado para dashboard."""
        metrics = self.get_metrics()
        return {
            "mode": self.current_status.mode,
            "risk_bias": self.current_status.risk_bias,
            "block_trading": self.current_status.block_trading,
            "reason": self.current_status.reason,
            **metrics
        }


# Singleton para uso global
_risk_manager: Optional[RuntimeRiskManager] = None


def get_risk_manager() -> RuntimeRiskManager:
    """Obtiene o crea el RiskManager global."""
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = RuntimeRiskManager()
    return _risk_manager
