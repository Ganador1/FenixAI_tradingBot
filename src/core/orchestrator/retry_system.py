# src/core/orchestrator/retry_system.py
"""
Retry system with exponential backoff for Fenix Trading Bot agents.

Provides retry logic with statistics tracking for LLM invocations.
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from src.core.orchestrator.json_parser import extract_json_from_content
from src.core.orchestrator.validation import (
    build_validation_feedback,
    validate_agent_response,
)

logger = logging.getLogger(__name__)


_llm_concurrency_semaphore: asyncio.Semaphore | None = None
_llm_concurrency_limit: int | None = None
_llm_concurrency_warned_invalid = False


def _flatten_text_like(value: Any) -> str:
    """Best-effort flattening of text payloads from heterogeneous LLM responses."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            text = _flatten_text_like(item)
            if text:
                chunks.append(text)
        return "\n".join(chunks).strip()
    if isinstance(value, dict):
        # Most common text-bearing keys across providers
        for key in (
            "text",
            "content",
            "response",
            "output_text",
            "generated_text",
            "thinking",
            "reasoning",
            "message",
        ):
            if key in value:
                text = _flatten_text_like(value.get(key))
                if text:
                    return text
        return ""
    return ""


def _extract_response_content(response: Any) -> str:
    """Extract text from LangChain/OpenAI/Ollama response objects robustly."""
    if response is None:
        return ""

    # 1) Direct content / text attributes
    for attr in ("content", "text"):
        if hasattr(response, attr):
            try:
                raw = getattr(response, attr)
                text = _flatten_text_like(raw)
                if text:
                    return text.strip()
            except Exception:
                pass

    # 2) Message-like nested structures
    for attr in ("message", "additional_kwargs", "kwargs", "response_metadata"):
        if hasattr(response, attr):
            try:
                raw = getattr(response, attr)
                text = _flatten_text_like(raw)
                if text:
                    return text.strip()
            except Exception:
                pass

    # 3) Dict-like responses
    if isinstance(response, dict):
        text = _flatten_text_like(response)
        if text:
            return text.strip()

    # 4) Last resort: string representation
    try:
        return str(response).strip()
    except Exception:
        return ""


def _get_llm_concurrency_semaphore() -> asyncio.Semaphore | None:
    """Optional global per-process limiter for concurrent LLM requests."""
    global _llm_concurrency_semaphore
    global _llm_concurrency_limit
    global _llm_concurrency_warned_invalid

    raw_limit = (os.getenv("FENIX_LLM_MAX_CONCURRENT_REQUESTS", "") or "").strip()
    if not raw_limit:
        return None

    try:
        limit = int(raw_limit)
    except Exception:
        if not _llm_concurrency_warned_invalid:
            logger.warning(
                "Invalid FENIX_LLM_MAX_CONCURRENT_REQUESTS=%r; disabling concurrency limiter.",
                raw_limit,
            )
            _llm_concurrency_warned_invalid = True
        return None

    if limit <= 0:
        return None

    if _llm_concurrency_semaphore is None or _llm_concurrency_limit != limit:
        _llm_concurrency_semaphore = asyncio.Semaphore(limit)
        _llm_concurrency_limit = limit
        logger.info(
            "LLM concurrency limiter enabled: max_concurrent_requests=%d",
            limit,
        )

    return _llm_concurrency_semaphore


@dataclass
class RetryStats:
    """Retry statistics per agent."""

    agent_type: str
    total_attempts: int = 0
    successful_first_try: int = 0
    retries_needed: int = 0
    failures: int = 0
    validation_errors: dict[str, int] = field(default_factory=dict)

    def record_attempt(self, success: bool, retry_count: int, validation_errors: list[str] = None):
        """Records an attempt."""
        self.total_attempts += 1
        if success:
            if retry_count == 0:
                self.successful_first_try += 1
            else:
                self.retries_needed += 1
        else:
            self.failures += 1

        # Count validation errors
        if validation_errors:
            for error in validation_errors:
                error_type = error.split(":")[0] if ":" in error else "unknown"
                self.validation_errors[error_type] = self.validation_errors.get(error_type, 0) + 1

    @property
    def success_rate(self) -> float:
        """Success rate (0.0 - 1.0)."""
        if self.total_attempts == 0:
            return 0.0
        return (self.successful_first_try + self.retries_needed) / self.total_attempts

    @property
    def retry_rate(self) -> float:
        """Retry rate needed (0.0 - 1.0)."""
        if self.total_attempts == 0:
            return 0.0
        return self.retries_needed / self.total_attempts


