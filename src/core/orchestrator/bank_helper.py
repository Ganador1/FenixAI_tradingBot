# src/core/orchestrator/bank_helper.py
"""
ReasoningBank helper functions for Fenix Trading Bot.

Provides context retrieval and decision storage from the
ReasoningBank memory system.
"""

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Check availability of ReasoningBank
try:
    from src.memory.reasoning_bank import get_reasoning_bank

    REASONING_BANK_AVAILABLE = True
except ImportError:
    REASONING_BANK_AVAILABLE = False
    get_reasoning_bank = None


def get_agent_context_from_bank(
    reasoning_bank: Any | None, agent_name: str, current_prompt: str, limit: int = 3
) -> str:
    """
    Gets relevant historical context from ReasoningBank.

    Searches for similar past decisions to inform the current agent.
    """
    if not reasoning_bank or not REASONING_BANK_AVAILABLE:
        return ""

    try:
        # Search for relevant entries
        relevant = reasoning_bank.get_relevant_context(
            agent_name=agent_name,
            current_prompt=current_prompt,
            limit=limit,
        )

        if not relevant:
            return ""

        context_parts = ["### Historical Context (similar past decisions):"]
        now = datetime.now().astimezone()

        for entry in relevant:
            success_status = ""
            if entry.success is not None:
                success_status = " ✓" if entry.success else " ✗"

            # Calculate relative time to give the model context about freshness
            time_str = "unknown time"
            try:
                dt_entry = datetime.fromisoformat(entry.created_at)
                if dt_entry.tzinfo is None:
                    dt_entry = dt_entry.replace(tzinfo=now.tzinfo)

                delta = now - dt_entry
                total_seconds = delta.total_seconds()

                if total_seconds < 60:
                    time_str = "just now"
                elif total_seconds < 3600:
                    time_str = f"{int(total_seconds // 60)}m ago"
                elif total_seconds < 86400:
                    time_str = f"{int(total_seconds // 3600)}h ago"
                else:
                    time_str = f"{int(delta.days)}d ago"
            except Exception:
                pass

            context_parts.append(
                f"- [{time_str}] [{entry.action}{success_status}] Conf: {entry.confidence:.0%} | "
                f"{entry.reasoning[:150]}..."
            )

        return "\n".join(context_parts)
    except Exception as e:
        logger.warning("Error getting context from ReasoningBank: %s", e)
        return ""


# Synthesized strategies are recomputed at most every TTL seconds (they only
# change as outcome labels accumulate, so frequent recomputation is wasted).
_STRATEGY_CACHE: dict[str, tuple[float, str]] = {}
_STRATEGY_CACHE_TTL = 600.0


def get_synthesized_strategies_block(
    reasoning_bank: Any | None,
    agent_name: str,
    max_strategies: int = 3,
) -> str:
    """Return a distilled-strategies prompt block (ReasoningBank paper core).

    Unlike raw historical context, these are aggregate rules mined from
    evaluated outcomes ("high-confidence BUYs succeed 73%"), which is the
    actionable distillation the paper showed matters most.
    """
    if not reasoning_bank or not REASONING_BANK_AVAILABLE:
        return ""

    import time as _time

    cached = _STRATEGY_CACHE.get(agent_name)
    if cached and (_time.monotonic() - cached[0]) < _STRATEGY_CACHE_TTL:
        return cached[1]

    block = ""
    try:
        strategies = reasoning_bank.synthesize_strategies(
            agent_name=agent_name,
            min_success_rate=0.60,
            min_sample_size=10,
        )
        if strategies:
            lines = ["### Proven strategies (mined from evaluated outcomes):"]
            for strat in strategies[:max_strategies]:
                rule = strat.get("rule", "")
                rate = strat.get("success_rate", 0.0)
                n = strat.get("sample_size", 0)
                reward = strat.get("avg_reward")
                # Skip non-actionable rules: "HOLD is effective" has ~zero
                # reward by construction and only reinforces the bot's
                # existing HOLD bias. Also skip rules with no positive
                # expected reward — high success rate with <=0 reward means
                # the rule wins often but earns nothing.
                if "HOLD" in rule.upper():
                    continue
                if isinstance(reward, (int, float)) and reward <= 0:
                    continue
                reward_txt = f", avg reward {reward:+.2f}%" if isinstance(reward, (int, float)) else ""
                lines.append(f"- {rule} (success {rate:.0%}, n={n}{reward_txt})")
            if len(lines) > 1:
                block = "\n".join(lines)
    except Exception as e:
        logger.debug("Strategy synthesis unavailable for %s: %s", agent_name, e)

    _STRATEGY_CACHE[agent_name] = (_time.monotonic(), block)
    return block


def store_agent_decision(
    reasoning_bank: Any | None,
    agent_name: str,
    prompt: str,
    result: dict,
    raw_response: str,
    backend: str,
    latency_ms: float,
) -> str | None:
    """
    Stores the agent decision in ReasoningBank.

    Returns:
        prompt_digest for later tracking
    """
    if not reasoning_bank or not REASONING_BANK_AVAILABLE:
        return None

    try:
        entry = reasoning_bank.store_entry(
            agent_name=agent_name,
            prompt=prompt,
            normalized_result=result,
            raw_response=raw_response,
            backend=backend,
            latency_ms=latency_ms,
            metadata={
                "source": "langgraph_orchestrator",
                "timestamp": datetime.now().isoformat(),
            },
        )
        return entry.prompt_digest
    except Exception as e:
        logger.warning("Error storing in ReasoningBank: %s", e)
        return None
