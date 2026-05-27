# src/core/orchestrator/agents/sentiment.py
"""
Sentiment Analyst Agent for Fenix Trading Bot.

Analyzes news, social media, and Fear & Greed Index to produce
a POSITIVE / NEGATIVE / NEUTRAL sentiment with confidence.

IMPROVEMENTS v2.1:
- News cache: reduces API calls and improves response time
- Shorter timeout: faster fallback on errors
- Simplified payload: reduces token usage
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any

from src.core.orchestrator.agents.base import (
    save_legacy_agent_log,
    store_to_reasoning_bank,
)
from src.core.orchestrator.retry_system import invoke_with_retry_and_validation
from src.core.orchestrator.state import FenixAgentState
from src.prompts.agent_prompts import format_prompt
from src.system.tracing import get_tracer

logger = logging.getLogger(__name__)

# NEWS CACHE: symbol -> (timestamp, news_list)
_news_cache: dict[str, tuple[float, list]] = {}
_NEWS_CACHE_TTL_SEC = 900  # 15 minutes cache


def _get_cached_news(symbol: str, fresh_news: list) -> list:
    """
    Returns cached news if available and fresh, otherwise updates cache.
    This reduces redundant news fetching and LLM processing.
    """
    global _news_cache
    now = time.time()

    if symbol in _news_cache:
        cached_time, cached_news = _news_cache[symbol]
        if now - cached_time < _NEWS_CACHE_TTL_SEC:
            logger.debug(f"📰 Using cached news for {symbol} (age: {int(now - cached_time)}s)")
            return cached_news

    # Update cache with fresh news
    _news_cache[symbol] = (now, fresh_news)
    return fresh_news


def create_sentiment_agent_node(llm: Any, reasoning_bank: Any = None):
    """Creates the sentiment agent node with retry and validation system."""

    async def sentiment_node(state: FenixAgentState) -> dict:
        start_time = datetime.now()

        try:
            # Build news summary from state news_data
            raw_news_list = state.get("news_data", [])
            symbol = state.get("symbol", "BTCUSDT")

            # Use cache to reduce processing
            news_list = _get_cached_news(symbol, raw_news_list)

            if news_list:
                # REDUCED: Limit to 3 items instead of 5 to reduce tokens
                news_items = [
                    f"- [{n.get('source', 'N/A')}] {n.get('title', 'Untitled')}: "
                    f"{n.get('summary', '')[:80]}..."  # Shorter summary
                    for n in news_list[:3]
                ]
                news_summary = "\n".join(news_items)
            else:
                news_summary = "No recent news available"

            timeframe = str(state.get("timeframe", "15m"))
            short_tf = timeframe in {"1m", "3m", "5m"}

            def _get_env_int(*keys: str, default: int) -> int:
                for key in keys:
                    raw = os.getenv(key)
                    if raw is None:
                        continue
                    try:
                        return int(raw)
                    except Exception:
                        continue
                return int(default)

            def _get_env_float(*keys: str, default: float) -> float:
                for key in keys:
                    raw = os.getenv(key)
                    if raw is None:
                        continue
                    try:
                        return float(raw)
                    except Exception:
                        continue
                return float(default)

            def _clip_text(value: Any, max_chars: int) -> str:
                text = str(value or "")
                if len(text) <= max_chars:
                    return text
                return text[: max(0, max_chars - 3)] + "..."

            def _compact_social_data(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
                # REDUCED: Fewer users and posts for faster processing
                max_users = max(1, _get_env_int("FENIX_SENTIMENT_MAX_USERS", default=3))
                max_posts = max(1, _get_env_int("FENIX_SENTIMENT_MAX_POSTS_PER_USER", default=1))
                max_chars = max(60, _get_env_int("FENIX_SENTIMENT_MAX_TEXT_CHARS", default=120))
                out: dict[str, Any] = {"twitter": {}, "reddit": {}}
                counts = {"twitter_posts": 0, "reddit_posts": 0}

                for source in ("twitter", "reddit"):
                    source_payload = raw.get(source, {}) if isinstance(raw, dict) else {}
                    if not isinstance(source_payload, dict):
                        continue
                    users = list(source_payload.items())[:max_users]
                    compact_users: dict[str, list[dict[str, Any]]] = {}
                    for username, posts in users:
                        if not isinstance(posts, list):
                            continue
                        compact_posts = []
                        for post in posts[:max_posts]:
                            if not isinstance(post, dict):
                                continue
                            compact_posts.append(
                                {
                                    "text": _clip_text(post.get("text", ""), max_chars),
                                    "source": post.get("source"),
                                    "timestamp_utc": post.get("timestamp_utc"),
                                }
                            )
                        if compact_posts:
                            compact_users[str(username)] = compact_posts
                            if source == "twitter":
                                counts["twitter_posts"] += len(compact_posts)
                            else:
                                counts["reddit_posts"] += len(compact_posts)
                    out[source] = compact_users
                return out, counts

            def _fallback_sentiment(
                reason: str, llm_report: dict[str, Any] | None = None
            ) -> dict[str, Any]:
                prev = state.get("sentiment_report") or {}
                if isinstance(prev, dict):
                    prev_sentiment = str(prev.get("overall_sentiment", "")).upper()
                    if prev_sentiment in {"POSITIVE", "NEGATIVE", "NEUTRAL"}:
                        try:
                            prev_conf = float(prev.get("confidence_score", 0.5) or 0.5)
                        except Exception:
                            prev_conf = 0.5
                        return {
                            "overall_sentiment": prev_sentiment,
                            "confidence_score": prev_conf,
                            "reasoning": "Fallback to previous sentiment snapshot due to transient LLM/provider issues.",
                            "sentiment_trend_short_term": prev.get(
                                "sentiment_trend_short_term", "NEUTRAL"
                            ),
                            "_fallback": "previous_sentiment",
                            "_fallback_reason": reason,
                            "_llm_error": (llm_report or {}).get("error"),
                        }
                return {
                    "overall_sentiment": "NEUTRAL",
                    "confidence_score": 0.5,
                    "reasoning": "Fallback neutral sentiment due to LLM timeout/provider errors.",
                    "sentiment_trend_short_term": "NEUTRAL",
                    "_fallback": "neutral_default",
                    "_fallback_reason": reason,
                    "_llm_error": (llm_report or {}).get("error"),
                }

            social_compact, social_counts = _compact_social_data(state.get("social_data", {}) or {})
            social_data_json = json.dumps(social_compact, ensure_ascii=False, separators=(",", ":"))
            fg_value = str(state.get("fear_greed_value", "N/A"))

            twitter_posts = state.get("social_data", {}).get("twitter", {}) or {}
            reddit_posts = state.get("social_data", {}).get("reddit", {}) or {}
            twitter_count = (
                sum(len(v) for v in twitter_posts.values())
                if isinstance(twitter_posts, dict)
                else 0
            )
            reddit_count = (
                sum(len(v) for v in reddit_posts.values()) if isinstance(reddit_posts, dict) else 0
            )

            messages = format_prompt(
                "sentiment_analyst",
                symbol=symbol,
                news_summary=news_summary,
                social_data=social_data_json,
                fear_greed_value=fg_value,
                additional_context=(
                    f"News were obtained from sources like CoinDesk and Cointelegraph. "
                    f"Total available articles: {len(news_list)}. "
                    f"Social: Twitter={twitter_count} ({social_counts['twitter_posts']} sampled), "
                    f"Reddit={reddit_count} ({social_counts['reddit_posts']} sampled), Fear&Greed={fg_value}"
                ),
            )

            if not messages:
                raise ValueError("Could not format sentiment prompt")

            llm_messages = [
                {"role": "system", "content": messages[0]["content"]},
                {"role": "user", "content": messages[1]["content"]},
            ]

            # REDUCED retries for faster fallback
            default_max_retries = 1 if short_tf else 1
            max_retries = max(
                0,
                _get_env_int(
                    "FENIX_SENTIMENT_MAX_RETRIES",
                    default=default_max_retries,
                ),
            )

            # DESACTIVADO: Timeout extendido para máxima calidad
            timeout_sec = _get_env_float(
                "FENIX_SENTIMENT_AGENT_TIMEOUT_SHORT_SEC"
                if short_tf
                else "FENIX_SENTIMENT_AGENT_TIMEOUT_SEC",
                "FENIX_SENTIMENT_AGENT_TIMEOUT_SEC",
                default=20.0,
            )

            try:
                coro = invoke_with_retry_and_validation(
                    llm=llm,
                    messages=llm_messages,
                    agent_type="sentiment_analyst",
                    max_retries=max_retries,
                    base_delay=0.3,
                    required_keys=["overall_sentiment"],
                )
                report, attempts, errors = await asyncio.wait_for(coro, timeout=timeout_sec)
            except asyncio.TimeoutError:
                logger.warning(
                    "sentiment_analyst timed out after %.1fs (tf=%s); using fallback sentiment",
                    float(timeout_sec or 0.0),
                    timeframe,
                )
                report = _fallback_sentiment("timeout")
                attempts = 0
                errors = ["Timeout waiting for sentiment_analyst LLM response"]

            if (
                errors
                or report.get("_validation_failed")
                or report.get("error")
                or report.get("parse_error")
            ):
                fallback = _fallback_sentiment("llm_error", llm_report=report)
                fallback["_llm_attempts"] = attempts
                fallback["_llm_errors"] = errors or report.get("_validation_errors") or []
                report = fallback
                attempts = 0
                errors = []

            raw_response = report.get("raw_response", json.dumps(report))
            save_legacy_agent_log("sentiment", llm_messages, raw_response, report)

            elapsed = (datetime.now() - start_time).total_seconds()
            report["_attempts"] = attempts
            if errors:
                report["_validation_errors"] = errors

            # Persist in ReasoningBank
            prompt_snippet = messages[1]["content"][:500] if messages and len(messages) > 1 else ""
            digest = store_to_reasoning_bank(
                reasoning_bank=reasoning_bank,
                agent_name="sentiment_agent",
                prompt=prompt_snippet,
                result=report,
                raw_response=raw_response,
                llm=llm,
                elapsed_ms=elapsed * 1000,
            )
            if digest:
                report["_reasoning_digest"] = digest

            return {
                "sentiment_report": report,
                "execution_times": {
                    **state.get("execution_times", {}),
                    "sentiment": elapsed,
                },
            }

        except Exception as e:
            logger.error(f"Error in sentiment agent: {e}")
            return {
                "sentiment_report": {"overall_sentiment": "NEUTRAL", "error": str(e)},
                "errors": state.get("errors", []) + [f"Sentiment: {e}"],
            }

    async def traced_sentiment_node(state: FenixAgentState) -> dict:
        with get_tracer().start_as_current_span("sentiment_agent"):
            return await sentiment_node(state)

    return traced_sentiment_node
