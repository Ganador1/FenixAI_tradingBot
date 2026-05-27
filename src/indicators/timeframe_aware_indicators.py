"""
Timeframe-Aware Indicator System for Fenix Trading Bot.

Automatically selects and weights indicators based on:
- Current timeframe (1m, 5m, 15m, 1h, etc.)
- Market regime (trending, ranging, volatile)
- Indicator effectiveness per timeframe

This module provides intelligent indicator selection that adapts
to the trading context rather than using fixed indicator sets.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    """Market regime classification."""

    TRENDING = "trending"
    RANGING = "ranging"
    VOLATILE = "volatile"
    LOW_VOLATILITY = "low_volatility"


@dataclass
class IndicatorProfile:
    """Profile for a specific indicator."""

    name: str
    description: str
    best_timeframes: list[str]  # e.g., ["1m", "5m", "15m"]
    worst_timeframes: list[str]  # Timeframes where it performs poorly
    market_regimes: list[MarketRegime]  # Best market conditions
    reliability_score: float  # 0.0 to 1.0 based on backtesting
    lag: Literal["low", "medium", "high"]
    noise_sensitivity: Literal["low", "medium", "high"]
    primary_use: str  # "entry", "exit", "trend", "momentum", "volume"
    requires: list[str]  # Required data feeds (e.g., ["orderbook", "funding"])


@dataclass
class IndicatorSuite:
    """Suite of indicators selected for current context."""

    timeframe: str
    market_regime: MarketRegime
    primary_indicators: list[str]  # Main indicators for this context
    secondary_indicators: list[str]  # Confirming indicators
    excluded_indicators: list[str]  # Indicators to avoid
    rationale: str  # Why these were selected
    confidence_boost: float  # Additional confidence factor (0.0 to 0.2)


# ============================================================================
# INDICATOR DATABASE
# ============================================================================

INDICATOR_PROFILES: dict[str, IndicatorProfile] = {
    # TREND INDICATORS
    "supertrend": IndicatorProfile(
        name="SuperTrend",
        description="Trend following with ATR-based volatility bands",
        best_timeframes=["5m", "15m", "1h", "4h"],
        worst_timeframes=["1m"],  # Too noisy on 1m
        market_regimes=[MarketRegime.TRENDING, MarketRegime.VOLATILE],
        reliability_score=0.78,
        lag="medium",
        noise_sensitivity="medium",
        primary_use="trend",
        requires=["ohlcv"],
    ),
    "ema_alignment": IndicatorProfile(
        name="EMA Alignment",
        description="Multiple EMA alignment (9, 21, 50, 200)",
        best_timeframes=["5m", "15m", "1h", "4h", "1d"],
        worst_timeframes=["1m"],
        market_regimes=[MarketRegime.TRENDING],
        reliability_score=0.72,
        lag="medium",
        noise_sensitivity="medium",
        primary_use="trend",
        requires=["ohlcv"],
    ),
    "hull_ma": IndicatorProfile(
        name="Hull Moving Average",
        description="Ultra-responsive moving average with minimal lag",
        best_timeframes=["1m", "5m", "15m"],
        worst_timeframes=["1h", "4h", "1d"],  # Too fast for higher timeframes
        market_regimes=[MarketRegime.TRENDING, MarketRegime.RANGING],
        reliability_score=0.75,
        lag="low",
        noise_sensitivity="high",
        primary_use="entry",
        requires=["ohlcv"],
    ),
    "ichimoku": IndicatorProfile(
        name="Ichimoku Cloud",
        description="Complete trend system with support/resistance",
        best_timeframes=["15m", "1h", "4h", "1d"],
        worst_timeframes=["1m", "5m"],  # Too complex for low timeframes
        market_regimes=[MarketRegime.TRENDING],
        reliability_score=0.80,
        lag="medium",
        noise_sensitivity="low",
        primary_use="trend",
        requires=["ohlcv"],
    ),
    # MOMENTUM INDICATORS
    "rsi": IndicatorProfile(
        name="RSI",
        description="Relative Strength Index - overbought/oversold",
        best_timeframes=["5m", "15m", "1h"],
        worst_timeframes=["1m"],  # Too noisy
        market_regimes=[MarketRegime.RANGING, MarketRegime.LOW_VOLATILITY],
        reliability_score=0.68,
        lag="low",
        noise_sensitivity="high",
        primary_use="momentum",
        requires=["ohlcv"],
    ),
    "macd": IndicatorProfile(
        name="MACD",
        description="Moving Average Convergence Divergence",
        best_timeframes=["5m", "15m", "1h", "4h"],
        worst_timeframes=["1m"],
        market_regimes=[MarketRegime.TRENDING, MarketRegime.RANGING],
        reliability_score=0.70,
        lag="medium",
        noise_sensitivity="medium",
        primary_use="momentum",
        requires=["ohlcv"],
    ),
    "fisher_transform": IndicatorProfile(
        name="Fisher Transform",
        description="Gaussian transformation for early reversal detection",
        best_timeframes=["1m", "5m", "15m"],
        worst_timeframes=["4h", "1d"],
        market_regimes=[MarketRegime.RANGING, MarketRegime.LOW_VOLATILITY],
        reliability_score=0.74,
        lag="low",
        noise_sensitivity="high",
        primary_use="entry",
        requires=["ohlcv"],
    ),
    "mfi": IndicatorProfile(
        name="Money Flow Index",
        description="RSI with volume - more reliable",
        best_timeframes=["5m", "15m", "1h"],
        worst_timeframes=["1m"],
        market_regimes=[MarketRegime.RANGING, MarketRegime.TRENDING],
        reliability_score=0.73,
        lag="low",
        noise_sensitivity="medium",
        primary_use="momentum",
        requires=["ohlcv"],
    ),
    # VOLUME INDICATORS
    "vwap": IndicatorProfile(
        name="VWAP",
        description="Volume-Weighted Average Price - institutional benchmark",
        best_timeframes=["1m", "5m", "15m", "1h"],
        worst_timeframes=["4h", "1d"],  # Less relevant for swing trading
        market_regimes=[MarketRegime.TRENDING, MarketRegime.RANGING, MarketRegime.VOLATILE],
        reliability_score=0.82,
        lag="low",
        noise_sensitivity="low",
        primary_use="entry",
        requires=["ohlcv", "volume"],
    ),
    "volume_profile": IndicatorProfile(
        name="Volume Profile",
        description="Volume distribution by price level (VPVR)",
        best_timeframes=["15m", "1h", "4h"],
        worst_timeframes=["1m"],  # Not enough data
        market_regimes=[MarketRegime.RANGING, MarketRegime.LOW_VOLATILITY],
        reliability_score=0.79,
        lag="low",
        noise_sensitivity="low",
        primary_use="trend",
        requires=["ohlcv", "volume"],
    ),
    "cmf": IndicatorProfile(
        name="Chaikin Money Flow",
        description="Volume-weighted accumulation/distribution",
        best_timeframes=["5m", "15m", "1h"],
        worst_timeframes=["1m"],
        market_regimes=[MarketRegime.TRENDING],
        reliability_score=0.71,
        lag="medium",
        noise_sensitivity="medium",
        primary_use="momentum",
        requires=["ohlcv"],
    ),
    # VOLATILITY INDICATORS
    "atr": IndicatorProfile(
        name="ATR",
        description="Average True Range - volatility measure",
        best_timeframes=["5m", "15m", "1h", "4h"],
        worst_timeframes=[],
        market_regimes=[MarketRegime.VOLATILE, MarketRegime.TRENDING, MarketRegime.RANGING],
        reliability_score=0.85,
        lag="low",
        noise_sensitivity="low",
        primary_use="exit",  # For stop losses
        requires=["ohlcv"],
    ),
    "bollinger_squeeze": IndicatorProfile(
        name="Bollinger Squeeze",
        description="Low volatility expansion predictor (BB inside Keltner)",
        best_timeframes=["5m", "15m", "1h"],
        worst_timeframes=["1m", "4h"],
        market_regimes=[MarketRegime.LOW_VOLATILITY],
        reliability_score=0.76,
        lag="low",
        noise_sensitivity="medium",
        primary_use="entry",
        requires=["ohlcv"],
    ),
    "chop": IndicatorProfile(
        name="Choppiness Index",
        description="Measures market choppiness vs trendiness (0-100 scale)",
        best_timeframes=["5m", "15m", "1h", "4h"],
        worst_timeframes=["1m"],
        market_regimes=[MarketRegime.RANGING, MarketRegime.TRENDING],
        reliability_score=0.82,
        lag="medium",
        noise_sensitivity="low",
        primary_use="trend",
        requires=["ohlcv"],
    ),
    "donchian": IndicatorProfile(
        name="Donchian Channels",
        description="Breakout detection based on N-period high/low",
        best_timeframes=["15m", "1h", "4h"],
        worst_timeframes=["1m", "5m"],
        market_regimes=[MarketRegime.TRENDING, MarketRegime.VOLATILE],
        reliability_score=0.78,
        lag="low",
        noise_sensitivity="low",
        primary_use="entry",
        requires=["ohlcv"],
    ),
    "bollinger_bands": IndicatorProfile(
        name="Bollinger Bands",
        description="Volatility bands for mean-reversion and expansion",
        best_timeframes=["5m", "15m", "1h"],
        worst_timeframes=["1m"],
        market_regimes=[MarketRegime.RANGING, MarketRegime.LOW_VOLATILITY, MarketRegime.VOLATILE],
        reliability_score=0.74,
        lag="medium",
        noise_sensitivity="medium",
        primary_use="entry",
        requires=["ohlcv"],
    ),
    "stoch": IndicatorProfile(
        name="Stochastic Oscillator",
        description="Momentum oscillator for overbought/oversold and turns",
        best_timeframes=["3m", "5m", "15m"],
        worst_timeframes=["1h", "4h", "1d"],
        market_regimes=[MarketRegime.RANGING, MarketRegime.LOW_VOLATILITY],
        reliability_score=0.70,
        lag="low",
        noise_sensitivity="high",
        primary_use="entry",
        requires=["ohlcv"],
    ),
    "williams_r": IndicatorProfile(
        name="Williams %R",
        description="Fast momentum oscillator to spot extremes",
        best_timeframes=["1m", "3m", "5m"],
        worst_timeframes=["1h", "4h", "1d"],
        market_regimes=[MarketRegime.RANGING, MarketRegime.LOW_VOLATILITY],
        reliability_score=0.68,
        lag="low",
        noise_sensitivity="high",
        primary_use="entry",
        requires=["ohlcv"],
    ),
    "cci": IndicatorProfile(
        name="CCI",
        description="Commodity Channel Index for cyclical turns",
        best_timeframes=["5m", "15m", "1h"],
        worst_timeframes=["1m"],
        market_regimes=[MarketRegime.RANGING, MarketRegime.VOLATILE],
        reliability_score=0.69,
        lag="low",
        noise_sensitivity="medium",
        primary_use="momentum",
        requires=["ohlcv"],
    ),
    "roc": IndicatorProfile(
        name="ROC",
        description="Rate of Change for trend acceleration",
        best_timeframes=["5m", "15m", "1h"],
        worst_timeframes=["1m"],
        market_regimes=[MarketRegime.TRENDING, MarketRegime.VOLATILE],
        reliability_score=0.67,
        lag="low",
        noise_sensitivity="medium",
        primary_use="momentum",
        requires=["ohlcv"],
    ),
    "obv": IndicatorProfile(
        name="OBV",
        description="On-Balance Volume for trend confirmation",
        best_timeframes=["15m", "1h", "4h"],
        worst_timeframes=["1m", "5m"],
        market_regimes=[MarketRegime.TRENDING],
        reliability_score=0.71,
        lag="medium",
        noise_sensitivity="low",
        primary_use="trend",
        requires=["ohlcv", "volume"],
    ),
    "sar": IndicatorProfile(
        name="Parabolic SAR",
        description="Trend-following stop and reversal points",
        best_timeframes=["1m", "5m", "15m"],
        worst_timeframes=["4h", "1d"],
        market_regimes=[MarketRegime.TRENDING, MarketRegime.VOLATILE],
        reliability_score=0.66,
        lag="low",
        noise_sensitivity="high",
        primary_use="exit",
        requires=["ohlcv"],
    ),
    "garch_volatility": IndicatorProfile(
        name="GARCH Volatility",
        description="Short-term volatility forecast from returns",
        best_timeframes=["15m", "1h", "4h"],
        worst_timeframes=["1m", "3m", "5m"],
        market_regimes=[MarketRegime.VOLATILE, MarketRegime.TRENDING],
        reliability_score=0.63,
        lag="medium",
        noise_sensitivity="low",
        primary_use="exit",
        requires=["ohlcv"],
    ),
    "keltner": IndicatorProfile(
        name="Keltner Channels",
        description="ATR-based trend channels for squeeze detection",
        best_timeframes=["5m", "15m", "1h"],
        worst_timeframes=["1m", "4h"],
        market_regimes=[MarketRegime.TRENDING, MarketRegime.LOW_VOLATILITY],
        reliability_score=0.79,
        lag="medium",
        noise_sensitivity="medium",
        primary_use="trend",
        requires=["ohlcv"],
    ),
    "adx": IndicatorProfile(
        name="ADX",
        description="Average Directional Index - trend strength",
        best_timeframes=["15m", "1h", "4h"],
        worst_timeframes=["1m", "5m"],  # Too slow for scalping
        market_regimes=[MarketRegime.TRENDING],
        reliability_score=0.77,
        lag="medium",
        noise_sensitivity="low",
        primary_use="trend",
        requires=["ohlcv"],
    ),
    # MICROSTRUCTURE INDICATORS (Require special data feeds)
    "obi": IndicatorProfile(
        name="Order Book Imbalance",
        description="Bid/Ask volume ratio",
        best_timeframes=["1m", "5m"],
        worst_timeframes=["15m", "1h", "4h"],  # Order book changes too slowly
        market_regimes=[MarketRegime.TRENDING, MarketRegime.RANGING, MarketRegime.VOLATILE],
        reliability_score=0.74,
        lag="low",
        noise_sensitivity="high",
        primary_use="entry",
        requires=["orderbook"],
    ),
    "cvd": IndicatorProfile(
        name="Cumulative Volume Delta",
        description="Cumulative buy-sell aggression",
        best_timeframes=["1m", "5m", "15m"],
        worst_timeframes=["1h", "4h"],
        market_regimes=[MarketRegime.TRENDING, MarketRegime.VOLATILE],
        reliability_score=0.76,
        lag="low",
        noise_sensitivity="medium",
        primary_use="momentum",
        requires=["trades"],
    ),
    "ofi": IndicatorProfile(
        name="Order Flow Imbalance (OFI)",
        description="Top-of-book flow imbalance (delta bid/ask queues)",
        best_timeframes=["1m", "3m", "5m"],
        worst_timeframes=["1h", "4h", "1d"],
        market_regimes=[MarketRegime.TRENDING, MarketRegime.RANGING, MarketRegime.VOLATILE],
        reliability_score=0.72,
        lag="low",
        noise_sensitivity="high",
        primary_use="entry",
        requires=["orderbook"],
    ),
    "qi": IndicatorProfile(
        name="Queue Imbalance (QI)",
        description="Bid vs ask queue dominance at top-of-book",
        best_timeframes=["1m", "3m", "5m"],
        worst_timeframes=["1h", "4h", "1d"],
        market_regimes=[MarketRegime.TRENDING, MarketRegime.RANGING],
        reliability_score=0.69,
        lag="low",
        noise_sensitivity="high",
        primary_use="entry",
        requires=["orderbook"],
    ),
    "mlofi": IndicatorProfile(
        name="Multi-Level OFI (MLOFI)",
        description="Aggregated order flow imbalance across top-N levels",
        best_timeframes=["1m", "3m", "5m"],
        worst_timeframes=["1h", "4h", "1d"],
        market_regimes=[MarketRegime.TRENDING, MarketRegime.VOLATILE],
        reliability_score=0.73,
        lag="low",
        noise_sensitivity="medium",
        primary_use="entry",
        requires=["orderbook"],
    ),
    "volume_imbalance": IndicatorProfile(
        name="Depth Volume Imbalance",
        description="Bid vs ask depth imbalance across top-N levels",
        best_timeframes=["1m", "3m", "5m"],
        worst_timeframes=["1h", "4h", "1d"],
        market_regimes=[MarketRegime.TRENDING, MarketRegime.RANGING, MarketRegime.VOLATILE],
        reliability_score=0.71,
        lag="low",
        noise_sensitivity="medium",
        primary_use="entry",
        requires=["orderbook"],
    ),
    "vpin_proxy": IndicatorProfile(
        name="VPIN (Proxy)",
        description="Order-flow toxicity proxy (0-1) from recent trade imbalance",
        best_timeframes=["1m", "3m", "5m"],
        worst_timeframes=["1h", "4h", "1d"],
        market_regimes=[MarketRegime.VOLATILE, MarketRegime.TRENDING],
        reliability_score=0.64,
        lag="low",
        noise_sensitivity="medium",
        primary_use="risk",
        requires=["trades"],
    ),
    "spread": IndicatorProfile(
        name="Spread",
        description="Bid-ask spread (liquidity/fee proxy)",
        best_timeframes=["1m", "3m", "5m", "15m"],
        worst_timeframes=["1h", "4h", "1d"],
        market_regimes=[
            MarketRegime.TRENDING,
            MarketRegime.RANGING,
            MarketRegime.VOLATILE,
            MarketRegime.LOW_VOLATILITY,
        ],
        reliability_score=0.86,
        lag="low",
        noise_sensitivity="low",
        primary_use="risk",
        requires=["orderbook"],
    ),
    "funding_rate": IndicatorProfile(
        name="Funding Rate",
        description="Perpetual funding cost - sentiment indicator",
        best_timeframes=["15m", "1h", "4h", "1d"],
        worst_timeframes=["1m", "5m"],  # Changes every 8 hours
        market_regimes=[MarketRegime.TRENDING, MarketRegime.VOLATILE],
        reliability_score=0.81,
        lag="low",
        noise_sensitivity="low",
        primary_use="exit",  # Good for catching reversals
        requires=["funding"],
    ),
    "open_interest": IndicatorProfile(
        name="Open Interest",
        description="Total open contracts + OI change",
        best_timeframes=["15m", "1h", "4h"],
        worst_timeframes=["1m", "5m"],
        market_regimes=[MarketRegime.TRENDING],
        reliability_score=0.78,
        lag="low",
        noise_sensitivity="low",
        primary_use="trend",
        requires=["oi"],
    ),
    "liquidation_heatmap": IndicatorProfile(
        name="Liquidation Heatmap",
        description="Accumulated liquidation levels",
        best_timeframes=["5m", "15m", "1h"],
        worst_timeframes=["1m"],  # Not enough liquidation data
        market_regimes=[MarketRegime.VOLATILE, MarketRegime.TRENDING],
        reliability_score=0.83,
        lag="low",
        noise_sensitivity="low",
        primary_use="entry",
        requires=["liquidations"],
    ),
    # PATTERN INDICATORS
    "harmonic_patterns": IndicatorProfile(
        name="Harmonic Patterns",
        description="Gartley, Butterfly, Bat patterns",
        best_timeframes=["15m", "1h", "4h"],
        worst_timeframes=["1m", "5m"],
        market_regimes=[MarketRegime.RANGING],
        reliability_score=0.72,
        lag="low",
        noise_sensitivity="medium",
        primary_use="entry",
        requires=["ohlcv"],
    ),
    "wyckoff_phases": IndicatorProfile(
        name="Wyckoff Phases",
        description="Accumulation/Distribution phase detection",
        best_timeframes=["1h", "4h", "1d"],
        worst_timeframes=["1m", "5m", "15m"],
        market_regimes=[MarketRegime.RANGING, MarketRegime.LOW_VOLATILITY],
        reliability_score=0.75,
        lag="medium",
        noise_sensitivity="low",
        primary_use="trend",
        requires=["ohlcv", "volume"],
    ),
    "market_cipher": IndicatorProfile(
        name="Market Cipher B",
        description="Composite: WaveTrend + MFI + VWAP",
        best_timeframes=["5m", "15m", "1h"],
        worst_timeframes=["1m", "4h"],
        market_regimes=[MarketRegime.TRENDING, MarketRegime.VOLATILE],
        reliability_score=0.77,
        lag="medium",
        noise_sensitivity="medium",
        primary_use="entry",
        requires=["ohlcv"],
    ),
}


# ============================================================================
# TIMEFRAME-SPECIFIC RECOMMENDATIONS
# ============================================================================

TIMEFRAME_RECOMMENDATIONS: dict[str, dict[str, Any]] = {
    "1m": {
        "primary": [
            "obi",
            "cvd",
            "ofi",
            "mlofi",
            "volume_imbalance",
            "vwap",
            "hull_ma",
            "fisher_transform",
        ],
        "secondary": ["qi", "vpin_proxy", "spread", "atr", "stoch", "williams_r", "sar"],
        "avoid": [
            "ema_alignment",
            "ichimoku",
            "adx",
            "volume_profile",
            "wyckoff_phases",
            "funding_rate",
        ],
        "max_indicators": 6,
        "rationale": "1m requires ultra-fast, low-lag indicators. Trend indicators too slow. Focus on microstructure and quick momentum.",
    },
    "3m": {
        "primary": [
            "obi",
            "cvd",
            "ofi",
            "volume_imbalance",
            "vwap",
            "hull_ma",
            "fisher_transform",
            "rsi",
        ],
        "secondary": ["mlofi", "qi", "vpin_proxy", "spread", "macd", "atr", "stoch", "cci", "roc"],
        "avoid": ["ichimoku", "adx", "volume_profile", "wyckoff_phases"],
        "max_indicators": 6,
        "rationale": "3m balances speed and reliability. Can use slightly slower indicators than 1m.",
    },
    "5m": {
        "primary": [
            "supertrend",
            "vwap",
            "macd",
            "mfi",
            "cvd",
            "obi",
            "bollinger_squeeze",
            "keltner",
        ],
        "secondary": [
            "rsi",
            "cmf",
            "atr",
            "market_cipher",
            "chop",
            "stoch",
            "williams_r",
            "bollinger_bands",
        ],
        "avoid": ["ichimoku", "adx", "wyckoff_phases", "funding_rate", "donchian"],
        "max_indicators": 7,
        "rationale": "5m is the sweet spot for scalping. Good balance of trend, momentum and squeeze detection indicators.",
    },
    "15m": {
        "primary": [
            "supertrend",
            "ema_alignment",
            "vwap",
            "volume_profile",
            "macd",
            "adx",
            "chop",
            "donchian",
            "keltner",
        ],
        "secondary": [
            "rsi",
            "mfi",
            "cmf",
            "atr",
            "funding_rate",
            "open_interest",
            "bollinger_squeeze",
            "stoch",
            "cci",
            "obv",
            "bollinger_bands",
            "obi",
            "cvd",
        ],
        "avoid": ["hull_ma", "fisher_transform"],  # Too noisy for this timeframe
        "max_indicators": 8,
        "rationale": "15m allows proper trend analysis with CHOP for regime detection and Donchian for breakouts. Best timeframe for most indicators.",
    },
    "1h": {
        "primary": [
            "supertrend",
            "ema_alignment",
            "ichimoku",
            "volume_profile",
            "adx",
            "funding_rate",
            "open_interest",
            "chop",
            "donchian",
        ],
        "secondary": [
            "macd",
            "rsi",
            "mfi",
            "cmf",
            "atr",
            "liquidation_heatmap",
            "keltner",
            "bollinger_squeeze",
            "obv",
            "bollinger_bands",
            "garch_volatility",
        ],
        "avoid": [
            "hull_ma",
            "fisher_transform",
            "obi",
            "cvd",
        ],  # Microstructure irrelevant at this scale
        "max_indicators": 9,
        "rationale": "1h is ideal for swing trading. Trend indicators shine. CHOP and Donchian help identify range vs trend conditions. Can use macro indicators like funding.",
    },
    "4h": {
        "primary": [
            "ema_alignment",
            "ichimoku",
            "volume_profile",
            "adx",
            "funding_rate",
            "open_interest",
            "wyckoff_phases",
            "chop",
            "donchian",
        ],
        "secondary": [
            "supertrend",
            "macd",
            "rsi",
            "atr",
            "harmonic_patterns",
            "keltner",
            "obv",
            "bollinger_bands",
            "garch_volatility",
        ],
        "avoid": [
            "hull_ma",
            "vwap",
            "fisher_transform",
            "obi",
            "cvd",
            "bollinger_squeeze",
        ],  # Too noisy
        "max_indicators": 8,
        "rationale": "4h for position trading. Focus on major trends and macro structure. CHOP and Donchian help identify long-term ranges. Avoid short-term noise.",
    },
    "1d": {
        "primary": ["ema_alignment", "ichimoku", "wyckoff_phases", "volume_profile", "adx"],
        "secondary": ["rsi", "macd", "atr", "obv", "garch_volatility"],
        "avoid": [
            "hull_ma",
            "vwap",
            "fisher_transform",
            "obi",
            "cvd",
            "bollinger_squeeze",
            "cmf",
            "mfi",
        ],
        "max_indicators": 6,
        "rationale": "Daily for long-term investing. Only major trend indicators. Ignore short-term noise completely.",
    },
}


def _augment_available_indicators(available: list[str] | None) -> list[str]:
    """Add synthetic indicator labels for multi-part indicators."""
    if not available:
        return []
    available_set = set(available)

    def has_any(keys: list[str]) -> bool:
        return any(k in available_set for k in keys)

    # Multi-part indicator aliases
    if has_any(["ema_9", "ema_21", "ma_50", "ma_200"]):
        available_set.add("ema_alignment")
    if has_any(["ichimoku_tenkan", "ichimoku_kijun", "ichimoku_senkou_a", "ichimoku_senkou_b"]):
        available_set.add("ichimoku")
    if has_any(["donchian_upper", "donchian_lower", "donchian_middle"]):
        available_set.add("donchian")
    if has_any(["keltner_middle", "keltner_upper", "keltner_lower"]):
        available_set.add("keltner")
    if has_any(["bollinger_upper", "bollinger_lower", "upper_band", "lower_band"]):
        available_set.add("bollinger_bands")
    if has_any(["bollinger_squeeze", "bb_inside_kc"]):
        available_set.add("bollinger_squeeze")
    if has_any(["vpvr_poc", "vpvr_value_area_high", "vpvr_value_area_low"]):
        available_set.add("volume_profile")
    if has_any(["stoch_k", "stoch_d"]):
        available_set.add("stoch")
    if has_any(["sar", "parabolic_sar"]):
        available_set.add("sar")
    if "garch_volatility_forecast" in available_set:
        available_set.add("garch_volatility")
    if has_any(["macd_line", "macd_signal", "macd_histogram"]):
        available_set.add("macd")

    return list(available_set)


# ============================================================================
# INDICATOR SELECTOR CLASS
# ============================================================================


class TimeframeAwareIndicatorSelector:
    """Intelligently selects indicators based on timeframe and market conditions."""

    def __init__(self):
        self.profiles = INDICATOR_PROFILES
        self.recommendations = TIMEFRAME_RECOMMENDATIONS

    def detect_market_regime(
        self,
        adx: float | None = None,
        atr: float | None = None,
        price_range_24h: float | None = None,
        bollinger_bandwidth: float | None = None,
        chop: float | None = None,
        donchian_width_pct: float | None = None,
        bb_inside_kc: bool | None = None,
    ) -> MarketRegime:
        """
        Detect current market regime based on available indicators.

        Uses multiple inputs for robust detection:
        - CHOP: Choppiness Index (>=61.8 = choppy/ranging, <=38.2 = trending)
        - Donchian width: Narrow range = consolidation
        - BB inside KC: TTM Squeeze = low volatility, breakout imminent
        - ADX: Traditional trend strength
        - ATR: Volatility measure
        """
        # CHOP-based detection (most reliable for regime)
        if chop is not None:
            if chop >= 61.8:
                return MarketRegime.RANGING  # Choppy, range-bound
            elif chop <= 38.2:
                return MarketRegime.TRENDING  # Directional movement

        # Donchian width + Squeeze detection
        if donchian_width_pct is not None:
            if donchian_width_pct < 0.5:  # Very tight range
                if bb_inside_kc:
                    return MarketRegime.LOW_VOLATILITY  # TTM Squeeze, compression
                return MarketRegime.RANGING  # Tight range but no squeeze yet

        # BB inside Keltner (TTM Squeeze) - classic compression signal
        if bb_inside_kc:
            return MarketRegime.LOW_VOLATILITY

        # ADX-based detection (fallback)
        if adx is not None:
            if adx > 40:
                return MarketRegime.TRENDING
            elif adx < 15:
                return MarketRegime.RANGING

        # ATR-based detection
        if atr is not None and price_range_24h is not None:
            atr_pct = (atr / price_range_24h) * 100 if price_range_24h > 0 else 0
            if atr_pct > 5:
                return MarketRegime.VOLATILE
            elif atr_pct < 1:
                return MarketRegime.LOW_VOLATILITY

        # Bollinger bandwidth (fallback)
        if bollinger_bandwidth is not None:
            if bollinger_bandwidth < 0.1:
                return MarketRegime.LOW_VOLATILITY
            elif bollinger_bandwidth > 0.5:
                return MarketRegime.VOLATILE

        # Default
        return MarketRegime.TRENDING

    def get_indicator_suite(
        self,
        timeframe: str,
        available_data_feeds: list[str] = None,
        available_indicators: list[str] | None = None,
        market_regime: MarketRegime | None = None,
        **regime_indicators,
    ) -> IndicatorSuite:
        """
        Get optimal indicator suite for current trading context.

        Args:
            timeframe: Current timeframe ("1m", "5m", "15m", etc.)
            available_data_feeds: List of available data sources
            market_regime: Optional override for market regime
            **regime_indicators: Values for auto-detecting regime (adx, atr, etc.)

        Returns:
            IndicatorSuite with selected indicators
        """
        available_data_feeds = available_data_feeds or ["ohlcv"]

        # Get base recommendations for timeframe
        tf_rec = self.recommendations.get(timeframe, self.recommendations["5m"])

        # Auto-detect market regime if not provided
        if market_regime is None:
            market_regime = self.detect_market_regime(**regime_indicators)

        # Filter by available data feeds
        feed_indicators = [
            name
            for name, profile in self.profiles.items()
            if all(req in available_data_feeds for req in profile.requires)
        ]
        # Optionally restrict to indicators that are actually computed upstream
        if available_indicators is not None:
            available_set = set(available_indicators)
            available_indicators = [name for name in feed_indicators if name in available_set]
        else:
            available_indicators = feed_indicators

        # Filter by market regime suitability
        regime_suitable = [
            name
            for name in available_indicators
            if market_regime in self.profiles[name].market_regimes
        ]

        # Select primary indicators (prefer regime-suitable, but keep a minimum set even when
        # regime classification is noisy on very low timeframes).
        primary_target = max(1, tf_rec["max_indicators"] // 2 + 1)
        primary_candidates = [ind for ind in tf_rec["primary"] if ind in regime_suitable]
        if len(primary_candidates) < primary_target:
            fallback_primary = [
                ind
                for ind in tf_rec["primary"]
                if ind in available_indicators and ind not in primary_candidates
            ]
            primary_candidates.extend(fallback_primary)
        primary = primary_candidates[:primary_target]

        # Select secondary indicators (same strategy: fill remaining slots with available
        # timeframe-recommended indicators even if regime filter excluded them).
        remaining_slots = max(0, tf_rec["max_indicators"] - len(primary))
        secondary_candidates = [ind for ind in tf_rec["secondary"] if ind in regime_suitable]
        if len(secondary_candidates) < remaining_slots:
            fallback_secondary = [
                ind
                for ind in tf_rec["secondary"]
                if ind in available_indicators
                and ind not in secondary_candidates
                and ind not in primary
            ]
            secondary_candidates.extend(fallback_secondary)
        secondary = secondary_candidates[:remaining_slots]

        # Build exclusion list with reasons
        excluded = tf_rec["avoid"].copy()
        for ind in available_indicators:
            if ind not in primary and ind not in secondary:
                if ind not in excluded:
                    profile = self.profiles[ind]
                    if market_regime not in profile.market_regimes:
                        excluded.append(f"{ind} (unsuitable for {market_regime.value})")
                    elif timeframe in profile.worst_timeframes:
                        excluded.append(f"{ind} (poor on {timeframe})")

        # Calculate confidence boost
        confidence_boost = 0.0
        if len(primary) >= 3:
            confidence_boost = 0.05
        if market_regime in [MarketRegime.TRENDING, MarketRegime.RANGING]:
            confidence_boost += 0.05  # Clear regimes are easier to trade
        if len([ind for ind in primary if "vwap" in ind or "volume" in ind]) > 0:
            confidence_boost += 0.05  # Volume confirmation adds reliability

        # Build rationale
        rationale = f"""
