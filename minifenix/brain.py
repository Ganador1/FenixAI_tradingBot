import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp

from .models import TradingRegime

logger = logging.getLogger("MiniFenix.Brain")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.getenv("MINIFENIX_LLM_MODEL", "ministral-3:14b-cloud")
OLLAMA_FALLBACK = os.getenv("MINIFENIX_LLM_FALLBACK", "nemotron-3-nano:30b-cloud")

SYSTEM_PROMPT = """\
You are a quantitative trader specialised in BTC/USDT Futures.
Decide the market regime for the next 30 seconds.

REPLY ONLY with valid JSON (no markdown, no extra text):
{"bias":"LONG"|"SHORT"|"NEUTRAL",
 "confidence":0.50-0.99,
 "max_spread_bps":0.5-10.0,
 "min_ofi_required":0.1-0.8,
 "reasoning":"one-line reason, max 80 chars"}

WHERE:
- bias: the direction you predict for price
- confidence: how certain you are (0.5=no idea, 0.99=very sure)
- max_spread_bps: maximum spread acceptable to trade (you decide)
- min_ofi_required: minimum OBI required to enter (you decide)

Example:
{"bias":"SHORT","confidence":0.75,"max_spread_bps":2.0,"min_ofi_required":0.3,\
"reasoning":"sustained negative OBI, price falling, bearish momentum"}
"""


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _extract_json_obj(text: str) -> Any | None:
    if not text:
        return None

    cleaned = _strip_code_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    while start != -1:
        depth = 0
        for idx in range(start, len(cleaned)):
            ch = cleaned[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start:idx + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
        start = cleaned.find("{", start + 1)
    return None


def _find_payload_with_bias(obj: Any) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        if "bias" in obj:
            return obj
        for value in obj.values():
            found = _find_payload_with_bias(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_payload_with_bias(value)
            if found:
                return found
    return None


def _to_float(value: Any, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        raw = value.strip().lower().replace(",", ".")
        if not raw:
            return default
        if raw.endswith("%"):
            try:
                return float(raw[:-1]) / 100.0
            except ValueError:
                return default
        try:
            return float(raw)
        except ValueError:
            mapping = {
                "low": 0.58,
                "baja": 0.58,
                "medium": 0.68,
                "media": 0.68,
                "moderate": 0.68,
                "high": 0.82,
                "alta": 0.82,
                "very high": 0.90,
                "muy alta": 0.90,
            }
            # Spanish synonyms kept for backwards compatibility with old prompts.
            return mapping.get(raw, default)
    return default


def _normalize_bias(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"long", "buy", "bullish", "alcista"}:
        return "LONG"
    if raw in {"short", "sell", "bearish", "bajista"}:
        return "SHORT"
    return "NEUTRAL"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class MarketContext:
    """Market context the Trigger sends to the Brain for analysis."""
    price: float = 0.0
    price_change_pct_1m: float = 0.0   # % change over the last minute (~200 ticks)
    obi: float = 0.0                   # Current Order Book Imbalance
    spread_bps: float = 0.0
    realized_vol: float = 0.0          # Realized volatility (20 ticks)
    ml_signal: str = "HOLD"            # ML model signal
    ml_confidence: float = 0.0
    ml_accuracy: float = 0.0
    tick_count: int = 0


class SlowBrain:
    """
    The 'Slow Loop' (Brain) connected to an Ollama cloud model.
    Analyses market context every N seconds and publishes a regime
    without blocking the microsecond Fast Loop.
    """

    def __init__(self, model: str = OLLAMA_MODEL):
        self.model = model
        self.fallback_model = OLLAMA_FALLBACK
        self.market_context: MarketContext = MarketContext()

        # Safe initial state (NEUTRAL until the LLM responds).
        self.current_regime = TradingRegime(
            bias="NEUTRAL",
            confidence=0.0,
            min_ofi_required=0.5,
            max_spread_bps=5.0,
            z_score_threshold=2.0,
            macro_context="Initialising...",
            timestamp=time.time(),
        )
        self._session: Optional[aiohttp.ClientSession] = None

    def update_market_context(self, ctx: MarketContext) -> None:
        """Trigger calls this each cycle to refresh the context."""
        self.market_context = ctx

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    @staticmethod
    def _parse_regime_payload(data: dict, model_name: str, fallback_to_thinking: bool = True) -> TradingRegime:
        message = data.get("message", {}) if isinstance(data, dict) else {}
        content = str(message.get("content", "") or "")
        thinking = str(message.get("thinking", "") or "")

        text_candidates = [content]
        if fallback_to_thinking:
            text_candidates.append(thinking)

        parsed_obj: Any | None = None
        source_hint = "content"
        for idx, text in enumerate(text_candidates):
            parsed_obj = _extract_json_obj(text)
            if parsed_obj is not None:
                source_hint = "thinking" if idx == 1 else "content"
                break

        if parsed_obj is None:
            preview = (content or thinking or "")[:120]
            raise ValueError(f"No JSON in response: {preview}")

        payload = _find_payload_with_bias(parsed_obj)
        if payload is None:
            if isinstance(parsed_obj, dict):
                payload = parsed_obj
            else:
                raise ValueError(f"JSON had no usable payload ({source_hint})")

        bias = _normalize_bias(payload.get("bias", "NEUTRAL"))
        confidence = _clamp(_to_float(payload.get("confidence", 0.6), 0.6), 0.50, 0.99)
        min_ofi = _clamp(_to_float(payload.get("min_ofi_required", 0.3), 0.3), 0.05, 0.90)
        max_spread = _clamp(_to_float(payload.get("max_spread_bps", 5.0), 5.0), 0.5, 12.0)

        reasoning = str(payload.get("reasoning", "") or "").strip()
        if not reasoning and source_hint == "thinking" and thinking:
            reasoning = thinking[:120]
        if not reasoning:
            reasoning = f"Parsed from {source_hint} ({model_name})"

        return TradingRegime(
            bias=bias,
            confidence=confidence,
            min_ofi_required=min_ofi,
            max_spread_bps=max_spread,
            z_score_threshold=2.0,
            macro_context=reasoning[:120],
            timestamp=time.time(),
        )

    async def analyze_market_with_llm(self) -> None:
        """Calls the configured Ollama cloud model with live market data."""
        ctx = self.market_context
        logger.info(
            "[BRAIN] analysing with %s | BTC=%.2f | d1m=%+.3f%% | OBI=%.3f | ML=%s(%.0f%%)",
            self.model, ctx.price, ctx.price_change_pct_1m, ctx.obi,
            ctx.ml_signal, ctx.ml_confidence * 100,
        )

        user_msg = (
            f"Live BTC/USDT data:\n"
            f"- Current price: ${ctx.price:,.2f}\n"
            f"- Last-minute change: {ctx.price_change_pct_1m:+.4f}%\n"
            f"- Order Book Imbalance (OBI): {ctx.obi:.4f} "
            f"({'buy-side pressure' if ctx.obi > 0 else 'sell-side pressure'})\n"
            f"- Current spread: {ctx.spread_bps:.2f} bps\n"
            f"- Realized volatility (20 ticks): {ctx.realized_vol:.6f}\n"
            f"- ML signal (LightGBM): {ctx.ml_signal} with {ctx.ml_confidence:.1%} confidence\n"
            f"- ML model running accuracy: {ctx.ml_accuracy:.1%}\n"
            f"- Ticks processed: {ctx.tick_count:,}\n\n"
            f"What is the market regime for the next 30 seconds?"
        )

        try:
            session = await self._get_session()
            t0 = time.time()
            async with session.post(
                OLLAMA_URL,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.1, "num_predict": 200},
                },
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                data = await resp.json()
                elapsed = time.time() - t0

            new_regime = self._parse_regime_payload(data, model_name=self.model)
            self.current_regime = new_regime

            logger.info(
                "[BRAIN] %s -> %s (conf=%s) in %.1fs",
                self.model, new_regime.bias, new_regime.confidence, elapsed,
            )
            logger.info("[BRAIN] reasoning: %s", new_regime.macro_context)
            logger.info(
                "[BRAIN] params -> MaxSpread=%sbps | MinOFI=%s",
                new_regime.max_spread_bps, new_regime.min_ofi_required,
            )

        except asyncio.TimeoutError:
            # Retry with the fallback model before giving up.
            logger.warning(
                "[BRAIN] timeout (%s) - trying %s...",
                self.model, self.fallback_model,
            )
            try:
                session = await self._get_session()
                async with session.post(
                    OLLAMA_URL,
                    json={
                        "model": self.fallback_model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_msg},
                        ],
                        "stream": False,
                        "format": "json",
                        "options": {"temperature": 0.1, "num_predict": 120},
                    },
                    timeout=aiohttp.ClientTimeout(total=4),
                ) as resp:
                    data = await resp.json()
                    parsed_regime = self._parse_regime_payload(data, model_name=self.fallback_model)
                    parsed_regime.macro_context = f"[{self.fallback_model}] {parsed_regime.macro_context}"[:120]
                    self.current_regime = parsed_regime
                    logger.info(
                        "[BRAIN] %s (fallback) -> %s (conf=%s)",
                        self.fallback_model, self.current_regime.bias,
                        self.current_regime.confidence,
                    )
            except (aiohttp.ClientError, json.JSONDecodeError, ValueError) as fe:
                logger.warning("[BRAIN] fallback also failed: %s - keeping previous regime", fe)
        except (aiohttp.ClientError, json.JSONDecodeError, ValueError) as e:
            logger.error("[BRAIN] LLM error: %s - keeping previous regime", e)

    async def run_loop(self, interval_seconds: int = 15) -> None:
        """Brain loop. Does NOT block the Fast Loop."""
        while True:
            try:
                await self.analyze_market_with_llm()
            except Exception as e:
                logger.error("Unexpected error in Brain: %s", e)
            logger.info("[BRAIN] sleeping %ds...", interval_seconds)
            await asyncio.sleep(interval_seconds)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
