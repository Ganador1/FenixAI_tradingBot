from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Avoid importing heavy agent modules at runtime to prevent transitive ML backends (TF/TFP/JAX/Torch)
# Use TYPE_CHECKING for type hints only. At runtime, alias to `Any` to keep this module lightweight.
if TYPE_CHECKING:
    from agents.enhanced_qabba_agent import (
        EnhancedQabbaOutput as QABBAAnalysisOutput,
    )
    from agents.enhanced_technical_analyst import (
        EnhancedTechnicalOutput as TechnicalAnalysisOutput,
    )
    from agents.visual_analyst_enhanced import (
        EnhancedVisualChartAnalysisOutput as VisualAnalysisOutput,
    )
else:
    TechnicalAnalysisOutput = Any  # type: ignore
    VisualAnalysisOutput = Any  # type: ignore
    QABBAAnalysisOutput = Any  # type: ignore


class SentimentOutput(BaseModel):
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    overall_sentiment: Literal["POSITIVE", "NEGATIVE", "NEUTRAL"] = Field(...)
    positive_texts_count: int = Field(..., ge=0)
    negative_texts_count: int = Field(..., ge=0)
    neutral_texts_count: int = Field(..., ge=0)
    reasoning: str
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    fear_greed_value_used: int = Field(..., ge=0, le=100)
    fear_greed_influence_factor: float = Field(..., ge=0.0, le=1.0)
    avg_data_quality_score: float = Field(..., ge=0.0, le=1.0)
    total_texts_analyzed_by_llm: int = Field(..., ge=0)
    total_texts_fetched_initially: int = Field(..., ge=0)
    top_keywords_found: list[str] = Field(default_factory=list)
    sentiment_trend_short_term: str = "INSUFFICIENT_DATA"


class FinalDecisionOutput(BaseModel):
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    final_decision: Literal["BUY", "SELL", "HOLD"] = Field(...)
    combined_reasoning: str
    confidence_in_decision: Literal["HIGH", "MEDIUM", "LOW"] = Field(...)
    key_conflicting_signals: list[str] = Field(default_factory=list)
    risk_assessment: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helper utilities for tests
# ---------------------------------------------------------------------------


def create_mock_outputs_for_testing() -> dict[str, BaseModel]:
    """Return a dictionary with mock outputs for all agents.
    Imported lazily to avoid heavy imports at module import-time.
    """
    from backtest import (
        get_mock_qabba_output,
        get_mock_sentiment_output,
        get_mock_technical_analysis_output,
        get_mock_visual_analysis_output,
    )

    tech_metrics = {
        "last_price": 100.0,
        "rsi": 50.0,
        "macd_line": 0.0,
        "signal_line": 0.0,
        "atr": 1.0,
        "adx": 20.0,
    }

    return {
        "sentiment": get_mock_sentiment_output(),
        "technical": get_mock_technical_analysis_output(tech_metrics),
        "visual": get_mock_visual_analysis_output(),
        "qabba": get_mock_qabba_output(),
    }


def convert_to_decision_inputs(
    sentiment: SentimentOutput,
    technical: TechnicalAnalysisOutput,
    visual: VisualAnalysisOutput,
    qabba: QABBAAnalysisOutput,
) -> tuple[SentimentOutput, TechnicalAnalysisOutput, VisualAnalysisOutput, QABBAAnalysisOutput]:
    """Prepare agent outputs for the decision agent.

    Currently this function simply returns the inputs unchanged, but having a
    dedicated conversion step allows future adaptations without changing the
    tests or callers.
    """
    return sentiment, technical, visual, qabba


# Tipos de salida estandarizados


class TechnicalAgentOutput(BaseModel):
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)
    signal: Literal["BUY", "SELL", "HOLD"] = Field(default="HOLD")
    action: Literal["BUY", "SELL", "HOLD"] = Field(default="HOLD")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    rationale: str = ""
    indicator_validations: dict[str, Any] = Field(default_factory=dict)


class VisualAgentOutput(BaseModel):
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)
    action: Literal["BUY", "SELL", "HOLD"] = Field(default="HOLD")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = ""
    chart_path: str | None = None


class QABBAAgentOutput(BaseModel):
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)
    signal: Literal["BUY", "SELL", "HOLD"] = Field(default="HOLD")
    action: Literal["BUY", "SELL", "HOLD"] = Field(default="HOLD")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    rationale: str = ""
    qabba_scores: dict[str, Any] | None = None
    dynamic_levels: dict[str, Any] | None = None


