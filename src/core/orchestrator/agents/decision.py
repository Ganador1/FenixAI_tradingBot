# src/core/orchestrator/agents/decision.py
"""
Decision Agent for Fenix Trading Bot.

Synthesizes all sub-agent reports (technical, sentiment, visual, QABBA)
into a final BUY / SELL / HOLD trade decision with confidence level.
Optionally invokes the Reasoning Judge for an independent critique.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any

from src.config.judge_config import get_judge_model_config
from src.core.orchestrator.agents.base import (
    save_legacy_agent_log,
)
from src.core.orchestrator.bank_helper import (
    REASONING_BANK_AVAILABLE,
    get_agent_context_from_bank,
    get_synthesized_strategies_block,
    store_agent_decision,
)
from src.core.orchestrator.retry_system import invoke_with_retry_and_validation
from src.core.orchestrator.state import FenixAgentState
from src.inference.reasoning_judge import ReasoningJudgePayload, ReasoningLLMJudge
from src.prompts.agent_prompts import format_prompt
from src.system.tracing import get_tracer

logger = logging.getLogger(__name__)


def _decision_safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return default


def _decision_weight(env_key: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(env_key, str(default))))
    except Exception:
        return default


def _decision_sentiment_signal(report: dict[str, Any]) -> str:
    sentiment_raw = str(report.get("overall_sentiment", report.get("signal", "NEUTRAL"))).upper()
    if sentiment_raw in {"POSITIVE", "BULLISH", "BUY"}:
        return "BUY"
    if sentiment_raw in {"NEGATIVE", "BEARISH", "SELL"}:
        return "SELL"
    return "HOLD"


def _decision_trim_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."


def _decision_prompt_report(
    report: Any,
    *,
    rationale_max_chars: int,
) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {}

    keep = {
        "signal",
        "action",
        "confidence",
        "rationale",
        "reason",
        "reasoning",
        "_directional_score",
        "_directional_score_source",
    }
    out = {k: report.get(k) for k in keep if k in report}

    rationale = (
        out.get("rationale")
        or out.get("reason")
        or out.get("reasoning")
        or report.get("combined_reasoning")
    )
    if rationale:
        out["rationale"] = _decision_trim_text(rationale, rationale_max_chars)
    out.pop("reason", None)
    out.pop("reasoning", None)
    return out


def _decision_market_metrics_for_prompt(
    state: FenixAgentState,
    indicators_for_decision: dict[str, Any],
) -> dict[str, Any]:
    timeframe = str(state.get("timeframe", "15m"))
    short_tf = timeframe in {"1m", "3m", "5m", "15m"}
    keep_keys = [
        "last_price",
        "rsi",
        "macd_histogram",
        "atr",
        "vwap",
        "bb_condition",
        "bb_inside_kc",
        "donchian_position",
        "chop",
        "chop_regime",
        "supertrend_direction",
        "supertrend_signal",
        "trend_conflict",
        "market_condition",
        "vpvr_value_area_low",
        "vpvr_value_area_high",
        "ema_9",
        "ema_20",
        "ema_21",
        "ma_50",
        "ma_200",
    ]
    if not short_tf:
        keep_keys.extend(
            [
                "mfi",
                "cmf",
                "bollinger_upper",
                "bollinger_lower",
                "bollinger_width",
            ]
        )

    metrics = {
        key: indicators_for_decision.get(key) for key in keep_keys if key in indicators_for_decision
    }
    for key in ("obi", "wdi", "trade_imbalance_5s"):
        if key in state:
            metrics[key] = state.get(key)
    return metrics


def _rebuild_directional_score_from_state(state: FenixAgentState) -> tuple[float, list[str]]:
    timeframe = str(state.get("timeframe", "15m"))
    short_tf = timeframe in {"1m", "3m", "5m"}

    tech_report = state.get("technical_report", {}) or {}
    qabba_report = state.get("qabba_report", {}) or {}
    visual_report = state.get("visual_report", {}) or {}
    sentiment_report = state.get("sentiment_report", {}) or {}

    tech_sig = str(tech_report.get("signal", "HOLD")).upper()
    qabba_sig = str(qabba_report.get("signal", "HOLD")).upper()
    visual_sig = str(visual_report.get("action", "HOLD")).upper()
    sentiment_sig = _decision_sentiment_signal(sentiment_report)

    tech_conf = _decision_safe_float(tech_report.get("confidence"))
    qabba_conf = _decision_safe_float(qabba_report.get("confidence"))
    visual_conf = _decision_safe_float(visual_report.get("confidence"))
    sentiment_conf = _decision_safe_float(
        sentiment_report.get("confidence_score", sentiment_report.get("confidence"))
    )

    w_tech = _decision_weight("FENIX_W_TECHNICAL", 0.45 if short_tf else 0.30)
    w_qabba = _decision_weight("FENIX_W_QABBA", 0.45 if short_tf else 0.30)
    w_visual = _decision_weight("FENIX_W_VISUAL", 0.10 if short_tf else 0.25)
    w_sentiment = _decision_weight("FENIX_W_SENTIMENT", 0.0 if short_tf else 0.15)

    # Agent scorecards (arXiv:2402.03755): scale static weights by each
    # agent's evaluated accuracy so consistently-right agents gain influence.
    try:
        from src.analysis.agent_scorecards import get_scorecard_multipliers

        _mults = get_scorecard_multipliers()
    except Exception:
        _mults = {}
    if _mults:
        w_tech *= _mults.get("tech", 1.0)
        w_qabba *= _mults.get("qabba", 1.0)
        w_visual *= _mults.get("visual", 1.0)
        w_sentiment *= _mults.get("sentiment", 1.0)

    active_agents: list[tuple[str, str, float, float]] = []
    if tech_report:
        active_agents.append(("tech", tech_sig, tech_conf, w_tech))
    if qabba_report:
        active_agents.append(("qabba", qabba_sig, qabba_conf, w_qabba))
    if visual_report:
        active_agents.append(("visual", visual_sig, visual_conf, w_visual))
    if sentiment_report and sentiment_sig != "HOLD":
        active_agents.append(("sentiment", sentiment_sig, sentiment_conf, w_sentiment))

    total_w = sum(weight for _, _, _, weight in active_agents) or 1.0
    directional_score = 0.0
    agent_votes: list[str] = []

    for name, signal, confidence, weight in active_agents:
        norm_w = weight / total_w
        if signal == "BUY":
            directional_score += confidence * norm_w
        elif signal == "SELL":
            directional_score -= confidence * norm_w
        agent_votes.append(f"{name}={signal}({confidence:.2f},w={norm_w:.2f})")

    obi = _decision_safe_float(state.get("obi"), 1.0)
    wdi = _decision_safe_float(state.get("wdi"))
    trade_imb = _decision_safe_float(state.get("trade_imbalance_5s"))

    obi_buy = _decision_weight("FENIX_OBI_BUY", 1.25)
    obi_sell = _decision_weight("FENIX_OBI_SELL", 0.80)
    micro_buy = obi >= obi_buy and wdi >= 0.10 and trade_imb >= 0.10
    micro_sell = obi <= obi_sell and wdi <= -0.10 and trade_imb <= -0.10

    if directional_score > 0 and micro_buy:
        directional_score *= 1.15
    elif directional_score < 0 and micro_sell:
        directional_score *= 1.15

    directional_score = max(-1.0, min(1.0, directional_score))
    return directional_score, agent_votes


def _attach_directional_fields(state: FenixAgentState, report: dict[str, Any]) -> dict[str, Any]:
    rebuilt_score, rebuilt_votes = _rebuild_directional_score_from_state(state)
    try:
        existing_score = report.get("_directional_score")
        if existing_score is None:
            raise ValueError("missing directional score")
        score = float(existing_score)
        report["_directional_score"] = max(-1.0, min(1.0, score))
        report.setdefault("_directional_score_source", "decision_agent_llm")
    except Exception:
        report["_directional_score"] = rebuilt_score
        report["_directional_score_source"] = "decision_agent_weighted_reports"

    if not isinstance(report.get("_directional_agent_votes"), list) or not report.get(
        "_directional_agent_votes"
    ):
        report["_directional_agent_votes"] = rebuilt_votes

    return report


def create_decision_agent_node(llm: Any, reasoning_bank: Any = None):
    """Creates the final decision agent node with retry and validation system."""

    async def decision_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()
        judge_verdict: dict[str, Any] | None = None
        # On short timeframes, a judge call adds latency. Keep it opt-in there.
        timeframe_for_judge = state.get("timeframe", "15m")
        _judge_env = os.getenv("FENIX_ENABLE_JUDGE")
        if _judge_env is None:
            enable_judge = timeframe_for_judge not in {"1m", "3m", "5m", "15m"}
        else:
            enable_judge = _judge_env != "0"

        try:
            timeframe = state.get("timeframe", "15m")
            short_tf = timeframe in {"1m", "3m", "5m", "15m"}
            # On short timeframes, long retry chains create "stale" decisions. Prefer quick recovery.
            # Increased retries slightly to reduce false HOLD fallbacks
            default_max_retries = 2 if short_tf else 3
            try:
                max_retries = int(os.getenv("FENIX_DECISION_MAX_RETRIES", str(default_max_retries)))
            except Exception:
                max_retries = default_max_retries

            indicators_for_decision = state.get("indicators_filtered", state.get("indicators", {}))
            rationale_max_chars = 220 if short_tf else 360
            market_metrics_for_prompt = _decision_market_metrics_for_prompt(
                state,
                indicators_for_decision if isinstance(indicators_for_decision, dict) else {},
            )

            # Always trim technical/qabba payloads: the raw reports often embed large indicator payloads
            # which inflate Decision tokens and increase invalid JSON/validation failures.
            tech_for_prompt = _decision_prompt_report(
                state.get("technical_report", {}) or {},
                rationale_max_chars=rationale_max_chars,
            )
            qabba_for_prompt = _decision_prompt_report(
                state.get("qabba_report", {}) or {},
                rationale_max_chars=rationale_max_chars,
            )
            sentiment_for_prompt = _decision_prompt_report(
                state.get("sentiment_report", {}) or {},
                rationale_max_chars=rationale_max_chars,
            )
            visual_for_prompt = _decision_prompt_report(
                state.get("visual_report", {}) or {},
                rationale_max_chars=rationale_max_chars,
            )

            # Web3 Intelligence data (from Binance Skills Hub, when enabled)
            web3_for_prompt = _decision_prompt_report(
                state.get("web3_intel_report", {}) or {},
                rationale_max_chars=rationale_max_chars,
            )
            # Enrich with web3-specific fields if present
            raw_web3 = state.get("web3_intel_report", {}) or {}
            if raw_web3 and not raw_web3.get("_validation_failed"):
                for extra_key in ("smart_money_bias", "social_hype_level", "web3_risk_flags"):
                    if extra_key in raw_web3:
                        web3_for_prompt[extra_key] = raw_web3[extra_key]

            messages = format_prompt(
                "decision_agent",
                symbol=state.get("symbol", "BTCUSDT"),
                technical_analysis=json.dumps(tech_for_prompt, indent=2, default=str),
                sentiment_analysis=json.dumps(sentiment_for_prompt, indent=2, default=str),
                visual_analysis=json.dumps(visual_for_prompt, indent=2, default=str),
                qabba_analysis=json.dumps(qabba_for_prompt, indent=2, default=str),
                market_metrics=json.dumps(market_metrics_for_prompt, default=str),
                active_positions="[]",
                web3_intelligence=json.dumps(web3_for_prompt, indent=2, default=str)
                if web3_for_prompt
                else "Not available",
            )

            if not messages:
                raise ValueError("Could not format decision prompt")

            # === REASONING BANK RETRIEVAL ===
            if reasoning_bank and REASONING_BANK_AVAILABLE:
                try:
                    bank_query = json.dumps(
                        {
                            "technical": tech_for_prompt.get("signal"),
                            "qabba": qabba_for_prompt.get("signal"),
                            "timeframe": timeframe,
                        }
                    )
                    historical_context = get_agent_context_from_bank(
                        reasoning_bank=reasoning_bank,
                        agent_name="decision_agent",
                        current_prompt=bank_query,
                        limit=3,
                    )
                    if historical_context and messages:
                        max_context_chars = 450 if short_tf else 700
                        try:
                            max_context_chars = int(
                                os.getenv(
                                    "FENIX_DECISION_REASONING_CONTEXT_MAX_CHARS",
                                    str(max_context_chars),
                                )
                            )
                        except Exception:
                            pass
                        historical_context = _decision_trim_text(
                            historical_context,
                            max_context_chars,
                        )
                        messages[0]["content"] = (
                            messages[0]["content"] + "\n\n" + historical_context
                        )
                        logger.info("🧠 ReasoningBank: Retrieved context for Decision Agent")

                    # Distilled strategies (ReasoningBank paper): aggregate
                    # rules mined from evaluated outcomes, not raw history.
                    strategies_block = get_synthesized_strategies_block(
                        reasoning_bank=reasoning_bank,
                        agent_name="decision_agent",
                    )
                    if strategies_block and messages:
                        messages[0]["content"] = (
                            messages[0]["content"] + "\n\n" + strategies_block
                        )
                        logger.info("🧠 ReasoningBank: Injected synthesized strategies")
                except Exception as e:
                    logger.warning(f"Decision Agent: Failed to retrieve reasoning context: {e}")
            # === END REASONING BANK RETRIEVAL ===

            llm_messages = [
                {"role": "system", "content": messages[0]["content"]},
                {"role": "user", "content": messages[1]["content"]},
            ]

            timeout_sec: float | None = None
            if short_tf:
                try:
                    timeout_sec = float(os.getenv("FENIX_DECISION_TIMEOUT_SHORT_SEC", "45.0"))
                except Exception:
                    timeout_sec = 45.0

            def _fallback_report() -> dict[str, Any]:
                """
                Fallback logic when LLM fails/times out.

                Key improvement: Be LESS conservative - prefer directional signals over HOLD
                when at least one agent is confident.
                """
                tech = str((state.get("technical_report") or {}).get("signal", "HOLD")).upper()
                qabba = str((state.get("qabba_report") or {}).get("signal", "HOLD")).upper()
                try:
                    tech_conf = float(
                        (state.get("technical_report") or {}).get("confidence", 0.0) or 0.0
                    )
                except Exception:
                    tech_conf = 0.0
                try:
                    qabba_conf = float(
                        (state.get("qabba_report") or {}).get("confidence", 0.0) or 0.0
                    )
                except Exception:
                    qabba_conf = 0.0
                # Lowered threshold from 0.85 to 0.65 to allow more directional fallbacks
                strong_th = float(os.getenv("FENIX_DECISION_FALLBACK_STRONG_CONF", "0.65"))

                final = "HOLD"
                conf = "LOW"
                conflicts: list[str] = []

                # Both agree on directional signal
                if tech in {"BUY", "SELL"} and tech == qabba:
                    final = tech
                    conf = (
                        "HIGH" if tech_conf >= strong_th and qabba_conf >= strong_th else "MEDIUM"
                    )

                # QABBA directional, Technical HOLD - trust QABBA if confident
                elif qabba in {"BUY", "SELL"} and tech == "HOLD":
                    final = qabba
                    conf = "MEDIUM" if qabba_conf >= strong_th else "LOW"
                    conflicts.append(f"Technical HOLD vs QABBA {qabba}")

                # Technical directional, QABBA HOLD - trust Technical if confident
                elif tech in {"BUY", "SELL"} and qabba == "HOLD":
                    final = tech
                    conf = "MEDIUM" if tech_conf >= strong_th else "LOW"
                    conflicts.append(f"QABBA HOLD vs Technical {tech}")

                # Conflict: both directional but disagree - pick the more confident one
                elif tech in {"BUY", "SELL"} and qabba in {"BUY", "SELL"} and tech != qabba:
                    conflicts.append(f"Technical {tech} vs QABBA {qabba}")
                    # Pick the agent with higher confidence
                    if tech_conf >= qabba_conf and tech_conf >= strong_th:
                        final = tech
                        conf = "MEDIUM"
                    elif qabba_conf >= strong_th:
                        final = qabba
                        conf = "MEDIUM"
                    # else stay HOLD

                return {
                    "final_decision": final,
                    "confidence_in_decision": conf,
                    "combined_reasoning": "Consensus fallback: using agent signal consensus to avoid stale/invalid LLM decisions.",
                    "key_conflicting_signals": conflicts,
                    "risk_assessment": {},
                }

            # DECISION AGENT MUST ALWAYS USE FULL LLM INFERENCE
            # Non-blocking mode REMOVED - A/B tests require actual LLM decisions
            # Timeout fallback remains for safety, but no pre-emptive shortcuts

            try:
                coro = invoke_with_retry_and_validation(
                    llm=llm,
                    messages=llm_messages,
                    agent_type="decision_agent",
                    max_retries=max_retries,
                    base_delay=0.3,
                    required_keys=[
                        "final_decision",
                        "confidence_in_decision",
                        "combined_reasoning",
                    ],
                )
                if timeout_sec is not None:
                    report, attempts, errors = await asyncio.wait_for(coro, timeout=timeout_sec)
                else:
                    report, attempts, errors = await coro
            except asyncio.TimeoutError:
                logger.warning(
                    "decision_agent timed out after %.1fs (tf=%s); using consensus fallback",
                    float(timeout_sec or 0.0),
                    timeframe,
                )
                report = _fallback_report()
                attempts = 0
                errors = ["Timeout waiting for decision_agent LLM response"]

            # If the LLM repeatedly returns invalid output, prefer a deterministic consensus fallback
            # over propagating partial/invalid reports downstream.
            if (
                errors
                or report.get("_validation_failed")
                or report.get("error")
                or report.get("parse_error")
            ):
                fallback = _fallback_report()
                fallback["_llm_attempts"] = attempts
                fallback["_llm_errors"] = errors or report.get("_validation_errors") or []
                fallback["_llm_error"] = report.get("error") or (
                    "validation_failed" if report.get("_validation_failed") else None
                )
                report = fallback
                attempts = 0
                errors = []

            report = _attach_directional_fields(state, report)
            raw_response = report.get("raw_response", json.dumps(report))
            save_legacy_agent_log("decision_agent", llm_messages, raw_response, report)

            elapsed = (datetime.now() - start_time).total_seconds()
            report["_attempts"] = attempts
            if errors:
                report["_validation_errors"] = errors

            # Store decision in Reasoning Bank + Judge integration
            if reasoning_bank and REASONING_BANK_AVAILABLE:
                try:
                    entry_digest = store_agent_decision(
                        reasoning_bank=reasoning_bank,
                        agent_name="decision_agent",
                        prompt=messages[1]["content"][:500],
                        result=report,
                        raw_response=raw_response,
                        backend=getattr(llm, "model", "langchain"),
                        latency_ms=elapsed * 1000,
                    )

                    # --- JUDGE INTEGRATION ---
                    if enable_judge and entry_digest and not errors:
                        try:
                            logger.info("⚖️ Calling Reasoning Judge...")
                            judge_config = get_judge_model_config()
                            judge = ReasoningLLMJudge(config=judge_config)

                            payload = ReasoningJudgePayload(
                                agent_name="decision_agent",
                                prompt=messages[1]["content"],
                                normalized_result=report,
                                raw_response=raw_response,
                                backend=judge_config.provider,
                                metadata={"source": "langgraph_orchestrator"},
                                latency_ms=elapsed * 1000,
                            )

                            verdict = judge.evaluate(payload)

                            if verdict:
                                logger.info(
                                    f"⚖️ Judge Verdict: {verdict.verdict} (Score: {verdict.score})"
                                )
                                reasoning_bank.attach_judge_feedback(
                                    agent_name="decision_agent",
                                    prompt_digest=entry_digest,
                                    judge_payload=verdict.as_entry_payload(),
                                )
                                judge_verdict = {
                                    "verdict": verdict.verdict,
                                    "score": verdict.score,
                                    "confidence": verdict.confidence,
                                    "critique": verdict.critique,
                                    "success_estimate": verdict.success_estimate,
                                }
                            else:
                                logger.warning("⚠️ Judge returned no verdict")

                        except Exception as judge_err:
                            logger.error(f"⚠️ Judge evaluation failed: {judge_err}")
                    # -------------------------

                except Exception as store_err:
                    logger.debug(f"Could not store decision: {store_err}")

            return {
                "decision_report": report,
                "final_trade_decision": report,
                "judge_verdict": judge_verdict,
                "execution_times": {
                    **state.get("execution_times", {}),
                    "decision": elapsed,
                },
            }

        except Exception as e:
            logger.error("Error in decision agent: %s", e)
            fallback_report = _attach_directional_fields(
                state,
                {
                    "final_decision": "HOLD",
                    "confidence_in_decision": "LOW",
                    "combined_reasoning": f"Decision agent error fallback: {e}",
                    "key_conflicting_signals": [f"Decision agent exception: {e}"],
                    "risk_assessment": {},
                    "_llm_error": str(e),
                },
            )
            return {
                "decision_report": fallback_report,
                "final_trade_decision": fallback_report,
                "errors": state.get("errors", []) + [f"Decision: {e}"],
            }

    async def traced_decision_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("decision_agent"):
            return await decision_node(state)

    return traced_decision_node
