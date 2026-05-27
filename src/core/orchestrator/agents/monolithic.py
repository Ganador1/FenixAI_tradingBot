# src/core/orchestrator/agents/monolithic.py
"""
Monolithic trading agent node.

Uses a single Decision LLM call with full market context (indicators + microstructure +
sentiment inputs) to produce final trade decision and risk payload.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from src.core.orchestrator.agents.base import save_legacy_agent_log
from src.core.orchestrator.retry_system import invoke_with_retry_and_validation
from src.core.orchestrator.state import FenixAgentState
from src.system.tracing import get_tracer

logger = logging.getLogger(__name__)


def _compact_indicators(indicators: Any) -> dict[str, Any]:
    if not isinstance(indicators, dict):
        return {}

    preferred = [
        "rsi",
        "macd",
        "macd_line",
        "macd_signal",
        "macd_hist",
        "ema_9",
        "ema_21",
        "ema_50",
        "ema_200",
        "atr",
        "adx",
        "stoch_k",
        "stoch_d",
        "bb_upper",
        "bb_middle",
        "bb_lower",
        "supertrend",
        "supertrend_signal",
        "vwap",
        "support",
        "resistance",
        "volume",
        "volume_sma",
    ]

    compact: dict[str, Any] = {}
    for key in preferred:
        if key in indicators:
            compact[key] = indicators.get(key)

    if len(compact) < 20:
        for key, value in indicators.items():
            if key in compact:
                continue
            if isinstance(value, (int, float, str, bool)) or value is None:
                compact[key] = value
            if len(compact) >= 30:
                break

    return compact


def _fallback_report(reason: str) -> dict[str, Any]:
    return {
        "final_decision": "HOLD",
        "confidence_in_decision": "LOW",
        "combined_reasoning": f"Monolithic fallback: {reason}",
        "risk_assessment": {
            "verdict": "DELAY",
            "reasoning": "Insufficient reliable monolithic output",
            "order_details": {},
        },
    }


def create_monolithic_agent_node(llm: Any, reasoning_bank: Any = None):
    """Creates monolithic single-LLM decision node."""

    async def monolithic_node(state: FenixAgentState) -> dict[str, Any]:
        start_time = datetime.now()

        try:
            timeframe = str(state.get("timeframe", "15m"))
            short_tf = timeframe in {"1m", "3m", "5m"}
            try:
                default_retries = 1 if short_tf else 2
                max_retries = int(os.getenv("FENIX_MONOLITHIC_MAX_RETRIES", str(default_retries)))
            except Exception:
                max_retries = 1 if short_tf else 2

            market_snapshot = {
                "symbol": state.get("symbol"),
                "timeframe": timeframe,
                "timestamp": state.get("timestamp"),
                "current_price": state.get("current_price"),
                "current_volume": state.get("current_volume"),
                "account_balance_usdt": state.get("account_balance_usdt"),
                "indicators": _compact_indicators(state.get("indicators", {})),
                "microstructure": {
                    "obi": state.get("obi"),
                    "cvd": state.get("cvd"),
                    "spread": state.get("spread"),
                    "spread_pct": state.get("spread_pct"),
                    "ofi": state.get("ofi"),
                    "ofi_norm": state.get("ofi_norm"),
                    "qi": state.get("qi"),
                    "mlofi": state.get("mlofi"),
                    "mlofi_norm": state.get("mlofi_norm"),
                    "volume_imbalance": state.get("volume_imbalance"),
                    "wdi": state.get("wdi"),
                    "liquidity_gap_pct": state.get("liquidity_gap_pct"),
                    "vpin_proxy": state.get("vpin_proxy"),
                    "trade_imbalance_5s": state.get("trade_imbalance_5s"),
                    "trade_volume_5s": state.get("trade_volume_5s"),
                    "trade_count_5s": state.get("trade_count_5s"),
                },
                "orderbook_depth": state.get("orderbook_depth", {}),
                "fear_greed_value": state.get("fear_greed_value", "N/A"),
                "social_data": state.get("social_data", {}),
                "news_headlines": [
                    str(x.get("title") or x.get("headline") or x)[:160]
                    for x in (state.get("news_data") or [])[:8]
                ],
                "chart_available": bool(state.get("chart_image_b64")),
            }

            system_prompt = (
                "You are a monolithic crypto trading decision engine. "
                "Use ONLY the provided market snapshot.\n\n"
                "Return ONLY valid JSON with this schema:\n"
                "{\n"
                '  "final_decision": "BUY" | "SELL" | "HOLD",\n'
                '  "confidence_in_decision": "HIGH" | "MEDIUM" | "LOW",\n'
                '  "combined_reasoning": "<short rationale>",\n'
                '  "risk_assessment": {\n'
                '    "verdict": "APPROVE" | "APPROVE_REDUCED" | "VETO" | "DELAY",\n'
                '    "reasoning": "<risk rationale>",\n'
                '    "order_details": {\n'
                '      "stop_loss": <number|null>,\n'
                '      "take_profit": <number|null>,\n'
                '      "risk_reward_ratio": <number|null>,\n'
                '      "approved_size": <number|null>\n'
                "    }\n"
                "  }\n"
                "}\n\n"
                "Do not include markdown or extra text."
            )
            user_prompt = (
                f"Market snapshot for {state.get('symbol')} ({timeframe}):\n"
                f"{json.dumps(market_snapshot, ensure_ascii=False, default=str)}"
            )

            llm_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            report, attempts, errors = await invoke_with_retry_and_validation(
                llm=llm,
                messages=llm_messages,
                agent_type="decision_agent",
                max_retries=max_retries,
                base_delay=0.3,
                required_keys=["final_decision", "confidence_in_decision", "combined_reasoning"],
            )

            if (
                errors
                or report.get("_validation_failed")
                or report.get("error")
                or report.get("parse_error")
            ):
                fallback = _fallback_report("validation/parse failure")
                fallback["_llm_attempts"] = attempts
                fallback["_llm_errors"] = errors or report.get("_validation_errors") or []
                report = fallback
                attempts = 0
                errors = []

            report["final_decision"] = str(report.get("final_decision", "HOLD")).upper()
            report["confidence_in_decision"] = str(
                report.get("confidence_in_decision", "LOW")
            ).upper()
            if report["confidence_in_decision"] not in {"HIGH", "MEDIUM", "LOW"}:
                report["confidence_in_decision"] = "LOW"

            risk_assessment = report.get("risk_assessment")
            if not isinstance(risk_assessment, dict):
                risk_assessment = {
                    "verdict": "DELAY",
                    "reasoning": "Risk payload missing in monolithic response",
                    "order_details": {},
                }
            risk_assessment["verdict"] = str(risk_assessment.get("verdict", "DELAY")).upper()
            if risk_assessment["verdict"] not in {"APPROVE", "APPROVE_REDUCED", "VETO", "DELAY"}:
                risk_assessment["verdict"] = "DELAY"
            if not isinstance(risk_assessment.get("order_details"), dict):
                risk_assessment["order_details"] = {}
            report["risk_assessment"] = risk_assessment

            raw_response = report.get("raw_response", json.dumps(report))
            save_legacy_agent_log("monolithic_agent", llm_messages, raw_response, report)

            elapsed = (datetime.now() - start_time).total_seconds()
            report["_attempts"] = attempts
            if errors:
                report["_validation_errors"] = errors

            return {
                "decision_report": report,
                "final_trade_decision": report,
                "risk_assessment": report.get("risk_assessment", {}),
                "judge_verdict": None,
                "execution_times": {
                    **state.get("execution_times", {}),
                    "decision": elapsed,
                },
            }

        except Exception as e:
            logger.error("Error in monolithic agent: %s", e)
            report = _fallback_report(str(e))
            return {
                "decision_report": report,
                "final_trade_decision": report,
                "risk_assessment": report.get("risk_assessment", {}),
                "errors": state.get("errors", []) + [f"Monolithic: {e}"],
            }

    async def traced_monolithic_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("monolithic_agent"):
            return await monolithic_node(state)

    return traced_monolithic_node
