#!/usr/bin/env python3
"""
Hybrid live paper trading runner with multi-timeframe engines.

Runs multiple TradingEngine instances (analysis only) and applies a
hybrid decision policy to simulate trades in paper mode.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from config.llm_provider_config import AgentProviderConfig, LLMProvidersConfig

logger = logging.getLogger("HybridLivePaper")


CONF_MAP = {
    "HIGH": 0.8,
    "MEDIUM": 0.6,
    "LOW": 0.4,
}


def _conf_to_float(conf: Any) -> float:
    if isinstance(conf, (int, float)):
        return float(conf)
    if isinstance(conf, str):
        return CONF_MAP.get(conf.upper(), 0.5)
    return 0.5


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed > 0:
        return parsed
    return None


def _extract_event_price(payload: dict[str, Any], engine_price: Any) -> float:
    full_data = payload.get("full_data") if isinstance(payload, dict) else None
    if not isinstance(full_data, dict):
        full_data = {}
    risk_data = payload.get("risk_assessment")
    if not isinstance(risk_data, dict):
        risk_data = full_data.get("risk_assessment")
    if not isinstance(risk_data, dict):
        risk_data = {}
    order_details = risk_data.get("order_details") if isinstance(risk_data, dict) else None
    if not isinstance(order_details, dict):
        order_details = {}
    dynamic_levels = risk_data.get("dynamic_risk_levels") if isinstance(risk_data, dict) else None
    if not isinstance(dynamic_levels, dict):
        dynamic_levels = {}

    for candidate in (
        engine_price,
        payload.get("price"),
        payload.get("current_price"),
        risk_data.get("entry_price"),
        order_details.get("entry_price"),
        dynamic_levels.get("entry_price"),
    ):
        parsed = _positive_float(candidate)
        if parsed is not None:
            return parsed
    return 0.0


@dataclass
class Signal:
    decision: str
    confidence: float
    price: float
    timestamp: str
    judge_verdict: str | None = None
    indicators: dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineSpec:
    timeframe: str
    tf_label: str
    llm_config: LLMProvidersConfig | None
    model_tag: str | None
    use_for_hybrid: bool = True


DEFAULT_OLLAMA_CLOUD_URL = os.getenv("OLLAMA_CLOUD_URL", "http://localhost:11434")
DEFAULT_OLLAMA_LOCAL_URL = os.getenv("OLLAMA_LOCAL_URL", "http://localhost:11434")
DEFAULT_FANOUT_VISION_MODEL = os.getenv("FENIX_FANOUT_VISION_MODEL", "qwen3-vl:235b-cloud")
TEAM_AGENT_KEYS = {"technical", "qabba", "decision", "sentiment", "visual", "risk_manager"}
TOKEN_ENV_KEYS = {
    "technical": "FENIX_TECHNICAL_MAX_TOKENS",
    "qabba": "FENIX_QABBA_MAX_TOKENS",
    "decision": "FENIX_DECISION_MAX_TOKENS",
    "sentiment": "FENIX_SENTIMENT_MAX_TOKENS",
    "visual": "FENIX_VISUAL_MAX_TOKENS",
    "risk_manager": "FENIX_RISK_MANAGER_MAX_TOKENS",
}


def _token_multiplier() -> float:
    raw = os.getenv("FENIX_MAX_TOKENS_MULTIPLIER", "1.0").strip()
    try:
        value = float(raw)
    except Exception:
        return 1.0
    if value <= 0:
        return 1.0
    return value


def _resolve_max_tokens(agent: str, base_tokens: int) -> int:
    env_key = TOKEN_ENV_KEYS.get(agent)
    if env_key:
        raw = os.getenv(env_key)
        if raw:
            try:
                parsed = int(raw)
                if parsed > 0:
                    return parsed
            except Exception:
                pass
    multiplier = _token_multiplier()
    return max(64, int(round(base_tokens * multiplier)))


def _default_api_base(provider_type: str, explicit_api_base: str | None) -> str:
    if explicit_api_base:
        return explicit_api_base
    if provider_type == "ollama_local":
        return DEFAULT_OLLAMA_LOCAL_URL
    return DEFAULT_OLLAMA_CLOUD_URL


def _get_llm_config_types() -> tuple[type[Any], type[Any]]:
    from config.llm_provider_config import AgentProviderConfig, LLMProvidersConfig

    return LLMProvidersConfig, AgentProviderConfig


def _get_trading_engine_type() -> type[Any]:
    from src.trading.engine import TradingEngine

    return TradingEngine


def _compatible_engine_kwargs(engine_type: type[Any], **kwargs: Any) -> dict[str, Any]:
    """
    Filter constructor kwargs to the current TradingEngine signature.

    Hybrid runner has historically drifted behind the live engine constructor.
    This keeps the script compatible across engine refactors by dropping
    no-longer-supported kwargs such as `enable_trading` or
    `market_data_force_new`.
    """
    try:
        supported = set(inspect.signature(engine_type).parameters)
    except Exception:
        return dict(kwargs)
    return {key: value for key, value in kwargs.items() if key in supported}


def _visual_enabled_for_timeframe(timeframe: str) -> bool:
    if os.getenv("FENIX_DISABLE_VISUAL_ALL_TF", "0") == "1":
        return False
    disable_visual_short_tf = os.getenv("FENIX_DISABLE_VISUAL_SHORT_TF", "0") == "1"
    return not (disable_visual_short_tf and timeframe in {"1m", "3m", "5m"})


def build_llm_config_for_model(
    model: str,
    *,
    vision_model: str | None = None,
    api_base: str | None = None,
    timeout: int = 120,
    disable_rotation: bool = True,
    provider_type: str = "ollama_cloud",
    risk_provider_type: str | None = None,
) -> LLMProvidersConfig:
    """Builds an LLMProvidersConfig with a single model across agents."""
    LLMProvidersConfig, AgentProviderConfig = _get_llm_config_types()
    base = {
        "model_name": model,
        "temperature": 0.1,
        # Keep token budgets conservative for cloud stability under multi-TF load.
        "max_tokens": 1200,
        "timeout": timeout,
        "extra_config": {"disable_rotation": disable_rotation},
    }

    def cfg(*, provider_override: str | None = None, **overrides: Any) -> AgentProviderConfig:
        active_provider = provider_override or provider_type
        payload = {
            **base,
            "provider_type": active_provider,
            "api_base": _default_api_base(
                active_provider,
                api_base if active_provider == provider_type else None,
            ),
        }
        payload.update(overrides)
        return AgentProviderConfig(**payload)

    visual_model = vision_model or model

    return LLMProvidersConfig(
        sentiment=cfg(temperature=0.15, max_tokens=_resolve_max_tokens("sentiment", 900)),
        technical=cfg(temperature=0.08, max_tokens=_resolve_max_tokens("technical", 1200)),
        visual=cfg(
            model_name=visual_model,
            supports_vision=True,
            max_tokens=_resolve_max_tokens("visual", 1000),
        ),
        qabba=cfg(temperature=0.06, max_tokens=_resolve_max_tokens("qabba", 800)),
        decision=cfg(temperature=0.12, max_tokens=_resolve_max_tokens("decision", 1000)),
        risk_manager=cfg(
            provider_override=risk_provider_type,
            temperature=0.1,
            max_tokens=_resolve_max_tokens("risk_manager", 700),
        ),
    )


def parse_team_models(raw: str | None) -> dict[str, str]:
    """
    Parse team model mapping from:
    technical=modelA,qabba=modelB,decision=modelC,sentiment=modelD,visual=modelV
    """
    mapping: dict[str, str] = {}
    if not raw:
        return mapping
    for chunk in raw.replace(";", ",").split(","):
        part = chunk.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"Invalid team mapping chunk: {part}")
        agent, model = part.split("=", 1)
        agent = agent.strip().lower()
        model = model.strip()
        if agent not in TEAM_AGENT_KEYS:
            raise ValueError(f"Invalid team agent key: {agent}")
        if not model:
            raise ValueError(f"Empty model for agent: {agent}")
        mapping[agent] = model
    return mapping


def build_llm_config_for_team(
    team_models: dict[str, str],
    *,
    default_model: str | None = None,
    vision_model: str | None = None,
    api_base: str | None = None,
    timeout: int = 120,
    disable_rotation: bool = True,
    provider_type: str = "ollama_cloud",
    risk_provider_type: str | None = None,
) -> LLMProvidersConfig:
    """
    Build a per-agent team config.
    Unspecified agents fall back to default_model (or first provided model).
    """
    LLMProvidersConfig, AgentProviderConfig = _get_llm_config_types()
    fallback_model = default_model or (next(iter(team_models.values())) if team_models else None)
    if not fallback_model:
        raise ValueError("No default model available for team config")

    base = {
        "temperature": 0.1,
        # Keep token budgets conservative for cloud stability under multi-TF load.
        "max_tokens": 1200,
        "timeout": timeout,
        "extra_config": {"disable_rotation": disable_rotation},
    }

    def model_for(agent: str, fallback: str | None = None) -> str:
        return team_models.get(agent) or fallback or fallback_model

    def cfg(
        agent: str,
        *,
        provider_override: str | None = None,
        **overrides: Any,
    ) -> AgentProviderConfig:
        active_provider = provider_override or provider_type
        payload = {
            **base,
            "provider_type": active_provider,
            "api_base": _default_api_base(
                active_provider,
                api_base if active_provider == provider_type else None,
            ),
            "model_name": model_for(agent),
        }
        payload.update(overrides)
        return AgentProviderConfig(**payload)

    return LLMProvidersConfig(
        sentiment=cfg(
            "sentiment",
            temperature=0.15,
            max_tokens=_resolve_max_tokens("sentiment", 900),
        ),
        technical=cfg(
            "technical",
            temperature=0.08,
            max_tokens=_resolve_max_tokens("technical", 1200),
        ),
        visual=cfg(
            "visual",
            model_name=model_for("visual", vision_model or model_for("technical")),
            supports_vision=True,
            max_tokens=_resolve_max_tokens("visual", 1000),
        ),
        qabba=cfg("qabba", temperature=0.06, max_tokens=_resolve_max_tokens("qabba", 800)),
        decision=cfg(
            "decision",
            temperature=0.12,
            max_tokens=_resolve_max_tokens("decision", 1000),
        ),
        risk_manager=cfg(
            "risk_manager",
            provider_override=risk_provider_type,
            temperature=0.1,
            max_tokens=_resolve_max_tokens("risk_manager", 700),
        ),
    )


class HybridController:
    def __init__(
        self,
        *,
        symbol: str,
        bias_tf: str,
        entry_tf: str,
        scout_tf: str | None,
        min_bias_conf: float = 0.6,  # REDUCIDO de 0.7 a 0.6 para más trades
        min_entry_conf: float = 0.6,  # REDUCIDO de 0.7 a 0.6 para más trades
        scout_block_conf: float = 0.70,  # REDUCIDO de 0.75 a 0.70
        bias_flip_conf: float = 0.8,  # Minimum confidence for bias_flip to trigger close
        bias_flip_cooldown_sec: float = 180.0,  # Don't allow bias_flip close within 3 min of open
        position_size_usd: float = 1000.0,
        run_context: dict[str, Any] | None = None,
    ) -> None:
        self.symbol = symbol
        self.bias_tf = bias_tf
        self.entry_tf = entry_tf
        self.scout_tf = scout_tf
        self.min_bias_conf = min_bias_conf
        self.min_entry_conf = min_entry_conf
        self.scout_block_conf = scout_block_conf
        self.bias_flip_conf = bias_flip_conf
        self.bias_flip_cooldown_sec = bias_flip_cooldown_sec
        self.position_size_usd = position_size_usd
        self.run_context = dict(run_context or {})
        self.latest: dict[str, Signal] = {}
        self.position: dict[str, Any] | None = None
        self.last_flip_time: datetime | None = None
        self.position_open_time: datetime | None = None

        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.signal_log = log_dir / f"hybrid_signals_{symbol}_{ts}.jsonl"
        self.trade_log = log_dir / f"hybrid_trades_{symbol}_{ts}.jsonl"

    def _log_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        try:
            if self.run_context:
                payload = {**payload, **self.run_context}
            with path.open("a") as f:
                f.write(json.dumps(payload) + "\n")
        except Exception:
            pass

    @staticmethod
    def _is_valid_price(price: Any) -> bool:
        try:
            return float(price) > 0.0
        except Exception:
            return False

    def _get_dynamic_confidence_thresholds(self, indicators: dict[str, Any]) -> tuple[float, float]:
        """
        Calculate dynamic confidence thresholds based on market regime.

        Returns:
            Tuple of (min_bias_conf, min_entry_conf)
        """
        chop = indicators.get("chop", 50)
        adx = indicators.get("adx", 25)
        donchian_width = indicators.get("donchian_width_pct", 1.0)

        # Strong trend: be more conservative
        if adx > 30 and chop < 40:
            return (0.70, 0.70)

        # Ranging/choppy market: allow lower confidence for mean-reversion trades
        elif chop >= 61.8 or donchian_width < 0.5:
            return (0.55, 0.55)  # More permissive

        # Normal conditions: use defaults
        else:
            return (self.min_bias_conf, self.min_entry_conf)

    def _generate_bollinger_bounce_signal(
        self, indicators: dict[str, Any], price: float
    ) -> Signal | None:
        """
        Generate mean-reversion signals based on Bollinger Bands bounce.

        For ranging markets: buy at lower band, sell at upper band.
        """
        bb_upper = indicators.get("bollinger_upper")
        bb_lower = indicators.get("bollinger_lower")
        bb_middle = indicators.get("bollinger_middle")
        percent_b = indicators.get("percent_b")
        bandwidth = indicators.get("bandwidth_pct")
        chop = indicators.get("chop", 50)

        # Skip if missing data or not ranging
        if bb_upper is None or bb_lower is None or percent_b is None:
            return None

        # Only apply in ranging markets with flat Bollinger Bands
        if chop < 55 or bandwidth is None or bandwidth > 3.0:
            return None

        decision = None
        confidence = 0.0
        rationale = ""

        # Price at lower band = BUY (bounce up)
        if percent_b <= 0.05:
            decision = "BUY"
            confidence = 0.65 + (0.05 - percent_b) * 2
            rationale = f"Bollinger Bounce: price at lower band (percent_b={percent_b:.3f})"

        # Price at upper band = SELL (bounce down)
        elif percent_b >= 0.95:
            decision = "SELL"
            confidence = 0.65 + (percent_b - 0.95) * 2
            rationale = f"Bollinger Bounce: price at upper band (percent_b={percent_b:.3f})"

        if decision:
            return Signal(
                decision=decision,
                confidence=min(confidence, 0.80),
                price=price,
                timestamp=datetime.now(timezone.utc).isoformat(),
                judge_verdict="APPROVE",
                indicators=dict(indicators),
            )

        return None

    def _calculate_oscillator_confluence(self, indicators: dict[str, Any]) -> dict | None:
        """
        Combine multiple oscillators for overbought/oversold signals.

        Returns signal dict if strong confluence detected.
        """
        rsi = indicators.get("rsi", 50)
        stoch_k = indicators.get("stoch_k", 50)
        williams_r = indicators.get("williams_r", -50)
        cci = indicators.get("cci", 0)

        # Normalize to 0-100 scale
        williams_norm = williams_r + 100  # -100..0 -> 0..100
        cci_norm = max(0, min(100, (cci + 200) / 4))  # -200..200 -> 0..100

        # Calculate composite oversold score
        oversold_score = (rsi + stoch_k + williams_norm + cci_norm) / 4

        signal = None
        strength = 0.0

        if oversold_score < 25:  # Strong oversold
            signal = "BUY"
            strength = (25 - oversold_score) / 25
        elif oversold_score > 75:  # Strong overbought
            signal = "SELL"
            strength = (oversold_score - 75) / 25

        if signal and strength > 0.3:
            return {
                "signal": signal,
                "strength": strength,
                "oversold_score": oversold_score,
                "components": {
                    "rsi": rsi,
                    "stoch_k": stoch_k,
                    "williams_r": williams_r,
                    "cci": cci,
                },
            }

        return None

    def _detect_false_breakout(
        self,
        indicators: dict[str, Any],
        recent_candles: list[dict],
    ) -> Signal | None:
        """
        Detect and fade false breakouts.

        When price briefly breaks Donchian but returns quickly with low volume.
        """
        if len(recent_candles) < 3:
            return None

        donchian_upper = indicators.get("donchian_upper")
        donchian_lower = indicators.get("donchian_lower")

        if donchian_upper is None or donchian_lower is None:
            return None

        price = recent_candles[-1]["close"]
        prev_price = recent_candles[-2]["close"]
        recent_high = max(c["high"] for c in recent_candles[-3:])
        recent_low = min(c["low"] for c in recent_candles[-3:])

        # False breakout above
        if recent_high > donchian_upper * 1.002:
            if price < donchian_upper:  # Returned inside
                # Check volume (if available)
                curr_vol = recent_candles[-1].get("volume", 0)
                prev_vol = recent_candles[-2].get("volume", 0)
                if prev_vol > 0 and curr_vol < prev_vol * 0.8:
                    return Signal(
                        decision="SELL",
                        confidence=0.70,
                        price=price,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        judge_verdict="APPROVE",
                        indicators=dict(indicators),
                    )

        # False breakout below
        if recent_low < donchian_lower * 0.998:
            if price > donchian_lower:  # Returned inside
                curr_vol = recent_candles[-1].get("volume", 0)
                prev_vol = recent_candles[-2].get("volume", 0)
                if prev_vol > 0 and curr_vol < prev_vol * 0.8:
                    return Signal(
                        decision="BUY",
                        confidence=0.70,
                        price=price,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        judge_verdict="APPROVE",
                        indicators=dict(indicators),
                    )

        return None

    def update_signal(
        self,
        *,
        timeframe: str,
        decision: str,
        confidence: float,
        price: float,
        timestamp: str,
        judge_verdict: str | None,
        model: str | None = None,
        indicators: dict[str, Any] | None = None,
    ) -> None:
        if not self._is_valid_price(price):
            logger.warning(
                "Ignoring hybrid signal with invalid price: symbol=%s tf=%s decision=%s price=%s",
                self.symbol,
                timeframe,
                decision,
                price,
            )
            return

        sig = Signal(
            decision=decision,
            confidence=confidence,
            price=price,
            timestamp=timestamp,
            judge_verdict=judge_verdict,
            indicators=dict(indicators or {}),
        )
        self.latest[timeframe] = sig
        self._log_jsonl(
            self.signal_log,
            {
                "timestamp": timestamp,
                "symbol": self.symbol,
                "timeframe": timeframe,
                "model": model,
                "decision": decision,
                "confidence": confidence,
                "price": price,
                "judge_verdict": judge_verdict,
                "indicators": sig.indicators,
            },
        )

        if timeframe in {self.bias_tf, self.entry_tf, self.scout_tf}:
            self.evaluate(trigger_tf=timeframe)

    def _close_position(self, price: float, timestamp: str, reason: str) -> None:
        if not self.position:
            return
        side = self.position["side"]
        entry = float(self.position["entry_price"])
        if entry <= 0 or not self._is_valid_price(price):
            logger.warning(
                "Skipping hybrid close with invalid prices: symbol=%s entry=%s exit=%s reason=%s",
                self.symbol,
                entry,
                price,
                reason,
            )
            return
        if side == "LONG":
            pnl_pct = (price - entry) / entry
        else:
            pnl_pct = (entry - price) / entry
        pnl_usd = self.position_size_usd * pnl_pct
        payload = {
            "timestamp": timestamp,
            "symbol": self.symbol,
            "action": "CLOSE",
            "side": side,
            "entry_price": entry,
            "exit_price": price,
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct * 100.0, 4),
            "reason": reason,
        }
        self._log_jsonl(self.trade_log, payload)
        self.position = None

    def _open_position(
        self, side: str, price: float, timestamp: str, reason: str, tp_sl_levels: dict | None = None
    ) -> None:
        if not self._is_valid_price(price):
            logger.warning(
                "Skipping hybrid open with invalid price: symbol=%s side=%s price=%s reason=%s",
                self.symbol,
                side,
                price,
                reason,
            )
            return
        payload = {
            "timestamp": timestamp,
            "symbol": self.symbol,
            "action": "OPEN",
            "side": side,
            "entry_price": price,
            "reason": reason,
        }
        # Add staged TP/SL levels when available.
        if tp_sl_levels:
            payload["tp_sl_levels"] = tp_sl_levels
        self._log_jsonl(self.trade_log, payload)
        self.position = {
            "side": side,
            "entry_price": price,
            "timestamp": timestamp,
            "reason": reason,
            "tp1_closed": False,  # Track staged partial exits.
            "tp2_closed": False,
            "tp3_closed": False,
            "tp_sl_levels": tp_sl_levels or {},  # Persist staged levels.
        }
        self.position_open_time = datetime.fromisoformat(
            timestamp.replace("Z", "+00:00").replace("+00:00", "")
        )

    def _partial_close(self, price: float, timestamp: str, tp_level: int, size_pct: float) -> None:
        """Partially close a position at staged take-profit levels."""
        if not self.position:
            return
        side = self.position["side"]
        entry = float(self.position["entry_price"])
        if entry <= 0 or not self._is_valid_price(price):
            logger.warning(
                "Skipping hybrid partial close with invalid prices: symbol=%s entry=%s exit=%s",
                self.symbol,
                entry,
                price,
            )
            return
        if side == "LONG":
            pnl_pct = (price - entry) / entry
        else:
            pnl_pct = (entry - price) / entry
        pnl_usd = self.position_size_usd * size_pct * pnl_pct
        payload = {
            "timestamp": timestamp,
            "symbol": self.symbol,
            "action": "PARTIAL_CLOSE",
            "side": side,
            "tp_level": tp_level,
            "close_pct": size_pct,
            "entry_price": entry,
            "exit_price": price,
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct * 100.0, 4),
            "reason": f"tp{tp_level}_hit",
        }
        self._log_jsonl(self.trade_log, payload)
        # Mark this take-profit level as closed.
        if tp_level == 1:
            self.position["tp1_closed"] = True
        elif tp_level == 2:
            self.position["tp2_closed"] = True
        elif tp_level == 3:
            self.position["tp3_closed"] = True

    def check_tp_sl_levels(self, price: float, timestamp: str) -> bool:
        """Check whether staged TP/SL levels require a partial or full close."""
        if not self.position or not self.position.get("tp_sl_levels"):
            return False

        levels = self.position["tp_sl_levels"]
        side = self.position["side"]

        # Check TP1 (30% of position).
        if not self.position.get("tp1_closed"):
            tp1 = levels.get("tp1_price", 0)
            if side == "LONG" and price >= tp1:
                self._partial_close(price, timestamp, 1, 0.30)
                return False  # Position remains open.
            elif side == "SHORT" and price <= tp1:
                self._partial_close(price, timestamp, 1, 0.30)
                return False

        # Check TP2 (40% of position).
        if not self.position.get("tp2_closed"):
            tp2 = levels.get("tp2_price", 0)
            if side == "LONG" and price >= tp2:
                self._partial_close(price, timestamp, 2, 0.40)
                return False
            elif side == "SHORT" and price <= tp2:
                self._partial_close(price, timestamp, 2, 0.40)
                return False

        # Check TP3 (30% of position) and close the remainder.
        if not self.position.get("tp3_closed"):
            tp3 = levels.get("tp3_price", 0)
            if side == "LONG" and price >= tp3:
                self._close_position(price, timestamp, reason="tp3_full_close")
                return True
            elif side == "SHORT" and price <= tp3:
                self._close_position(price, timestamp, reason="tp3_full_close")
                return True

        # Check Stop Loss
        sl = levels.get("stop_loss", 0)
        if side == "LONG" and price <= sl:
            self._close_position(price, timestamp, reason="stop_loss")
            return True
        elif side == "SHORT" and price >= sl:
            self._close_position(price, timestamp, reason="stop_loss")
            return True

        return False

    def _build_range_signal(self, entry: Signal, bias: Signal | None) -> Signal | None:
        indicators = entry.indicators or {}
        if not indicators:
            return None

        bounce_signal = self._generate_bollinger_bounce_signal(indicators, entry.price)
        oscillator_signal = self._calculate_oscillator_confluence(indicators)
        recent_candles = indicators.get("recent_candles") or indicators.get("_recent_candles") or []
        false_breakout_signal = None
        if recent_candles:
            false_breakout_signal = self._detect_false_breakout(indicators, recent_candles)

        if false_breakout_signal:
            return false_breakout_signal

        if bounce_signal:
            if oscillator_signal and oscillator_signal.get("signal") == bounce_signal.decision:
                return Signal(
                    decision=bounce_signal.decision,
                    confidence=min(
                        0.85,
                        max(bounce_signal.confidence, 0.55 + oscillator_signal["strength"] * 0.20),
                    ),
                    price=entry.price,
                    timestamp=entry.timestamp,
                    judge_verdict="APPROVE",
                    indicators=dict(indicators),
                )
            return bounce_signal

        if oscillator_signal:
            if (
                bias
                and bias.decision in {"BUY", "SELL"}
                and bias.decision != oscillator_signal["signal"]
            ):
                return None
            return Signal(
                decision=str(oscillator_signal["signal"]).upper(),
                confidence=min(0.75, 0.55 + oscillator_signal["strength"] * 0.25),
                price=entry.price,
                timestamp=entry.timestamp,
                judge_verdict="APPROVE",
                indicators=dict(indicators),
            )

        return None

    def evaluate(self, trigger_tf: str) -> None:
        bias = self.latest.get(self.bias_tf)
        entry = self.latest.get(self.entry_tf)
        scout = self.latest.get(self.scout_tf) if self.scout_tf else None

        if not entry:
            return

        now_ts = entry.timestamp
        price = entry.price
        entry_indicators = entry.indicators or {}
        min_bias_conf, min_entry_conf = self._get_dynamic_confidence_thresholds(entry_indicators)

        # Check staged TP/SL exits when a position is already open.
        if self.position:
            closed = self.check_tp_sl_levels(price, now_ts)
            if closed:
                return  # Position closed; nothing else to do on this tick.

        # Bias gating
        allowed_action = "HOLD"
        if bias and bias.decision in {"BUY", "SELL"} and bias.confidence >= min_bias_conf:
            if entry.decision == bias.decision and entry.confidence >= min_entry_conf:
                allowed_action = entry.decision
        else:
            if entry.decision in {"BUY", "SELL"} and entry.confidence >= min_entry_conf:
                allowed_action = entry.decision

        range_signal = self._build_range_signal(entry, bias)
        if allowed_action == "HOLD" and range_signal and range_signal.confidence >= min_entry_conf:
            allowed_action = range_signal.decision

        # Scout veto
        if scout and allowed_action in {"BUY", "SELL"}:
            if (
                scout.decision in {"BUY", "SELL"}
                and scout.decision != allowed_action
                and scout.confidence >= self.scout_block_conf
            ):
                allowed_action = "HOLD"

        # Bias flip handling - IMPROVED: requires high confidence and respects cooldown
        if bias and self.position:
            position_side = self.position["side"]
            opposite_direction = "BUY" if position_side == "SHORT" else "SELL"

            # Only close if bias flips to opposite direction with HIGH confidence
            if bias.decision == opposite_direction and bias.confidence >= self.bias_flip_conf:
                # Check cooldown - don't flip too soon after opening
                now_dt = datetime.fromisoformat(now_ts.replace("Z", "+00:00").replace("+00:00", ""))
                if self.position_open_time:
                    time_since_open = (now_dt - self.position_open_time).total_seconds()
                    if time_since_open < self.bias_flip_cooldown_sec:
                        # Skip bias_flip close - position too young
                        pass
                    else:
                        # Calculate current PnL to log it
                        entry_price = float(self.position["entry_price"])
                        if position_side == "LONG":
                            pnl_pct = (price - entry_price) / entry_price * 100
                        else:
                            pnl_pct = (entry_price - price) / entry_price * 100
                        self._close_position(
                            price, now_ts, reason=f"bias_flip(pnl={pnl_pct:+.2f}%)"
                        )

        # Execute hybrid action
        if allowed_action in {"BUY", "SELL"}:
            side = "LONG" if allowed_action == "BUY" else "SHORT"
            if not self.position:
                # Calculate staged TP/SL levels with range-market adaptation.
                try:
                    from src.risk.dynamic_stop_loss import DynamicStopLossCalculator, StopLossConfig

                    cfg = StopLossConfig(max_risk_per_trade_pct=2.0)
                    calc = DynamicStopLossCalculator(cfg)
                    indicators = dict(entry_indicators)
                    atr_estimate = float(indicators.get("atr") or price * 0.005)

                    # Use ranging-aware calculation
                    levels = calc.calculate_for_ranging_market(
                        entry_price=price,
                        atr=atr_estimate,
                        balance_usd=10000.0,
                        decision=allowed_action,
                        indicators=indicators,
                    )
                    tp_sl_levels = {
                        "stop_loss": levels.stop_loss,
                        "take_profit": levels.take_profit,
                        "tp1_price": levels.tp1_price,
                        "tp2_price": levels.tp2_price,
                        "tp3_price": levels.tp3_price,
                        "tp1_size_pct": levels.tp1_size_pct,
                        "tp2_size_pct": levels.tp2_size_pct,
                        "tp3_size_pct": levels.tp3_size_pct,
                    }
                except Exception as e:
                    logger.warning(f"Error calculating TP/SL levels: {e}")
                    tp_sl_levels = None
                self._open_position(
                    side, price, now_ts, reason="hybrid_entry", tp_sl_levels=tp_sl_levels
                )
            elif self.position["side"] != side:
                # Check 5-minute cooldown for flips
                now_dt = datetime.fromisoformat(now_ts.replace("Z", "+00:00"))
                if self.last_flip_time and (now_dt - self.last_flip_time).total_seconds() < 300:
                    return  # Skip flip due to cooldown

                self._close_position(price, now_ts, reason="hybrid_flip")
                # Calculate staged TP/SL levels for the new trade.
                try:
                    from src.risk.dynamic_stop_loss import DynamicStopLossCalculator, StopLossConfig

                    cfg = StopLossConfig(max_risk_per_trade_pct=2.0)
                    calc = DynamicStopLossCalculator(cfg)
                    indicators = dict(entry_indicators)
                    atr_estimate = float(indicators.get("atr") or price * 0.005)

                    levels = calc.calculate_for_ranging_market(
                        entry_price=price,
                        atr=atr_estimate,
                        balance_usd=10000.0,
                        decision=allowed_action,
                        indicators=indicators,
                    )
                    tp_sl_levels = {
                        "stop_loss": levels.stop_loss,
                        "take_profit": levels.take_profit,
                        "tp1_price": levels.tp1_price,
                        "tp2_price": levels.tp2_price,
                        "tp3_price": levels.tp3_price,
                        "tp1_size_pct": levels.tp1_size_pct,
                        "tp2_size_pct": levels.tp2_size_pct,
                        "tp3_size_pct": levels.tp3_size_pct,
                    }
                except Exception as e:
                    logger.warning(f"Error calculating TP/SL levels: {e}")
                    tp_sl_levels = None
                self._open_position(
                    side, price, now_ts, reason="hybrid_flip", tp_sl_levels=tp_sl_levels
                )
                self.last_flip_time = now_dt


async def run(
    *,
    symbol: str,
    timeframes: list[str],
    bias_tf: str,
    entry_tf: str,
    scout_tf: str | None,
    run_minutes: int | None,
    position_size_usd: float,
    fanout_tf: str | None = None,
    fanout_models: list[str] | None = None,
    fanout_primary: str | None = None,
    fanout_vision_model: str | None = None,
    base_model: str | None = None,
    base_vision_model: str | None = None,
    team_models: str | None = None,
    team_tag: str | None = None,
    risk_provider: str | None = None,
    team_provider: str = "ollama_cloud",
    run_context: dict[str, Any] | None = None,
) -> None:
    TradingEngine = _get_trading_engine_type()
    controller = HybridController(
        symbol=symbol,
        bias_tf=bias_tf,
        entry_tf=entry_tf,
        scout_tf=scout_tf,
        position_size_usd=position_size_usd,
        run_context=run_context,
    )

    engines: list[Any] = []
    tasks: list[asyncio.Task] = []

    engine_specs: list[EngineSpec] = []
    fanout_models = [m for m in (fanout_models or []) if m]
    fanout_vision_model = fanout_vision_model or DEFAULT_FANOUT_VISION_MODEL
    base_cfg = None
    base_label: str | None = None
    if team_models:
        team_map = parse_team_models(team_models)
        base_cfg = build_llm_config_for_team(
            team_map,
            default_model=base_model,
            vision_model=base_vision_model or fanout_vision_model,
            disable_rotation=True,
            provider_type=team_provider,
            risk_provider_type=risk_provider,
        )
        base_label = team_tag.strip() if team_tag and team_tag.strip() else "team"
    elif base_model:
        base_cfg = build_llm_config_for_model(
            base_model,
            vision_model=base_vision_model or fanout_vision_model,
            disable_rotation=True,
            provider_type=team_provider,
            risk_provider_type=risk_provider,
        )
        base_label = base_model

    for tf in timeframes:
        if fanout_tf and tf == fanout_tf and fanout_models:
            primary_model = fanout_primary or None
            if primary_model and primary_model not in fanout_models:
                fanout_models = [primary_model] + fanout_models

            if primary_model:
                primary_cfg = build_llm_config_for_model(
                    primary_model,
                    vision_model=fanout_vision_model,
                    disable_rotation=True,
                    provider_type=team_provider,
                    risk_provider_type=risk_provider,
                )
                engine_specs.append(
                    EngineSpec(
                        timeframe=tf,
                        tf_label=tf,
                        llm_config=primary_cfg,
                        model_tag=primary_model,
                        use_for_hybrid=True,
                    )
                )
            else:
                engine_specs.append(
                    EngineSpec(
                        timeframe=tf,
                        tf_label=tf,
                        llm_config=None,
                        model_tag=None,
                        use_for_hybrid=True,
                    )
                )

            for model in fanout_models:
                if primary_model and model == primary_model:
                    continue
                llm_cfg = build_llm_config_for_model(
                    model,
                    vision_model=fanout_vision_model,
                    disable_rotation=True,
                    provider_type=team_provider,
                    risk_provider_type=risk_provider,
                )
                engine_specs.append(
                    EngineSpec(
                        timeframe=tf,
                        tf_label=f"{tf}@{model}",
                        llm_config=llm_cfg,
                        model_tag=model,
                        use_for_hybrid=False,
                    )
                )
        else:
            engine_specs.append(
                EngineSpec(
                    timeframe=tf,
                    tf_label=tf,
                    llm_config=base_cfg,
                    model_tag=base_label,
                    use_for_hybrid=True,
                )
            )

    disable_sentiment_short_tf = os.getenv("FENIX_DISABLE_SENTIMENT_SHORT_TF", "1") == "1"

    for spec in engine_specs:
        enable_visual_agent = _visual_enabled_for_timeframe(spec.timeframe)
        enable_sentiment_agent = not (
            disable_sentiment_short_tf and spec.timeframe in {"1m", "3m", "5m"}
        )
        engine = TradingEngine(
            **_compatible_engine_kwargs(
                TradingEngine,
                symbol=symbol,
                timeframe=spec.timeframe,
                paper_trading=True,
                enable_trading=False,
                enable_visual_agent=enable_visual_agent,
                enable_sentiment_agent=enable_sentiment_agent,
                llm_config=spec.llm_config,
                market_data_force_new=True,
            )
        )

        async def on_event(
            event: str,
            payload: dict[str, Any],
            *,
            _tf=spec.tf_label,
            _engine=engine,
            _model=spec.model_tag,
        ):
            if event != "final_decision":
                return
            decision = str(payload.get("decision", "HOLD")).upper()
            confidence = _conf_to_float(payload.get("confidence"))
            price = _extract_event_price(payload, _engine.market_data.current_price)
            timestamp = payload.get("timestamp") or datetime.now(timezone.utc).isoformat()
            judge_verdict = payload.get("judge_verdict")
            indicators = payload.get("indicators") or {}
            controller.update_signal(
                timeframe=_tf,
                decision=decision,
                confidence=confidence,
                price=price,
                timestamp=timestamp,
                judge_verdict=judge_verdict,
                model=_model,
                indicators=indicators,
            )

        engine.on_agent_event = on_event
        engines.append(engine)
        tasks.append(asyncio.create_task(engine.start()))

    try:
        if run_minutes:
            await asyncio.sleep(run_minutes * 60)
        else:
            while True:
                await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_timeout = 20.0
        for engine in engines:
            try:
                await asyncio.wait_for(engine.stop(), timeout=stop_timeout)
            except Exception:
                pass
        for task in tasks:
            task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=stop_timeout
            )
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Run hybrid live paper trading (multi-timeframe)")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading pair")
    parser.add_argument("--timeframes", default="1m,3m,15m", help="Comma-separated timeframes")
    parser.add_argument("--bias-tf", default="15m", help="Bias timeframe (higher TF)")
    parser.add_argument("--entry-tf", default="3m", help="Entry timeframe")
    parser.add_argument("--scout-tf", default="1m", help="Scout timeframe (optional)")
    parser.add_argument("--fanout-tf", default=None, help="Timeframe to fan-out models (e.g. 1h)")
    parser.add_argument("--fanout-models", default=None, help="Comma-separated models for fan-out")
    parser.add_argument(
        "--fanout-primary", default=None, help="Model to use for hybrid bias timeframe"
    )
    parser.add_argument("--fanout-vision-model", default=None, help="Vision model for fan-out runs")
    parser.add_argument("--base-model", default=None, help="Fixed model for all non-fanout engines")
    parser.add_argument(
        "--base-vision-model", default=None, help="Vision model when using --base-model"
    )
    parser.add_argument(
        "--team-models",
        default=None,
        help=(
            "Per-agent team map "
            "(technical=...,qabba=...,decision=...,sentiment=...,visual=...,risk_manager=...)"
        ),
    )
    parser.add_argument("--team-tag", default=None, help="Label for team run in signal logs")
    parser.add_argument("--run-minutes", type=int, default=None, help="Stop after N minutes")
    parser.add_argument(
        "--position-usd", type=float, default=1000.0, help="Paper position size USD"
    )
    # ── Benchmark experiment flags ──────────────────────────────────────────
    parser.add_argument(
        "--disable-reasoning-bank",
        action="store_true",
        help="Disable ReasoningBank writes/reads (for A/B testing)",
    )
    parser.add_argument(
        "--monolithic-mode",
        action="store_true",
        help="Tag run as monolithic experiment and export FENIX_MONOLITHIC_MODE=1",
    )
    parser.add_argument(
        "--disable-risk-manager",
        action="store_true",
        help="Disable Risk Manager agent (auto-approve all trades)",
    )
    parser.add_argument(
        "--disable-judge",
        action="store_true",
        help="Disable Judge evaluation (let Decision agent decide freely)",
    )
    parser.add_argument(
        "--single-timeframe",
        default=None,
        help="Override --timeframes to run only this one TF (for isolation tests)",
    )
    parser.add_argument("--run-tag", default=None, help="Benchmark run tag metadata")
    parser.add_argument("--slot-name", default=None, help="Benchmark slot name metadata")
    parser.add_argument(
        "--slot-index", type=int, default=None, help="Benchmark slot index metadata"
    )
    parser.add_argument("--experiment", default=None, help="Benchmark experiment metadata")
    parser.add_argument(
        "--experiment-id", type=int, default=None, help="Benchmark experiment id metadata"
    )
    parser.add_argument(
        "--risk-provider",
        choices=("ollama_cloud", "ollama_local"),
        default=None,
        help="Optional provider override only for risk_manager",
    )
    parser.add_argument(
        "--team-provider",
        choices=("ollama_cloud", "ollama_local"),
        default="ollama_cloud",
        help="Provider for non-risk agents in --team-models / --base-model modes",
    )
    args = parser.parse_args()

    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    scout_tf = args.scout_tf.strip() if args.scout_tf and args.scout_tf.strip() else None
    if scout_tf and scout_tf.lower() in {"none", "null", "off"}:
        scout_tf = None
    fanout_models = (
        [m.strip() for m in (args.fanout_models or "").split(",") if m.strip()]
        if args.fanout_models
        else []
    )
    fanout_tf = args.fanout_tf.strip() if args.fanout_tf and args.fanout_tf.strip() else None
    fanout_primary = (
        args.fanout_primary.strip() if args.fanout_primary and args.fanout_primary.strip() else None
    )
    fanout_vision_model = (
        args.fanout_vision_model.strip()
        if args.fanout_vision_model and args.fanout_vision_model.strip()
        else None
    )
    base_model = args.base_model.strip() if args.base_model and args.base_model.strip() else None
    base_vision_model = (
        args.base_vision_model.strip()
        if args.base_vision_model and args.base_vision_model.strip()
        else None
    )
    team_models = (
        args.team_models.strip() if args.team_models and args.team_models.strip() else None
    )
    team_tag = args.team_tag.strip() if args.team_tag and args.team_tag.strip() else None
    risk_provider = args.risk_provider.strip() if args.risk_provider else None
    team_provider = args.team_provider.strip() if args.team_provider else "ollama_cloud"

    risk_model_name = ""
    if team_models:
        try:
            risk_model_name = parse_team_models(team_models).get("risk_manager", "").lower()
        except Exception:
            risk_model_name = ""
    risk_is_glm5 = "glm-5" in risk_model_name or "glm5" in risk_model_name

    # ── Benchmark experiment env vars ────────────────────────────────────────
    if args.disable_reasoning_bank:
        os.environ["FENIX_DISABLE_REASONING_BANK"] = "1"
        # This is the flag currently consumed by LangGraph orchestrator.
        os.environ["FENIX_ENABLE_REASONING_BANK"] = "0"
    if args.monolithic_mode:
        os.environ["FENIX_MONOLITHIC_MODE"] = "1"
    # ── Disable Risk Manager and Judge ──────────────────────────────────────
    if args.disable_risk_manager:
        os.environ["FENIX_DISABLE_RISK_MANAGER"] = "1"
    if args.disable_judge:
        os.environ["FENIX_ENABLE_JUDGE"] = "0"
    # Compatibility alias for clients that expect OLLAMA_API_KEY.
    if os.getenv("OLLAMA_CLOUD_API_KEY") and not os.getenv("OLLAMA_API_KEY"):
        os.environ["OLLAMA_API_KEY"] = os.environ["OLLAMA_CLOUD_API_KEY"]
    # Default hardening for cloud runs; callers can override via env.
    # Timeouts increased to handle occasional cloud latency spikes (2026-02-23)
    os.environ.setdefault("FENIX_SHORT_TF_NONBLOCKING", "1")
    os.environ.setdefault("FENIX_TECH_TIMEOUT_SHORT_SEC", "90")
    os.environ.setdefault(
        "FENIX_TECHNICAL_TIMEOUT_SHORT_SEC", os.environ["FENIX_TECH_TIMEOUT_SHORT_SEC"]
    )
    os.environ.setdefault("FENIX_QABBA_TIMEOUT_SHORT_SEC", "90")
    os.environ.setdefault("FENIX_DECISION_TIMEOUT_SHORT_SEC", "60")
    os.environ.setdefault("FENIX_VISUAL_TIMEOUT_SHORT_SEC", "35")
    os.environ.setdefault("FENIX_RISK_TIMEOUT_SEC", "120" if risk_is_glm5 else "45")
    os.environ.setdefault("FENIX_TECH_MAX_RETRIES", "2")
    os.environ.setdefault("FENIX_TECHNICAL_MAX_RETRIES", os.environ["FENIX_TECH_MAX_RETRIES"])
    os.environ.setdefault("FENIX_QABBA_MAX_RETRIES", "2")
    os.environ.setdefault("FENIX_DECISION_MAX_RETRIES", "2")
    os.environ.setdefault("FENIX_SENTIMENT_MAX_RETRIES", "2")
    os.environ.setdefault("FENIX_SENTIMENT_AGENT_TIMEOUT_SHORT_SEC", "30")
    os.environ.setdefault("FENIX_SENTIMENT_AGENT_TIMEOUT_SEC", "45")
    os.environ.setdefault("FENIX_RETRY_429_WAIT_SEC", "10")
    os.environ.setdefault("FENIX_RETRY_429_WAIT_JITTER_SEC", "5")
    os.environ.setdefault("FENIX_RETRY_503_WAIT_SEC", "15")
    os.environ.setdefault("FENIX_RETRY_503_WAIT_JITTER_SEC", "10")
    os.environ.setdefault("FENIX_LLM_MAX_CONCURRENT_REQUESTS", "3" if risk_is_glm5 else "6")
    os.environ.setdefault("FENIX_AGENT_CACHE_ON_TIMEOUT", "1")
    os.environ.setdefault("FENIX_AGENT_CACHE_TTL_SHORT_SEC", "300")
    os.environ.setdefault("FENIX_DISABLE_VISUAL_SHORT_TF", "1")
    if args.single_timeframe:
        tf = args.single_timeframe.strip()
        timeframes = [tf]
        # Keep the hybrid policy consistent in single-TF isolation experiments.
        args.bias_tf = tf
        args.entry_tf = tf
        scout_tf = tf

    if args.bias_tf not in timeframes:
        args.bias_tf = timeframes[0]
    if args.entry_tf not in timeframes:
        args.entry_tf = timeframes[0]
    if scout_tf and scout_tf not in timeframes:
        scout_tf = timeframes[0]

    run_context: dict[str, Any] = {}
    if args.run_tag:
        run_context["run_tag"] = args.run_tag
    if args.slot_name:
        run_context["slot_name"] = args.slot_name
    if args.slot_index is not None:
        run_context["slot"] = args.slot_index
    if args.experiment:
        run_context["experiment"] = args.experiment
    if args.experiment_id is not None:
        run_context["experiment_id"] = args.experiment_id
    if args.monolithic_mode:
        run_context["monolithic_mode"] = True
    if args.disable_reasoning_bank:
        run_context["disable_reasoning_bank"] = True
    if args.single_timeframe:
        run_context["single_timeframe"] = args.single_timeframe.strip()

    asyncio.run(
        run(
            symbol=args.symbol,
            timeframes=timeframes,
            bias_tf=args.bias_tf,
            entry_tf=args.entry_tf,
            scout_tf=scout_tf,
            run_minutes=args.run_minutes,
            position_size_usd=args.position_usd,
            fanout_tf=fanout_tf,
            fanout_models=fanout_models,
            fanout_primary=fanout_primary,
            fanout_vision_model=fanout_vision_model,
            base_model=base_model,
            base_vision_model=base_vision_model,
            team_models=team_models,
            team_tag=team_tag,
            risk_provider=risk_provider,
            team_provider=team_provider,
            run_context=run_context,
        )
    )


if __name__ == "__main__":
    main()
