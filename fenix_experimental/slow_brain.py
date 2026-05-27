from __future__ import annotations

import time
from typing import Any

from .models import ExperimentalRegime

_CONF_MAP = {"LOW": 0.4, "MEDIUM": 0.6, "HIGH": 0.8, "VERY_HIGH": 0.9}
_SIGNAL_MAP = {
    "BUY": "LONG",
    "LONG": "LONG",
    "SELL": "SHORT",
    "SHORT": "SHORT",
    "HOLD": "NEUTRAL",
    "NEUTRAL": "NEUTRAL",
}


def _as_confidence(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, str):
        return _CONF_MAP.get(value.upper(), 0.5)
    return 0.5


def _as_bias(report: dict[str, Any]) -> str:
    raw = str(report.get("signal") or report.get("decision") or "HOLD").upper()
    return _SIGNAL_MAP.get(raw, "NEUTRAL")


class SlowBrain:
    """Publishes a short-lived trading regime every 15 seconds."""

    def __init__(self, ttl_seconds: int = 15):
        self.ttl_seconds = ttl_seconds
        self.last_regime: ExperimentalRegime | None = None

    def publish_regime(
        self,
        *,
        technical_report: dict[str, Any] | None,
        qabba_report: dict[str, Any] | None,
        timestamp: float | None = None,
    ) -> ExperimentalRegime:
        now = float(timestamp if timestamp is not None else time.time())
        technical = dict(technical_report or {})
        qabba = dict(qabba_report or {})

        tech_bias = _as_bias(technical)
        qabba_bias = _as_bias(qabba)
        tech_conf = _as_confidence(technical.get("confidence"))
        qabba_conf = _as_confidence(qabba.get("confidence"))
        adx = float(technical.get("adx") or technical.get("trend_strength") or 20.0)
        chop = float(qabba.get("chop") or technical.get("chop") or 50.0)

        bias = "NEUTRAL"
        confidence = 0.45
        reason = "Technical/QABBA inconclusive"

        if tech_bias == qabba_bias and tech_bias in {"LONG", "SHORT"}:
            bias = tech_bias
            confidence = max(0.55, min(0.92, (tech_conf + qabba_conf) / 2.0))
            reason = f"Technical and QABBA aligned on {bias}"
        elif qabba_bias in {"LONG", "SHORT"} and qabba_conf >= 0.72 and tech_bias == "NEUTRAL":
            bias = qabba_bias
            confidence = min(0.85, 0.52 + qabba_conf * 0.42)
            reason = f"QABBA carries directional bias ({bias}) while technical is neutral"
        elif tech_bias in {"LONG", "SHORT"} and tech_conf >= 0.76 and qabba_bias == "NEUTRAL":
            bias = tech_bias
            confidence = min(0.84, 0.50 + tech_conf * 0.40)
            reason = f"Technical carries directional bias ({bias}) while QABBA is neutral"
        elif tech_bias in {"LONG", "SHORT"} and qabba_bias in {"LONG", "SHORT"}:
            bias = "NEUTRAL"
            confidence = max(0.35, min(tech_conf, qabba_conf))
            reason = f"Technical ({tech_bias}) and QABBA ({qabba_bias}) disagree"

        if bias == "NEUTRAL" or chop >= 60:
            max_spread_bps = 0.90
            min_ofi_required = 0.12
        elif adx >= 30 and chop < 45:
            max_spread_bps = 1.80
            min_ofi_required = 0.05
        else:
            max_spread_bps = 1.20
            min_ofi_required = 0.08

        regime = ExperimentalRegime(
            bias=bias,
            confidence=confidence,
            max_spread_bps=max_spread_bps,
            min_ofi_required=min_ofi_required,
            ttl_seconds=self.ttl_seconds,
            reasoning=reason,
            timestamp=now,
            metadata={
                "technical_bias": tech_bias,
                "technical_confidence": tech_conf,
                "qabba_bias": qabba_bias,
                "qabba_confidence": qabba_conf,
                "adx": adx,
                "chop": chop,
            },
        )
        self.last_regime = regime
        return regime
