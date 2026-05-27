"""
Dynamic Stop-Loss Calculator based on ATR (Average True Range).

Implements adaptive stop-loss and take-profit levels that adjust
to market volatility and trading fees.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class StopLossConfig:
    """Configuration for dynamic stop-loss calculation.

    NOTE: max_risk_per_trade_pct is a PERCENTAGE (2.0 = 2%), used as pct/100
    in calculations. This is different from config_loader's max_risk_per_trade
    which is a FRACTION (0.02 = 2%). The conversion happens in
    DynamicStopLossCalculator._calculate().

    CHANGED 2026-02-14: Reduced min_profit_pct thresholds for less aggressive vetoes.
    - Low vol: 0.2% -> 0.10%
    - Moderate vol: 0.35% -> 0.15%
    - High vol: 0.5% -> 0.20%
    Reasoning: Previous values caused ~50% veto rate on potentially profitable trades.
    """

    atr_multiplier_sl: float = 1.5  # Multiplier for stop-loss distance
    atr_multiplier_tp: float = 3.0  # Multiplier for take-profit (RR ratio)
    min_risk_reward_ratio: float = (
        1.5  # CHANGED 2026-02-17: was 2.0, now 1.5 — less vetoes on marginal RR
    )
    max_risk_per_trade_pct: float = 2.0  # 2.0 means 2% of balance (divided by 100 in calc)
    trading_fee_pct: float = 0.1  # 0.1% per trade
    min_profit_pct: float = 0.15  # Minimum profit after fees (reduced from 0.5)
    min_profit_pct_low_vol: float = 0.10  # Reduced from 0.2
    min_profit_pct_moderate_vol: float = 0.15  # Reduced from 0.35
    min_profit_pct_high_vol: float = 0.20  # Reduced from 0.5

    # Volatility-based adjustments
    low_volatility_threshold: float = 0.5  # ATR/balance ratio
    high_volatility_threshold: float = 2.0

    # Multipliers for different volatility regimes
    conservative_sl_multiplier: float = 1.5
    moderate_sl_multiplier: float = 2.0
    aggressive_sl_multiplier: float = 2.5


@dataclass
class DynamicRiskLevels:
    """Calculated risk levels for a trade with scaled TP/SL."""

    stop_loss: float
    take_profit: float
    position_size: float
    max_loss_usd: float
    risk_reward_ratio: float
    atr_used: float
    sl_distance_pct: float
    tp_distance_pct: float
    fees_usd: float
    net_profit_potential: float
    min_profit_pct_used: float
    volatility_regime: str  # "low", "moderate", "high"
    recommended_verdict: str  # "APPROVE", "APPROVE_REDUCED", "VETO"
    warnings: list[str]
    # NUEVO: TP/SL escalonados
    tp1_price: float = 0.0  # Primer nivel de take profit (30% de posición)
    tp2_price: float = 0.0  # Segundo nivel de take profit (40% de posición)
    tp3_price: float = 0.0  # Tercer nivel de take profit (30% de posición)
    tp1_size_pct: float = 0.30  # 30% de la posición
    tp2_size_pct: float = 0.40  # 40% de la posición
    tp3_size_pct: float = 0.30  # 30% de la posición
    trailing_stop_active: bool = False  # Si el trailing stop está activo
    trailing_stop_price: float = 0.0  # Precio actual del trailing stop
    breakeven_triggered: bool = False  # Si se movió el SL a breakeven

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "stop_loss": round(self.stop_loss, 8),
            "take_profit": round(self.take_profit, 8),
            "position_size": round(self.position_size, 8),
            "max_loss_usd": round(self.max_loss_usd, 2),
            "risk_reward_ratio": round(self.risk_reward_ratio, 2),
            "atr_used": round(self.atr_used, 8),
            "sl_distance_pct": round(self.sl_distance_pct, 4),
            "tp_distance_pct": round(self.tp_distance_pct, 4),
            "fees_usd": round(self.fees_usd, 2),
            "net_profit_potential": round(self.net_profit_potential, 2),
            "min_profit_pct_used": round(self.min_profit_pct_used, 4),
            "volatility_regime": self.volatility_regime,
            "recommended_verdict": self.recommended_verdict,
            "warnings": self.warnings,
            # NUEVO: TP escalonados
            "tp1_price": round(self.tp1_price, 8),
            "tp2_price": round(self.tp2_price, 8),
            "tp3_price": round(self.tp3_price, 8),
            "tp1_size_pct": self.tp1_size_pct,
            "tp2_size_pct": self.tp2_size_pct,
            "tp3_size_pct": self.tp3_size_pct,
            "trailing_stop_active": self.trailing_stop_active,
            "trailing_stop_price": round(self.trailing_stop_price, 8),
            "breakeven_triggered": self.breakeven_triggered,
        }


class DynamicStopLossCalculator:
    """Calculates dynamic stop-loss based on ATR and market conditions."""

    def __init__(self, config: StopLossConfig | None = None):
        self.config = config or StopLossConfig()

    def _get_timeframe_floor_pcts(self, timeframe: str) -> tuple[float, float]:
        """
        Return minimum SL/TP distance floors by timeframe.

        Shorter timeframes need wider floors to absorb fees, spread and
        trigger noise. The returned values are fractions of entry price,
        so 0.003 means 0.3%.
        """
        tf = str(timeframe or "").strip().lower()
        if tf in {"1m", "2m"}:
            return 0.0030, 0.0050
        if tf == "3m":
            return 0.0025, 0.0045
        if tf == "5m":
            return 0.0020, 0.0040
        if tf == "15m":
            return 0.0015, 0.0030
        if tf == "30m":
            return 0.0012, 0.0025
        if tf in {"1h", "2h"}:
            return 0.0010, 0.0020
        return 0.0010, 0.0020

    def _get_min_profit_pct(self, volatility_regime: str) -> float:
        """
        Resolve adaptive minimum net profit target (after fees).
        Uses volatility regime defaults and optional env overrides.
        """
        dynamic_enabled = os.getenv("FENIX_DYNAMIC_MIN_PROFIT", "1") == "1"
        if not dynamic_enabled:
            try:
                return float(os.getenv("FENIX_MIN_PROFIT_PCT", str(self.config.min_profit_pct)))
            except Exception:
                return float(self.config.min_profit_pct)

        by_regime = {
            "low": self.config.min_profit_pct_low_vol,
            "moderate": self.config.min_profit_pct_moderate_vol,
            "high": self.config.min_profit_pct_high_vol,
        }
        base_value = float(by_regime.get(volatility_regime, self.config.min_profit_pct))

        env_key = f"FENIX_MIN_PROFIT_PCT_{volatility_regime.upper()}"
        if (raw := os.getenv(env_key)) is not None:
            try:
                return max(0.0, float(raw))
            except Exception:
                pass

        if (raw_global := os.getenv("FENIX_MIN_PROFIT_PCT")) is not None:
            try:
                return max(0.0, float(raw_global))
            except Exception:
                pass

        return max(0.0, base_value)

    def calculate(
        self,
        entry_price: float,
        atr: float,
        balance_usd: float,
        decision: str,  # "BUY" or "SELL"
        current_volatility: str = "MEDIUM",
        open_positions: int = 0,
        symbol: str = "",
        timeframe: str = "",
    ) -> DynamicRiskLevels:
        """
        Calculate dynamic stop-loss and take-profit levels.

        Args:
            entry_price: Trade entry price
            atr: Average True Range value
            balance_usd: Account balance in USD
            decision: "BUY" or "SELL"
            current_volatility: "LOW", "MEDIUM", or "HIGH"
            open_positions: Number of current open positions
            symbol: Trading pair symbol

        Returns:
            DynamicRiskLevels with calculated values
        """
        warnings = []

        # Validate inputs
        if atr <= 0:
            warnings.append("ATR is zero or negative, using default 1% of price")
            atr = entry_price * 0.01

        if entry_price <= 0:
            raise ValueError(f"Invalid entry price: {entry_price}")

        # Determine volatility regime
        atr_pct = atr / entry_price * 100
        if atr_pct < 0.5:
            volatility_regime = "low"
            sl_multiplier = self.config.conservative_sl_multiplier
        elif atr_pct < 1.5:
            volatility_regime = "moderate"
            sl_multiplier = self.config.moderate_sl_multiplier
        else:
            volatility_regime = "high"
            sl_multiplier = self.config.aggressive_sl_multiplier
            warnings.append(f"High volatility detected ({atr_pct:.2f}%), using wider stops")

        # Calculate distances
        sl_distance = atr * sl_multiplier
        min_sl_pct, min_tp_pct = self._get_timeframe_floor_pcts(timeframe)
        min_sl_distance = entry_price * min_sl_pct
        min_tp_distance = entry_price * min_tp_pct
        sl_distance = max(sl_distance, min_sl_distance)
        tp_distance = max(sl_distance * self.config.atr_multiplier_tp, min_tp_distance)

        # Calculate fees (round trip: entry + exit)
        fee_buffer = entry_price * (self.config.trading_fee_pct * 2 / 100)

        # Calculate stop-loss and take-profit prices
        if decision == "BUY":
            stop_loss = entry_price - sl_distance - fee_buffer
            take_profit = entry_price + tp_distance + fee_buffer
        elif decision == "SELL":
            stop_loss = entry_price + sl_distance + fee_buffer
            take_profit = entry_price - tp_distance - fee_buffer
        else:
            raise ValueError(f"Invalid decision: {decision}, must be BUY or SELL")

        # Calculate distances in percentage
        sl_distance_pct = abs((stop_loss - entry_price) / entry_price * 100)
        tp_distance_pct = abs((take_profit - entry_price) / entry_price * 100)

        # Calculate risk/reward ratio
        risk_reward_ratio = tp_distance_pct / sl_distance_pct if sl_distance_pct > 0 else 0

        # Calculate maximum loss allowed (2% of balance)
        max_loss_usd = balance_usd * (self.config.max_risk_per_trade_pct / 100)

        # Calculate position size based on risk
        if sl_distance_pct > 0:
            position_size = max_loss_usd / (entry_price * sl_distance_pct / 100)
        else:
            position_size = 0
            warnings.append("Stop-loss distance is zero, cannot calculate position size")

        # Calculate fees in USD
        # Fees are calculated on both entry and exit notionals
        fees_usd = position_size * entry_price * (self.config.trading_fee_pct * 2 / 100)

        # Calculate net profit potential (after fees)
        # Since take_profit price ALREADY includes fee_buffer, the expected net return
        # is simply the target distance multiplied by the position size.
        net_profit_potential = position_size * tp_distance

        # NUEVO: Calcular niveles escalonados de take profit
        # TP1: 1.0x ATR distance (30% de posición)
        # TP2: 2.0x ATR distance (40% de posición)
        # TP3: 3.0x ATR distance (30% de posición)
        tp1_distance = max(atr * 1.0, entry_price * (min_tp_pct * 0.40))
        tp2_distance = max(atr * 2.0, entry_price * (min_tp_pct * 0.70))
        tp3_distance = max(atr * 3.0, entry_price * min_tp_pct)

        if decision == "BUY":
            tp1_price = entry_price + tp1_distance + fee_buffer
            tp2_price = entry_price + tp2_distance + fee_buffer
            tp3_price = entry_price + tp3_distance + fee_buffer
            # Trailing stop inicial (no activo)
            trailing_stop_price = entry_price - sl_distance * 0.5  # 50% del SL inicial
        else:  # SELL
            tp1_price = entry_price - tp1_distance - fee_buffer
            tp2_price = entry_price - tp2_distance - fee_buffer
            tp3_price = entry_price - tp3_distance - fee_buffer
            # Trailing stop inicial (no activo)
            trailing_stop_price = entry_price + sl_distance * 0.5  # 50% del SL inicial

        # Check minimum profit requirement (adaptive by volatility regime).
        min_profit_pct_used = self._get_min_profit_pct(volatility_regime)
        min_profit_required = position_size * entry_price * (min_profit_pct_used / 100)
        if net_profit_potential < min_profit_required:
            warnings.append(
                f"Net profit (${net_profit_potential:.2f}) below minimum required "
                f"(${min_profit_required:.2f}, {min_profit_pct_used:.2f}% target)"
            )

        # Determine verdict
        recommended_verdict = self._determine_verdict(
            risk_reward_ratio=risk_reward_ratio,
            sl_distance_pct=sl_distance_pct,
            open_positions=open_positions,
            warnings=warnings,
        )

        # Additional warnings for extreme conditions
        if sl_distance_pct > 5:
            warnings.append(f"Wide stop-loss ({sl_distance_pct:.2f}%), consider reducing position")

        if risk_reward_ratio < self.config.min_risk_reward_ratio:
            warnings.append(
                f"Risk/Reward ratio ({risk_reward_ratio:.2f}) below minimum "
                f"({self.config.min_risk_reward_ratio})"
            )

        return DynamicRiskLevels(
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_size=position_size,
            max_loss_usd=max_loss_usd,
            risk_reward_ratio=risk_reward_ratio,
            atr_used=atr,
            sl_distance_pct=sl_distance_pct,
            tp_distance_pct=tp_distance_pct,
            fees_usd=fees_usd,
            net_profit_potential=net_profit_potential,
            min_profit_pct_used=min_profit_pct_used,
            volatility_regime=volatility_regime,
            recommended_verdict=recommended_verdict,
            warnings=warnings,
            # NUEVO: TP escalonados
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            tp3_price=tp3_price,
            tp1_size_pct=0.30,
            tp2_size_pct=0.40,
            tp3_size_pct=0.30,
            trailing_stop_active=False,
            trailing_stop_price=trailing_stop_price,
            breakeven_triggered=False,
        )

    def _determine_verdict(
        self,
        risk_reward_ratio: float,
        sl_distance_pct: float,
        open_positions: int,
        warnings: list[str],
    ) -> str:
        """Determine the recommended verdict based on risk metrics.

        CHANGED 2026-02-14: Reduced veto aggressiveness.
        - VETO only for extreme conditions (RR < 0.8, SL > 10%, 4+ positions)
        - Most marginal trades now get APPROVE_REDUCED instead of VETO
        - This allows more trades while still managing risk via position sizing
        """
        # VETO conditions (only extreme cases)
        if risk_reward_ratio < 0.8:  # Was 1.0
            return "VETO"

        if sl_distance_pct > 10:  # Was 8% - allow wider stops for crypto volatility
            return "VETO"

        if open_positions >= 4:  # Was 3 - allow one more concurrent position
            return "VETO"

        # APPROVE_REDUCED conditions
        if risk_reward_ratio < self.config.min_risk_reward_ratio:
            return "APPROVE_REDUCED"

        if sl_distance_pct > 4:  # Was 3%
            return "APPROVE_REDUCED"

        if len(warnings) >= 3:  # Was 2 - require more warnings
            return "APPROVE_REDUCED"

        # Otherwise APPROVE
        return "APPROVE"

    def get_volatility_adjustment(self, atr: float, price: float) -> dict:
        """
        Get volatility-based adjustments for the trade.

        Returns:
            Dict with volatility assessment and recommended adjustments
        """
        atr_pct = (atr / price) * 100

        if atr_pct < 0.3:
            return {
                "regime": "very_low",
                "adjustment": "tighten_stops",
                "sl_multiplier": 1.0,
                "description": "Very low volatility - use tighter stops",
            }
        elif atr_pct < 0.7:
            return {
                "regime": "low",
                "adjustment": "standard",
                "sl_multiplier": 1.5,
                "description": "Low volatility - standard ATR multiplier",
            }
        elif atr_pct < 1.5:
            return {
                "regime": "moderate",
                "adjustment": "widen_stops",
            }

    def update_trailing_stop(
        self,
        levels: DynamicRiskLevels,
        current_price: float,
        entry_price: float,
        decision: str,
        atr: float,
    ) -> DynamicRiskLevels:
        """
        Update trailing stop based on current price movement.

        Trailing stop activates after price moves 1.0x ATR in favor.
        Then it trails at 1.5x ATR behind the price.

        Args:
            levels: Current DynamicRiskLevels
            current_price: Current market price
            entry_price: Original entry price
            decision: "BUY" or "SELL"
            atr: Current ATR value

        Returns:
            Updated DynamicRiskLevels with new trailing stop price
        """
        if decision == "BUY":
            # For longs: trailing stop follows price up
            price_movement = current_price - entry_price
            activation_distance = atr * 1.0  # Activate after 1x ATR profit

            if price_movement >= activation_distance:
                # Trailing stop is 1.5x ATR below current price
                new_trailing_stop = current_price - (atr * 1.5)

                # Only move stop up, never down
                if new_trailing_stop > levels.trailing_stop_price:
                    levels.trailing_stop_active = True
                    levels.trailing_stop_price = new_trailing_stop

                    # Move to breakeven after 2x ATR profit
                    if price_movement >= (atr * 2.0) and not levels.breakeven_triggered:
                        levels.breakeven_triggered = True
                        levels.warnings.append(
                            f"Trailing stop moved to breakeven+ (price: {current_price:.2f})"
                        )

        else:  # SELL
            # For shorts: trailing stop follows price down
            price_movement = entry_price - current_price
            activation_distance = atr * 1.0

            if price_movement >= activation_distance:
                # Trailing stop is 1.5x ATR above current price
                new_trailing_stop = current_price + (atr * 1.5)

                # Only move stop down, never up
                if new_trailing_stop < levels.trailing_stop_price:
                    levels.trailing_stop_active = True
                    levels.trailing_stop_price = new_trailing_stop

                    # Move to breakeven after 2x ATR profit
                    if price_movement >= (atr * 2.0) and not levels.breakeven_triggered:
                        levels.breakeven_triggered = True
                        levels.warnings.append(
                            f"Trailing stop moved to breakeven+ (price: {current_price:.2f})"
                        )

        return levels

    def check_partial_close(
        self,
        levels: DynamicRiskLevels,
        current_price: float,
        entry_price: float,
        decision: str,
    ) -> tuple[bool, float, float]:
        """
        Check if we should partially close position at TP1, TP2, or TP3.

        Args:
            levels: Current DynamicRiskLevels with TP levels
            current_price: Current market price
            entry_price: Original entry price
            decision: "BUY" or "SELL"

        Returns:
            Tuple of (should_close, close_size_pct, tp_level)
            should_close: True if we should close portion
            close_size_pct: Percentage of position to close (0.0-1.0)
            tp_level: Which TP level triggered (1, 2, or 3)
        """
        if decision == "BUY":
            if current_price >= levels.tp3_price:
                return (True, levels.tp3_size_pct, 3)
            elif current_price >= levels.tp2_price:
                return (True, levels.tp2_size_pct, 2)
            elif current_price >= levels.tp1_price:
                return (True, levels.tp1_size_pct, 1)
        else:  # SELL
            if current_price <= levels.tp3_price:
                return (True, levels.tp3_size_pct, 3)
            elif current_price <= levels.tp2_price:
                return (True, levels.tp2_size_pct, 2)
            elif current_price <= levels.tp1_price:
                return (True, levels.tp1_size_pct, 1)

        return (False, 0.0, 0)

    def should_close_position(
        self,
        levels: DynamicRiskLevels,
        current_price: float,
        decision: str,
    ) -> tuple[bool, str]:
        """
        Check if position should be closed (SL hit, trailing stop, or TP hit).

        Args:
            levels: Current DynamicRiskLevels
            current_price: Current market price
            decision: "BUY" or "SELL"

        Returns:
            Tuple of (should_close, reason)
        """
        if decision == "BUY":
            # Check stop loss
            if current_price <= levels.stop_loss:
                return (True, "stop_loss")

            # Check trailing stop (if active)
            if levels.trailing_stop_active and current_price <= levels.trailing_stop_price:
                return (True, "trailing_stop")

            # Check take profit (full position close at TP3)
            if current_price >= levels.tp3_price:
                return (True, "take_profit_full")

        else:  # SELL
            # Check stop loss
            if current_price >= levels.stop_loss:
                return (True, "stop_loss")

            # Check trailing stop (if active)
            if levels.trailing_stop_active and current_price >= levels.trailing_stop_price:
                return (True, "trailing_stop")

            # Check take profit (full position close at TP3)
            if current_price <= levels.tp3_price:
                return (True, "take_profit_full")

        return (False, "")

    def calculate_for_ranging_market(
        self,
        entry_price: float,
        atr: float,
        balance_usd: float,
        decision: str,
        indicators: dict | None = None,
    ) -> DynamicRiskLevels:
        """
        Calculate TP/SL levels adapted for ranging/choppy markets.

        When CHOP >= 61.8 or donchian_width < 0.5%, reduces TP distances
        to make them more achievable in sideways markets.

        Args:
            entry_price: Trade entry price
            atr: Average True Range value
            balance_usd: Account balance in USD
            decision: "BUY" or "SELL"
            indicators: Dict with chop, donchian_width_pct, bb_squeeze, etc.

        Returns:
            DynamicRiskLevels with adjusted TP/SL for ranging markets
        """
        indicators = indicators or {}
        chop = indicators.get("chop", 50)
        donchian_width = indicators.get("donchian_width_pct", 1.0)
        bb_squeeze = indicators.get("bb_squeeze", False)

        # Detect ranging market conditions
        is_choppy = chop >= 61.8
        is_tight_range = donchian_width < 0.5
        is_squeeze = bb_squeeze

        # If any ranging condition is met, use ranging mode
        ranging_mode = is_choppy or is_tight_range or is_squeeze

        # Calculate base levels first
        base_levels = self.calculate(
            entry_price=entry_price,
            atr=atr,
            balance_usd=balance_usd,
            decision=decision,
        )

        if not ranging_mode:
            return base_levels

        # Adjust for ranging market - tighter TP/SL
        fee_buffer = entry_price * (self.config.trading_fee_pct * 2 / 100)
        direction = 1 if decision == "BUY" else -1

        # Ranging market multipliers (reduced from default)
        # Default: TP1=1.0x, TP2=2.0x, TP3=3.0x, SL=1.5x
        # Ranging: TP1=0.5x, TP2=1.0x, TP3=1.5x, SL=0.8x
        tp1_mult = 0.5
        tp2_mult = 1.0
        tp3_mult = 1.5
        sl_mult = 0.8

        # Calculate adjusted distances
        tp1_distance = atr * tp1_mult
        tp2_distance = atr * tp2_mult
        tp3_distance = atr * tp3_mult
        sl_distance = atr * sl_mult

        # Calculate prices
        if decision == "BUY":
            tp1_price = entry_price + tp1_distance + fee_buffer
            tp2_price = entry_price + tp2_distance + fee_buffer
            tp3_price = entry_price + tp3_distance + fee_buffer
            stop_loss = entry_price - sl_distance - fee_buffer
            trailing_stop_price = entry_price - sl_distance * 0.5
        else:  # SELL
            tp1_price = entry_price - tp1_distance - fee_buffer
            tp2_price = entry_price - tp2_distance - fee_buffer
            tp3_price = entry_price - tp3_distance - fee_buffer
            stop_loss = entry_price + sl_distance + fee_buffer
            trailing_stop_price = entry_price + sl_distance * 0.5

        # Adjust position sizes for ranging mode
        # Take more profit at TP1 since it's more achievable
        tp1_size_pct = 0.50  # Was 0.30
        tp2_size_pct = 0.30  # Was 0.40
        tp3_size_pct = 0.20  # Was 0.30

        # Update levels
        base_levels.tp1_price = tp1_price
        base_levels.tp2_price = tp2_price
        base_levels.tp3_price = tp3_price
        base_levels.stop_loss = stop_loss
        base_levels.trailing_stop_price = trailing_stop_price
        base_levels.tp1_size_pct = tp1_size_pct
        base_levels.tp2_size_pct = tp2_size_pct
        base_levels.tp3_size_pct = tp3_size_pct

        # Update distances
        base_levels.sl_distance_pct = abs((stop_loss - entry_price) / entry_price * 100)
        base_levels.tp_distance_pct = abs((tp3_price - entry_price) / entry_price * 100)

        # Add warning about ranging mode
        ranging_reason = []
        if is_choppy:
            ranging_reason.append(f"CHOP={chop:.1f}")
        if is_tight_range:
            ranging_reason.append(f"Donchian={donchian_width:.2f}%")
        if is_squeeze:
            ranging_reason.append("Squeeze")

        base_levels.warnings.append(
            f"RANGING MODE ({', '.join(ranging_reason)}): "
            f"TP1={tp1_mult}x ATR (was 1.0x), SL={sl_mult}x ATR (was 1.5x)"
        )
        base_levels.volatility_regime = "ranging"

        return base_levels

    def get_volatility_adjustment_complete(self, atr: float, price: float) -> dict:
        """
        Complete volatility-based adjustments for the trade.

        Returns:
            Dict with volatility assessment and recommended adjustments
        """
        atr_pct = (atr / price) * 100

        if atr_pct < 0.3:
            return {
                "regime": "very_low",
                "adjustment": "tighten_stops",
                "sl_multiplier": 1.0,
                "description": "Very low volatility - use tighter stops",
            }
        elif atr_pct < 0.7:
            return {
                "regime": "low",
                "adjustment": "standard",
                "sl_multiplier": 1.5,
                "description": "Low volatility - standard ATR multiplier",
            }
        elif atr_pct < 1.5:
            return {
                "regime": "moderate",
                "adjustment": "widen_stops",
                "sl_multiplier": 2.0,
                "description": "Moderate volatility - widen stops slightly",
            }
        elif atr_pct < 3.0:
            return {
                "regime": "high",
                "adjustment": "significant_widening",
                "sl_multiplier": 2.5,
                "description": "High volatility - significant widening needed",
            }
        else:
            return {
                "regime": "extreme",
                "adjustment": "avoid_or_very_wide",
                "sl_multiplier": 3.0,
                "description": "Extreme volatility - avoid trade or very wide stops",
            }


# Singleton instance for easy access
_default_calculator: DynamicStopLossCalculator | None = None


def get_dynamic_stop_loss_calculator(
    config: StopLossConfig | None = None,
) -> DynamicStopLossCalculator:
    """Get or create the default calculator instance."""
    global _default_calculator
    if _default_calculator is None or config is not None:
        _default_calculator = DynamicStopLossCalculator(config)
    return _default_calculator


def calculate_dynamic_risk_levels(
    entry_price: float, atr: float, balance_usd: float, decision: str, **kwargs
) -> DynamicRiskLevels:
    """
    Convenience function to calculate dynamic risk levels.

    Example:
        levels = calculate_dynamic_risk_levels(
            entry_price=0.004250,
            atr=0.000042,
            balance_usd=10000,
            decision="BUY",
            symbol="1000PEPEUSDT",
        )
        print(f"Stop Loss: {levels.stop_loss}")
        print(f"Take Profit: {levels.take_profit}")
    """
    calculator = get_dynamic_stop_loss_calculator()
    return calculator.calculate(entry_price, atr, balance_usd, decision, **kwargs)
