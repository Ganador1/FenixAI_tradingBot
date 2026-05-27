from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any

from .base import GenerationParams, InferenceProvider, ProviderError

logger = logging.getLogger(__name__)


class GroqProvider(InferenceProvider):
    """Provider for Groq API."""

    def name(self) -> str:
        return "groq"

    def generate_text(
        self, model_id: str, prompt: str, params: GenerationParams | None = None
    ) -> dict[str, Any]:
        messages = []
        try:
            from groq import Groq
        except ImportError as exc:
            raise ProviderError("groq package not installed. Run 'pip install groq'") from exc

        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ProviderError("GROQ_API_KEY environment variable not set")

        client = Groq(api_key=api_key)
        params = params or GenerationParams()

        try:
            if params.extra and params.extra.get("system"):
                messages.append({"role": "system", "content": params.extra["system"]})

            messages.append({"role": "user", "content": prompt})

            # Use with_raw_response to access headers if available
            headers = {}
            if hasattr(client.chat.completions, "with_raw_response"):
                response = client.chat.completions.with_raw_response.create(
                    model=model_id,
                    messages=messages,
                    temperature=params.temperature,
                    max_tokens=params.max_tokens,
                    top_p=params.top_p,
                    stream=False,
                )
                completion = response.parse()
                headers = dict(response.headers)
            else:
                completion = client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    temperature=params.temperature,
                    max_tokens=params.max_tokens,
                    top_p=params.top_p,
                    stream=False,
                )

            text = completion.choices[0].message.content
            usage = getattr(completion, "usage", None)
            return {
                "text": text,
                "usage": {
                    "prompt_tokens": getattr(usage, "prompt_tokens", None),
                    "completion_tokens": getattr(usage, "completion_tokens", None),
                    "total_tokens": getattr(usage, "total_tokens", None),
                },
                "finish_reason": completion.choices[0].finish_reason,
                "headers": headers,
            }

        except Exception as e:
            # Try to detect rate limit scenarios and retry with backoff
            is_rate_limit = False
            retry_after = None
            last_exc = e
            try:
                # Groq SDK errors may contain a response object with headers
                resp = getattr(e, "response", None)
                if resp is not None:
                    status = getattr(resp, "status", getattr(resp, "status_code", None))
                    if status == 429:
                        is_rate_limit = True
                    headers = getattr(resp, "headers", None) or {}
                    # Try to extract various header names used by different providers
                    retry_after = (
                        headers.get("retry-after")
                        or headers.get("Retry-After")
                        or headers.get("Retry-After-Seconds")
                    )
                    remaining = (
                        headers.get("x-ratelimit-remaining-requests")
                        or headers.get("x-ratelimit-remaining")
                        or headers.get("ratelimit-remaining")
                    )
                    if remaining is not None:
                        try:
                            rem_int = int(remaining)
                            logger.debug(f"Groq reported remaining requests: {rem_int}")
                        except Exception:
                            logger.debug(
                                f"Groq rate-limit header present but could not parse: {remaining}"
                            )
            except Exception:
                pass

            if is_rate_limit:
                # Attempt up to 3 retries with exponential backoff if we can
                backoff_seconds = 1
                retries = 3
                if retry_after:
                    try:
                        # Retry-After may be an HTTP-date or number of seconds
                        try:
                            backoff_seconds = float(retry_after)
                        except Exception:
                            from email.utils import parsedate_to_datetime

                            dt = parsedate_to_datetime(retry_after)
                            backoff_seconds = max(0, (dt - datetime.utcnow()).total_seconds())
                    except Exception:
                        # Keep default backoff if parse fails
                        backoff_seconds = 1
                # Clamp to a reasonable cap (avoid waiting hours by mis-parsed values)
                backoff_seconds = max(1.0, min(backoff_seconds, 60.0))
                for i in range(retries):
                    logger.warning(
                        "Groq rate limited: retrying in %s seconds (attempt %d)",
                        backoff_seconds,
                        i + 1,
                    )
                    time.sleep(backoff_seconds)
                    backoff_seconds *= 2
                    try:
                        completion = client.chat.completions.create(
                            model=model_id,
                            messages=messages,
                            temperature=params.temperature,
                            max_tokens=params.max_tokens,
                            top_p=params.top_p,
                            stream=False,
                        )
                        text = completion.choices[0].message.content
                        usage = getattr(completion, "usage", None)
                        return {
                            "text": text,
                            "usage": {
                                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                                "completion_tokens": getattr(usage, "completion_tokens", None),
                                "total_tokens": getattr(usage, "total_tokens", None),
                            },
                            "finish_reason": completion.choices[0].finish_reason,
                        }
                    except Exception as inner_e:
                        last_exc = inner_e
                        continue
                logger.error(
                    "Groq generation failed after retries due to rate limiting: %s", last_exc
                )
                raise ProviderError(
                    "Groq rate limit exceeded", is_rate_limit=True, cause=last_exc
                ) from last_exc

            logger.error("Groq generation failed: %s", e)
            raise ProviderError(f"Groq error: {e}", cause=e) from e