Timeframe: {timeframe} | Market Regime: {market_regime.value}
Selection Strategy:
- Primary indicators ({len(primary)}): Optimized for {timeframe} speed/reliability
- Secondary indicators ({len(secondary)}): Confirming signals
- Excluded ({len(excluded)}): Unsuitable for current context
- Confidence boost: +{confidence_boost:.0%}

Key considerations for {timeframe}:
{tf_rec["rationale"]}
        """.strip()

        return IndicatorSuite(
            timeframe=timeframe,
            market_regime=market_regime,
            primary_indicators=primary,
            secondary_indicators=secondary,
            excluded_indicators=excluded,
            rationale=rationale,
            confidence_boost=confidence_boost,
        )

    def get_agent_prompt_addendum(self, suite: IndicatorSuite) -> str:
        """Generate prompt text to guide agents on which indicators to prioritize."""
        primary_details = []
        for ind in suite.primary_indicators:
            profile = self.profiles[ind]
            primary_details.append(f"  - {profile.name}: {profile.description}")

        secondary_details = []
        for ind in suite.secondary_indicators:
            profile = self.profiles[ind]
            secondary_details.append(f"  - {profile.name}: {profile.description}")

        prompt = f"""
=== TIMEFRAME-OPTIMIZED INDICATOR GUIDANCE ===
Current Context: {suite.timeframe} timeframe | {suite.market_regime.value.replace("_", " ").title()} market

