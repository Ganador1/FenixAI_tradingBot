"""
MiniFenix SOTA Feature Engineering Module.
Based on academic literature on Limit Order Book prediction:
- DeepLOB (arXiv:1808.03668) - multi-level LOB features
- LOBCAST benchmark (arXiv:2308.01915) - 15 SOTA models compared
- Microstructure features used by quant funds (Citadel, Two Sigma, Jump)

This module transforms raw Binance WebSocket data into high quality
features for feeding ML models.
"""
from __future__ import annotations

import collections
import math
import numpy as np
from dataclasses import dataclass, field


@dataclass
class LOBSnapshot:
    """A full snapshot of the Limit Order Book at one instant."""
    bid: float = 0.0
    bid_qty: float = 0.0
    ask: float = 0.0
    ask_qty: float = 0.0
    timestamp_ms: int = 0


class FeatureEngine:
    """
    Real-time feature engineering engine.
    Computes ~25 features per tick, running in microseconds.
    """
    def __init__(self, lookback: int = 100):
        self.lookback = lookback

        # Circular buffers for raw data
        self.mid_prices: collections.deque[float] = collections.deque(maxlen=lookback)
        self.spreads_bps: collections.deque[float] = collections.deque(maxlen=lookback)
        self.obis: collections.deque[float] = collections.deque(maxlen=lookback)  # Order Book Imbalance
        self.ofis: collections.deque[float] = collections.deque(maxlen=lookback)  # Order Flow Imbalance (delta)
        self.volumes: collections.deque[float] = collections.deque(maxlen=lookback)
        self.returns: collections.deque[float] = collections.deque(maxlen=lookback)
        self.timestamps: collections.deque[int] = collections.deque(maxlen=lookback)

        # For computing rolling stats
        self._prev_mid: float = 0.0
        self._prev_bid_qty: float = 0.0
        self._prev_ask_qty: float = 0.0

        # Extra buffers for velocity/acceleration
        self.price_velocity: collections.deque[float] = collections.deque(maxlen=lookback)
        self.obi_velocity: collections.deque[float] = collections.deque(maxlen=lookback)

        # Feature names for interpretability
        self.feature_names = [
            # Group 1: Price (7 features) - velocity/acceleration added
            "mid_price_return",           # Mid-price return
            "log_return",                 # Log return
            "price_z_score_20",           # Price z-score (window 20)
            "price_z_score_50",           # Price z-score (window 50)
            "price_momentum_10",          # Momentum (current price vs 10 ticks ago)
            "price_velocity",             # Price velocity (delta/tick)
            "price_acceleration",         # Price acceleration (delta of velocity)

            # Group 2: Spread (3 features)
            "spread_bps",                 # Spread in basis points
            "spread_z_score",             # Spread z-score
            "spread_relative",            # Spread relative to median

            # Group 3: Order Book Imbalance (7 features) - velocity/acceleration added
            "obi_raw",                    # Instantaneous OBI
            "obi_ema_5",                  # Exponentially smoothed OBI (5 ticks)
            "obi_ema_20",                 # Exponentially smoothed OBI (20 ticks)
            "obi_momentum",               # OBI change (derivative)
            "obi_z_score",                # OBI z-score
            "obi_velocity",               # OBI velocity (change/tick)
            "obi_acceleration",           # OBI acceleration

            # Group 4: Order Flow Imbalance (5 features) - pressure added
            "ofi",                        # Instantaneous Order Flow Imbalance (bid-ask qty delta)
            "ofi_cumulative_10",          # Cumulative OFI over 10 ticks (sum of actual deltas)
            "ofi_intensity",              # Flow intensity
            "ofi_velocity",               # OFI change
            "microstructure_pressure",    # Microstructure pressure: (bid_qty * bid_change) - (ask_qty * ask_change)

            # Group 5: Volatility (4 features)
            "realized_volatility_20",     # Realized volatility (20 ticks)
            "realized_volatility_50",     # Realized volatility (50 ticks)
            "vol_ratio",                  # Short/long volatility ratio
            "max_drawdown_20",            # Max drawdown over 20 ticks

            # Group 6: Statistical Momentum (3 features)
            "rsi_14",                     # RSI computed over ticks (not candles)
            "mean_reversion_signal",      # Mean-reversion signal
            "trend_strength",             # Trend strength (OLS slope)

            # Group 7: Advanced Microstructure (3 features)
            "kyle_lambda_proxy",          # Kyle's Lambda proxy (price impact)
            "vpin_proxy",                 # Volume-sync Probability of Informed Trading
            "tick_frequency",             # Tick frequency (activity)
        ]

    @property
    def n_features(self) -> int:
        return len(self.feature_names)

    @property
    def ready(self) -> bool:
        """We need at least 50 ticks for stable features."""
        return len(self.mid_prices) >= 50

    def update(self, snap: LOBSnapshot) -> np.ndarray | None:
        """
        Update buffers and compute all features.
        Returns a numpy vector of shape (n_features,) or None if there is not enough data.
        """
        prev_mid = self._prev_mid
        mid = (snap.bid + snap.ask) / 2 if (snap.bid + snap.ask) > 0 else 0.0
        spread_bps = ((snap.ask - snap.bid) / mid * 10000) if mid > 0 else 0.0
        total_q = snap.bid_qty + snap.ask_qty
        obi = (snap.bid_qty - snap.ask_qty) / total_q if total_q > 0 else 0.0
        ret = (mid / prev_mid - 1) if prev_mid > 0 else 0.0

        # OFI based on DeepLOB: change in buy-side vs sell-side pressure
        delta_bid_q = snap.bid_qty - self._prev_bid_qty if self._prev_bid_qty > 0 else 0.0
        delta_ask_q = snap.ask_qty - self._prev_ask_qty if self._prev_ask_qty > 0 else 0.0
        ofi_raw = delta_bid_q - delta_ask_q

        # Compute velocity/acceleration
        price_velocity = ret  # velocity = change per tick
        obi_velocity = obi - self.obis[-1] if len(self.obis) > 0 else 0.0
        ofi_velocity = ofi_raw - self.ofis[-1] if len(self.ofis) > 0 else 0.0

        # Compute microstructure pressure
        delta_bid = snap.bid - self._prev_mid * 2 + snap.ask if self._prev_mid > 0 else 0
        delta_ask = snap.ask - self._prev_mid * 2 + snap.bid if self._prev_mid > 0 else 0
        micro_pressure = (snap.bid_qty * delta_bid) - (snap.ask_qty * delta_ask)

        # Store in buffers
        self.mid_prices.append(mid)
        self.spreads_bps.append(spread_bps)
        self.obis.append(obi)
        self.ofis.append(ofi_raw)   # BUG FIX: real OFI, not OBI
        self.returns.append(ret)
        self.volumes.append(total_q)
        self.timestamps.append(snap.timestamp_ms)
        self.price_velocity.append(price_velocity)
        self.obi_velocity.append(obi_velocity)

        self._prev_mid = mid
        self._prev_bid_qty = snap.bid_qty
        self._prev_ask_qty = snap.ask_qty

        if not self.ready:
            return None

        # ===== COMPUTE FEATURES =====
        prices = np.array(self.mid_prices)
        rets = np.array(self.returns)
        spreads = np.array(self.spreads_bps)
        obis_arr = np.array(self.obis)
        vols = np.array(self.volumes)

        features = np.zeros(self.n_features, dtype=np.float32)
        idx = 0

        # --- Group 1: Price ---
        features[idx] = ret; idx += 1
        features[idx] = math.log(mid / prev_mid) if prev_mid > 0 and mid > 0 else 0.0; idx += 1
        features[idx] = self._z_score(prices, 20); idx += 1
        features[idx] = self._z_score(prices, 50); idx += 1
        features[idx] = (mid / prices[-11] - 1) if len(prices) > 10 and prices[-11] > 0 else 0.0; idx += 1
        # Price velocity and acceleration
        p_vel = np.array(self.price_velocity)
        features[idx] = p_vel[-1] if len(p_vel) > 0 else 0.0; idx += 1
        features[idx] = (p_vel[-1] - p_vel[-2]) if len(p_vel) >= 2 else 0.0; idx += 1

        # --- Group 2: Spread ---
        features[idx] = spread_bps; idx += 1
        features[idx] = self._z_score(spreads, 20); idx += 1
        med_spread = np.median(spreads[-20:]) if len(spreads) >= 20 else np.median(spreads)
        features[idx] = (spread_bps / med_spread - 1) if med_spread > 0 else 0.0; idx += 1

        # --- Group 3: OBI ---
        features[idx] = obi; idx += 1
        features[idx] = self._ema(obis_arr, 5); idx += 1
        features[idx] = self._ema(obis_arr, 20); idx += 1
        features[idx] = obi - obis_arr[-2] if len(obis_arr) >= 2 else 0.0; idx += 1
        features[idx] = self._z_score(obis_arr, 20); idx += 1
        # OBI velocity and acceleration
        o_vel = np.array(self.obi_velocity)
        features[idx] = o_vel[-1] if len(o_vel) > 0 else 0.0; idx += 1
        features[idx] = (o_vel[-1] - o_vel[-2]) if len(o_vel) >= 2 else 0.0; idx += 1

        # --- Group 4: OFI (BUG FIX: use self.ofis not self.obis) ---
        features[idx] = ofi_raw; idx += 1
        # ofi_cumulative_10: sum of the last 10 real OFI deltas (not OBI)
        ofi_10 = float(np.sum(list(self.ofis)[-10:])) if len(self.ofis) >= 10 else 0.0
        features[idx] = ofi_10; idx += 1
        features[idx] = abs(ofi_raw) / (total_q + 1e-8); idx += 1
        # OFI velocity and microstructure pressure
        features[idx] = ofi_velocity; idx += 1
        features[idx] = micro_pressure / (total_q + 1e-8); idx += 1

        # --- Group 5: Volatility (BUG FIX: no sqrt(N), pure tick-to-tick std) ---
        # realized_volatility_20/50: std of returns over window (unscaled - comparable to each other)
        features[idx] = np.std(rets[-20:]) if len(rets) >= 20 else 0.0; idx += 1
        features[idx] = np.std(rets[-50:]) if len(rets) >= 50 else 0.0; idx += 1
        vol_short = np.std(rets[-10:]) if len(rets) >= 10 else 1e-8
        vol_long = np.std(rets[-50:]) if len(rets) >= 50 else 1e-8
        features[idx] = vol_short / (vol_long + 1e-8); idx += 1  # ratio in same scale now
        features[idx] = self._max_drawdown(prices[-20:]); idx += 1

        # --- Group 6: Momentum ---
        features[idx] = self._tick_rsi(rets, 14); idx += 1
        features[idx] = -self._z_score(prices, 20); idx += 1  # reversion signal: sell when price is high
        features[idx] = self._trend_strength(prices[-20:]); idx += 1

        # --- Group 7: Advanced microstructure ---
        features[idx] = self._kyle_lambda(rets, vols, 20); idx += 1
        features[idx] = self._vpin_proxy(rets, 20); idx += 1
        ts = np.array(self.timestamps)
        if len(ts) >= 2 and ts[-1] > ts[-11 if len(ts) > 10 else 0]:
            elapsed_sec = (ts[-1] - ts[-min(10, len(ts)-1)]) / 1000
            features[idx] = min(10, len(ts)-1) / (elapsed_sec + 1e-6)
        idx += 1

        return features

    # ===== Ultra-fast computation helpers =====

    @staticmethod
    def _z_score(arr: np.ndarray, window: int) -> float:
        if len(arr) < window:
            return 0.0
        segment = arr[-window:]
        std = np.std(segment)
        if std < 1e-10:
            return 0.0
        return float((arr[-1] - np.mean(segment)) / std)

    @staticmethod
    def _ema(arr: np.ndarray, span: int) -> float:
        if len(arr) == 0:
            return 0.0
        alpha = 2 / (span + 1)
        ema = arr[0]
        for val in arr[1:]:
            ema = alpha * val + (1 - alpha) * ema
        return float(ema)

    @staticmethod
    def _max_drawdown(prices: np.ndarray) -> float:
        if len(prices) < 2:
            return 0.0
        peak = prices[0]
        max_dd = 0.0
        for p in prices[1:]:
            if p > peak:
                peak = p
            dd = (peak - p) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @staticmethod
    def _tick_rsi(returns: np.ndarray, period: int) -> float:
        if len(returns) < period:
            return 50.0
        recent = returns[-period:]
        gains = np.sum(recent[recent > 0])
        losses = -np.sum(recent[recent < 0])
        if losses < 1e-10:
            return 100.0 if gains > 0 else 50.0
        rs = gains / losses
        return 100.0 - (100.0 / (1 + rs))

    @staticmethod
    def _trend_strength(prices: np.ndarray) -> float:
        """Normalised OLS slope as a measure of trend strength."""
        n = len(prices)
        if n < 3:
            return 0.0
        x = np.arange(n, dtype=np.float64)
        y = prices.astype(np.float64)
        mean_x, mean_y = x.mean(), y.mean()
        denom = np.sum((x - mean_x) ** 2)
        if denom < 1e-10:
            return 0.0
        slope = np.sum((x - mean_x) * (y - mean_y)) / denom
        # Normalise by the mean price
        return slope / (mean_y + 1e-10)

    @staticmethod
    def _kyle_lambda(returns: np.ndarray, volumes: np.ndarray, window: int) -> float:
        """
        Kyle's Lambda proxy: measures how much the price moves per unit of volume.
        High lambda = illiquid market = more impact.
        """
        if len(returns) < window or len(volumes) < window:
            return 0.0
        r = returns[-window:]
        v = volumes[-window:]
        signed_vol = r * v  # Volume Direction (positive = buy, negative = sell)
        denom = np.sum(np.abs(signed_vol))
        if denom < 1e-10:
            return 0.0
        return float(np.sum(np.abs(r)) / denom)

    @staticmethod
    def _vpin_proxy(returns: np.ndarray, window: int) -> float:
        """
        VPIN proxy: fraction of 'informed' trades vs total.
        High VPIN = high probability of private information in the market.
        """
        if len(returns) < window:
            return 0.0
        r = returns[-window:]
        abs_sum = np.sum(np.abs(r))
        if abs_sum < 1e-10:
            return 0.0
        return float(np.abs(np.sum(r)) / abs_sum)
