from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

Bias = Literal["LONG", "SHORT", "NEUTRAL"]
Action = Literal["LONG", "SHORT", "HOLD"]


@dataclass
class ExperimentalRegime:
    bias: Bias
    confidence: float
    max_spread_bps: float
    min_ofi_required: float
    ttl_seconds: int
    reasoning: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_fresh(self, now: float | None = None) -> bool:
        if now is None:
            now = time.time()
        return (now - self.timestamp) <= self.ttl_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "bias": self.bias,
            "confidence": round(float(self.confidence), 4),
            "max_spread_bps": round(float(self.max_spread_bps), 4),
            "min_ofi_required": round(float(self.min_ofi_required), 4),
            "ttl_seconds": int(self.ttl_seconds),
            "reasoning": self.reasoning,
            "timestamp": float(self.timestamp),
            "metadata": dict(self.metadata),
        }


@dataclass
class MarketTick:
    bid: float
    ask: float
    bid_qty: float
    ask_qty: float
    timestamp: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0 if self.bid > 0 and self.ask > 0 else 0.0

    @property
    def spread_bps(self) -> float:
        mid = self.mid
        if mid <= 0:
            return 0.0
        return (self.ask - self.bid) / mid * 10000.0

    @property
    def ofi(self) -> float:
        total = self.bid_qty + self.ask_qty
        if total <= 0:
            return 0.0
        return (self.bid_qty - self.ask_qty) / total


@dataclass
class TriggerDecision:
    action: Action
    confidence: float
    price: float
    timestamp: float
    reason: str
    regime_bias: Bias
    ofi: float
    spread_bps: float
    pred_bps: float = 0.0
    direction_accuracy: float = 0.0
    source: str = "heuristic"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_engine_decision(self) -> str:
        if self.action == "LONG":
            return "BUY"
        if self.action == "SHORT":
            return "SELL"
        return "HOLD"

    def to_payload(self) -> dict[str, Any]:
        return {
            "decision": self.to_engine_decision(),
            "confidence": round(float(self.confidence), 4),
            "price": round(float(self.price), 6),
            "reasoning": self.reason,
            "regime_bias": self.regime_bias,
            "ofi": round(float(self.ofi), 4),
            "spread_bps": round(float(self.spread_bps), 4),
            "pred_bps": round(float(self.pred_bps), 4),
            "direction_accuracy": round(float(self.direction_accuracy), 4),
            "source": self.source,
            "timestamp": float(self.timestamp),
            "metadata": dict(self.metadata),
        }
