# src/core/orchestrator/agents/risk.py
"""
Risk Manager Agent for Fenix Trading Bot.

Evaluates the proposed trade decision using dynamic ATR-based stop-loss/take-profit,
historical win-rate from ReasoningBank, and an LLM-based risk assessment.
Can veto, reduce, delay or approve trades.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any

from src.core.orchestrator.agents.base import (
    save_legacy_agent_log,
    store_to_reasoning_bank,
)
from src.core.orchestrator.bank_helper import REASONING_BANK_AVAILABLE
from src.core.orchestrator.retry_system import invoke_with_retry_and_validation
from src.core.orchestrator.state import FenixAgentState
from src.prompts.agent_prompts import format_prompt
from src.risk.dynamic_stop_loss import calculate_dynamic_risk_levels
from src.system.tracing import get_tracer

logger = logging.getLogger(__name__)

# Flip cooldown tracker: stores {symbol: {"last_action": "BUY"|"SELL", "ts": float}}
# ADDED 2026-02-17: Prevents rapid LONG→SHORT→LONG flips that caused overtrading (+84% flips).
_flip_tracker: dict[str, dict] = {}

# Flip confirmation buffer: stores {symbol: {"action": "BUY"|"SELL", "count": int, "ts": float}}
# ADDED 2026-02-18: Even with 300s cooldown, MiniMax/Cogito flip at 72-100% rate (observed 18 Feb).
# Requires N consecutive same-direction signals before executing a flip = fewer whipsaw losses.
_flip_confirm: dict[str, dict] = {}


def create_risk_agent_node(llm: Any, reasoning_bank: Any = None):
    """
    Creates the risk agent node with retry and validation system.

    This agent evaluates the final decision and can veto it if the risk
    is too high.
    """

    async def risk_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()

        try:
            decision = state.get("final_trade_decision", {})
            proposed_action = decision.get("final_decision", decision.get("action", "HOLD"))

            # If HOLD, no risk to evaluate
            if proposed_action == "HOLD":
                return {
                    "risk_assessment": {
                        "verdict": "APPROVE",
                        "reason": "No action proposed",
                        "adjusted_position_size": 0,
                    },
                    "execution_times": {
                        **state.get("execution_times", {}),
                        "risk": 0.01,
                    },
                }

            # === TREND REGIME FILTER (deterministic pre-filter) ===
            # CHANGED 2026-02-17: Block SHORTs in bullish regime and LONGs in bearish regime
            # unless decision confidence is HIGH (>= 0.8).
            # Rationale: SHORTs had 0% WR in last benchmark (-$16.97) due to trading
            # against the prevailing trend. This filter prevents that.
            # FIXED 2026-02-18: Added minimum EMA separation threshold (0.3% of price)
            # so the filter only activates on STRONG trends, not consolidation zones.
            # Previous run had 25 vetos in 1 slot because any EMA alignment triggered it.
            if os.getenv("FENIX_TREND_REGIME_FILTER", "1") == "1":
                try:
                    indicators = state.get("indicators", {})
                    ema_20 = float(indicators.get("ema_20") or 0)
                    ema_50 = float(indicators.get("ema_50") or 0)
                    current_price = float(state.get("current_price") or 0)
                    confidence_raw = decision.get("confidence_in_decision", "MEDIUM")
                    is_high_confidence = str(confidence_raw).upper() in (
                        "HIGH",
                        "VERY_HIGH",
                        "0.8",
                        "0.9",
                        "1.0",
                    )
                    try:
                        is_high_confidence = is_high_confidence or float(confidence_raw) >= 0.8
                    except (ValueError, TypeError):
                        pass

                    # Minimum EMA separation as % of price — configurable via env
                    # 0.3% means EMAs must be at least $200 apart on a $66k BTC
                    min_ema_sep_pct = float(os.getenv("FENIX_TREND_MIN_EMA_SEP_PCT", "0.3")) / 100.0

                    if ema_20 > 0 and ema_50 > 0 and current_price > 0:
                        ema_separation_pct = abs(ema_20 - ema_50) / current_price
                        trend_is_strong = ema_separation_pct >= min_ema_sep_pct

                        # Only apply filter on STRONG confirmed trends, skip on consolidation
                        bullish_regime = (
                            (current_price > ema_20) and (ema_20 > ema_50) and trend_is_strong
                        )
                        bearish_regime = (
                            (current_price < ema_20) and (ema_20 < ema_50) and trend_is_strong
                        )

                        logger.debug(
                            f"Trend filter: EMA sep={ema_separation_pct * 100:.3f}% "
                            f"(min={min_ema_sep_pct * 100:.2f}%), "
                            f"strong={trend_is_strong}, bullish={bullish_regime}, bearish={bearish_regime}"
                        )

                        if proposed_action == "SELL" and bullish_regime and not is_high_confidence:
                            reason = (
                                f"TREND REGIME VETO: SHORT blocked — bullish regime "
                                f"(price={current_price:.2f} > EMA20={ema_20:.2f} > EMA50={ema_50:.2f}), "
                                f"confidence={confidence_raw} is not HIGH. "
                                f"Counter-trend SHORTs had 0% WR historically."
                            )
                            logger.warning(reason)
                            return {
                                "risk_assessment": {
                                    "verdict": "VETO",
                                    "risk_score": 9.0,
                                    "reason": reason,
                                    "warnings": [reason],
                                    "suggestions": ["Wait for bearish regime before shorting"],
                                    "order_details": {},
                                },
                                "execution_times": {
                                    **state.get("execution_times", {}),
                                    "risk": (datetime.now() - start_time).total_seconds(),
                                },
                            }

                        if proposed_action == "BUY" and bearish_regime and not is_high_confidence:
                            reason = (
                                f"TREND REGIME VETO: LONG blocked — bearish regime "
                                f"(price={current_price:.2f} < EMA20={ema_20:.2f} < EMA50={ema_50:.2f}), "
                                f"confidence={confidence_raw} is not HIGH."
                            )
                            logger.warning(reason)
                            return {
                                "risk_assessment": {
                                    "verdict": "VETO",
                                    "risk_score": 9.0,
                                    "reason": reason,
                                    "warnings": [reason],
                                    "suggestions": ["Wait for bullish regime before longing"],
                                    "order_details": {},
                                },
                                "execution_times": {
                                    **state.get("execution_times", {}),
                                    "risk": (datetime.now() - start_time).total_seconds(),
                                },
                            }
                except Exception as _trend_err:
                    logger.debug(f"Trend regime filter skipped: {_trend_err}")
            # === END TREND REGIME FILTER ===

            # === FLIP COOLDOWN FILTER (deterministic pre-filter) ===
            # CHANGED 2026-02-17: Prevents rapid direction changes (LONG→SHORT within N min).
            # In Slot 1 (17 Feb), 84% of trades were flips — this was the main cause of losses.
            flip_cooldown_sec = float(
                os.getenv("FENIX_FLIP_COOLDOWN_SEC", "0")
            )  # 0 = disabled (histórico: 300s causaba 54% veto rate y PnL negativo vs 0% cooldown=+$587)
            symbol_key = state.get("symbol", "UNKNOWN")
            if flip_cooldown_sec > 0:
                try:
                    import time as _time

                    now_ts = _time.time()
                    prev = _flip_tracker.get(symbol_key)
                    if prev and prev.get("last_action") and proposed_action in ("BUY", "SELL"):
                        opposite = {"BUY": "SELL", "SELL": "BUY"}.get(proposed_action)
                        if prev["last_action"] == opposite:
                            elapsed_since_flip = now_ts - prev.get("ts", 0)
                            if elapsed_since_flip < flip_cooldown_sec:
                                remaining = flip_cooldown_sec - elapsed_since_flip
                                reason = (
                                    f"FLIP COOLDOWN VETO: {proposed_action} blocked — "
                                    f"last trade was {prev['last_action']} only {elapsed_since_flip:.0f}s ago. "
                                    f"Cooldown: {flip_cooldown_sec:.0f}s ({remaining:.0f}s remaining). "
                                    f"Flip rate was 84%% historically — this prevents overtrading."
                                )
                                logger.warning(reason)
                                return {
                                    "risk_assessment": {
                                        "verdict": "VETO",
                                        "risk_score": 8.0,
                                        "reason": reason,
                                        "warnings": [reason],
                                        "suggestions": [
                                            f"Wait {remaining:.0f}s before flipping direction"
                                        ],
                                        "order_details": {},
                                    },
                                    "execution_times": {
                                        **state.get("execution_times", {}),
                                        "risk": (datetime.now() - start_time).total_seconds(),
                                    },
                                }
                except Exception as _flip_err:
                    logger.debug(f"Flip cooldown filter skipped: {_flip_err}")
            # === END FLIP COOLDOWN FILTER ===

            # === FLIP CONFIRMATION FILTER (deterministic pre-filter) ===
            # ADDED 2026-02-18: MiniMax/Cogito showed 72-100% flip rate even after 300s cooldown.
            # Requires FENIX_FLIP_CONFIRM_N consecutive same-direction signals before executing a flip.
            # Default=1 means disabled (1 signal = immediate). Set to 2 to require confirmation.
            flip_confirm_n = int(
                os.getenv("FENIX_FLIP_CONFIRM_N", "1")
            )  # 1 = disabled (N=2 demostró acc 25.6% = peor resultado chuck champion)
            if flip_confirm_n > 1 and proposed_action in ("BUY", "SELL"):
                try:
                    import time as _time2

                    _now_confirm = _time2.time()
                    _confirm_window = float(
                        os.getenv("FENIX_FLIP_CONFIRM_WINDOW_SEC", "480")
                    )  # 8min window
                    _prev_exec = _flip_tracker.get(symbol_key)
                    if _prev_exec and _prev_exec.get("last_action"):
                        _opposite_confirm = {"BUY": "SELL", "SELL": "BUY"}.get(proposed_action)
                        if _prev_exec["last_action"] == _opposite_confirm:
                            # This is a proposed flip — check confirmation buffer
                            _pending = _flip_confirm.get(symbol_key)
                            if (
                                _pending
                                and _pending["action"] == proposed_action
                                and (_now_confirm - _pending.get("ts", 0)) < _confirm_window
                            ):
                                # Same direction as pending buffer — increment count
                                _new_count = _pending["count"] + 1
                                if _new_count >= flip_confirm_n:
                                    # Confirmed! Clear buffer and let trade proceed
                                    _flip_confirm.pop(symbol_key, None)
                                    logger.info(
                                        f"FLIP CONFIRMED [{symbol_key}]: {proposed_action} "
                                        f"confirmed {flip_confirm_n}x — executing."
                                    )
                                else:
                                    _flip_confirm[symbol_key] = {
                                        "action": proposed_action,
                                        "count": _new_count,
                                        "ts": _now_confirm,
                                    }
                                    _confirm_reason = (
                                        f"FLIP CONFIRM PENDING [{symbol_key}]: {proposed_action} "
                                        f"buffered {_new_count}/{flip_confirm_n} — need "
                                        f"{flip_confirm_n - _new_count} more same-direction signal(s)."
                                    )
                                    logger.warning(_confirm_reason)
                                    return {
                                        "risk_assessment": {
                                            "verdict": "VETO",
                                            "risk_score": 6.0,
                                            "reason": _confirm_reason,
                                            "warnings": [_confirm_reason],
                                            "suggestions": ["Wait for confirmation signal"],
                                            "order_details": {},
                                        },
                                        "execution_times": {
                                            **state.get("execution_times", {}),
                                            "risk": (datetime.now() - start_time).total_seconds(),
                                        },
                                    }
                            else:
                                # Start new confirmation buffer (new direction or expired window)
                                _flip_confirm[symbol_key] = {
                                    "action": proposed_action,
                                    "count": 1,
                                    "ts": _now_confirm,
                                }
                                _confirm_reason = (
                                    f"FLIP CONFIRM PENDING [{symbol_key}]: {proposed_action} "
                                    f"buffered 1/{flip_confirm_n} — need "
                                    f"{flip_confirm_n - 1} more same-direction signal(s). "
                                    f"Flip rate was 72-100%% historically — confirmation required."
                                )
                                logger.warning(_confirm_reason)
                                return {
                                    "risk_assessment": {
                                        "verdict": "VETO",
                                        "risk_score": 6.0,
                                        "reason": _confirm_reason,
                                        "warnings": [_confirm_reason],
                                        "suggestions": ["Wait for confirmation signal"],
                                        "order_details": {},
                                    },
                                    "execution_times": {
                                        **state.get("execution_times", {}),
                                        "risk": (datetime.now() - start_time).total_seconds(),
                                    },
                                }
                        else:
                            # Same direction as last — clear any stale confirmation buffer
                            _flip_confirm.pop(symbol_key, None)
                except Exception as _confirm_err:
                    logger.debug(f"Flip confirm filter skipped: {_confirm_err}")
            # === END FLIP CONFIRMATION FILTER ===

            # Historical context from ReasoningBank
            historical_context = ""
            if reasoning_bank and REASONING_BANK_AVAILABLE:
                try:
                    success_rate = reasoning_bank.get_success_rate("decision_agent", lookback=20)
                    historical_context = (
                        f"\n### Recent Decision History:\n"
                        f"- Win Rate: {success_rate.get('win_rate', 0):.1%}\n"
                        f"- Total trades: {success_rate.get('total', 0)}\n"
                        f"- Current streak: {success_rate.get('streak', 0)} "
                        f"{'wins' if success_rate.get('last_was_win') else 'losses'}\n"
                    )
                except Exception:
                    pass

            # === DYNAMIC STOP-LOSS CALCULATION ===
            dynamic_risk_levels = None
            try:
                entry_price = float(state.get("current_price", 0))
                atr = float(state.get("indicators", {}).get("atr", 0))
                try:
                    balance = float(state.get("account_balance_usdt") or 0.0)
                except Exception:
                    balance = 0.0
                if balance <= 0:
                    # Optional fallback for environments that cannot fetch balance reliably.
                    try:
                        balance = float(os.getenv("FENIX_BALANCE_FALLBACK_USDT", "0") or 0.0)
                    except Exception:
                        balance = 0.0

                if entry_price > 0 and atr > 0 and balance > 0:
                    dynamic_risk_levels = calculate_dynamic_risk_levels(
                        entry_price=entry_price,
                        atr=atr,
                        balance_usd=balance,
                        decision=proposed_action,
                        symbol=state.get("symbol", "UNKNOWN"),
                        timeframe=str(state.get("timeframe", "")),
                        current_volatility="MEDIUM",
                        open_positions=0,
                    )
                    logger.info(
                        f"Dynamic Risk Levels: SL={dynamic_risk_levels.stop_loss:.8f}, "
                        f"TP={dynamic_risk_levels.take_profit:.8f}, "
                        f"RR={dynamic_risk_levels.risk_reward_ratio:.2f}, "
                        f"Regime={dynamic_risk_levels.volatility_regime}"
                    )
                elif entry_price > 0 and atr > 0 and balance <= 0:
                    logger.warning(
                        "Risk agent: missing account balance (state.account_balance_usdt). "
                        "Set balance cache or FENIX_BALANCE_FALLBACK_USDT to enable sizing."
                    )
            except Exception as e:
                logger.warning(f"Could not calculate dynamic stop-loss: {e}")
            # === END DYNAMIC STOP-LOSS ===

            min_profit_after_fees = 0.5
            if dynamic_risk_levels is not None:
                try:
                    min_profit_after_fees = float(dynamic_risk_levels.min_profit_pct_used)
                except Exception:
                    min_profit_after_fees = 0.5
            else:
                try:
                    min_profit_after_fees = float(os.getenv("FENIX_MIN_PROFIT_PCT", "0.5"))
                except Exception:
                    min_profit_after_fees = 0.5

            # Deterministic risk path (skip LLM) for low-latency operation
            if os.getenv("FENIX_RISK_DETERMINISTIC", "0") == "1":
                elapsed = (datetime.now() - start_time).total_seconds()
                if dynamic_risk_levels:
                    verdict = str(dynamic_risk_levels.recommended_verdict or "APPROVE").upper()
                    # DynamicStopLossCalculator returns a *quantity-like* position_size (units),
                    # but the RiskManager contract expects notional size in USDT.
                    risk_score = {
                        "APPROVE": 3.0,
                        "APPROVE_REDUCED": 6.0,
                        "DELAY": 7.0,
                        "VETO": 9.0,
                    }.get(verdict, 5.0)
                    report = {
                        "verdict": verdict,
                        "risk_score": risk_score,
                        "reasoning": "Deterministic ATR-based risk evaluation (LLM disabled).",
                        "order_details": _dynamic_order_details(dynamic_risk_levels, entry_price),
                        "warnings": (dynamic_risk_levels.warnings or [])[:2],
                        "suggestions": [],
                        "dynamic_risk_levels": dynamic_risk_levels.to_dict(),
                    }
                else:
                    missing: list[str] = []
                    if not entry_price:
                        missing.append("entry_price")
                    if not atr:
                        missing.append("atr")
                    try:
                        bal_val = float(state.get("account_balance_usdt") or 0.0)
                    except Exception:
                        bal_val = 0.0
                    if bal_val <= 0 and not os.getenv("FENIX_BALANCE_FALLBACK_USDT"):
                        missing.append("account_balance_usdt")
                    report = {
                        "verdict": "DELAY",
                        "risk_score": 7.0,
                        "reasoning": "Deterministic risk could not compute SL/TP/size (missing required inputs).",
                        "order_details": {},
                        "warnings": [f"Missing: {', '.join(missing) if missing else 'unknown'}"],
                        "suggestions": ["Wait for more data", "Ensure balance cache is enabled"],
                    }
                # Track only approved actions; do not track blocked intents.
                try:
                    import time as _time3

                    _verdict = str(report.get("verdict", "")).upper()
                    if proposed_action in ("BUY", "SELL") and _verdict in {
                        "APPROVE",
                        "APPROVE_REDUCED",
                    }:
                        _flip_tracker[symbol_key] = {
                            "last_action": proposed_action,
                            "ts": _time3.time(),
                        }
                except Exception:
                    pass
                return {
                    "risk_assessment": report,
                    "execution_times": {
                        **state.get("execution_times", {}),
                        "risk": elapsed,
                    },
                }

            messages = format_prompt(
                "risk_manager",
                decision=proposed_action,
                symbol=state.get("symbol", "UNKNOWN"),
                confidence=str(decision.get("confidence_in_decision", "MEDIUM")),
                entry_price=str(state.get("current_price", "N/A")),
                balance=str(
                    state.get("account_balance_usdt")
                    or os.getenv("FENIX_BALANCE_FALLBACK_USDT", "N/A")
                ),
                open_positions=str(state.get("open_positions", 0)),
                daily_pnl=str(state.get("daily_pnl", 0)),
                current_drawdown=str(state.get("current_drawdown", "0%")),
                atr=str(state.get("indicators", {}).get("atr", "N/A")),
                volatility="MEDIUM",
                liquidity="HIGH",
                max_risk_per_trade="2",
                max_total_exposure="350",
                min_profit_after_fees=f"{min_profit_after_fees:.2f}",
            )

            if not messages:
                raise ValueError("Could not format risk prompt")

            # Add historical context to prompt
            if historical_context:
                messages[1]["content"] += f"\n\n{historical_context}"

            # Add dynamic stop-loss levels to prompt if calculated
            if dynamic_risk_levels:
                dynamic_context = (
                    f"\n### DYNAMIC RISK CALCULATION (ATR-Based):\n"
                    f"- Calculated Stop Loss: {dynamic_risk_levels.stop_loss:.8f}\n"
                    f"- Calculated Take Profit: {dynamic_risk_levels.take_profit:.8f}\n"
                    f"- Position Qty (units): {dynamic_risk_levels.position_size:.8f}\n"
                    f"- Approved Notional (USDT): {float(dynamic_risk_levels.position_size) * float(entry_price):.2f} (leveraged — this is NOT the risk amount)\n"
                    f"- ** MAX LOSS if SL hit: ${dynamic_risk_levels.max_loss_usd:.2f} ({dynamic_risk_levels.max_loss_usd / balance * 100:.1f}% of balance) — THIS is the actual risk **\n"
                    f"- Max Loss (USD): ${dynamic_risk_levels.max_loss_usd:.2f}\n"
                    f"- Risk/Reward Ratio: {dynamic_risk_levels.risk_reward_ratio:.2f}\n"
                    f"- Min Profit Target (after fees): {dynamic_risk_levels.min_profit_pct_used:.2f}%\n"
                    f"- ATR Used: {dynamic_risk_levels.atr_used:.8f}\n"
                    f"- Volatility Regime: {dynamic_risk_levels.volatility_regime.upper()}\n"
                    f"- SL Distance: {dynamic_risk_levels.sl_distance_pct:.2f}%\n"
                    f"- TP Distance: {dynamic_risk_levels.tp_distance_pct:.2f}%\n"
                    f"- Dynamic Warnings: "
                    f"{', '.join(dynamic_risk_levels.warnings) if dynamic_risk_levels.warnings else 'None'}\n"
                    f"- System Recommendation: {dynamic_risk_levels.recommended_verdict}\n\n"
                    f"**Use these calculated levels as the basis for your evaluation. "
                    f"Adjust only if market conditions warrant.**\n"
                )
                messages[1]["content"] += dynamic_context

                # Store in state for potential use by execution layer
                state["dynamic_risk_levels"] = dynamic_risk_levels.to_dict()

            llm_messages = [
                {"role": "system", "content": messages[0]["content"]},
                {"role": "user", "content": messages[1]["content"]},
            ]

            # Timeout wrapper for risk manager LLM call
            try:
                risk_timeout_sec = float(os.getenv("FENIX_RISK_TIMEOUT_SEC", "45.0"))
            except Exception:
                risk_timeout_sec = 45.0

            try:
                report, attempts, errors = await asyncio.wait_for(
                    invoke_with_retry_and_validation(
                        llm=llm,
                        messages=llm_messages,
                        agent_type="risk_manager",
                        max_retries=2,  # Reduced from 3 to fit within timeout
                        base_delay=0.2,
                        required_keys=["verdict"],
                    ),
                    timeout=risk_timeout_sec,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "risk_manager timed out after %.1fs; using deterministic fallback",
                    risk_timeout_sec,
                )
                # Fallback to deterministic risk evaluation
                if dynamic_risk_levels:
                    verdict = str(dynamic_risk_levels.recommended_verdict or "APPROVE").upper()
                    risk_score = {
                        "APPROVE": 3.0,
                        "APPROVE_REDUCED": 6.0,
                        "DELAY": 7.0,
                        "VETO": 9.0,
                    }.get(verdict, 5.0)
                    report = {
                        "verdict": verdict,
                        "risk_score": risk_score,
                        "reasoning": f"Timeout fallback: ATR-based risk evaluation (LLM timed out after {risk_timeout_sec:.0f}s).",
                        "order_details": _dynamic_order_details(dynamic_risk_levels, entry_price),
                        "warnings": dynamic_risk_levels.warnings or [],
                        "suggestions": [],
                        "dynamic_risk_levels": dynamic_risk_levels.to_dict(),
                        "_timeout": True,
                    }
                else:
                    report = {
                        "verdict": "DELAY",
                        "risk_score": 7.0,
                        "reasoning": f"Risk manager timed out after {risk_timeout_sec:.0f}s and no dynamic risk data available. Blocking trade for safety.",
                        "order_details": {},
                        "warnings": ["Risk evaluation timed out"],
                        "suggestions": ["Retry after market stabilizes"],
                        "_timeout": True,
                    }
                attempts = 0
                errors = [f"Timeout after {risk_timeout_sec:.0f}s"]

            raw_response = report.get("raw_response", json.dumps(report))
            save_legacy_agent_log("risk_manager", llm_messages, raw_response, report)

            elapsed = (datetime.now() - start_time).total_seconds()
            report["_attempts"] = attempts
            if errors:
                report["_validation_errors"] = errors

            # Recovery path: if LLM returned malformed/empty output (no verdict),
            # fallback to deterministic ATR-based report instead of blocking by missing verdict.
            verdict_norm = str(report.get("verdict", "")).upper().strip()
            if verdict_norm not in {"APPROVE", "APPROVE_REDUCED", "VETO", "DELAY"}:
                logger.warning(
                    "risk_manager returned no valid verdict; applying deterministic parse-error fallback"
                )
                if dynamic_risk_levels:
                    recovered_verdict = str(
                        dynamic_risk_levels.recommended_verdict or "APPROVE"
                    ).upper()
                    report = {
                        **report,
                        "verdict": recovered_verdict,
                        "risk_score": {
                            "APPROVE": 3.0,
                            "APPROVE_REDUCED": 6.0,
                            "DELAY": 7.0,
                            "VETO": 9.0,
                        }.get(recovered_verdict, 5.0),
                        "reason": "Parse-error fallback: deterministic ATR-based risk evaluation.",
                        "reasoning": "Parse-error fallback: deterministic ATR-based risk evaluation.",
                        "order_details": _dynamic_order_details(dynamic_risk_levels, entry_price),
                        "warnings": list(
                            set(
                                (report.get("warnings") or [])
                                + (dynamic_risk_levels.warnings or [])
                            )
                        ),
                        "suggestions": report.get("suggestions") or [],
                        "dynamic_risk_levels": dynamic_risk_levels.to_dict(),
                        "_parse_fallback": True,
                        "parse_error": False,
                        "_validation_failed": False,
                    }
                else:
                    report = {
                        **report,
                        "verdict": "DELAY",
                        "risk_score": 7.0,
                        "reason": "Parse-error fallback: missing deterministic inputs, delaying for safety.",
                        "reasoning": "Parse-error fallback: missing deterministic inputs, delaying for safety.",
                        "order_details": report.get("order_details") or {},
                        "warnings": list(
                            set((report.get("warnings") or []) + ["Invalid/empty LLM response"])
                        ),
                        "suggestions": report.get("suggestions")
                        or ["Retry after market stabilizes"],
                        "_parse_fallback": True,
                        "parse_error": False,
                        "_validation_failed": False,
                    }

                for noisy_key in ("error", "raw_response"):
                    if noisy_key in report:
                        report.pop(noisy_key, None)

            # Persist final normalized report (after parse fallback, if applied).
            try:
                save_legacy_agent_log("risk_manager", llm_messages, json.dumps(report), report)
            except Exception:
                pass

            # Store in ReasoningBank
            if reasoning_bank and REASONING_BANK_AVAILABLE:
                prompt_summary = f"Risk eval: {proposed_action} @ {state.get('current_price')}"
                store_to_reasoning_bank(
                    reasoning_bank=reasoning_bank,
                    agent_name="risk_manager",
                    prompt=prompt_summary,
                    result=report,
                    raw_response=raw_response,
                    llm=llm,
                    elapsed_ms=elapsed * 1000,
                )

            # Merge dynamic risk levels into report
            if dynamic_risk_levels:
                report["dynamic_risk_levels"] = dynamic_risk_levels.to_dict()
                order_details = dict(report.get("order_details") or {})
                if not _has_usable_order_details(order_details, proposed_action, entry_price):
                    logger.warning(
                        "risk_manager returned unusable order_details for %s %s at %.8f; "
                        "using deterministic ATR levels",
                        state.get("symbol", "UNKNOWN"),
                        proposed_action,
                        entry_price,
                    )
                    report["order_details"] = _dynamic_order_details(
                        dynamic_risk_levels, entry_price
                    )
                    report["_order_details_fallback"] = True
                if dynamic_risk_levels.warnings:
                    existing_warnings = report.get("warnings", [])
                    report["warnings"] = list(set(existing_warnings + dynamic_risk_levels.warnings))

            # Track only approved actions; do not track blocked intents.
            try:
                import time as _time4

                _verdict = str(report.get("verdict", "")).upper()
                if proposed_action in ("BUY", "SELL") and _verdict in {
                    "APPROVE",
                    "APPROVE_REDUCED",
                }:
                    _flip_tracker[symbol_key] = {
                        "last_action": proposed_action,
                        "ts": _time4.time(),
                    }
            except Exception:
                pass

            return {
                "risk_assessment": report,
                "execution_times": {
                    **state.get("execution_times", {}),
                    "risk": elapsed,
                },
            }

        except Exception as e:
            logger.error("Error in risk agent: %s", e)
            return {
                "risk_assessment": {"verdict": "APPROVE", "error": str(e)},
                "errors": state.get("errors", []) + [f"Risk: {e}"],
            }

    async def traced_risk_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("risk_manager"):
            return await risk_node(state)

    return traced_risk_node


def _dynamic_order_details(dynamic_risk_levels: Any, entry_price: float) -> dict[str, float]:
    approved_notional = (
        float(dynamic_risk_levels.position_size) * float(entry_price)
        if entry_price
        else float(dynamic_risk_levels.position_size)
    )
    return {
        "approved_size": approved_notional,
        "stop_loss": float(dynamic_risk_levels.stop_loss),
        "take_profit": float(dynamic_risk_levels.take_profit),
        "max_loss_usd": float(dynamic_risk_levels.max_loss_usd),
    }


def _has_usable_order_details(
    order_details: dict[str, Any], proposed_action: str, entry_price: float
) -> bool:
    if not order_details:
        return False
    approved_size = float(order_details.get("approved_size") or 0.0)
    stop_loss = float(order_details.get("stop_loss") or 0.0)
    take_profit = float(order_details.get("take_profit") or 0.0)
    if approved_size <= 0 or stop_loss <= 0 or take_profit <= 0 or entry_price <= 0:
        return False
    if proposed_action == "BUY":
        return stop_loss < entry_price < take_profit
    if proposed_action == "SELL":
        return take_profit < entry_price < stop_loss
    return False
