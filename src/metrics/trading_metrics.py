"""Métricas de Trading para FenixAI.

Calcula métricas estándar de trading:
- Win Rate, Profit Factor, Sharpe Ratio
- Max Drawdown, Payoff Ratio
- Métricas de agente (accuracy por tipo)
"""
from __future__ import annotations

import math
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class TradeMetrics:
    """Métricas calculadas sobre trades."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float  # 0-1
    avg_win: float
    avg_loss: float
    win_loss_ratio: float  # avg_win / avg_loss
    profit_factor: float  # sum(wins) / sum(losses)
    payoff_ratio: float  # avg_win / abs(avg_loss)
    sharpe_ratio: float
    max_drawdown_pct: float
    max_drawdown_dollars: float
    current_drawdown_pct: float
    total_pnl: float
    avg_pnl: float
    expectancy: float  # (win_rate * avg_win) - (loss_rate * abs(avg_loss))


@dataclass
class AgentMetrics:
    """Métricas por agente."""
    agent_name: str
    total_decisions: int
    correct_decisions: int  # Contra ground truth o eval
    accuracy: float
    avg_confidence: float
    avg_latency_ms: float
    success_rate: float  # Basado en ReasoningBank outcomes


class TradingMetricsDashboard:
    """
    Dashboard de métricas de trading.
    
    Calcula métricas financieras estándar y mantiene historial.
    """
    
    def __init__(self, storage_path: str = "logs/metrics.jsonl"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._pnl_history: List[float] = []
        self._return_history: List[float] = []  # Returns como %
    
    def calculate_trade_metrics(self, trades: List[Dict[str, Any]]) -> TradeMetrics:
        """Calcula métricas financieras desde lista de trades."""
        if not trades:
            return TradeMetrics(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        
        total_trades = len(trades)
        
        # Separar ganadores y perdedores
        wins = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) <= 0]
        
        winning_trades = len(wins)
        losing_trades = len(losses)
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        
        # P&L
        total_wins = sum(t.get("pnl", 0) for t in wins)
        total_losses = abs(sum(t.get("pnl", 0) for t in losses))
        total_pnl = total_wins - total_losses
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0.0
        
        # Promedios
        avg_win = total_wins / winning_trades if winning_trades > 0 else 0.0
        avg_loss = -(total_losses / losing_trades) if losing_trades > 0 else 0.0  # Negativo
        win_loss_ratio = avg_win / abs(avg_loss) if avg_loss != 0 else 0.0
        payoff_ratio = win_loss_ratio
        
        # Profit Factor
        profit_factor = total_wins / total_losses if total_losses > 0 else float('inf') if total_wins > 0 else 0.0
        
        # Expectancy
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * abs(avg_loss))
        
        # Sharpe Ratio (retornos diarios)
        sharpe = self._calculate_sharpe(trades)
        
        # Drawdown
        max_dd_pct, max_dd_dol, curr_dd_pct = self._calculate_drawdown(trades)
        
        return TradeMetrics(
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            win_loss_ratio=win_loss_ratio,
            profit_factor=profit_factor,
            payoff_ratio=payoff_ratio,
            sharpe_ratio=sharpe,
            max_drawdown_pct=max_dd_pct,
            max_drawdown_dollars=max_dd_dol,
            current_drawdown_pct=curr_dd_pct,
            total_pnl=total_pnl,
            avg_pnl=avg_pnl,
            expectancy=expectancy,
        )
    
    def _calculate_sharpe(self, trades: List[Dict[str, Any]], risk_free_rate: float = 0.0) -> float:
        """Calcula Sharpe Ratio asumiendo retornos diarios."""
        returns = []
        for trade in trades:
            # Calcular retorno como % de capital si está disponible
            capital = trade.get("capital_employed", trade.get("position_size", 0))
            pnl = trade.get("pnl", 0)
            if capital > 0:
                returns.append(pnl / capital)
        
        if len(returns) < 10:  # Necesitamos suficientes datos
            return 0.0
        
        avg_return = sum(returns) / len(returns)
        variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance)
        
        if std_dev == 0:
            return 0.0
        
        # Sharpe = (mean_return - risk_free) / std_dev
        # Ajustado a período (asumiendo 252 días trading si es por día)
        sharpe = (avg_return - risk_free_rate) / std_dev
        return sharpe * math.sqrt(252)  # Annualizado
    
    def _calculate_drawdown(self, trades: List[Dict[str, Any]]) -> Tuple[float, float, float]:
        """Calcula drawdown máximo y actual."""
        if not trades:
            return 0.0, 0.0, 0.0
        
        # Equity curve
        equity = [0.0]
        for trade in trades:
            equity.append(equity[-1] + trade.get("pnl", 0))
        
        peak = equity[0]
        max_drawdown_pct = 0.0
        max_drawdown_dollars = 0.0
        
        for val in equity:
            if val > peak:
                peak = val
            else:
                dd = (peak - val) / peak if peak > 0 else 0
                dd_dol = peak - val
                if dd > max_drawdown_pct:
                    max_drawdown_pct = dd
                    max_drawdown_dollars = dd_dol
        
        # Drawdown actual
        current = equity[-1]
        current_dd_pct = (peak - current) / peak if peak > 0 else 0.0
        
        return max_drawdown_pct * 100, max_drawdown_dollars, current_dd_pct * 100
    
    def calculate_agent_metrics(
        self,
        reasoning_bank: Any,
        agent_name: str,
        lookback: int = 50
    ) -> AgentMetrics:
        """Calcula métricas para un agente específico."""
        if not reasoning_bank:
            return AgentMetrics(agent_name, 0, 0, 0.0, 0.0, 0.0, 0.0)
        
        try:
            from src.memory.reasoning_bank import ReasoningEntry
            entries = reasoning_bank.get_recent(agent_name, lookback)
        except Exception:
            from src.memory.reasoning_bank_optimized import ReasoningEntryOptimized
            entries = reasoning_bank.get_recent(agent_name, lookback)
        
        total = len(entries)
        if total == 0:
            return AgentMetrics(agent_name, 0, 0, 0.0, 0.0, 0.0, 0.0)
        
        # Calcular éxitos
        successful = [e for e in entries if e.success is True]
        success_rate = len(successful) / total if successful else 0.0
        
        # Confidence promedio
        avg_confidence = sum(e.confidence for e in entries) / total
        
        # Latency promedio
        lats = [e.latency_ms for e in entries if e.latency_ms is not None]
        avg_latency = sum(lats) / len(lats) if lats else 0.0
        
        # Accuracy (contra outcomes)
        evaluated = [e for e in entries if e.success is not None]
        correct = len([e for e in evaluated if e.success])
        accuracy = correct / len(evaluated) if evaluated else 0.0
        
        return AgentMetrics(
            agent_name=agent_name,
            total_decisions=total,
            correct_decisions=correct,
            accuracy=accuracy,
            avg_confidence=avg_confidence,
            avg_latency_ms=avg_latency,
            success_rate=success_rate,
        )
    
    def generate_dashboard(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Genera dashboard completo de métricas."""
        metrics = self.calculate_trade_metrics(trades)
        
        # Métricas por agente (si hay reasoning bank)
        try:
            from src.memory.reasoning_bank import get_reasoning_bank
            bank = get_reasoning_bank()
            agent_metrics = {
                "technical": self.calculate_agent_metrics(bank, "technical_agent", 50),
                "qabba": self.calculate_agent_metrics(bank, "qabba_agent", 50),
                "decision": self.calculate_agent_metrics(bank, "decision_agent", 20),
            }
        except Exception:
            agent_metrics = {}
        
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trading_metrics": {
                "total_trades": metrics.total_trades,
                "win_rate": f"{metrics.win_rate:.1%}",
                "profit_factor": f"{metrics.profit_factor:.2f}",
                "sharpe_ratio": f"{metrics.sharpe_ratio:.2f}",
                "max_drawdown_pct": f"{metrics.max_drawdown_pct:.1f}%",
                "max_drawdown_dollars": f"${metrics.max_drawdown_dollars:.2f}",
                "avg_pnl": f"${metrics.avg_pnl:.2f}",
                "expectancy": f"${metrics.expectancy:.2f}",
                "payoff_ratio": f"{metrics.payoff_ratio:.2f}",
            },
            "agent_metrics": {
                name: {
                    "total": m.total_decisions,
                    "accuracy": f"{m.accuracy:.1%}",
                    "success_rate": f"{m.success_rate:.1%}",
                    "avg_confidence": f"{m.avg_confidence:.1%}",
                    "avg_latency_ms": f"{m.avg_latency_ms:.0f}ms",
                }
                for name, m in agent_metrics.items()
            },
            "raw": metrics,  # Para cálculos
        }
    
    def save_metrics(self, dashboard: Dict[str, Any]) -> None:
        """Persiste métricas a JSONL."""
        try:
            with open(self.storage_path, "a") as f:
                f.write(json.dumps(dashboard) + "\n")
        except Exception as e:
            logger.warning(f"Could not save metrics: {e}")


# Singleton
_dashboard: Optional[TradingMetricsDashboard] = None


def get_metrics_dashboard() -> TradingMetricsDashboard:
    """Obtiene o crea el dashboard global."""
    global _dashboard
    if _dashboard is None:
        _dashboard = TradingMetricsDashboard()
    return _dashboard


def format_metrics_for_display(metrics: TradeMetrics) -> str:
    """Formatea métricas para display en consola/frontend."""
    lines = [
        "=" * 50,
        "  📊 TRADING METRICS",
        "=" * 50,
        f"  Total Trades:     {metrics.total_trades}",
        f"  Win Rate:         {metrics.win_rate:.1%}",
        f"  Profit Factor:    {metrics.profit_factor:.2f}",
        f"  Sharpe Ratio:     {metrics.sharpe_ratio:.2f}",
        f"  Max Drawdown:     {metrics.max_drawdown_pct:.1f}% (${metrics.max_drawdown_dollars:.2f})",
        f"  Avg PnL/Trade:    ${metrics.avg_pnl:.2f}",
        f"  Expectancy:       ${metrics.expectancy:.2f}",
        f"  Payoff Ratio:     {metrics.payoff_ratio:.2f}",
        "=" * 50,
    ]
    return "\n".join(lines)