def to_technical_output(data: Any) -> TechnicalAgentOutput:
    if isinstance(data, TechnicalAgentOutput):
        return data
    if hasattr(data, "signal") or hasattr(data, "action"):
        return TechnicalAgentOutput(
            signal=getattr(data, "signal", getattr(data, "action", "HOLD")),
            action=getattr(data, "action", getattr(data, "signal", "HOLD")),
            confidence=float(getattr(data, "confidence", 0.5) or 0.5),
            rationale=str(
                getattr(data, "rationale", getattr(data, "reason", getattr(data, "reasoning", "")))
            )
            or "",
            indicator_validations=getattr(data, "indicator_validations", {}) or {},
        )
    if isinstance(data, dict):
        return TechnicalAgentOutput(
            signal=str(data.get("signal", data.get("action", "HOLD"))).upper(),
            action=str(data.get("action", data.get("signal", "HOLD"))).upper(),
            confidence=float(data.get("confidence", 0.5) or 0.5),
            rationale=str(
                data.get("rationale") or data.get("reason") or data.get("reasoning") or ""
            ),
            indicator_validations=data.get("indicadores", {}) or {},
        )
    return TechnicalAgentOutput()


def to_visual_output(data: Any) -> VisualAgentOutput:
    if isinstance(data, VisualAgentOutput):
        return data
    if hasattr(data, "action"):
        return VisualAgentOutput(
            action=getattr(data, "action", "HOLD"),
            confidence=float(getattr(data, "confidence", 0.5) or 0.5),
            reason=str(
                getattr(data, "reason", getattr(data, "rationale", getattr(data, "reasoning", "")))
            )
            or "",
            chart_path=getattr(data, "chart_path", None),
        )
    if isinstance(data, dict):
        return VisualAgentOutput(
            action=str(data.get("action", "HOLD")).upper(),
            confidence=float(data.get("confidence", 0.5) or 0.5),
            reason=str(data.get("reason") or data.get("rationale") or data.get("reasoning") or ""),
            chart_path=data.get("chart_path"),
        )
    return VisualAgentOutput()


def to_qabba_output(data: Any) -> QABBAAgentOutput:
    if isinstance(data, QABBAAgentOutput):
        return data
    if hasattr(data, "signal") or hasattr(data, "action"):
        sig = getattr(data, "signal", getattr(data, "action", "HOLD"))
        act = getattr(data, "action", getattr(data, "signal", "HOLD"))
        if isinstance(sig, str) and sig.endswith("_QABBA"):
            sig = sig.split("_")[0]
        if isinstance(act, str) and act.endswith("_QABBA"):
            act = act.split("_")[0]
        return QABBAAgentOutput(
            signal=sig,
            action=act,
            confidence=float(getattr(data, "confidence", 0.5) or 0.5),
            rationale=str(
                getattr(data, "rationale", getattr(data, "reason", getattr(data, "reasoning", "")))
            )
            or "",
            qabba_scores=getattr(data, "qabba_scores", None),
            dynamic_levels=getattr(data, "dynamic_levels", None),
        )
    if isinstance(data, dict):
        sig = str(data.get("signal", data.get("action", "HOLD"))).upper()
        act = str(data.get("action", data.get("signal", "HOLD"))).upper()
        if sig.endswith("_QABBA"):
            sig = sig.split("_")[0]
        if act.endswith("_QABBA"):
            act = act.split("_")[0]
        return QABBAAgentOutput(
            signal=sig,
            action=act,
            confidence=float(data.get("confidence", 0.5) or 0.5),
            rationale=str(
                data.get("rationale") or data.get("reason") or data.get("reasoning") or ""
            ),
            qabba_scores=data.get("qabba_scores"),
            dynamic_levels=data.get("dynamic_levels"),
        )
    return QABBAAgentOutput()


class RiskManagerOutput(BaseModel):
    """Structured output for the Risk Manager agent."""

    verdict: str = "APPROVE"  # APPROVE, VETO, DELAY
    position_size_pct: float = 0.02
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04
    reasoning: str = ""
    risk_level: str = "MEDIUM"  # LOW, MEDIUM, HIGH
    max_exposure_pct: float = 0.05
    adjusted_confidence: float = 0.5