# Global retry statistics
_retry_stats: dict[str, RetryStats] = {}


def get_retry_stats(agent_type: str | None = None) -> RetryStats | dict[str, RetryStats]:
    """Gets retry statistics."""
    if agent_type:
        return _retry_stats.get(agent_type, RetryStats(agent_type))
    return _retry_stats.copy()


def reset_retry_stats():
    """Resets retry statistics."""
    global _retry_stats
    _retry_stats = {}


async def invoke_with_retry_and_validation(
    llm: Any,
    messages: list,
    agent_type: str,
    max_retries: int = 3,
    base_delay: float = 0.3,
    required_keys: list[str] | None = None,
) -> tuple[dict[str, Any], int, list[str]]:
    """
    Invokes the LLM with retry and validation system.

    Args:
        llm: LLM Instance
        messages: List of messages for the LLM
        agent_type: Agent type (for validation)
        max_retries: Maximum number of retries
        base_delay: Base delay for exponential backoff
        required_keys: Additional required fields

    Returns:
        Tuple of (parsed_response, attempts_made, errors_list)
    """
    if agent_type not in _retry_stats:
        _retry_stats[agent_type] = RetryStats(agent_type)

    stats = _retry_stats[agent_type]
    last_errors: list[str] = []
    perf_log = os.getenv("FENIX_LLM_TIMING_LOG", "0") == "1"
    try:
        retry_429_base = float(os.getenv("FENIX_RETRY_429_WAIT_SEC", "30.0"))
    except Exception:
        retry_429_base = 30.0
    try:
        retry_429_jitter = float(os.getenv("FENIX_RETRY_429_WAIT_JITTER_SEC", "10.0"))
    except Exception:
        retry_429_jitter = 10.0
    overall_t0 = time.perf_counter()
    llm_attempt_ms: list[float] = []
    json_extract_ms_total = 0.0
    validation_ms_total = 0.0

    def _attach_perf(payload: dict[str, Any]) -> dict[str, Any]:
        """Attach timing metadata without affecting agent validation."""
        try:
            perf = payload.setdefault("_perf", {})
            perf["agent_type"] = agent_type
            perf["llm_attempt_ms"] = [float(x) for x in llm_attempt_ms]
            perf["llm_total_ms"] = float(sum(llm_attempt_ms))
            perf["json_extract_ms"] = float(json_extract_ms_total)
            perf["validation_ms"] = float(validation_ms_total)
            perf["total_ms"] = float((time.perf_counter() - overall_t0) * 1000.0)
        except Exception:
            # Never break agent output on perf instrumentation.
            pass
        return payload

    for attempt in range(max_retries + 1):
        try:
            # Add exponential delay after first attempt
            if attempt > 0:
                delay = base_delay * (2 ** (attempt - 1))  # 1s, 2s, 4s
                logger.info(
                    f"⏳ Retry {attempt}/{max_retries} for {agent_type}: waiting {delay}s..."
                )
                await asyncio.sleep(delay)

            # Invoke LLM
            llm_t0 = time.perf_counter()
            semaphore = _get_llm_concurrency_semaphore()
            if semaphore is not None:
                async with semaphore:
                    response = await llm.ainvoke(messages)
            else:
                response = await llm.ainvoke(messages)
            llm_ms = (time.perf_counter() - llm_t0) * 1000.0
            llm_attempt_ms.append(llm_ms)
            content = _extract_response_content(response)
            response_meta = getattr(response, "response_metadata", None) or {}

            # Extract JSON
            extract_t0 = time.perf_counter()
            parsed = extract_json_from_content(content, required_keys=required_keys)
            json_extract_ms_total += (time.perf_counter() - extract_t0) * 1000.0

            if parsed is None:
                if not content and isinstance(response_meta, dict):
                    done_reason = response_meta.get("done_reason")
                    eval_count = response_meta.get("eval_count")
                    last_errors = [
                        "Failed to extract valid JSON from response "
                        f"(empty_content done_reason={done_reason} eval_count={eval_count})"
                    ]
                else:
                    last_errors = ["Failed to extract valid JSON from response"]
                if attempt < max_retries:
                    # Add parsing error feedback
                    feedback = f"""⚠️ JSON PARSE ERROR (Attempt {attempt + 1}/{max_retries + 1})

Your response could not be parsed as valid JSON.

RAW RESPONSE PREVIEW:
{content[:500]}...

CRITICAL OUTPUT REQUIREMENTS:
- Your ENTIRE response must be a single JSON object - NOTHING ELSE
- Do NOT use markdown code blocks (no ```json or ```)
- Do NOT include any text before or after the JSON
- Do NOT add explanations or comments outside the JSON
- The JSON must start with {{ and end with }}
- Check for syntax errors (missing quotes, commas, brackets)

EXAMPLE OF CORRECT OUTPUT:
{{"signal": "BUY", "confidence": 0.8, "reasoning": "Analysis here"}}

INCORRECT OUTPUTS (DO NOT DO THIS):
- ```json {{"signal": "BUY"}} ```
- Here is my analysis: {{"signal": "BUY"}}
- {{"signal": "BUY"}} (with extra text after)

Retry with ONLY the corrected JSON object."""
                    messages = messages + [{"role": "user", "content": feedback}]
                    continue
                else:
                    stats.record_attempt(False, attempt, last_errors)
                    out = _attach_perf(
                        {
                            "error": "Failed to parse JSON after all retries",
                            "raw_response": content[:1000],
                            "parse_error": True,
                        }
                    )
                    if perf_log:
                        try:
                            perf = out.get("_perf", {}) or {}
                            logger.info(
                                "⏱️ %s LLM timing (parse_error): attempts=%d llm_total=%.0fms json=%.0fms total=%.0fms",
                                agent_type,
                                attempt + 1,
                                float(perf.get("llm_total_ms", 0.0) or 0.0),
                                float(perf.get("json_extract_ms", 0.0) or 0.0),
                                float(perf.get("total_ms", 0.0) or 0.0),
                            )
                        except Exception:
                            pass
                    return out, attempt + 1, last_errors

            # Validar respuesta
            validation_t0 = time.perf_counter()
            validation_errors = validate_agent_response(agent_type, parsed)
            validation_ms_total += (time.perf_counter() - validation_t0) * 1000.0

            if not validation_errors:
                # Success!
                stats.record_attempt(True, attempt)
                logger.info(f"✅ {agent_type}: Valid response on attempt {attempt + 1}")
                parsed = _attach_perf(parsed)
                if perf_log:
                    try:
                        perf = parsed.get("_perf", {}) or {}
                        logger.info(
                            "⏱️ %s LLM timing: attempts=%d llm_total=%.0fms json=%.0fms validate=%.0fms total=%.0fms",
                            agent_type,
                            attempt + 1,
                            float(perf.get("llm_total_ms", 0.0) or 0.0),
                            float(perf.get("json_extract_ms", 0.0) or 0.0),
                            float(perf.get("validation_ms", 0.0) or 0.0),
                            float(perf.get("total_ms", 0.0) or 0.0),
                        )
                    except Exception:
                        pass
                return parsed, attempt + 1, []

            # Validation failed
            last_errors = validation_errors
            logger.warning(
                f"⚠️ {agent_type}: Validation failed on attempt {attempt + 1}: {validation_errors}"
            )

            if attempt < max_retries:
                # Build feedback and retry
                feedback = build_validation_feedback(agent_type, validation_errors, attempt + 1)
                messages = messages + [{"role": "user", "content": feedback}]
            else:
                # Retries exhausted
                stats.record_attempt(False, attempt, validation_errors)
                logger.error(f"❌ {agent_type}: All {max_retries + 1} attempts failed")

                # Return best possible response with error metadata
                parsed["_validation_errors"] = validation_errors
                parsed["_validation_failed"] = True
                parsed["_attempts"] = attempt + 1
                parsed = _attach_perf(parsed)
                if perf_log:
                    try:
                        perf = parsed.get("_perf", {}) or {}
                        logger.info(
                            "⏱️ %s LLM timing (validation_failed): attempts=%d llm_total=%.0fms total=%.0fms",
                            agent_type,
                            attempt + 1,
                            float(perf.get("llm_total_ms", 0.0) or 0.0),
                            float(perf.get("total_ms", 0.0) or 0.0),
                        )
                    except Exception:
                        pass
                return parsed, attempt + 1, validation_errors

        except Exception as e:
            last_errors = [f"Exception during invocation: {str(e)}"]
            logger.error(f"❌ {agent_type}: Exception on attempt {attempt + 1}: {e}")

            # 400 bad request is usually non-retriable (invalid payload/context).
            err_str = str(e).lower()
            if "status code: 400" in err_str or "bad request" in err_str:
                logger.error(f"❌ {agent_type}: non-retriable bad request, failing fast")
                stats.record_attempt(False, attempt, last_errors)
                out = _attach_perf(
                    {
                        "error": str(e),
                        "exception": True,
                        "attempts": attempt + 1,
                        "non_retriable": True,
                    }
                )
                return out, attempt + 1, last_errors

            # 429 / too-many-concurrent: wait longer before retrying
            err_str = str(e).lower()
            if (
                "429" in err_str or "too many concurrent" in err_str or "rate limit" in err_str
            ) and attempt < max_retries:
                import random

                wait_429 = max(0.0, retry_429_base) + random.uniform(0, max(0.0, retry_429_jitter))
                logger.warning(
                    f"⏳ {agent_type}: 429/concurrent limit — waiting {wait_429:.1f}s before retry {attempt + 1}/{max_retries}..."
                )
                await asyncio.sleep(wait_429)
                continue

            # 503 Service Unavailable: server overloaded, retry with longer wait
            if (
                "503" in err_str
                or "service temporarily unavailable" in err_str
                or "service unavailable" in err_str
            ) and attempt < max_retries:
                import random

                wait_503_base = float(os.getenv("FENIX_RETRY_503_WAIT_SEC", "15.0"))
                wait_503_jitter = float(os.getenv("FENIX_RETRY_503_WAIT_JITTER_SEC", "10.0"))
                wait_503 = max(0.0, wait_503_base) + random.uniform(0, max(0.0, wait_503_jitter))
                logger.warning(
                    f"⏳ {agent_type}: 503 service unavailable — waiting {wait_503:.1f}s before retry {attempt + 1}/{max_retries}..."
                )
                await asyncio.sleep(wait_503)
                continue

            # 524 Cloudflare timeout from Ollama Cloud: retry with a bounded backoff.
            if (
                "524" in err_str
                or "origin_response_timeout" in err_str
                or "proxy read timeout" in err_str
            ) and attempt < max_retries:
                import random

                wait_524_base = float(os.getenv("FENIX_RETRY_524_WAIT_SEC", "20.0"))
                wait_524_jitter = float(os.getenv("FENIX_RETRY_524_WAIT_JITTER_SEC", "10.0"))
                wait_524 = max(0.0, wait_524_base) + random.uniform(0, max(0.0, wait_524_jitter))
                logger.warning(
                    f"⏳ {agent_type}: 524 cloud timeout — waiting {wait_524:.1f}s before retry {attempt + 1}/{max_retries}..."
                )
                await asyncio.sleep(wait_524)
                continue

            if attempt >= max_retries:
                stats.record_attempt(False, attempt, last_errors)
                out = _attach_perf({"error": str(e), "exception": True, "attempts": attempt + 1})
                if perf_log:
                    try:
                        perf = out.get("_perf", {}) or {}
                        logger.info(
                            "⏱️ %s LLM timing (exception): attempts=%d llm_total=%.0fms total=%.0fms",
                            agent_type,
                            attempt + 1,
                            float(perf.get("llm_total_ms", 0.0) or 0.0),
                            float(perf.get("total_ms", 0.0) or 0.0),
                        )
                    except Exception:
                        pass
                return out, attempt + 1, last_errors

    # Should never reach here, but for safety
    stats.record_attempt(False, max_retries, last_errors)
    return {"error": "Unexpected retry loop exit"}, max_retries + 1, last_errors


def log_retry_stats():
    """Log retry statistics."""
    logger.info("=" * 60)
    logger.info("RETRY SYSTEM STATISTICS")
    logger.info("=" * 60)

    total_attempts = sum(s.total_attempts for s in _retry_stats.values())
    if total_attempts == 0:
        logger.info("No attempts recorded yet.")
        return

    for agent_type, stats in _retry_stats.items():
        if stats.total_attempts == 0:
            continue

        logger.info(f"\n{agent_type.upper()}:")
        logger.info(f"  Total attempts: {stats.total_attempts}")
        logger.info(f"  Success rate: {stats.success_rate:.1%}")
        logger.info(f"  First-try success: {stats.successful_first_try}")
        logger.info(f"  Retries needed: {stats.retries_needed}")
        logger.info(f"  Failures: {stats.failures}")

        if stats.validation_errors:
            logger.info("  Most common validation errors:")
            sorted_errors = sorted(
                stats.validation_errors.items(), key=lambda x: x[1], reverse=True
            )
            for error_type, count in sorted_errors[:3]:
                logger.info(f"    - {error_type}: {count} occurrences")

    logger.info("=" * 60)
