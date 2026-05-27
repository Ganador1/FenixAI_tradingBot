"""
Advanced Technical Indicators for Fenix Trading Bot.

Implements high-value indicators:
- VWAP (Volume-Weighted Average Price)
- Funding Rate (for crypto perpetuals)
- Open Interest
- Liquidation Heatmap
- Hull Moving Average
- Fisher Transform
- And more...
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class IndicatorValue:
    """Standardized indicator output."""

    value: float
    interpretation: str
    signal: str  # "bullish", "bearish", "neutral"
    confidence: float  # 0.0 to 1.0
    metadata: dict[str, Any]


class VWAPIndicator:
    """Volume-Weighted Average Price - institutional benchmark."""

    def __init__(self, anchor: str = "session"):
        """
        Args:
            anchor: "session" (daily reset), "week", "month", or "infinite"
        """
        self.anchor = anchor

    def calculate(
        self,
        highs: list[float],
        lows: list[float],
        closes: list[float],
        volumes: list[float],
        timestamps: list | None = None,
    ) -> IndicatorValue:
        """
        Calculate VWAP and generate signal.

        Returns:
            IndicatorValue with VWAP and price position analysis
        """
        if not highs or not closes or not volumes:
            return IndicatorValue(
                value=0.0,
                interpretation="Insufficient data",
                signal="neutral",
                confidence=0.0,
                metadata={},
            )

        # Calculate typical price
        typical_prices = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]

        # Calculate VWAP
        cumulative_tpv = np.cumsum([tp * v for tp, v in zip(typical_prices, volumes)])
        cumulative_volume = np.cumsum(volumes)

        vwap_values = cumulative_tpv / cumulative_volume
        current_vwap = vwap_values[-1]
        current_price = closes[-1]

        # Calculate bands (standard deviation)
        squared_diffs = [(tp - vwap) ** 2 for tp, vwap in zip(typical_prices, vwap_values)]
        variance = np.average(squared_diffs, weights=volumes)
        std_dev = np.sqrt(variance)

        upper_band = current_vwap + std_dev
        lower_band = current_vwap - std_dev
        upper_band_2 = current_vwap + 2 * std_dev
        lower_band_2 = current_vwap - 2 * std_dev

        # Determine position relative to VWAP
        price_position = (current_price - current_vwap) / current_vwap * 100

        # Generate interpretation
        if current_price > upper_band_2:
            interpretation = (
                f"Price {price_position:.2f}% above VWAP - extremely overextended, likely reversion"
            )
            signal = "bearish"
            confidence = 0.75
        elif current_price > upper_band:
            interpretation = (
                f"Price {price_position:.2f}% above VWAP - premium zone, caution on longs"
            )
            signal = "neutral_bearish"
            confidence = 0.60
        elif current_price < lower_band_2:
            interpretation = (
                f"Price {price_position:.2f}% below VWAP - extremely oversold, value zone"
            )
            signal = "bullish"
            confidence = 0.75
        elif current_price < lower_band:
            interpretation = (
                f"Price {price_position:.2f}% below VWAP - discount zone, consider longs"
            )
            signal = "neutral_bullish"
            confidence = 0.60
        else:
            interpretation = (
                f"Price near VWAP ({price_position:.2f}%) - fair value, wait for deviation"
            )
            signal = "neutral"
            confidence = 0.40

        return IndicatorValue(
            value=current_vwap,
            interpretation=interpretation,
            signal=signal,
            confidence=confidence,
            metadata={
                "vwap": current_vwap,
                "upper_band_1": upper_band,
                "upper_band_2": upper_band_2,
                "lower_band_1": lower_band,
                "lower_band_2": lower_band_2,
                "price_position_pct": price_position,
                "distance_from_vwap": current_price - current_vwap,
            },
        )


class FundingRateIndicator:
    """Crypto perpetual funding rate - sentiment predictor."""

    def __init__(self):
        self.threshold_extreme = 0.01  # 1% is extreme
        self.threshold_high = 0.005  # 0.5% is high

    def calculate(
        self,
        funding_rate: float,  # e.g., 0.0001 = 0.01%
        predicted_funding: float | None = None,
        premium_index: float | None = None,
    ) -> IndicatorValue:
        """
        Analyze funding rate for trading signals.

        Strategy:
        - Extreme positive funding (longs pay shorts) = potential top
        - Extreme negative funding (shorts pay longs) = potential bottom
        - Funding changes direction = sentiment shift
        """
        funding_pct = funding_rate * 100  # Convert to percentage

        # Determine signal
        if funding_pct > self.threshold_extreme * 100:  # > 1%
            interpretation = f"Extreme positive funding ({funding_pct:.3f}%) - longs are desperate, potential top forming"
            signal = "bearish"
            confidence = 0.80
        elif funding_pct > self.threshold_high * 100:  # > 0.5%
            interpretation = (
                f"High positive funding ({funding_pct:.3f}%) - bullish sentiment extreme, caution"
            )
            signal = "neutral_bearish"
            confidence = 0.65
        elif funding_pct < -self.threshold_extreme * 100:  # < -1%
            interpretation = f"Extreme negative funding ({funding_pct:.3f}%) - shorts are desperate, potential bottom forming"
            signal = "bullish"
            confidence = 0.80
        elif funding_pct < -self.threshold_high * 100:  # < -0.5%
            interpretation = f"High negative funding ({funding_pct:.3f}%) - bearish sentiment extreme, value opportunity"
            signal = "neutral_bullish"
            confidence = 0.65
        else:
            interpretation = f"Normal funding ({funding_pct:.4f}%) - balanced sentiment"
            signal = "neutral"
            confidence = 0.30

        metadata = {
            "funding_rate_pct": funding_pct,
            "threshold_extreme": self.threshold_extreme * 100,
            "threshold_high": self.threshold_high * 100,
        }

        if predicted_funding is not None:
            metadata["predicted_funding_pct"] = predicted_funding * 100
            metadata["funding_direction"] = (
                "increasing" if predicted_funding > funding_rate else "decreasing"
            )

        if premium_index is not None:
            metadata["premium_index"] = premium_index

        return IndicatorValue(
            value=funding_rate,
            interpretation=interpretation,
            signal=signal,
            confidence=confidence,
            metadata=metadata,
        )


class OpenInterestIndicator:
    """Open Interest analysis - trend strength confirmation."""

    def calculate(
        self,
        oi_current: float,
        oi_previous: float | None = None,
        price_change_pct: float | None = None,
        volume_change_pct: float | None = None,
    ) -> IndicatorValue:
        """
        Analyze Open Interest for trend confirmation.

        Key combinations:
        - Price ↑ + OI ↑ = Strong uptrend (new money entering)
        - Price ↑ + OI ↓ = Weak uptrend (short covering)
        - Price ↓ + OI ↑ = Strong downtrend (new shorts)
        - Price ↓ + OI ↓ = Weak downtrend (longs giving up)
        """
        if oi_previous is None or price_change_pct is None:
            return IndicatorValue(
                value=oi_current,
                interpretation="Insufficient data for OI analysis",
                signal="neutral",
                confidence=0.0,
                metadata={"oi_current": oi_current},
            )

        oi_change_pct = ((oi_current - oi_previous) / oi_previous) * 100

        # Determine scenario
        price_up = price_change_pct > 0
        oi_up = oi_change_pct > 0

        if price_up and oi_up:
            interpretation = f"Price +{price_change_pct:.2f}% with OI +{oi_change_pct:.2f}% - Strong uptrend, new money entering"
            signal = "bullish"
            confidence = 0.75
        elif price_up and not oi_up:
            interpretation = f"Price +{price_change_pct:.2f}% but OI {oi_change_pct:.2f}% - Weak uptrend, likely short covering"
            signal = "neutral_bearish"
            confidence = 0.55
        elif not price_up and oi_up:
            interpretation = f"Price {price_change_pct:.2f}% with OI +{oi_change_pct:.2f}% - Strong downtrend, aggressive shorting"
            signal = "bearish"
            confidence = 0.75
        else:  # not price_up and not oi_up
            interpretation = f"Price {price_change_pct:.2f}% and OI {oi_change_pct:.2f}% - Weak downtrend, longs capitulating"
            signal = "neutral_bullish"
            confidence = 0.55

        return IndicatorValue(
            value=oi_current,
            interpretation=interpretation,
            signal=signal,
            confidence=confidence,
            metadata={
                "oi_current": oi_current,
                "oi_previous": oi_previous,
                "oi_change_pct": oi_change_pct,
                "price_change_pct": price_change_pct,
                "scenario": "strong_trend" if (price_up == oi_up) else "weak_trend",
            },
        )


class HullMovingAverage:
    """Hull Moving Average - ultra-responsive with minimal lag."""

    def __init__(self, period: int = 16):
        self.period = period

    def calculate(self, closes: list[float]) -> IndicatorValue:
        """Calculate HMA and generate signal."""
        if len(closes) < self.period:
            return IndicatorValue(
                value=closes[-1] if closes else 0,
                interpretation="Insufficient data",
                signal="neutral",
                confidence=0.0,
                metadata={},
            )

        series = pd.Series(closes)

        # HMA calculation
        half_period = self.period // 2
        sqrt_period = int(np.sqrt(self.period))

        wma_half = series.rolling(window=half_period).mean()
        wma_full = series.rolling(window=self.period).mean()

        raw_hma = 2 * wma_half - wma_full
        hma = raw_hma.rolling(window=sqrt_period).mean()

        current_hma = hma.iloc[-1]
        current_price = closes[-1]

        # Trend direction
        if len(hma) >= 3:
            hma_slope = hma.iloc[-1] - hma.iloc[-3]
        else:
            hma_slope = 0

        # Signal generation
        if current_price > current_hma and hma_slope > 0:
            interpretation = "Price above rising HMA - bullish momentum"
            signal = "bullish"
            confidence = 0.70
        elif current_price < current_hma and hma_slope < 0:
            interpretation = "Price below falling HMA - bearish momentum"
            signal = "bearish"
            confidence = 0.70
        elif current_price > current_hma:
            interpretation = "Price above HMA but flattening - caution on bullish bias"
            signal = "neutral_bullish"
            confidence = 0.50
        else:
            interpretation = "Price below HMA but flattening - caution on bearish bias"
            signal = "neutral_bearish"
            confidence = 0.50

        return IndicatorValue(
            value=current_hma,
            interpretation=interpretation,
            signal=signal,
            confidence=confidence,
            metadata={
                "hma": current_hma,
                "price": current_price,
                "slope": hma_slope,
                "distance_pct": (current_price - current_hma) / current_hma * 100,
            },
        )


class FisherTransform:
    """Fisher Transform - early reversal detection via Gaussian transformation."""

    def __init__(self, period: int = 10):
        self.period = period

    def calculate(self, highs: list[float], lows: list[float]) -> IndicatorValue:
        """
        Calculate Fisher Transform.

        Values > 2.0 or < -2.0 indicate extreme conditions (reversal likely)
        Crosses of the signal line indicate entry points
        """
        if len(highs) < self.period or len(lows) < self.period:
            return IndicatorValue(
                value=0,
                interpretation="Insufficient data",
                signal="neutral",
                confidence=0.0,
                metadata={},
            )

        # Calculate midprice
        midprices = [(h + l) / 2 for h, l in zip(highs, lows)]

        # Get highest high and lowest low of period
        highest_high = max(highs[-self.period :])
        lowest_low = min(lows[-self.period :])

        if highest_high == lowest_low:
            return IndicatorValue(0, "No price movement", "neutral", 0.0, {})

        # Calculate value
        current_mid = midprices[-1]
        value = 0.33 * 2 * ((current_mid - lowest_low) / (highest_high - lowest_low) - 0.5)

        # Bound value
        value = max(min(value, 0.999), -0.999)

        # Fisher transform
        fisher = 0.5 * np.log((1 + value) / (1 - value))

        # Signal logic
        if fisher > 2.0:
            interpretation = f"Fisher {fisher:.2f} - Extremely overbought, reversal likely"
            signal = "bearish"
            confidence = 0.80
        elif fisher > 1.5:
            interpretation = f"Fisher {fisher:.2f} - Overbought zone, caution on longs"
            signal = "neutral_bearish"
            confidence = 0.60
        elif fisher < -2.0:
            interpretation = f"Fisher {fisher:.2f} - Extremely oversold, reversal likely"
            signal = "bullish"
            confidence = 0.80
        elif fisher < -1.5:
            interpretation = f"Fisher {fisher:.2f} - Oversold zone, value opportunity"
            signal = "neutral_bullish"
            confidence = 0.60
        else:
            interpretation = f"Fisher {fisher:.2f} - Neutral zone, no extreme conditions"
            signal = "neutral"
            confidence = 0.30

        return IndicatorValue(
            value=fisher,
            interpretation=interpretation,
            signal=signal,
            confidence=confidence,
            metadata={
                "fisher": fisher,
                "trigger_level": 2.0,
                "value_raw": value,
            },
        )


class LiquidationHeatmapIndicator:
    """Liquidation level analysis - predicts price magnet zones."""

    def calculate(
        self,
        liquidation_levels: list[tuple[float, float]],  # (price, liquidation_amount)
        current_price: float,
        lookback_periods: int = 3,
    ) -> IndicatorValue:
        """
        Analyze liquidation clusters to predict price direction.

        Large liquidation clusters act as "magnets" - price tends to move there
        to liquidate positions before reversing.
        """
        if not liquidation_levels:
            return IndicatorValue(
                value=0,
                interpretation="No liquidation data available",
                signal="neutral",
                confidence=0.0,
                metadata={},
            )

        # Find largest liquidation clusters above and below current price
        long_liquidations = [(p, a) for p, a in liquidation_levels if p < current_price]
        short_liquidations = [(p, a) for p, a in liquidation_levels if p > current_price]

        total_long_liq = sum(a for _, a in long_liquidations) if long_liquidations else 0
        total_short_liq = sum(a for _, a in short_liquidations) if short_liquidations else 0

        # Find closest significant clusters
        significant_threshold = (
            max(total_long_liq, total_short_liq) * 0.2
            if (total_long_liq + total_short_liq) > 0
            else 0
        )

        closest_long_cluster = None
        closest_short_cluster = None

        if long_liquidations:
            sorted_long = sorted(long_liquidations, key=lambda x: current_price - x[0])
            closest_long_cluster = next(
                (item for item in sorted_long if item[1] > significant_threshold), None
            )

        if short_liquidations:
            sorted_short = sorted(short_liquidations, key=lambda x: x[0] - current_price)
            closest_short_cluster = next(
                (item for item in sorted_short if item[1] > significant_threshold), None
            )

        # Determine magnet direction
        long_distance = (
            current_price - closest_long_cluster[0] if closest_long_cluster else float("inf")
        )
        short_distance = (
            closest_short_cluster[0] - current_price if closest_short_cluster else float("inf")
        )

        if total_long_liq > total_short_liq * 2 and closest_long_cluster:
            interpretation = f"Large long liquidation cluster at {closest_long_cluster[0]:.2f} (${closest_long_cluster[1]:,.0f}) - price may drop to liquidate"
            signal = "bearish"
            confidence = min(0.85, 0.60 + (total_long_liq / 1000000))  # Scale with size
        elif total_short_liq > total_long_liq * 2 and closest_short_cluster:
            interpretation = f"Large short liquidation cluster at {closest_short_cluster[0]:.2f} (${closest_short_cluster[1]:,.0f}) - price may rise to liquidate"
            signal = "bullish"
            confidence = min(0.85, 0.60 + (total_short_liq / 1000000))
        elif closest_long_cluster and closest_short_cluster:
            if long_distance < short_distance:
                interpretation = f"Closer long liq cluster at {closest_long_cluster[0]:.2f}, but shorts also exposed at {closest_short_cluster[0]:.2f}"
                signal = "neutral_bearish"
                confidence = 0.50
            else:
                interpretation = f"Closer short liq cluster at {closest_short_cluster[0]:.2f}, but longs also exposed at {closest_long_cluster[0]:.2f}"
                signal = "neutral_bullish"
                confidence = 0.50
        else:
            interpretation = "No significant liquidation clusters nearby"
            signal = "neutral"
            confidence = 0.20

        return IndicatorValue(
            value=max(total_long_liq, total_short_liq),
            interpretation=interpretation,
            signal=signal,
            confidence=confidence,
            metadata={
                "total_long_liquidations": total_long_liq,
                "total_short_liquidations": total_short_liq,
                "closest_long_cluster": closest_long_cluster,
                "closest_short_cluster": closest_short_cluster,
                "current_price": current_price,
            },
        )


# ============================================================================
# FACTORY AND CONVENIENCE FUNCTIONS
# ============================================================================


def calculate_all_advanced_indicators(
    ohlcv_data: dict[str, list[float]],
    funding_rate: float | None = None,
    open_interest: dict | None = None,
    liquidation_data: list | None = None,
) -> dict[str, IndicatorValue]:
    """
    Calculate all available advanced indicators.

    Args:
        ohlcv_data: Dict with keys 'highs', 'lows', 'closes', 'volumes'
        funding_rate: Current funding rate (optional)
        open_interest: Dict with 'current', 'previous', 'price_change' (optional)
        liquidation_data: List of (price, amount) tuples (optional)

    Returns:
        Dict mapping indicator names to IndicatorValue objects
    """
    results = {}

    highs = ohlcv_data.get("highs", [])
    lows = ohlcv_data.get("lows", [])
    closes = ohlcv_data.get("closes", [])
    volumes = ohlcv_data.get("volumes", [])

    # VWAP
    if all([highs, lows, closes, volumes]):
        vwap = VWAPIndicator()
        results["vwap"] = vwap.calculate(highs, lows, closes, volumes)

    # Hull MA
    if closes:
        hma = HullMovingAverage()
        results["hull_ma"] = hma.calculate(closes)

    # Fisher Transform
    if highs and lows:
        fisher = FisherTransform()
        results["fisher_transform"] = fisher.calculate(highs, lows)

    # Funding Rate
    if funding_rate is not None:
        funding = FundingRateIndicator()
        results["funding_rate"] = funding.calculate(funding_rate)

    # Open Interest
    if open_interest:
        oi = OpenInterestIndicator()
        results["open_interest"] = oi.calculate(
            oi_current=open_interest.get("current", 0),
            oi_previous=open_interest.get("previous"),
            price_change_pct=open_interest.get("price_change"),
        )

    # Liquidation Heatmap
    if liquidation_data and closes:
        liq = LiquidationHeatmapIndicator()
        results["liquidation_heatmap"] = liq.calculate(
            liquidation_levels=liquidation_data,
            current_price=closes[-1],
        )

    return results
