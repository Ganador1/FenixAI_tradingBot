# src/core/orchestrator/agents/technical.py
"""
Technical Analyst Agent for Fenix Trading Bot.

Analyzes indicators, microstructure, and multi-timeframe context to produce
a BUY / SELL / HOLD signal with confidence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any

from src.core.orchestrator.agent_cache import AgentReportCache
from src.core.orchestrator.agents.base import (
    save_legacy_agent_log,
    store_to_reasoning_bank,
)
from src.core.orchestrator.bank_helper import (
    REASONING_BANK_AVAILABLE,
    get_agent_context_from_bank,
)
from src.core.orchestrator.retry_system import invoke_with_retry_and_validation
from src.core.orchestrator.state import FenixAgentState
from src.indicators.advanced_indicators import calculate_all_advanced_indicators
from src.indicators.timeframe_aware_indicators import (
    format_indicator_guidance,
    get_optimal_indicators,
)
from src.prompts.agent_prompts import format_prompt
from src.system.tracing import get_tracer

logger = logging.getLogger(__name__)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _confidence_score_from_label(value: Any) -> float:
    label = str(value or "").strip().upper()
    return {"LOW": 0.35, "MEDIUM": 0.60, "HIGH": 0.85}.get(label, 0.35)


def _confidence_label_from_score(value: Any) -> str:
    try:
        score = float(value)
    except Exception:
        return "LOW"
    if score >= 0.75:
        return "HIGH"
    if score >= 0.50:
        return "MEDIUM"
    return "LOW"


def _normalize_technical_report(report: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(report or {})
    confidence = _safe_float(normalized.get("confidence"))
    if confidence is None:
        confidence = _confidence_score_from_label(normalized.get("confidence_level"))
    confidence = max(0.0, min(1.0, float(confidence or 0.0)))
    normalized["confidence"] = confidence
    normalized["confidence_level"] = _confidence_label_from_score(confidence)
    return normalized


def _collect_price_levels(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [level for item in value if (level := _safe_float(item)) is not None]
    scalar = _safe_float(value)
    return [scalar] if scalar is not None else []


def _directional_bias_from_indicators(
    indicators: dict[str, Any],
    *,
    current_price: float | None,
) -> tuple[float, list[str], bool]:
    bull = 0.0
    bear = 0.0
    reasons: list[str] = []

    ema_9 = _safe_float(indicators.get("ema_9"))
    ema_20 = _safe_float(indicators.get("ema_20"))
    ema_50 = _safe_float(indicators.get("ema_50"))
    ema_200 = _safe_float(indicators.get("ema_200"))
    vwap = _safe_float(indicators.get("vwap"))
    macd_hist = _safe_float(indicators.get("macd_hist"))
    rsi = _safe_float(indicators.get("rsi"))
    cmf = _safe_float(indicators.get("cmf"))
    adx = _safe_float(indicators.get("adx"))
    bandwidth_pct = _safe_float(indicators.get("bandwidth_pct"))

    if all(value is not None for value in (ema_9, ema_20, ema_50)):
        if ema_9 > ema_20 > ema_50:
            bull += 0.26
            reasons.append("bullish_ema_stack")
        elif ema_9 < ema_20 < ema_50:
            bear += 0.26
            reasons.append("bearish_ema_stack")

    if all(value is not None for value in (current_price, ema_20)):
        if current_price > ema_20:
            bull += 0.08
            reasons.append("price_above_ema20")
        elif current_price < ema_20:
            bear += 0.08
            reasons.append("price_below_ema20")

    if all(value is not None for value in (current_price, ema_200)):
        if current_price > ema_200:
            bull += 0.08
            reasons.append("price_above_ema200")
        elif current_price < ema_200:
            bear += 0.08
            reasons.append("price_below_ema200")

    if all(value is not None for value in (current_price, vwap)):
        if current_price > vwap:
            bull += 0.10
            reasons.append("price_above_vwap")
        elif current_price < vwap:
            bear += 0.10
            reasons.append("price_below_vwap")

    if macd_hist is not None:
        if macd_hist > 0:
            bull += 0.14
            reasons.append("positive_macd_hist")
        elif macd_hist < 0:
            bear += 0.14
            reasons.append("negative_macd_hist")

    supertrend_signal = str(indicators.get("supertrend_signal") or "").strip().lower()
    if "bull" in supertrend_signal:
        bull += 0.14
        reasons.append("bullish_supertrend")
    elif "bear" in supertrend_signal:
        bear += 0.14
        reasons.append("bearish_supertrend")

    if cmf is not None:
        if cmf >= 0.10:
            bull += 0.14
            reasons.append("positive_cmf")
        elif cmf <= -0.10:
            bear += 0.14
            reasons.append("negative_cmf")

    if rsi is not None:
        if 52.0 <= rsi <= 68.0:
            bull += 0.10
            reasons.append("constructive_rsi")
        elif 32.0 <= rsi <= 48.0:
            bear += 0.10
            reasons.append("weak_rsi")
        elif rsi >= 72.0:
            bear += 0.06
            reasons.append("overbought_rsi")
        elif rsi <= 28.0:
            bull += 0.06
            reasons.append("oversold_rsi")

    squeeze = bool(indicators.get("bb_inside_kc")) or bool(indicators.get("bb_squeeze"))
    if bandwidth_pct is not None and bandwidth_pct <= 0.025:
        squeeze = True

    score = bull - bear
    if adx is not None and adx >= 25.0 and abs(score) >= 0.18:
        if score > 0:
            score += 0.08
            reasons.append("trend_strength_confirms_bull")
        else:
            score -= 0.08
            reasons.append("trend_strength_confirms_bear")

    return score, reasons, squeeze


def _apply_hold_override_policy(
    report: dict[str, Any],
    indicators: dict[str, Any],
    *,
    current_price: float | None,
) -> dict[str, Any]:
    adjusted = dict(report or {})
    original_signal = str(adjusted.get("signal") or "HOLD").upper()
    adjusted["_llm_signal_original"] = original_signal

    score, reasons, squeeze = _directional_bias_from_indicators(
        indicators,
        current_price=current_price,
    )
    adjusted["_directional_bias_score"] = round(score, 3)
    adjusted["_directional_bias_reasons"] = reasons

    if original_signal != "HOLD":
        adjusted["_signal_adjustment"] = "none"
        return adjusted

    min_rr = _safe_float(os.getenv("FENIX_TECHNICAL_HOLD_OVERRIDE_MIN_RR", "1.25")) or 1.25
    base_threshold = (
        _safe_float(os.getenv("FENIX_TECHNICAL_HOLD_OVERRIDE_BASE_THRESHOLD", "0.42")) or 0.42
    )
    squeeze_penalty = (
        _safe_float(os.getenv("FENIX_TECHNICAL_HOLD_OVERRIDE_SQUEEZE_PENALTY", "0.12")) or 0.12
    )
    min_confidence = (
        _safe_float(os.getenv("FENIX_TECHNICAL_HOLD_OVERRIDE_MIN_CONFIDENCE", "0.64")) or 0.64
    )
    proximity_pct = (
        _safe_float(os.getenv("FENIX_TECHNICAL_HOLD_OVERRIDE_PROXIMITY_PCT", "0.004")) or 0.004
    )
    threshold = base_threshold + (squeeze_penalty if squeeze else 0.0)

    rr = _safe_float(adjusted.get("risk_reward_ratio"))
    if rr is None or rr < min_rr:
        adjusted["_signal_adjustment"] = "none"
        return adjusted

    resistance_levels = _collect_price_levels(adjusted.get("resistance_level"))
    resistance_levels.extend(_collect_price_levels(indicators.get("resistances")))
    support_levels = _collect_price_levels(adjusted.get("support_level"))
    support_levels.extend(_collect_price_levels(indicators.get("supports")))

    proposed_signal = "BUY" if score >= threshold else "SELL" if score <= -threshold else "HOLD"
    if proposed_signal == "BUY" and current_price and resistance_levels:
        if any(
            abs((level - current_price) / current_price) <= proximity_pct
            for level in resistance_levels
        ):
            adjusted["_signal_adjustment"] = "none"
            return adjusted
    if proposed_signal == "SELL" and current_price and support_levels:
        if any(
            abs((current_price - level) / current_price) <= proximity_pct
            for level in support_levels
        ):
            adjusted["_signal_adjustment"] = "none"
            return adjusted

    if proposed_signal == "HOLD":
        adjusted["_signal_adjustment"] = "none"
        return adjusted

    adjusted["signal"] = proposed_signal
    existing_conf = _safe_float(adjusted.get("confidence")) or 0.0
    adjusted["confidence"] = max(existing_conf, min(0.84, min_confidence + abs(score) * 0.18))
    adjusted["confidence_level"] = _confidence_label_from_score(adjusted["confidence"])
    reason_prefix = (
        f"Directional override from HOLD to {proposed_signal}: "
        f"score={score:.2f}, rr={rr:.2f}, evidence={', '.join(reasons[:5])}."
    )
    base_reason = str(adjusted.get("reasoning") or adjusted.get("rationale") or "").strip()
    adjusted["reasoning"] = f"{reason_prefix} {base_reason}".strip()
    adjusted["rationale"] = adjusted["reasoning"]
    adjusted["_signal_adjustment"] = "hold_override"
    return adjusted


def create_technical_agent_node(
    llm: Any,
    reasoning_bank: Any = None,
    agent_cache: AgentReportCache | None = None,
):
    """Creates the technical agent node with retry and validation system."""

    async def technical_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()

        try:
            indicators = state.get("indicators", {}) or {}
            mtf_context = state.get("mtf_context", {}) or {}
            timeframe = str(state.get("timeframe", "15m"))
            short_tf = timeframe in {"1m", "3m", "5m"}
            # Short-TF nonblocking mode is controlled via env and defaults to OFF for benchmark quality.
            # This ensures we get full LLM reports instead of heuristics.
            nonblocking_short_tf = short_tf and (
                os.getenv("FENIX_SHORT_TF_NONBLOCKING", "0") == "1"
            )

            def _get_env_first(*keys: str, default: str) -> str:
                for key in keys:
                    value = os.getenv(key)
                    if value is not None:
                        return value
                return default

            def _fallback_report(full_indicators: dict[str, Any]) -> dict[str, Any]:
                # Minimal, deterministic fallback used when the LLM is too slow on low timeframes.
                try:
                    rsi = float(full_indicators.get("rsi", 50) or 50)
                except Exception:
                    rsi = 50.0

                macd_hist = full_indicators.get("macd_hist")
                if macd_hist is None:
                    macd_hist = full_indicators.get("macd_histogram")
                try:
                    macd_hist_f = float(macd_hist or 0.0)
                except Exception:
                    macd_hist_f = 0.0

                signal = "HOLD"
                confidence = 0.55
                if rsi < 30 and macd_hist_f > 0:
                    signal = "BUY"
                    confidence = 0.65
                elif rsi > 70 and macd_hist_f < 0:
                    signal = "SELL"
                    confidence = 0.65

                rationale = "Timeout fallback (short TF): using RSI+MACD heuristic to avoid stale LLM decisions."
                return {
                    "signal": signal,
                    "confidence": confidence,
                    "rationale": rationale,
                    "indicator_validations": {},
                }

            def _build_minimal_payload_short_tf(full: dict[str, Any]) -> dict[str, Any]:
                # Hot path: keep this fast. Avoid indicator-suite selection and large nested payloads.
                keys = [
                    "market_condition",
                    "market_condition_note",
                    "trend_conflict",
                    "chop",
                    "chop_regime",
                    "adx",
                    "atr",
                    "rsi",
                    "mfi",
                    "fisher_transform",
                    "vwap",
                    "hull_ma",
                    "ema_9",
                    "ema_20",
                    "ema_21",
                    "ema_50",
                    "ema_200",
                    "macd_line",
                    "signal_line",
                    "macd_hist",
                    "supertrend_signal",
                    "sar",
                    "bb_inside_kc",
                    "bb_squeeze",
                    "bandwidth_pct",
                    "percent_b",
                    "donchian_width_pct",
                    "donchian_upper",
                    "donchian_lower",
                    "supports",
                    "resistances",
                ]

                out: dict[str, Any] = {"_timeframe": timeframe}
                for k in keys:
                    v = full.get(k)
                    if v is None:
                        continue
                    if isinstance(v, (int, float, str, bool)):
                        out[k] = v
                    elif isinstance(v, list) and len(v) <= 20:
                        out[k] = v
                return out

            # Nonblocking short-TF path: return cached/fallback quickly and refresh in background.
            # This keeps 1m/3m/5m cycles in the ms range even when cloud LLM latency is high.
            # Force LLM analysis for paper data capture
            nonblocking_short_tf = False
            if nonblocking_short_tf:
                cache_enabled = os.getenv("FENIX_AGENT_CACHE_ON_TIMEOUT", "1") == "1"
                try:
                    cache_ttl_sec = float(os.getenv("FENIX_AGENT_CACHE_TTL_SHORT_SEC", "180.0"))
                except Exception:
                    cache_ttl_sec = 180.0
                try:
                    refresh_min_sec = float(os.getenv("FENIX_AGENT_CACHE_REFRESH_MIN_SEC", "30.0"))
                except Exception:
                    refresh_min_sec = 30.0
                try:
                    refresh_timeout_sec = float(
                        os.getenv("FENIX_AGENT_CACHE_ASYNC_REFRESH_TIMEOUT_SEC", "30.0")
                    )
                except Exception:
                    refresh_timeout_sec = 30.0

                # Minimal payload for downstream consumers (keeps Decision prompts small when enabled).
                indicators_for_llm = _build_minimal_payload_short_tf(indicators)

                cached_report: dict[str, Any] | None = None
                cached_age: float | None = None
                if cache_enabled and agent_cache is not None:
                    cached = agent_cache.get(
                        agent="technical",
                        symbol=state.get("symbol", "BTCUSDT"),
                        timeframe=timeframe,
                        ttl_sec=cache_ttl_sec,
                    )
                    if cached is not None:
                        cached_report, cached_age = cached

                if cached_report is not None:
                    report = cached_report
                    report["_cache_info"] = {
                        "reason": "nonblocking_cache_hit",
                        "age_sec": float(cached_age or 0.0),
                        "ttl_sec": float(cache_ttl_sec),
                    }
                    logger.info(
                        "technical_analyst (nonblocking, tf=%s): cache hit (age=%.0fs, ttl=%.0fs)",
                        timeframe,
                        float(cached_age or 0.0),
                        float(cache_ttl_sec),
                    )
                else:
                    report = _fallback_report(indicators)
                    report["_cache_info"] = {"reason": "nonblocking_fallback"}
                    logger.info(
                        "technical_analyst (nonblocking, tf=%s): no cache, using fallback",
                        timeframe,
                    )

                # Background refresh (disabled under pytest unless explicitly enabled).
                async_refresh = os.getenv("FENIX_AGENT_CACHE_ASYNC_REFRESH", "1") == "1"
                if os.getenv("PYTEST_CURRENT_TEST") is not None:
                    async_refresh = (
                        os.getenv("FENIX_AGENT_CACHE_ASYNC_REFRESH_UNDER_PYTEST", "0") == "1"
                    )

                default_max_retries = 1
                try:
                    max_retries = int(
                        _get_env_first(
                            "FENIX_TECH_MAX_RETRIES",
                            "FENIX_TECHNICAL_MAX_RETRIES",
                            default=str(default_max_retries),
                        )
                    )
                except Exception:
                    max_retries = default_max_retries

                if cache_enabled and async_refresh and agent_cache is not None:
                    sym = state.get("symbol", "BTCUSDT")
                    if agent_cache.can_start_refresh(
                        agent="technical",
                        symbol=sym,
                        timeframe=timeframe,
                        min_interval_sec=refresh_min_sec,
                    ):
                        # Snapshot only what we need to build the prompt in the background.
                        sym_s = sym
                        mtf_s = dict(mtf_context)
                        micro_summary = (
                            f"OBI={state.get('obi', 'N/A')}, "
                            f"TOBliq={state.get('tob_liquidity', 'N/A')}, "
                            f"OFI={state.get('ofi', 'N/A')}, "
                            f"OFInorm={state.get('ofi_norm', 'N/A')}, "
                            f"QI={state.get('qi', 'N/A')}, "
                            f"MLOFI={state.get('mlofi', 'N/A')}, "
                            f"MLOFInorm={state.get('mlofi_norm', 'N/A')}, "
                            f"WDI={state.get('wdi', 'N/A')}, "
                            f"VPIN={state.get('vpin_proxy', 'N/A')}, "
                            f"Spread={state.get('spread', 'N/A')} ({state.get('spread_pct', 'N/A')}%), "
                            f"Microprice={state.get('microprice', 'N/A')}, "
                            f"MicroBps={state.get('microprice_bps', 'N/A')}, "
                            f"Gap%={state.get('liquidity_gap_pct', 'N/A')}, "
                            f"CVDd5s={state.get('cvd_delta_5s', 'N/A')}, "
                            f"TImb5s={state.get('trade_imbalance_5s', 'N/A')}, "
                            f"TInt={state.get('trade_intensity_5s', 'N/A')}"
                        )
                        price_s = str(state.get("current_price", "N/A"))
                        vol_s = str(state.get("current_volume", "N/A"))

                        async def _refresh() -> None:
                            try:
                                msgs = format_prompt(
                                    "technical_analyst",
                                    symbol=sym_s,
                                    timeframe=timeframe,
                                    indicators_json=json.dumps(indicators_for_llm, default=str),
                                    microstructure_summary=micro_summary,
                                    htf_context=json.dumps(mtf_s.get("htf", {}), default=str),
                                    ltf_context=json.dumps(mtf_s.get("ltf", {}), default=str),
                                    current_price=price_s,
                                    current_volume=vol_s,
                                )
                                if not msgs:
                                    return
                                llm_messages = [
                                    {"role": "system", "content": msgs[0]["content"]},
                                    {"role": "user", "content": msgs[1]["content"]},
                                ]

                                (
                                    refreshed,
                                    refresh_attempts,
                                    refresh_errors,
                                ) = await asyncio.wait_for(
                                    invoke_with_retry_and_validation(
                                        llm=llm,
                                        messages=llm_messages,
                                        agent_type="technical_analyst",
                                        max_retries=max_retries,
                                        base_delay=0.3,
                                        required_keys=["signal"],
                                    ),
                                    timeout=refresh_timeout_sec,
                                )
                                if (
                                    refresh_attempts > 0
                                    and not refresh_errors
                                    and not refreshed.get("_validation_failed")
                                    and not refreshed.get("error")
                                ):
                                    agent_cache.set(
                                        agent="technical",
                                        symbol=sym_s,
                                        timeframe=timeframe,
                                        report=refreshed,
                                    )
                                    llm_ms = None
                                    try:
                                        llm_ms = float(
                                            (refreshed.get("_perf") or {}).get("llm_total_ms")
                                            or 0.0
                                        )
                                    except Exception:
                                        llm_ms = None
                                    if llm_ms:
                                        logger.info(
                                            "technical_analyst async refresh cached (tf=%s, attempts=%s, llm_ms=%.0f)",
                                            timeframe,
                                            refresh_attempts,
                                            float(llm_ms),
                                        )
                                    else:
                                        logger.info(
                                            "technical_analyst async refresh cached (tf=%s, attempts=%s)",
                                            timeframe,
                                            refresh_attempts,
                                        )
                            except asyncio.TimeoutError:
                                logger.debug(
                                    "technical_analyst async refresh timed out after %.1fs (tf=%s)",
                                    float(refresh_timeout_sec),
                                    timeframe,
                                )
                            except Exception as e:
                                logger.debug("technical_analyst async refresh failed: %s", e)

                        agent_cache.set_inflight(
                            agent="technical",
                            symbol=sym,
                            timeframe=timeframe,
                            task=asyncio.create_task(_refresh()),
                        )

                raw_response = json.dumps(report)
                elapsed = (datetime.now() - start_time).total_seconds()
                report["_attempts"] = 0
                report["_nonblocking"] = True
                attach_full = os.getenv("FENIX_ATTACH_FULL_INDICATORS_TO_TECH_REPORT", "0") == "1"
                if attach_full:
                    report["indicators"] = state.get("indicators", {})
                    report["_indicators_scope"] = "full"
                else:
                    report["indicators"] = indicators_for_llm
                    report["_indicators_scope"] = "filtered"

                return {
                    "technical_report": report,
                    "indicators_filtered": indicators_for_llm,
                    "messages": state.get("messages", [])
                    + [{"role": "assistant", "content": raw_response}],
                    "execution_times": {
                        **state.get("execution_times", {}),
                        "technical": elapsed,
                    },
                }

            # === TIMEFRAME-AWARE INDICATOR SELECTION ===
            indicator_guidance = ""
            indicator_suite = None
            try:
                adx = indicators.get("adx")
                atr = indicators.get("atr")
                chop = indicators.get("chop")
                donchian_width = indicators.get("donchian_width_pct")
                bb_inside_kc = indicators.get("bb_inside_kc")

                indicator_suite = get_optimal_indicators(
                    timeframe=timeframe,
                    available_feeds=["ohlcv", "volume"],
                    available_indicators=list(indicators.keys()),
                    adx=adx,
                    atr=atr,
                    chop=chop,
                    donchian_width_pct=donchian_width,
                    bb_inside_kc=bb_inside_kc,
                )

                indicator_guidance = format_indicator_guidance(indicator_suite)

                # Calculate advanced indicators if data available
                highs = state.get("price_history", {}).get("highs", [])
                lows = state.get("price_history", {}).get("lows", [])
                closes = state.get("price_history", {}).get("closes", [])
                volumes = state.get("price_history", {}).get("volumes", [])

                if all([highs, lows, closes, volumes]):
                    advanced = calculate_all_advanced_indicators(
                        ohlcv_data={
                            "highs": highs,
                            "lows": lows,
                            "closes": closes,
                            "volumes": volumes,
                        }
                    )
                    for name, value in advanced.items():
                        indicators[f"advanced_{name}"] = {
                            "value": value.value,
                            "signal": value.signal,
                            "interpretation": value.interpretation,
                            "confidence": value.confidence,
                            "metadata": value.metadata,
                        }

                logger.info(
                    f"📊 Technical: Using {len(indicator_suite.primary_indicators)} "
                    f"primary indicators for {timeframe}"
                )

            except Exception as e:
                logger.warning(f"Could not generate indicator guidance: {e}")
            # === END INDICATOR SELECTION ===

            # === FILTER DEGENERATE INDICATORS ===
            filtered_indicators: dict[str, Any] = {}
            removed_count = 0
            degenerate_keys: list[str] = []

            for key, value in indicators.items():
                if key.startswith("_") or key.endswith("_raw") or key.endswith("_degenerate"):
                    continue

                is_degenerate = indicators.get(f"{key}_degenerate", False)
                reliability = indicators.get(f"{key}_reliability", "NORMAL")
                market_condition = indicators.get("market_condition", "NORMAL")

                if is_degenerate:
                    degenerate_keys.append(f"{key} (flagged)")
                    removed_count += 1
                    continue

                if reliability in ["REPLACED_NEUTRAL", "REPLACED_FLAT"]:
                    degenerate_keys.append(f"{key} ({reliability})")
                    removed_count += 1
                    continue

                if market_condition == "EXTREME_CONSOLIDATION":
                    if key in [
                        "adx",
                        "aroon_up",
                        "aroon_down",
                        "ichimoku_tenkan",
                        "ichimoku_kijun",
                        "ichimoku_senkou_a",
                        "ichimoku_senkou_b",
                    ]:
                        degenerate_keys.append(f"{key} (EXTREME_CONSOLIDATION)")
                        removed_count += 1
                        continue

                filtered_indicators[key] = value

            if removed_count > 0:
                logger.warning(
                    f"🧹 Filtered {removed_count} degenerate indicators: "
                    f"{', '.join(degenerate_keys[:5])}"
                )
                filtered_indicators["_data_quality_note"] = (
                    f"{removed_count} unreliable indicators filtered due to market conditions"
                )

            indicators_for_agent = filtered_indicators if filtered_indicators else indicators
            # === END FILTER DEGENERATE INDICATORS ===

            # === MINIMAL PAYLOAD (LATENCY OPTIMIZATION) ===
            minimal_env = os.getenv("FENIX_MINIMAL_INDICATORS")
            minimal_by_default = timeframe in {"1m", "3m", "5m"}
            use_minimal_payload = (
                minimal_by_default if minimal_env is None else (minimal_env == "1")
            )

            def _is_small_value(v: Any) -> bool:
                if v is None:
                    return True
                if isinstance(v, (int, float, str, bool)):
                    return True
                if isinstance(v, dict):
                    if len(v) > 30:
                        return False
                    return all(
                        isinstance(x, (int, float, str, bool, type(None))) for x in v.values()
                    )
                return False

            def _build_minimal_payload(full: dict[str, Any]) -> dict[str, Any]:
                primary = list(getattr(indicator_suite, "primary_indicators", []) or [])
                secondary = list(getattr(indicator_suite, "secondary_indicators", []) or [])
                core = [
                    "market_condition",
                    "market_condition_note",
                    "trend_conflict",
                    "chop",
                    "chop_regime",
                    "adx",
                    "atr",
                    "rsi",
                    "mfi",
                    "fisher_transform",
                    "vwap",
                    "hull_ma",
                    "ema_9",
                    "ema_20",
                    "ema_21",
                    "ema_50",
                    "ema_200",
                    "macd_line",
                    "signal_line",
                    "macd_hist",
                    "supertrend_signal",
                    "sar",
                    "bb_inside_kc",
                    "bb_squeeze",
                    "bandwidth_pct",
                    "percent_b",
                    "donchian_width_pct",
                    "donchian_upper",
                    "donchian_lower",
                    "supports",
                    "resistances",
                ]

                wanted_keys = list(dict.fromkeys(primary + secondary + core))
                out: dict[str, Any] = {
                    "_timeframe": timeframe,
                    "_primary": primary,
                    "_secondary": secondary,
                }

                for k in wanted_keys:
                    if k in full and _is_small_value(full[k]):
                        out[k] = full[k]

                for k in [
                    "_data_quality_note",
                    "_data_quality_warnings",
                    "_degenerate_indicator_count",
                ]:
                    if k in full and _is_small_value(full[k]):
                        out[k] = full[k]

                return out

            indicators_for_llm = (
                _build_minimal_payload(indicators_for_agent)
                if use_minimal_payload
                else indicators_for_agent
            )
            # === END MINIMAL PAYLOAD ===

            # === REASONING BANK RETRIEVAL ===
            historical_context = ""
            if reasoning_bank and REASONING_BANK_AVAILABLE:
                try:
                    indicators_json_str = json.dumps(indicators_for_agent, indent=2, default=str)
                    historical_context = get_agent_context_from_bank(
                        reasoning_bank=reasoning_bank,
                        agent_name="technical_agent",
                        current_prompt=indicators_json_str,
                        limit=3,
                    )
                    if historical_context:
                        logger.info("🧠 ReasoningBank: Retrieved context for Technical Agent")
                except Exception as e:
                    logger.warning(f"Failed to retrieve reasoning context: {e}")
            # === END REASONING BANK RETRIEVAL ===

            # Format prompt
            messages = format_prompt(
                "technical_analyst",
                symbol=state.get("symbol", "BTCUSDT"),
                timeframe=timeframe,
                indicators_json=json.dumps(indicators_for_llm, indent=2, default=str),
                microstructure_summary=(
                    f"OBI={state.get('obi', 'N/A')}, "
                    f"TOBliq={state.get('tob_liquidity', 'N/A')}, "
                    f"OFI={state.get('ofi', 'N/A')}, "
                    f"OFInorm={state.get('ofi_norm', 'N/A')}, "
                    f"QI={state.get('qi', 'N/A')}, "
                    f"MLOFI={state.get('mlofi', 'N/A')}, "
                    f"MLOFInorm={state.get('mlofi_norm', 'N/A')}, "
                    f"WDI={state.get('wdi', 'N/A')}, "
                    f"VPIN={state.get('vpin_proxy', 'N/A')}, "
                    f"Spread={state.get('spread', 'N/A')} ({state.get('spread_pct', 'N/A')}%), "
                    f"Microprice={state.get('microprice', 'N/A')}, "
                    f"MicroBps={state.get('microprice_bps', 'N/A')}, "
                    f"Gap%={state.get('liquidity_gap_pct', 'N/A')}, "
                    f"CVDd5s={state.get('cvd_delta_5s', 'N/A')}, "
                    f"TImb5s={state.get('trade_imbalance_5s', 'N/A')}, "
                    f"TInt={state.get('trade_intensity_5s', 'N/A')}"
                ),
                htf_context=json.dumps(mtf_context.get("htf", {}), default=str),
                ltf_context=json.dumps(mtf_context.get("ltf", {}), default=str),
                current_price=str(state.get("current_price", "N/A")),
                current_volume=str(state.get("current_volume", "N/A")),
            )

            # Combine System Contexts (Reasoning + Guidance)
            system_append_parts = []
            if historical_context:
                system_append_parts.append(historical_context)
            if indicator_guidance:
                system_append_parts.append(indicator_guidance)

            if system_append_parts and messages:
                combined_append = "\n\n" + "\n\n".join(system_append_parts)
                messages[0]["content"] = messages[0]["content"] + combined_append

            if not messages:
                raise ValueError("Could not format technical prompt")

            llm_messages = [
                {"role": "system", "content": messages[0]["content"]},
                {"role": "user", "content": messages[1]["content"]},
            ]

            default_max_retries = 1 if short_tf else 3
            try:
                max_retries = int(
                    _get_env_first(
                        "FENIX_TECH_MAX_RETRIES",
                        "FENIX_TECHNICAL_MAX_RETRIES",
                        default=str(default_max_retries),
                    )
                )
            except Exception:
                max_retries = default_max_retries

            timeout_sec: float | None = None
            if short_tf:
                try:
                    timeout_sec = float(
                        _get_env_first(
                            "FENIX_TECH_TIMEOUT_SHORT_SEC",
                            "FENIX_TECHNICAL_TIMEOUT_SHORT_SEC",
                            default="45.0",
                        )
                    )
                except Exception:
                    timeout_sec = 45.0

            try:
                coro = invoke_with_retry_and_validation(
                    llm=llm,
                    messages=llm_messages,
                    agent_type="technical_analyst",
                    max_retries=max_retries,
                    base_delay=0.3,
                    required_keys=["signal"],
                )
                if timeout_sec is not None:
                    report, attempts, errors = await asyncio.wait_for(coro, timeout=timeout_sec)
                else:
                    report, attempts, errors = await coro
            except asyncio.TimeoutError:
                logger.warning(
                    "technical_analyst timed out after %.1fs (tf=%s); using deterministic fallback",
                    float(timeout_sec or 0.0),
                    timeframe,
                )
                used_cache = False
                try:
                    cache_ttl_sec = float(os.getenv("FENIX_AGENT_CACHE_TTL_SHORT_SEC", "180.0"))
                except Exception:
                    cache_ttl_sec = 180.0
                cache_enabled = os.getenv("FENIX_AGENT_CACHE_ON_TIMEOUT", "1") == "1"
                if short_tf and cache_enabled and agent_cache is not None:
                    cached = agent_cache.get(
                        agent="technical",
                        symbol=state.get("symbol", "BTCUSDT"),
                        timeframe=timeframe,
                        ttl_sec=cache_ttl_sec,
                    )
                    if cached is not None:
                        cached_report, age_sec = cached
                        try:
                            cached_conf = float(cached_report.get("confidence", 0.55) or 0.55)
                        except Exception:
                            cached_conf = 0.55
                        cached_report["confidence"] = max(0.50, min(0.95, cached_conf * 0.90))
                        cached_report["rationale"] = (
                            f"CACHED (timeout, age={age_sec:.0f}s): "
                            f"{cached_report.get('rationale') or cached_report.get('reasoning') or ''}"
                        ).strip()
                        cached_report["_cache_info"] = {
                            "reason": "llm_timeout",
                            "age_sec": float(age_sec),
                            "ttl_sec": float(cache_ttl_sec),
                        }
                        report = cached_report
                        used_cache = True
                        logger.info(
                            "technical_analyst timeout (tf=%s): using cached report (age=%.0fs, ttl=%.0fs)",
                            timeframe,
                            float(age_sec),
                            float(cache_ttl_sec),
                        )

                if not used_cache:
                    report = _fallback_report(indicators_for_agent)

                attempts = 0
                errors = ["Timeout waiting for technical_analyst LLM response"]

            raw_response = report.get("raw_response", json.dumps(report))
            save_legacy_agent_log("technical_enhanced", llm_messages, raw_response, report)

            elapsed = (datetime.now() - start_time).total_seconds()
            report = _normalize_technical_report(report)
            report = _apply_hold_override_policy(
                report,
                indicators_for_agent if isinstance(indicators_for_agent, dict) else {},
                current_price=_safe_float(state.get("current_price")),
            )
            report["_attempts"] = attempts
            if errors:
                report["_validation_errors"] = errors
            # Keep the decision prompt small on low timeframes: attach the same filtered payload we sent
            # to the LLM by default. Full raw indicators are still available on the engine result.
            attach_full = os.getenv("FENIX_ATTACH_FULL_INDICATORS_TO_TECH_REPORT", "0") == "1"
            if attach_full:
                report["indicators"] = state.get("indicators", {})
                report["_indicators_scope"] = "full"
            else:
                report["indicators"] = indicators_for_llm
                report["_indicators_scope"] = "filtered"

            # Cache only *valid* LLM reports (not fallbacks, not validation failures).
            if (
                agent_cache is not None
                and attempts > 0
                and not errors
                and not report.get("_validation_failed")
                and not report.get("error")
            ):
                try:
                    agent_cache.set(
                        agent="technical",
                        symbol=state.get("symbol", "BTCUSDT"),
                        timeframe=timeframe,
                        report=report,
                    )
                except Exception:
                    pass

            # Store result in ReasoningBank
            prompt_snippet = messages[1]["content"][:500] if messages and len(messages) > 1 else ""
            digest = store_to_reasoning_bank(
                reasoning_bank=reasoning_bank,
                agent_name="technical_agent",
                prompt=prompt_snippet,
                result=report,
                raw_response=raw_response,
                llm=llm,
                elapsed_ms=elapsed * 1000,
            )
            if digest:
                report["_reasoning_digest"] = digest

            return {
                "technical_report": report,
                "indicators_filtered": indicators_for_llm,
                "messages": state.get("messages", [])
                + [{"role": "assistant", "content": raw_response}],
                "execution_times": {
                    **state.get("execution_times", {}),
                    "technical": elapsed,
                },
            }

        except Exception as e:
            logger.error(f"Error in technical agent: {e}")
            return {
                "technical_report": {"signal": "HOLD", "error": str(e)},
                "errors": state.get("errors", []) + [f"Technical: {e}"],
            }

    async def traced_technical_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("technical_agent"):
            return await technical_node(state)

    return traced_technical_node
