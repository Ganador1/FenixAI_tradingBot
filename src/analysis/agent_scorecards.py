# src/analysis/agent_scorecards.py
"""Agent performance scorecards (multi-agent LLM trading, arXiv:2402.03755).

Tracks each agent's evaluated accuracy from ReasoningBank outcome labels and
converts it into a bounded weight multiplier for the Decision Agent's
directional score. Agents that have demonstrably been right more often get
more voting power; agents that have been wrong lose influence — instead of
the static per-timeframe weights used previously.

Design constraints:
- Read-only over ReasoningBank (no new persistence).
- Cheap: results cached with a TTL so the hot decision path never recomputes.
- Conservative: multipliers bounded to [0.5, 1.5] and only applied once an
  agent has a minimum number of evaluated entries (cold start = neutral 1.0).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Map decision-agent vote keys -> ReasoningBank agent names.
AGENT_BANK_NAMES = {
    "tech": "technical_agent",
    "qabba": "qabba_agent",
    "visual": "visual_agent",
    "sentiment": "sentiment_agent",
}

_MIN_EVALUATED = int(os.getenv("FENIX_SCORECARD_MIN_EVALUATED", "20"))
_LOOKBACK = int(os.getenv("FENIX_SCORECARD_LOOKBACK", "100"))
_CACHE_TTL_SECONDS = float(os.getenv("FENIX_SCORECARD_CACHE_TTL", "300"))
_MULT_MIN = 0.5
_MULT_MAX = 1.5
# Accuracy mapped linearly: 35% accuracy -> 0.5x, 50% -> 1.0x, 65% -> 1.5x.
_ACC_LOW = 0.35
_ACC_HIGH = 0.65


@dataclass
class AgentScore:
    agent: str
    evaluated: int
    success_rate: float
    avg_reward: float
    multiplier: float


def _accuracy_to_multiplier(success_rate: float) -> float:
    """Linear map from accuracy to a bounded weight multiplier."""
    if success_rate <= _ACC_LOW:
        return _MULT_MIN
    if success_rate >= _ACC_HIGH:
        return _MULT_MAX
    span = _ACC_HIGH - _ACC_LOW
    return _MULT_MIN + (success_rate - _ACC_LOW) / span * (_MULT_MAX - _MULT_MIN)


class AgentScorecards:
    """TTL-cached accuracy multipliers per agent."""

    def __init__(self, reasoning_bank=None):
        self._bank = reasoning_bank
        self._cache: dict[str, AgentScore] = {}
        self._cache_time: float = 0.0

    def _resolve_bank(self):
        if self._bank is not None:
            return self._bank
        try:
            from src.memory.reasoning_bank import get_reasoning_bank

            self._bank = get_reasoning_bank()
        except Exception as e:  # pragma: no cover - import-time env issues
            logger.warning("AgentScorecards: could not load ReasoningBank: %s", e)
        return self._bank

    def _refresh(self) -> None:
        bank = self._resolve_bank()
        if bank is None:
            return
        scores: dict[str, AgentScore] = {}
        for vote_key, bank_name in AGENT_BANK_NAMES.items():
            try:
                stats = bank.get_success_rate(bank_name, lookback=_LOOKBACK)
            except Exception as e:
                logger.debug("Scorecard lookup failed for %s: %s", bank_name, e)
                continue
            evaluated = int(stats.get("total_evaluated", 0) or 0)
            success_rate = float(stats.get("success_rate", 0.0) or 0.0)
            avg_reward = float(stats.get("avg_reward", 0.0) or 0.0)
            multiplier = (
                _accuracy_to_multiplier(success_rate) if evaluated >= _MIN_EVALUATED else 1.0
            )
            scores[vote_key] = AgentScore(
                agent=bank_name,
                evaluated=evaluated,
                success_rate=success_rate,
                avg_reward=avg_reward,
                multiplier=multiplier,
            )
        self._cache = scores
        self._cache_time = time.monotonic()
        applied = {k: round(v.multiplier, 2) for k, v in scores.items() if v.multiplier != 1.0}
        if applied:
            logger.info("Agent scorecard multipliers active: %s", applied)

    def get_multipliers(self) -> dict[str, float]:
        """Return {vote_key: multiplier} for all tracked agents (cached)."""
        if not self._cache or (time.monotonic() - self._cache_time) > _CACHE_TTL_SECONDS:
            self._refresh()
        return {key: score.multiplier for key, score in self._cache.items()}

    def get_scores(self) -> dict[str, AgentScore]:
        """Return full scorecards (cached); useful for dashboards/logging."""
        if not self._cache or (time.monotonic() - self._cache_time) > _CACHE_TTL_SECONDS:
            self._refresh()
        return dict(self._cache)


_scorecards: AgentScorecards | None = None


def get_agent_scorecards() -> AgentScorecards:
    global _scorecards
    if _scorecards is None:
        _scorecards = AgentScorecards()
    return _scorecards


def get_scorecard_multipliers() -> dict[str, float]:
    """Convenience accessor used by the Decision Agent.

    Disabled (all 1.0) when FENIX_SCORECARD_WEIGHTS=0.
    """
    if os.getenv("FENIX_SCORECARD_WEIGHTS", "1") == "0":
        return {}
    try:
        return get_agent_scorecards().get_multipliers()
    except Exception as e:  # never break the decision path
        logger.warning("Scorecard multipliers unavailable: %s", e)
        return {}
