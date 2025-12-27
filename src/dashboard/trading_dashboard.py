# src/dashboard/trading_dashboard.py
"""
Trading Dashboard for Fenix.

Provides real-time visualization of:
- Agent pipeline status
- Performance metrics
- Decisions and signals
- ReasoningBank statistics
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AgentStatus:
    """Status of an agent."""

    name: str
    status: str = "idle"  # idle, running, completed, error
    last_signal: str | None = None
    last_confidence: str | None = None
    last_latency_ms: float = 0.0
    invocations: int = 0
    errors: int = 0
    updated_at: str | None = None


@dataclass
class PipelineMetrics:
    """Pipeline metrics."""

    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    avg_latency_ms: float = 0.0
    last_run_time: str | None = None
    buy_signals: int = 0
    sell_signals: int = 0
    hold_signals: int = 0


class TradingDashboard:
    """
    Dashboard for monitoring the Fenix trading system.

    Features:
    - Visualization of agent states
    - Real-time metrics
    - Decision history
    - ReasoningBank statistics
    """

    def __init__(self):
        self.agents: dict[str, AgentStatus] = {}
        self.pipeline_metrics = PipelineMetrics()
        self.decision_history: list[dict[str, Any]] = []
        self.max_history = 100

        # Initialize known agents
        agent_names = [
            "technical_analyst",
            "sentiment_analyst",
            "visual_analyst",
            "qabba_analyst",
            "decision_agent",
            "risk_manager",
        ]
        for name in agent_names:
            self.agents[name] = AgentStatus(name=name)

    def update_agent_status(
        self,
        agent_name: str,
        status: str,
        signal: str | None = None,
        confidence: str | None = None,
        latency_ms: float = 0.0,
        error: bool = False,
    ) -> None:
        """Updates the status of an agent."""
        if agent_name not in self.agents:
            self.agents[agent_name] = AgentStatus(name=agent_name)

        agent = self.agents[agent_name]
        agent.status = status
        agent.updated_at = datetime.now().isoformat()

        if signal:
            agent.last_signal = signal
        if confidence:
            agent.last_confidence = confidence
        if latency_ms > 0:
            agent.last_latency_ms = latency_ms

        agent.invocations += 1
        if error:
            agent.errors += 1

    def record_pipeline_run(
        self,
        success: bool,
        latency_ms: float,
        final_signal: str | None = None,
        state: dict[str, Any] | None = None,
    ) -> None:
        """Records a complete pipeline execution."""
        self.pipeline_metrics.total_runs += 1
        self.pipeline_metrics.last_run_time = datetime.now().isoformat()

        if success:
            self.pipeline_metrics.successful_runs += 1
        else:
            self.pipeline_metrics.failed_runs += 1

        # Update average latency (moving average)
        n = self.pipeline_metrics.total_runs
        old_avg = self.pipeline_metrics.avg_latency_ms
        self.pipeline_metrics.avg_latency_ms = (old_avg * (n - 1) + latency_ms) / n

        # Count signals
        if final_signal:
            signal_upper = final_signal.upper()
            if signal_upper == "BUY":
                self.pipeline_metrics.buy_signals += 1
            elif signal_upper == "SELL":
                self.pipeline_metrics.sell_signals += 1
            else:
                self.pipeline_metrics.hold_signals += 1

        # Add to history
        decision_entry = {
            "timestamp": datetime.now().isoformat(),
            "success": success,
            "latency_ms": latency_ms,
            "signal": final_signal,
            "state_summary": self._summarize_state(state) if state else None,
        }
        self.decision_history.append(decision_entry)

        # Maintain history limit
        if len(self.decision_history) > self.max_history:
            self.decision_history = self.decision_history[-self.max_history:]

    def _summarize_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Summarizes the state for storage."""
        summary = {}

        if "symbol" in state:
            summary["symbol"] = state["symbol"]
        if "timeframe" in state:
            summary["timeframe"] = state["timeframe"]
        if "final_decision" in state:
            decision = state["final_decision"]
            if isinstance(decision, dict):
                summary["signal"] = decision.get("signal")
                summary["confidence"] = decision.get("confidence_level")
        if "execution_times" in state:
            summary["execution_times"] = state["execution_times"]

        return summary

    def get_dashboard_data(self) -> dict[str, Any]:
        """Returns all dashboard data."""
        return {
            "agents": {
                name: {
                    "status": a.status,
                    "last_signal": a.last_signal,
                    "last_confidence": a.last_confidence,
                    "last_latency_ms": f"{a.last_latency_ms:.1f}ms",
                    "invocations": a.invocations,
                    "errors": a.errors,
                    "updated_at": a.updated_at,
                }
                for name, a in self.agents.items()
            },
            "pipeline": {
                "total_runs": self.pipeline_metrics.total_runs,
                "success_rate": self._calc_success_rate(),
                "avg_latency_ms": f"{self.pipeline_metrics.avg_latency_ms:.1f}ms",
                "signal_distribution": {
                    "buy": self.pipeline_metrics.buy_signals,
                    "sell": self.pipeline_metrics.sell_signals,
                    "hold": self.pipeline_metrics.hold_signals,
                },
                "last_run": self.pipeline_metrics.last_run_time,
            },
            "recent_decisions": self.decision_history[-10:],
        }

    def _calc_success_rate(self) -> str:
        """Calculates the success rate."""
        total = self.pipeline_metrics.total_runs
        if total == 0:
            return "N/A"
        rate = self.pipeline_metrics.successful_runs / total
        return f"{rate:.1%}"

    def print_status(self) -> None:
        """Prints the dashboard status to the console."""
        data = self.get_dashboard_data()

        print("\n" + "=" * 60)
        print("FENIX TRADING DASHBOARD")
        print("=" * 60)

        # Agent status
        print("\n📊 AGENTS:")
        print("-" * 40)
        for name, agent_data in data["agents"].items():
            status_emoji = self._get_status_emoji(agent_data["status"])
            signal = agent_data["last_signal"] or "-"
            confidence = agent_data["last_confidence"] or "-"
            print(
                f"  {status_emoji} {name:20} | "
                f"Signal: {signal:6} | Conf: {confidence:6} | "
                f"Latency: {agent_data['last_latency_ms']}"
            )

        # Pipeline metrics
        print("\n📈 PIPELINE:")
        print("-" * 40)
        pipeline = data["pipeline"]
        print(f"  Total Runs: {pipeline['total_runs']}")
        print(f"  Success Rate: {pipeline['success_rate']}")
        print(f"  Avg Latency: {pipeline['avg_latency_ms']}")

        dist = pipeline["signal_distribution"]
        total_signals = dist["buy"] + dist["sell"] + dist["hold"]
        if total_signals > 0:
            print("\n  📊 Signal Distribution:")
            print(f"    BUY:  {dist['buy']:4} ({dist['buy']/total_signals*100:.1f}%)")
            print(f"    SELL: {dist['sell']:4} ({dist['sell']/total_signals*100:.1f}%)")
            print(f"    HOLD: {dist['hold']:4} ({dist['hold']/total_signals*100:.1f}%)")

        # Recent decisions
        recent = data["recent_decisions"]
        if recent:
            print("\n📜 RECENT DECISIONS:")
            print("-" * 40)
            for dec in recent[-5:]:
                ts = dec["timestamp"].split("T")[1][:8]
                signal = dec.get("signal", "N/A")
                latency = dec.get("latency_ms", 0)
                status = "✅" if dec.get("success") else "❌"
                print(f"  {ts} {status} {signal:6} ({latency:.0f}ms)")

        print("\n" + "=" * 60)

    def _get_status_emoji(self, status: str) -> str:
        """Returns an emoji based on the status."""
        status_emojis = {
            "idle": "⚪",
            "running": "🔵",
            "completed": "🟢",
            "error": "🔴",
        }
        return status_emojis.get(status, "⚪")


class LiveDashboard:
    """Live dashboard that updates continuously."""

    def __init__(self, dashboard: TradingDashboard, refresh_interval: float = 1.0):
        self.dashboard = dashboard
        self.refresh_interval = refresh_interval
        self.running = False

    def start(self) -> None:
        """Starts the live dashboard."""
        self.running = True
        logger.info("Starting live dashboard")

        try:
            while self.running:
                self._clear_screen()
                self.dashboard.print_status()
                time.sleep(self.refresh_interval)
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        """Stops the dashboard."""
        self.running = False
        logger.info("Dashboard stopped")

    def _clear_screen(self) -> None:
        """Clears the screen."""
        print("\033[H\033[J", end="")


# Singleton
_dashboard: TradingDashboard | None = None


def get_dashboard() -> TradingDashboard:
    """Gets the singleton instance of the dashboard."""
    global _dashboard
    if _dashboard is None:
        _dashboard = TradingDashboard()
    return _dashboard
