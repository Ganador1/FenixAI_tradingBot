"""Runtime risk feedback configuration models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RiskFeedbackLoopConfig(BaseModel):
    """Thresholds and multipliers used by the runtime risk feedback loop."""

    enabled: bool = Field(default=True, description="Toggle for the risk feedback loop.")
    lookback_trades: int = Field(
        default=12, ge=1, description="Number of recent trades considered for feedback metrics."
    )

    caution_drawdown_pct: float = Field(
        default=4.0, ge=0.0, description="Drawdown percentage that triggers a soft cooldown."
    )
    severe_drawdown_pct: float = Field(
        default=6.5,
        ge=0.0,
        description="Drawdown percentage that triggers the hard protection mode.",
    )

    caution_daily_loss_pct: float = Field(
        default=2.0, ge=0.0, description="Daily loss percentage that triggers a soft cooldown."
    )
    severe_daily_loss_pct: float = Field(
        default=3.5,
        ge=0.0,
        description="Daily loss percentage that triggers a hard protection mode.",
    )

    loss_streak_caution: int = Field(
        default=3, ge=1, description="Consecutive losses that trigger a soft cooldown."
    )
    loss_streak_halt: int = Field(
        default=5, ge=1, description="Consecutive losses that trigger a hard protection mode."
    )

    negative_avg_pnl_threshold: float = Field(
        default=-8.0, description="Average PnL threshold (USD) that triggers a soft cooldown."
    )

    hot_streak_win_rate: float = Field(
        default=0.68, ge=0.0, le=1.0, description="Win rate required to consider a hot streak."
    )
    hot_streak_min_trades: int = Field(
        default=6,
        ge=1,
        description="Minimum amount of recent trades required for hot streak detection.",
    )
    hot_streak_min_avg_pnl: float = Field(
        default=12.0, description="Minimum average PnL (USD) required to consider a hot streak."
    )

    caution_cooldown_seconds: int = Field(
        default=300, ge=0, description="Cooldown duration after a soft trigger."
    )
    severe_cooldown_seconds: int = Field(
        default=900, ge=0, description="Cooldown duration after a hard trigger."
    )

    cooldown_risk_bias: float = Field(
        default=0.7, ge=0.0, description="Risk multiplier applied during soft cooldown."
    )
    drawdown_risk_bias: float = Field(
        default=0.45, ge=0.0, description="Risk multiplier applied during drawdown protection."
    )
    hot_streak_risk_bias: float = Field(
        default=1.12, ge=0.0, description="Risk multiplier applied during a hot streak."
    )


class RiskFeedbackStatus(BaseModel):
    """Represents the latest runtime risk feedback state."""

    mode: Literal["NORMAL", "CAUTION", "SEVERE", "HOT"] = "NORMAL"
    risk_bias: float = Field(default=1.0, ge=0.0)
    reason: str = Field(default="Performance stable")
    block_trading: bool = False
    cooldown_seconds: int = Field(default=0, ge=0)
    expires_at: datetime | None = None
    metrics_snapshot: dict[str, float] = Field(default_factory=dict)

    def describe(self) -> str:
        """Short human-readable summary used for logging."""

        base = f"mode={self.mode} bias={self.risk_bias:.2f}"
        if self.cooldown_seconds:
            base += f" cooldown={self.cooldown_seconds}s"
        return f"{base} reason='{self.reason}'"
