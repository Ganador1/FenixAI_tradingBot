# src/core/orchestrator/agents/qabba.py
"""
QABBA Agent for Fenix Trading Bot.

Quantitative Algorithmic Bid-ask Balance Analysis — evaluates
order-book micro-structure (OBI, CVD, OFI, QI, MLOFI, VPIN, spread)
to produce a BUY / SELL / HOLD signal.
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
from src.indicators.advanced_indicators import FundingRateIndicator
from src.indicators.timeframe_aware_indicators import (
    format_indicator_guidance,
    get_optimal_indicators,
)
from src.prompts.agent_prompts import format_prompt
from src.system.tracing import get_tracer

logger = logging.getLogger(__name__)


def _qabba_trim_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    trimmed = text[:max_chars].rstrip()
    for sep in ("\n", ". ", "; ", ", "):
        head = trimmed.rsplit(sep, 1)[0].strip()
        if head:
            return head
    return trimmed


def _qabba_format_recent_micro_trades(state: FenixAgentState, limit: int = 20) -> str:
    trades = state.get("recent_trades_5s") or state.get("recent_trades") or []
    if not isinstance(trades, list) or not trades:
        return "[]"
    try:
        return json.dumps(trades[-limit:], default=str, separators=(",", ":"))
    except Exception:
        return "[]"


def _qabba_derived_feature_keys(timeframe: str) -> tuple[str, ...]:
    if timeframe in {"1m", "3m", "5m"}:
        return (
            "tob_liquidity",
            "wdi",
            "liquidity_gap_pct",
            "microprice_bps",
            "ofi_norm",
            "mlofi_norm",
            "trade_buy_vol_5s",
            "trade_sell_vol_5s",
            "cvd_delta_5s",
            "trade_intensity_5s",
            "avg_trade_size_5s",
        )
    return (
        "wdi",
        "liquidity_gap_pct",
        "microprice_bps",
        "cvd_delta_5s",
        "trade_intensity_5s",
    )


def _qabba_build_advanced_microstructure(state: FenixAgentState, timeframe: str) -> dict[str, Any]:
    advanced_microstructure: dict[str, Any] = {}
    derived: dict[str, Any] = {}
    for key in _qabba_derived_feature_keys(timeframe):
        if (val := state.get(key)) is not None:
            derived[key] = val
    if derived:
        advanced_microstructure["derived_features"] = derived

    funding_rate = state.get("funding_rate")
    if funding_rate is not None:
        funding_ind = FundingRateIndicator()
        funding_result = funding_ind.calculate(funding_rate)
        advanced_microstructure["funding_rate"] = {
            "value": funding_result.value,
            "signal": funding_result.signal,
            "interpretation": funding_result.interpretation,
            "confidence": funding_result.confidence,
        }

    return advanced_microstructure


def create_qabba_agent_node(
    llm: Any,
    reasoning_bank: Any = None,
    agent_cache: AgentReportCache | None = None,
):
    """Creates the QABBA agent node (microstructure) with retry and validation system."""

    async def qabba_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()

        try:
            timeframe = str(state.get("timeframe", "5m"))
            short_tf = timeframe in {"1m", "3m", "5m"}
            try:
                reasoning_context_max_chars = int(
                    os.getenv(
                        "FENIX_QABBA_REASONING_CONTEXT_MAX_CHARS",
                        "450" if timeframe == "15m" else "700",
                    )
                )
            except Exception:
                reasoning_context_max_chars = 450 if timeframe == "15m" else 700
            try:
                guidance_max_chars = int(
                    os.getenv(
                        "FENIX_QABBA_GUIDANCE_MAX_CHARS", "600" if timeframe == "15m" else "900"
                    )
                )
            except Exception:
                guidance_max_chars = 600 if timeframe == "15m" else 900
            # Short-TF nonblocking mode is controlled via env and defaults to OFF for benchmark quality.
            # This ensures we get full LLM reports instead of heuristics.
            nonblocking_short_tf = short_tf and (
                os.getenv("FENIX_SHORT_TF_NONBLOCKING", "0") == "1"
            )

            def _fallback_report() -> dict[str, Any]:
                # Deterministic microstructure fallback for short TF timeouts.
                try:
                    obi = float(state.get("obi", 1.0) or 1.0)
                except Exception:
                    obi = 1.0
                try:
                    wdi = float(state.get("wdi", 0.0) or 0.0)
                except Exception:
                    wdi = 0.0
                try:
                    vol_imb = float(state.get("volume_imbalance", 0.0) or 0.0)
                except Exception:
                    vol_imb = 0.0
                try:
                    ofi = float(state.get("ofi", 0.0) or 0.0)
                except Exception:
                    ofi = 0.0
                try:
                    qi = float(state.get("qi", 0.0) or 0.0)
                except Exception:
                    qi = 0.0
                try:
                    mlofi = float(state.get("mlofi", 0.0) or 0.0)
                except Exception:
                    mlofi = 0.0
                try:
                    vpin = float(state.get("vpin_proxy", 0.0) or 0.0)
                except Exception:
                    vpin = 0.0
                try:
                    spread_pct = float(state.get("spread_pct", 0.0) or 0.0)
                except Exception:
                    spread_pct = 0.0
                try:
                    trade_imb = float(state.get("trade_imbalance_5s", 0.0) or 0.0)
                except Exception:
                    trade_imb = 0.0
                try:
                    micro_bps = float(state.get("microprice_bps", 0.0) or 0.0)
                except Exception:
                    micro_bps = 0.0
                try:
                    gap_pct = float(state.get("liquidity_gap_pct", 0.0) or 0.0)
                except Exception:
                    gap_pct = 0.0
                try:
                    cvd_delta = float(state.get("cvd_delta_5s", 0.0) or 0.0)
                except Exception:
                    cvd_delta = 0.0

                # Basic guardrails
                if spread_pct > 0.15:
                    return {
                        "signal": "HOLD",
                        "confidence": 0.55,
                        "rationale": f"Timeout fallback: wide spread ({spread_pct:.3f}%)",
                    }
                if gap_pct > 0.10 and spread_pct > 0.08:
                    return {
                        "signal": "HOLD",
                        "confidence": 0.55,
                        "rationale": f"Timeout fallback: thin book gap ({gap_pct:.3f}%) + spread ({spread_pct:.3f}%)",
                    }

                score = 0.0
                parts: list[str] = []

                if obi >= 1.25:
                    score += 1.0
                    parts.append(f"OBI {obi:.2f}")
                elif obi <= 0.80:
                    score -= 1.0
                    parts.append(f"OBI {obi:.2f}")

                if wdi >= 0.15:
                    score += 0.4
                    parts.append(f"WDI {wdi:+.2f}")
                elif wdi <= -0.15:
                    score -= 0.4
                    parts.append(f"WDI {wdi:+.2f}")

                if vol_imb >= 0.15:
                    score += 0.5
                    parts.append(f"VImb {vol_imb:.2f}")
                elif vol_imb <= -0.15:
                    score -= 0.5
                    parts.append(f"VImb {vol_imb:.2f}")

                if ofi:
                    score += 0.6 if ofi > 0 else -0.6
                    parts.append("OFI+" if ofi > 0 else "OFI-")
                if abs(qi) >= 0.05:
                    score += 0.4 if qi > 0 else -0.4
                    parts.append(f"QI {qi:+.2f}")
                if mlofi:
                    score += 0.4 if mlofi > 0 else -0.4
                    parts.append("MLOFI+" if mlofi > 0 else "MLOFI-")

                if abs(trade_imb) >= 0.20:
                    score += 0.7 if trade_imb > 0 else -0.7
                    parts.append(f"TImb {trade_imb:+.2f}")

                if abs(micro_bps) >= 2.0:
                    score += 0.25 if micro_bps > 0 else -0.25
                    parts.append(f"MicroBps {micro_bps:+.1f}")

                if abs(cvd_delta) >= 0.5:
                    score += 0.35 if cvd_delta > 0 else -0.35
                    parts.append("CVDd+" if cvd_delta > 0 else "CVDd-")

                # Toxicity guard
                if vpin >= 0.90:
                    score *= 0.5
                    parts.append(f"VPIN {vpin:.2f}")

                signal = "HOLD"
                if score >= 1.2:
                    signal = "BUY"
                elif score <= -1.2:
                    signal = "SELL"

                conf = max(0.55, min(0.90, abs(score) / 2.0 + 0.55))
                rationale = "Timeout fallback (short TF): " + (
                    ", ".join(parts)[:160] if parts else "weak microstructure"
                )
                return {"signal": signal, "confidence": conf, "rationale": rationale}

            # Nonblocking short-TF path: return cached/fallback quickly and refresh in background.
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
                        os.getenv("FENIX_AGENT_CACHE_ASYNC_REFRESH_TIMEOUT_SEC", "300.0")
                    )
                except Exception:
                    refresh_timeout_sec = 30.0

                cached_report: dict[str, Any] | None = None
                cached_age: float | None = None
                if cache_enabled and agent_cache is not None:
                    cached = agent_cache.get(
                        agent="qabba",
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
                        "qabba_analyst (nonblocking, tf=%s): cache hit (age=%.0fs, ttl=%.0fs)",
                        timeframe,
                        float(cached_age or 0.0),
                        float(cache_ttl_sec),
                    )
                else:
                    report = _fallback_report()
                    report["_cache_info"] = {"reason": "nonblocking_fallback"}
                    logger.info(
                        "qabba_analyst (nonblocking, tf=%s): no cache, using fallback", timeframe
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
                        os.getenv("FENIX_QABBA_MAX_RETRIES", str(default_max_retries))
                    )
                except Exception:
                    max_retries = default_max_retries

                if cache_enabled and async_refresh and agent_cache is not None:
                    sym = state.get("symbol", "BTCUSDT")
                    if agent_cache.can_start_refresh(
                        agent="qabba",
                        symbol=sym,
                        timeframe=timeframe,
                        min_interval_sec=refresh_min_sec,
                    ):
                        sym_s = sym
                        ind_s = dict(state.get("indicators", {}) or {})

                        async def _refresh() -> None:
                            try:
                                indicator_guidance = ""
                                advanced_microstructure: dict[str, Any] = {}

                                try:
                                    indicator_suite = get_optimal_indicators(
                                        timeframe=timeframe,
                                        available_feeds=["orderbook", "trades"],
                                    )
                                    indicator_guidance = format_indicator_guidance(indicator_suite)
                                except Exception:
                                    indicator_guidance = ""

                                advanced_microstructure = _qabba_build_advanced_microstructure(
                                    state, timeframe
                                )

                                msgs = format_prompt(
                                    "qabba_analyst",
                                    symbol=sym_s,
                                    obi_value=str(state.get("obi", 1.0)),
                                    cvd_value=str(state.get("cvd", 0)),
                                    mid_price_value=str(state.get("mid_price", 0.0)),
                                    microprice_value=str(state.get("microprice", 0.0)),
                                    spread_value=str(state.get("spread", 0.01)),
                                    spread_pct_value=str(state.get("spread_pct", 0.0)),
                                    ofi_value=str(state.get("ofi", 0)),
                                    qi_value=str(state.get("qi", 0)),
                                    mlofi_value=str(state.get("mlofi", 0)),
                                    volume_imbalance_value=str(state.get("volume_imbalance", 0)),
                                    vpin_proxy_value=str(state.get("vpin_proxy", 0)),
                                    bid_depth=str(
                                        state.get("orderbook_depth", {}).get("bid_depth", "N/A")
                                    ),
                                    ask_depth=str(
                                        state.get("orderbook_depth", {}).get("ask_depth", "N/A")
                                    ),
                                    total_liquidity=str(
                                        state.get("orderbook_depth", {}).get("total", "N/A")
                                    ),
                                    cvd_delta_5s_value=str(state.get("cvd_delta_5s", 0.0)),
                                    trade_imbalance_5s_value=str(
                                        state.get("trade_imbalance_5s", 0.0)
                                    ),
                                    trade_volume_5s_value=str(state.get("trade_volume_5s", 0.0)),
                                    trade_count_5s_value=str(state.get("trade_count_5s", 0)),
                                    recent_trades=_qabba_format_recent_micro_trades(state),
                                    current_price=str(state.get("current_price", "N/A")),
                                    market_context=json.dumps(
                                        {
                                            "market_condition": ind_s.get("market_condition"),
                                            "chop": ind_s.get("chop"),
                                            "chop_regime": ind_s.get("chop_regime"),
                                            "bb_inside_kc": ind_s.get("bb_inside_kc"),
                                            "bb_squeeze": ind_s.get("bb_squeeze"),
                                            "donchian_width_pct": ind_s.get("donchian_width_pct"),
                                            "trend_conflict": ind_s.get("trend_conflict"),
                                        },
                                        default=str,
                                    ),
                                )
                                if not msgs:
                                    return

                                if indicator_guidance:
                                    msgs[0]["content"] += "\n\n" + indicator_guidance
                                if advanced_microstructure:
                                    adv_json = json.dumps(advanced_microstructure, indent=2)
                                    msgs[1]["content"] += (
                                        f"\n\nADVANCED MICROSTRUCTURE INDICATORS:\n{adv_json}"
                                    )

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
                                        agent_type="qabba_analyst",
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
                                        agent="qabba",
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
                                            "qabba_analyst async refresh cached (tf=%s, attempts=%s, llm_ms=%.0f)",
                                            timeframe,
                                            refresh_attempts,
                                            float(llm_ms),
                                        )
                                    else:
                                        logger.info(
                                            "qabba_analyst async refresh cached (tf=%s, attempts=%s)",
                                            timeframe,
                                            refresh_attempts,
                                        )
                            except asyncio.TimeoutError:
                                logger.debug(
                                    "qabba_analyst async refresh timed out after %.1fs (tf=%s)",
                                    float(refresh_timeout_sec),
                                    timeframe,
                                )
                            except Exception as e:
                                logger.debug("qabba_analyst async refresh failed: %s", e)

                        agent_cache.set_inflight(
                            agent="qabba",
                            symbol=sym,
                            timeframe=timeframe,
                            task=asyncio.create_task(_refresh()),
                        )

                raw_response = json.dumps(report)
                elapsed = (datetime.now() - start_time).total_seconds()
                report["_attempts"] = 0
                report["_nonblocking"] = True

                return {
                    "qabba_report": report,
                    "execution_times": {
                        **state.get("execution_times", {}),
                        "qabba": elapsed,
                    },
                }

            # === TIMEFRAME-AWARE INDICATOR SELECTION FOR QABBA ===
            # QABBA is microstructure-first. On higher TFs, still treat microstructure as the
            # primary "timing" layer (entries/exits), even if the overall strategy is HTF.
            default_guidance_tf = os.getenv("FENIX_QABBA_GUIDANCE_TIMEFRAME", "5m")
            guidance_tf = timeframe if timeframe in {"1m", "3m", "5m"} else default_guidance_tf
            indicator_guidance = ""
            advanced_microstructure: dict[str, Any] = {}

            try:
                indicator_suite = get_optimal_indicators(
                    timeframe=guidance_tf,
                    available_feeds=["orderbook", "trades"],
                )
                indicator_guidance = format_indicator_guidance(indicator_suite)
                if guidance_tf != timeframe:
                    indicator_guidance += (
                        f"\n\nNOTE: Actual analysis timeframe is {timeframe}. "
                        "Use microstructure indicators primarily for entry/exit timing and "
                        "reduce confidence unless order flow signals are clearly extreme."
                    )

                # Light "feature pack" for the LLM: derived microstructure signals that are cheap to compute.
                # This makes the agent less dependent on single raw metrics like OBI or spread.
                advanced_microstructure = _qabba_build_advanced_microstructure(state, timeframe)

                logger.info(
                    f"📊 QABBA: Using {len(indicator_suite.primary_indicators)} "
                    f"primary microstructure indicators for {timeframe} (guidance_tf={guidance_tf})"
                )

            except Exception as e:
                logger.warning(f"Could not generate QABBA indicator guidance: {e}")
            # === END INDICATOR SELECTION ===

            # === REASONING BANK RETRIEVAL ===
            historical_context = ""
            if reasoning_bank and REASONING_BANK_AVAILABLE:
                try:
                    obi_val = state.get("obi", 1.0)
                    cvd_val = state.get("cvd", 0)
                    spread_val = state.get("spread", 0.01)
                    qabba_query = f"OBI: {obi_val}, CVD: {cvd_val}, Spread: {spread_val}"

                    historical_context = get_agent_context_from_bank(
                        reasoning_bank=reasoning_bank,
                        agent_name="qabba_agent",
                        current_prompt=qabba_query,
                        limit=3,
                    )
                    if historical_context:
                        historical_context = _qabba_trim_text(
                            historical_context, reasoning_context_max_chars
                        )
                        logger.info("🧠 ReasoningBank: Retrieved context for QABBA Agent")
                except Exception as e:
                    logger.warning(f"Failed to retrieve QABBA reasoning context: {e}")
            # === END REASONING BANK RETRIEVAL ===

            messages = format_prompt(
                "qabba_analyst",
                symbol=state.get("symbol", "BTCUSDT"),
                obi_value=str(state.get("obi", 1.0)),
                cvd_value=str(state.get("cvd", 0)),
                mid_price_value=str(state.get("mid_price", 0.0)),
                microprice_value=str(state.get("microprice", 0.0)),
                spread_value=str(state.get("spread", 0.01)),
                spread_pct_value=str(state.get("spread_pct", 0.0)),
                ofi_value=str(state.get("ofi", 0)),
                qi_value=str(state.get("qi", 0)),
                mlofi_value=str(state.get("mlofi", 0)),
                volume_imbalance_value=str(state.get("volume_imbalance", 0)),
                vpin_proxy_value=str(state.get("vpin_proxy", 0)),
                bid_depth=str(state.get("orderbook_depth", {}).get("bid_depth", "N/A")),
                ask_depth=str(state.get("orderbook_depth", {}).get("ask_depth", "N/A")),
                total_liquidity=str(state.get("orderbook_depth", {}).get("total", "N/A")),
                cvd_delta_5s_value=str(state.get("cvd_delta_5s", 0.0)),
                trade_imbalance_5s_value=str(state.get("trade_imbalance_5s", 0.0)),
                trade_volume_5s_value=str(state.get("trade_volume_5s", 0.0)),
                trade_count_5s_value=str(state.get("trade_count_5s", 0)),
                recent_trades=_qabba_format_recent_micro_trades(state),
                current_price=str(state.get("current_price", "N/A")),
                market_context=json.dumps(
                    {
                        "market_condition": state.get("indicators", {}).get("market_condition"),
                        "chop": state.get("indicators", {}).get("chop"),
                        "chop_regime": state.get("indicators", {}).get("chop_regime"),
                        "bb_inside_kc": state.get("indicators", {}).get("bb_inside_kc"),
                        "bb_squeeze": state.get("indicators", {}).get("bb_squeeze"),
                        "donchian_width_pct": state.get("indicators", {}).get("donchian_width_pct"),
                        "trend_conflict": state.get("indicators", {}).get("trend_conflict"),
                    },
                    default=str,
                ),
            )

            # Add indicator guidance and advanced microstructure data
            if messages:
                combined_append_parts = []
                if historical_context:
                    combined_append_parts.append(historical_context)
                if indicator_guidance:
                    combined_append_parts.append(
                        _qabba_trim_text(indicator_guidance, guidance_max_chars)
                    )
                if combined_append_parts:
                    messages[0]["content"] += "\n\n" + "\n\n".join(combined_append_parts)

                if advanced_microstructure:
                    advanced_json = json.dumps(advanced_microstructure, indent=2)
                    messages[1]["content"] += (
                        f"\n\nADVANCED MICROSTRUCTURE INDICATORS:\n{advanced_json}"
                    )

            if not messages:
                raise ValueError("Could not format QABBA prompt")

            llm_messages = [
                {"role": "system", "content": messages[0]["content"]},
                {"role": "user", "content": messages[1]["content"]},
            ]

            default_max_retries = 1 if short_tf else 3
            try:
                max_retries = int(os.getenv("FENIX_QABBA_MAX_RETRIES", str(default_max_retries)))
            except Exception:
                max_retries = default_max_retries

            timeout_sec: float | None = None
            if short_tf:
                try:
                    timeout_sec = float(os.getenv("FENIX_QABBA_TIMEOUT_SHORT_SEC", "30.0"))
                except Exception:
                    timeout_sec = 30.0

            try:
                coro = invoke_with_retry_and_validation(
                    llm=llm,
                    messages=llm_messages,
                    agent_type="qabba_analyst",
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
                    "qabba_analyst timed out after %.1fs (tf=%s); using deterministic fallback",
                    float(timeout_sec or 0.0),
                    timeframe,
                )
                used_cache = False
                cache_enabled = os.getenv("FENIX_AGENT_CACHE_ON_TIMEOUT", "1") == "1"
                try:
                    cache_ttl_sec = float(os.getenv("FENIX_AGENT_CACHE_TTL_SHORT_SEC", "180.0"))
                except Exception:
                    cache_ttl_sec = 180.0

                if short_tf and cache_enabled and agent_cache is not None:
                    cached = agent_cache.get(
                        agent="qabba",
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
                            "qabba_analyst timeout (tf=%s): using cached report (age=%.0fs, ttl=%.0fs)",
                            timeframe,
                            float(age_sec),
                            float(cache_ttl_sec),
                        )

                if not used_cache:
                    report = _fallback_report()

                attempts = 0
                errors = ["Timeout waiting for qabba_analyst LLM response"]

            raw_response = report.get("raw_response", json.dumps(report))
            save_legacy_agent_log("qabba_enhanced", llm_messages, raw_response, report)

            elapsed = (datetime.now() - start_time).total_seconds()
            report["_attempts"] = attempts
            if errors:
                report["_validation_errors"] = errors

            # Store in ReasoningBank
            prompt_snippet = messages[1]["content"][:500] if messages and len(messages) > 1 else ""
            digest = store_to_reasoning_bank(
                reasoning_bank=reasoning_bank,
                agent_name="qabba_agent",
                prompt=prompt_snippet,
                result=report,
                raw_response=raw_response,
                llm=llm,
                elapsed_ms=elapsed * 1000,
            )
            if digest:
                report["_reasoning_digest"] = digest

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
                        agent="qabba",
                        symbol=state.get("symbol", "BTCUSDT"),
                        timeframe=timeframe,
                        report=report,
                    )
                except Exception:
                    pass

            return {
                "qabba_report": report,
                "execution_times": {
                    **state.get("execution_times", {}),
                    "qabba": elapsed,
                },
            }

        except Exception as e:
            logger.error(f"Error in QABBA agent: {e}")
            return {
                "qabba_report": {"signal": "HOLD", "error": str(e)},
                "errors": state.get("errors", []) + [f"QABBA: {e}"],
            }

    async def traced_qabba_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("qabba_agent"):
            return await qabba_node(state)

    return traced_qabba_node