PRIMARY INDICATORS (Weight: High - Use for main signals):
{chr(10).join(primary_details) if primary_details else "  - Using default indicator set"}

SECONDARY INDICATORS (Weight: Medium - Use for confirmation):
{chr(10).join(secondary_details) if secondary_details else "  - No secondary indicators selected"}

EXCLUDED FOR THIS CONTEXT (Do not rely on):
{chr(10).join([f"  - {ind}" for ind in suite.excluded_indicators[:5]]) if suite.excluded_indicators else "  - None excluded"}

CONFIDENCE ADJUSTMENT: +{suite.confidence_boost:.0%}

ANALYSIS GUIDANCE:
1. Prioritize signals from PRIMARY indicators
2. Use SECONDARY indicators for confirmation only
3. Ignore or weight down EXCLUDED indicators if mentioned
4. Consider {suite.market_regime.value.replace("_", " ")} characteristics in your analysis
5. Expected indicator reliability: {80 + int(suite.confidence_boost * 100)}%

{suite.rationale}
==============================================
"""
        return prompt


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================


def get_optimal_indicators(
    timeframe: str,
    available_feeds: list[str] | None = None,
    available_indicators: list[str] | None = None,
    **market_conditions,
) -> IndicatorSuite:
    """Convenience function to get optimal indicators for any timeframe."""
    selector = TimeframeAwareIndicatorSelector()
    augmented_indicators = None
    if available_indicators is not None:
        augmented_indicators = _augment_available_indicators(available_indicators)
    return selector.get_indicator_suite(
        timeframe=timeframe,
        available_data_feeds=available_feeds,
        available_indicators=augmented_indicators,
        **market_conditions,
    )


def format_indicator_guidance(suite: IndicatorSuite) -> str:
    """Format indicator guidance for agent prompts."""
    selector = TimeframeAwareIndicatorSelector()
    return selector.get_agent_prompt_addendum(suite)


# Example usage for different timeframes
if __name__ == "__main__":
    # Example: 5m scalping setup
    suite_5m = get_optimal_indicators(
        timeframe="5m",
        available_feeds=["ohlcv", "volume", "orderbook", "trades"],
        adx=25,
        atr=0.5,
    )
    print("5M SCALPING SETUP:")
    print(format_indicator_guidance(suite_5m))
    print("\n" + "=" * 60 + "\n")

    # Example: 1h swing setup
    suite_1h = get_optimal_indicators(
        timeframe="1h",
        available_feeds=["ohlcv", "volume", "funding", "oi"],
        adx=35,
    )
    print("1H SWING SETUP:")
    print(format_indicator_guidance(suite_1h))
