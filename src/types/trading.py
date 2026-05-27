# src/types/trading.py
"""
Trading-related type definitions for Fenix Trading Bot.

Provides TypedDicts for structured data types used throughout the trading system.
"""

from typing import Any, Literal, TypedDict

# Type aliases for common types
TradingSignal = Literal["BUY", "SELL", "HOLD"]
ConfidenceLevel = Literal["HIGH", "MEDIUM", "LOW"]
RiskMode = Literal["NORMAL", "CAUTION", "SEVERE", "HOT", "CIRCUIT_BREAKER"]


class TechnicalReport(TypedDict, total=False):
    """Output from the Technical Analyst agent."""

    signal: TradingSignal
    confidence: float  # 0.0 - 1.0
    rationale: str
    indicator_validations: dict[str, Any]
    convergence_score: float
    key_levels: dict[str, float]
    _attempts: int
    _validation_errors: list[str]
    indicators: dict[str, Any]


class SentimentReport(TypedDict, total=False):
    """Output from the Sentiment Analyst agent."""

    overall_sentiment: Literal["POSITIVE", "NEGATIVE", "NEUTRAL"]
    confidence_score: float  # 0.0 - 1.0
    news_summary: str
    social_summary: str
    fear_greed_index: int
    key_events: list[str]
    _attempts: int
    _validation_errors: list[str]


class VisualReport(TypedDict, total=False):
    """Output from the Visual Analyst agent."""

    action: TradingSignal
    confidence: float  # 0.0 - 1.0
    reason: str
    chart_path: str | None
    patterns_detected: list[str]
    key_levels_visual: dict[str, float]
    _attempts: int
    _validation_errors: list[str]


class QABBAReport(TypedDict, total=False):
    """Output from the QABBA (Microstructure) agent."""

    signal: TradingSignal
    confidence: float  # 0.0 - 1.0
    rationale: str
    qabba_scores: dict[str, float]
    dynamic_levels: dict[str, float]
    orderbook_imbalance: float
    volume_profile: dict[str, Any]
    _attempts: int
    _validation_errors: list[str]


class RiskAssessment(TypedDict, total=False):
    """Risk assessment included in final decision."""

    position_size_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    risk_reward_ratio: float
    max_drawdown_risk: float


class FinalDecision(TypedDict, total=False):
    """Output from the Decision Agent."""

    final_decision: TradingSignal
    confidence_in_decision: ConfidenceLevel
    combined_reasoning: str
    key_conflicting_signals: list[str]
    agent_weights: dict[str, float]
    risk_assessment: RiskAssessment
    entry_price: float
    stop_loss: float
    take_profit: float
    _attempts: int
    _validation_errors: list[str]


class RiskVerdict(TypedDict, total=False):
    """Output from the Risk Manager agent."""

    verdict: Literal["APPROVE", "APPROVE_REDUCED", "VETO", "DELAY"]
    risk_score: float  # 0 - 10
    reasoning: str
    adjusted_position_size: float
    adjusted_stop_loss: float
    warnings: list[str]
    _attempts: int
    _validation_errors: list[str]


class TradeResult(TypedDict, total=False):
    """Result of a completed trade."""

    trade_id: str
    symbol: str
    side: TradingSignal
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_percent: float
    entry_time: str
    exit_time: str
    duration_seconds: float
    exit_reason: str


class TradingMetrics(TypedDict, total=False):
    """Aggregated trading metrics."""

    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    total_pnl_percent: float
    avg_win: float
    avg_loss: float
    best_trade: float
    worst_trade: float
    consecutive_wins: int
    consecutive_losses: int
    max_drawdown: float
    sharpe_ratio: float


class OrderInfo(TypedDict, total=False):
    """Information about a placed order."""

    order_id: int
    client_order_id: str
    symbol: str
    side: str
    type: str
    status: str
    price: float
    quantity: float
    executed_qty: float
    avg_price: float
    stop_price: float
    time: int
    update_time: int


class PositionInfo(TypedDict, total=False):
    """Information about a position."""

    symbol: str
    position_amt: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    liquidation_price: float
    leverage: int
    margin_type: str
    isolated_margin: float
    position_side: str
