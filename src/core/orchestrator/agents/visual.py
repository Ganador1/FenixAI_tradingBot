# src/core/orchestrator/agents/visual.py
"""
Visual Analyst Agent for Fenix Trading Bot.

Sends a chart image (base64) to a vision-capable LLM and parses
BUY / SELL / HOLD with confidence and reasoning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.core.orchestrator.agent_cache import AgentReportCache
from src.core.orchestrator.agents.base import (
    save_legacy_agent_log,
    store_to_reasoning_bank,
)
from src.core.orchestrator.json_parser import extract_json_from_content
from src.core.orchestrator.state import FenixAgentState
from src.core.orchestrator.validation import (
    build_validation_feedback,
    validate_agent_response,
)
from src.prompts.agent_prompts import format_prompt
from src.system.tracing import get_tracer

logger = logging.getLogger(__name__)


def create_visual_agent_node(
    llm: Any,
    reasoning_bank: Any = None,
    agent_cache: AgentReportCache | None = None,
):
    """Creates the visual agent node with retry and validation system."""

    async def visual_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()

        try:
            chart_b64 = state.get("chart_image_b64")
            timeframe = str(state.get("timeframe", "15m"))
            symbol = str(state.get("symbol", "BTCUSDT")).upper()
            short_tf = timeframe in {"1m", "3m", "5m"}
            # Non-blocking DISABLED - Decision agent must receive full LLM analysis from all sub-agents
            nonblocking_short_tf = False  # HARD-CODED OFF

            logger.info(
                f"🖼️ Visual Agent: LLM type: {type(llm)}, "
                f"model: {getattr(llm, 'model', 'unknown')}, "
                f"base_url: {getattr(llm, 'base_url', 'unknown')}"
            )
            logger.info(
                f"🖼️ Visual Agent: chart_image_b64 present = {chart_b64 is not None}, "
                f"length = {len(chart_b64) if chart_b64 else 0}"
            )

            if not chart_b64:
                logger.warning("🖼️ Visual Agent: No chart image in state")
                return {
                    "visual_report": {
                        "action": "HOLD",
                        "confidence": 0.5,
                        "reason": "No chart image available",
                    },
                }

            # Prepare message with image
            image_prompt = format_prompt(
                "visual_analyst",
                symbol=symbol,
                timeframe=timeframe,
                candle_count=50,
                visible_indicators="EMA 9/21, Bollinger Bands, SuperTrend",
                current_price=str(state.get("current_price", "N/A")),
                price_range="N/A",
            )

            if not image_prompt:
                raise ValueError("Could not format visual prompt")

            logger.info(
                f"🖼️ Visual Agent: Sending image ({len(chart_b64)} chars) to vision model..."
            )

            vision_content = [
                {"type": "text", "text": image_prompt[1]["content"]},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{chart_b64}"},
                },
            ]

            llm_messages = [
                SystemMessage(content=image_prompt[0]["content"]),
                HumanMessage(content=vision_content),
            ]

            def _fallback_report(reason: str) -> dict[str, Any]:
                return {
                    "action": "HOLD",
                    "confidence": 0.5,
                    "reason": reason,
                }

            async def _compute_report(
                *,
                max_retries: int,
                timeout_sec: float | None,
            ) -> tuple[dict[str, Any], int, list[str]]:
                base_delay = 0.3
                last_errors: list[str] = []
                report: dict[str, Any] | None = None
                attempt = 0
                # Copy per invocation so feedback appends don't leak across callers (async refresh etc).
                local_messages = list(llm_messages)

                for attempt in range(max_retries + 1):
                    try:
                        if attempt > 0:
                            delay = base_delay * (2 ** (attempt - 1))
                            logger.info(
                                "⏳ Visual Agent retry %s/%s: waiting %.1fs...",
                                attempt,
                                max_retries,
                                delay,
                            )
                            await asyncio.sleep(delay)

                        if timeout_sec is not None:
                            response = await asyncio.wait_for(
                                llm.ainvoke(local_messages), timeout=float(timeout_sec)
                            )
                        else:
                            response = await llm.ainvoke(local_messages)

                        content = response.content

                        logger.info(
                            "🖼️ Visual Agent: Response received, length=%s",
                            len(content) if content else 0,
                        )

                        report = extract_json_from_content(content, required_keys=["action"])

                        if report is None:
                            last_errors = ["Failed to extract valid JSON from response"]
                            if attempt < max_retries:
                                feedback = (
                                    f"\n⚠️ JSON PARSE ERROR (Attempt {attempt + 1}/{max_retries + 1})\n\n"
                                    f"Your response could not be parsed as valid JSON.\n\n"
                                    f"RAW RESPONSE PREVIEW:\n{content[:500]}...\n\n"
                                    f"CORRECTION INSTRUCTIONS:\n"
                                    f"- Ensure your response is VALID JSON only (no markdown code blocks)\n"
                                    f"- No extra text before or after JSON\n"
                                    f"- Check for syntax errors\n\n"
                                    f"Retry with valid JSON.\n"
                                )
                                local_messages.append(HumanMessage(content=feedback))
                                continue

                            report = {
                                "action": "HOLD",
                                "confidence": 0.5,
                                "reason": content[:1000] if content else "Parse error",
                                "_validation_failed": True,
                                "_validation_errors": last_errors,
                            }
                            break

                        validation_errors = validate_agent_response("visual_analyst", report)
                        if not validation_errors:
                            logger.info(
                                "✅ Visual Agent: Valid response on attempt %s", attempt + 1
                            )
                            break

                        last_errors = validation_errors
                        logger.warning(
                            "⚠️ Visual Agent: Validation failed on attempt %s: %s",
                            attempt + 1,
                            validation_errors,
                        )

                        if attempt < max_retries:
                            feedback = build_validation_feedback(
                                "visual_analyst", validation_errors, attempt + 1
                            )
                            local_messages.append(HumanMessage(content=feedback))
                        else:
                            report["_validation_failed"] = True
                            report["_validation_errors"] = validation_errors

                    except asyncio.TimeoutError:
                        last_errors = [f"Timeout after {timeout_sec}s"]
                        raise
                    except Exception as e:
                        last_errors = [f"Exception: {str(e)}"]
                        logger.error("❌ Visual Agent: Exception on attempt %s: %s", attempt + 1, e)
                        if attempt >= max_retries:
                            report = {
                                "action": "HOLD",
                                "confidence": 0.5,
                                "error": str(e),
                                "_exception": True,
                            }
                            break

                if report is None:
                    report = _fallback_report("All attempts failed")

                if "reason" not in report:
                    report["reason"] = "Visual analysis completed"

                report["_attempts"] = attempt + 1
                return report, attempt + 1, last_errors

            # Nonblocking short-TF mode: do not stall the graph waiting on a vision LLM.
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

                cached_report: dict[str, Any] | None = None
                cached_age: float | None = None
                if cache_enabled and agent_cache is not None:
                    cached = agent_cache.get(
                        agent="visual",
                        symbol=symbol,
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
                        "visual_analyst (nonblocking, tf=%s): cache hit (age=%.0fs, ttl=%.0fs)",
                        timeframe,
                        float(cached_age or 0.0),
                        float(cache_ttl_sec),
                    )
                else:
                    report = _fallback_report("Nonblocking short-TF: visual analysis deferred.")
                    report["_cache_info"] = {"reason": "nonblocking_fallback"}
                    logger.info(
                        "visual_analyst (nonblocking, tf=%s): no cache, using fallback", timeframe
                    )

                # Background refresh (disabled under pytest unless explicitly enabled).
                async_refresh = os.getenv("FENIX_AGENT_CACHE_ASYNC_REFRESH", "1") == "1"
                if os.getenv("PYTEST_CURRENT_TEST") is not None:
                    async_refresh = (
                        os.getenv("FENIX_AGENT_CACHE_ASYNC_REFRESH_UNDER_PYTEST", "0") == "1"
                    )

                if cache_enabled and async_refresh and agent_cache is not None:
                    if agent_cache.can_start_refresh(
                        agent="visual",
                        symbol=symbol,
                        timeframe=timeframe,
                        min_interval_sec=refresh_min_sec,
                    ):

                        async def _refresh() -> None:
                            try:
                                refreshed, _attempts, errs = await _compute_report(
                                    max_retries=1,
                                    timeout_sec=min(15.0, float(refresh_timeout_sec)),
                                )
                                if (
                                    not errs
                                    and not refreshed.get("_validation_failed")
                                    and not refreshed.get("error")
                                ):
                                    agent_cache.set(
                                        agent="visual",
                                        symbol=symbol,
                                        timeframe=timeframe,
                                        report=refreshed,
                                    )
                                    logger.info(
                                        "visual_analyst async refresh cached (tf=%s)", timeframe
                                    )
                            except asyncio.TimeoutError:
                                logger.debug(
                                    "visual_analyst async refresh timed out after %.1fs (tf=%s)",
                                    float(refresh_timeout_sec),
                                    timeframe,
                                )
                            except Exception as e:
                                logger.debug("visual_analyst async refresh failed: %s", e)

                        agent_cache.set_inflight(
                            agent="visual",
                            symbol=symbol,
                            timeframe=timeframe,
                            task=asyncio.create_task(_refresh()),
                        )

                elapsed = (datetime.now() - start_time).total_seconds()
                report["_elapsed_seconds"] = elapsed
                report["_nonblocking"] = True
                return {
                    "visual_report": report,
                    "execution_times": {
                        **state.get("execution_times", {}),
                        "visual": elapsed,
                    },
                }

            # Blocking path (higher TF): bound total time waiting on the vision model.
            timeout_sec: float | None = None
            if short_tf:
                try:
                    timeout_sec = float(os.getenv("FENIX_VISUAL_TIMEOUT_SHORT_SEC", "60.0"))
                except Exception:
                    timeout_sec = 60.0
            else:
                try:
                    timeout_sec = float(os.getenv("FENIX_VISUAL_TIMEOUT_SEC", "60.0"))
                except Exception:
                    timeout_sec = 60.0

            max_retries = 2 if short_tf else 3
            try:
                report, attempts_used, errors = await _compute_report(
                    max_retries=max_retries,
                    timeout_sec=timeout_sec,
                )
            except asyncio.TimeoutError:
                used_cache = False
                errors = [f"Timeout waiting for visual_analyst after {timeout_sec}s"]
                report = None
                cache_enabled = os.getenv("FENIX_AGENT_CACHE_ON_TIMEOUT", "1") == "1"
                try:
                    cache_ttl_sec = float(os.getenv("FENIX_AGENT_CACHE_TTL_SHORT_SEC", "180.0"))
                except Exception:
                    cache_ttl_sec = 180.0
                if cache_enabled and agent_cache is not None:
                    cached = agent_cache.get(
                        agent="visual",
                        symbol=symbol,
                        timeframe=timeframe,
                        ttl_sec=cache_ttl_sec,
                    )
                    if cached is not None:
                        cached_report, age_sec = cached
                        try:
                            cached_conf = float(cached_report.get("confidence", 0.5) or 0.5)
                        except Exception:
                            cached_conf = 0.5
                        cached_report["confidence"] = max(0.35, min(0.95, cached_conf * 0.90))
                        cached_report["reason"] = (
                            f"CACHED (timeout, age={age_sec:.0f}s): {cached_report.get('reason') or ''}"
                        ).strip()
                        cached_report["_cache_info"] = {
                            "reason": "llm_timeout",
                            "age_sec": float(age_sec),
                            "ttl_sec": float(cache_ttl_sec),
                        }
                        report = cached_report
                        used_cache = True
                        logger.info(
                            "visual_analyst timeout (tf=%s): using cached report (age=%.0fs, ttl=%.0fs)",
                            timeframe,
                            float(age_sec),
                            float(cache_ttl_sec),
                        )
                if not used_cache:
                    report = _fallback_report("Timeout waiting for vision LLM; using HOLD.")
                attempts_used = 0

            # Cache only valid LLM reports.
            try:
                cache_enabled = os.getenv("FENIX_AGENT_CACHE_ON_TIMEOUT", "1") == "1"
                if (
                    cache_enabled
                    and agent_cache is not None
                    and not errors
                    and not report.get("_validation_failed")
                    and not report.get("error")
                ):
                    agent_cache.set(
                        agent="visual", symbol=symbol, timeframe=timeframe, report=report
                    )
            except Exception:
                pass

            attempt = int(attempts_used or report.get("_attempts") or 0)
            last_errors = errors

            # Ensure minimum fields
            # DEBUG LOGGING (disabled by default)
            if os.getenv("FENIX_DEBUG_VISUAL_RAW", "0") == "1":
                try:
                    debug_path = os.getenv(
                        "FENIX_DEBUG_VISUAL_RAW_PATH", "logs/debug_visual_raw.log"
                    )
                    Path(debug_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(debug_path, "a", encoding="utf-8") as f:
                        f.write(
                            f"\n--- {datetime.now()} ---\n{json.dumps(report, indent=2)}\n----------------\n"
                        )
                except Exception:
                    pass

            logger.info(f"🖼️ Visual Agent: Parsed JSON with action={report.get('action')}")

            # Legacy logging - avoid logging base64 image
            log_messages = [
                SystemMessage(content=image_prompt[0]["content"]),
                HumanMessage(
                    content=f"[IMAGE CONTENT HIDDEN] \nPrompt: {image_prompt[1]['content']}"
                ),
            ]
            save_legacy_agent_log("visual", log_messages, json.dumps(report), report)

            elapsed = (datetime.now() - start_time).total_seconds()
            report["_elapsed_seconds"] = elapsed

            # Persist in ReasoningBank
            prompt_snippet = (
                image_prompt[1]["content"][:500] if image_prompt and len(image_prompt) > 1 else ""
            )
            digest = store_to_reasoning_bank(
                reasoning_bank=reasoning_bank,
                agent_name="visual_agent",
                prompt=prompt_snippet,
                result=report,
                raw_response=json.dumps(report),
                llm=llm,
                elapsed_ms=elapsed * 1000,
            )
            if digest:
                report["_reasoning_digest"] = digest

            logger.info(f"🖼️ Visual Agent: Completed in {elapsed:.2f}s")

            return {
                "visual_report": report,
                "execution_times": {
                    **state.get("execution_times", {}),
                    "visual": elapsed,
                },
            }

        except Exception as e:
            logger.error(f"Error in visual agent: {e}")
            return {
                "visual_report": {
                    "action": "HOLD",
                    "error": str(e),
                    "reason": f"Error in visual analysis: {str(e)}",
                },
                "errors": state.get("errors", []) + [f"Visual: {e}"],
            }

    async def traced_visual_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("visual_agent"):
            return await visual_node(state)

    return traced_visual_node
