"""
NanoFenix v3 — Multi-Scale Feature Engine.

Combines the best feature ideas from all predecessors:
- V2 bar-level features (returns, EMA, OBI, spread, volatility)
- V2 aggTrade features (buy_vol_ratio, trade_direction, VWAP)
- V0 Deep LOB features (WAP distance, depth OBI, price pressure) — SIMPLIFIED
- Multi-scale: features at 5s, 15s, 30s, 60s, 120s, 300s lookbacks
- Order flow acceleration (2nd derivative of OFI)
- Volume profile (relative volume vs historical mean)

Total: 28 features — more than V2 (20) but less than V0+neural (40+).
All computed in <100μs per bar on Apple M4.
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

# ── macOS ARM64 BLAS safety ──
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")


# ═══════════════════════════════════════════════════════════════════════════════
# Bar Data
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Bar:
    """1-second OHLCV bar aggregated from bookTicker + aggTrade streams."""

    timestamp: float
    open: float
    high: float
    low: float
    close: float
    tick_count: int
    obi_sum: float  # Sum of OBI values in this bar
    spread_sum: float  # Sum of spread_bps values in this bar
    buy_vol: float = 0.0  # Aggressive buy volume (aggTrade)
    sell_vol: float = 0.0  # Aggressive sell volume (aggTrade)
    buy_count: int = 0
    sell_count: int = 0
    vwap_num: float = 0.0  # Σ(price × qty)
    vwap_den: float = 0.0  # Σ(qty)

    @property
    def obi_mean(self) -> float:
        return self.obi_sum / max(1, self.tick_count)

    @property
    def spread_mean(self) -> float:
        return self.spread_sum / max(1, self.tick_count)

    @property
    def buy_vol_ratio(self) -> float:
        total = self.buy_vol + self.sell_vol
        return self.buy_vol / total if total > 0 else 0.5

    @property
    def trade_direction(self) -> float:
        total = self.buy_count + self.sell_count
        return (self.buy_count - self.sell_count) / total if total > 0 else 0.0

    @property
    def vwap(self) -> float:
        return self.vwap_num / self.vwap_den if self.vwap_den > 0 else self.close

    @property
    def range_bps(self) -> float:
        if self.open <= 0:
            return 0.0
        return (self.high - self.low) / self.open * 10000

    @property
    def total_vol(self) -> float:
        return self.buy_vol + self.sell_vol


# ═══════════════════════════════════════════════════════════════════════════════
# Bar Aggregator (from V2, enhanced with depth LOB snapshot)
# ═══════════════════════════════════════════════════════════════════════════════


class BarAggregator:
    """Aggregates raw bookTicker ticks into 1-second bars."""

    def __init__(self, interval: float = 1.0):
        self._interval = interval
        self._current: Bar | None = None
        self._bar_start: float = 0.0
        self._tick_count: int = 0

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def process_tick(self, mid: float, obi: float, spread_bps: float, ts: float) -> Bar | None:
        self._tick_count += 1

        if self._current is None:
            self._bar_start = ts
            self._current = Bar(
                timestamp=ts,
                open=mid,
                high=mid,
                low=mid,
                close=mid,
                tick_count=1,
                obi_sum=obi,
                spread_sum=spread_bps,
            )
            return None

        bar = self._current
        bar.close = mid
        bar.high = max(bar.high, mid)
        bar.low = min(bar.low, mid)
        bar.tick_count += 1
        bar.obi_sum += obi
        bar.spread_sum += spread_bps

        if ts - self._bar_start >= self._interval:
            completed = self._current
            self._current = None
            return completed
        return None

    def process_trade(self, price: float, qty: float, is_buyer_maker: bool) -> None:
        if self._current is None or price <= 0 or qty <= 0:
            return
        if is_buyer_maker:
            self._current.sell_vol += qty
            self._current.sell_count += 1
        else:
            self._current.buy_vol += qty
            self._current.buy_count += 1
        self._current.vwap_num += price * qty
        self._current.vwap_den += qty

    def export_state(self) -> dict[str, object]:
        current = None
        if self._current is not None:
            current = dict(vars(self._current))
        return {
            "interval": float(self._interval),
            "bar_start": float(self._bar_start),
            "tick_count": int(self._tick_count),
            "current": current,
        }

    def restore_state(self, state: dict[str, object] | None) -> None:
        if not state:
            return
        try:
            self._interval = float(state.get("interval", self._interval))
            self._bar_start = float(state.get("bar_start", 0.0) or 0.0)
            self._tick_count = int(state.get("tick_count", 0) or 0)
            current = state.get("current")
            if isinstance(current, dict):
                self._current = Bar(**current)
            else:
                self._current = None

            # If monotonic time restarted (e.g. reboot), a persisted bar_start can
            # end up in the future and freeze bar completion forever.
            now_mono = time.monotonic()
            if self._bar_start > now_mono + self._interval:
                self._bar_start = 0.0
                self._current = None
            # Also drop stale partially open bars from old sessions.
            elif self._current is not None and (now_mono - self._bar_start) > max(
                5.0, self._interval * 10.0
            ):
                self._bar_start = 0.0
                self._current = None
        except Exception:
            self._current = None


# ═══════════════════════════════════════════════════════════════════════════════
# Feature Names (28 features)
# ═══════════════════════════════════════════════════════════════════════════════

FEATURE_NAMES = [
    # ── Price momentum (6) ──
    "return_5s",  # 5-bar return (bps)
    "return_15s",  # 15-bar return (bps)
    "return_30s",  # 30-bar return (bps)
    "return_60s",  # 60-bar return (bps)
    "return_120s",  # 120-bar return (bps)
    "return_300s",  # 300-bar return (bps)
    # ── Trend structure (3) ──
    "ema_cross_5_20",  # EMA(5) - EMA(20) in bps (short-term trend)
    "ema_cross_20_60",  # EMA(20) - EMA(60) in bps (medium-term trend)
    "ema_cross_60_200",  # EMA(60) - EMA(200) in bps (long-term trend) — NEW
    # ── Order book pressure (4) ──
    "obi_mean_5",  # Mean OBI over 5 bars
    "obi_zscore_30",  # OBI z-score over 30 bars (short-term extreme)
    "obi_zscore_120",  # OBI z-score over 120 bars (long-term extreme) — NEW
    "obi_acceleration",  # OBI momentum: change in 5-bar mean vs 15-bar mean — NEW
    # ── Order flow (aggTrade) (4) ──
    "buy_vol_ratio_5",  # Aggressive buy fraction [-1, +1]
    "buy_vol_ratio_30",  # Aggressive buy fraction 30 bars — NEW
    "trade_dir_10",  # Net trade direction [-1, +1]
    "vwap_dev_60",  # Price vs 60-bar VWAP (bps, mean-reversion signal)
    # ── Spread & microstructure (2) ──
    "spread_mean_5",  # Mean spread 5 bars
    "spread_zscore_60",  # Spread z-score 60 bars (unusually wide = volatility) — NEW
    # ── Volatility (4) ──
    "volatility_30",  # Realized vol 30 bars
    "volatility_120",  # Realized vol 120 bars
    "vol_ratio",  # vol_30 / vol_120 (expanding/contracting vol) — NEW
    "vol_trend",  # Volume trend (recent vs prior tick count)
    # ── Market state (3) ──
    "momentum_10",  # Bullish ratio over 10 bars (RSI-like)
    "price_position_60",  # Price in 60-bar range [0, 1]
    "range_bps_30",  # 30-bar range in bps (regime: trending vs ranging) — WIDENED
    # ── Regime confirmation (2) — NEW ──
    "trend_consistency",  # % of last 30 bars that moved in same direction as 30-bar return
    "flow_price_agree",  # 1 if buy_vol_ratio and return agree, 0 otherwise (flow confirming price)
]

N_FEATURES = len(FEATURE_NAMES)
FEATURE_LOOKBACK = 320  # Bars needed before first feature vector (300 + margin)


# ═══════════════════════════════════════════════════════════════════════════════
# Multi-Scale Feature Engine
# ═══════════════════════════════════════════════════════════════════════════════


class MultiScaleFeatureEngine:
    """
    Computes 28 features from 1-second bar history.

    Multi-scale: uses lookbacks from 5 to 300 bars for different
    time horizons. All computations are pure numpy, <100μs per bar.
    """

    def __init__(self, max_bars: int = 7200):
        self._closes: deque[float] = deque(maxlen=max_bars)
        self._obis: deque[float] = deque(maxlen=max_bars)
        self._spreads: deque[float] = deque(maxlen=max_bars)
        self._tick_counts: deque[int] = deque(maxlen=max_bars)
        self._buy_vol_ratios: deque[float] = deque(maxlen=max_bars)
        self._trade_dirs: deque[float] = deque(maxlen=max_bars)
        self._vwaps: deque[float] = deque(maxlen=max_bars)
        self._total_vols: deque[float] = deque(maxlen=max_bars)
        self._bar_count: int = 0

        # Incremental EMA state
        self._ema5: float = 0.0
        self._ema20: float = 0.0
        self._ema60: float = 0.0
        self._ema200: float = 0.0
        self._alpha5 = 2.0 / 6
        self._alpha20 = 2.0 / 21
        self._alpha60 = 2.0 / 61
        self._alpha200 = 2.0 / 201

    @property
    def bar_count(self) -> int:
        return self._bar_count

    @property
    def is_warm(self) -> bool:
        return self._bar_count >= FEATURE_LOOKBACK

    def add_bar(self, bar: Bar) -> None:
        close = bar.close
        self._closes.append(close)
        self._obis.append(bar.obi_mean)
        self._spreads.append(bar.spread_mean)
        self._tick_counts.append(bar.tick_count)
        self._buy_vol_ratios.append(bar.buy_vol_ratio)
        self._trade_dirs.append(bar.trade_direction)
        self._vwaps.append(bar.vwap)
        self._total_vols.append(bar.total_vol)
        self._bar_count += 1

        # Update EMAs incrementally (O(1) per bar)
        if self._bar_count == 1:
            self._ema5 = self._ema20 = self._ema60 = self._ema200 = close
        else:
            self._ema5 += self._alpha5 * (close - self._ema5)
            self._ema20 += self._alpha20 * (close - self._ema20)
            self._ema60 += self._alpha60 * (close - self._ema60)
            self._ema200 += self._alpha200 * (close - self._ema200)

    def current_mid(self) -> float:
        return self._closes[-1] if self._closes else 0.0

    def compute_features(self) -> np.ndarray | None:
        if not self.is_warm:
            return None

        closes = list(self._closes)
        obis = list(self._obis)
        spreads = list(self._spreads)
        tick_counts = list(self._tick_counts)
        bvrs = list(self._buy_vol_ratios)
        tdirs = list(self._trade_dirs)
        vwaps = list(self._vwaps)
        mid = closes[-1]

        # ── Returns at multiple scales (bps) ──
        def ret_bps(n: int) -> float:
            if len(closes) <= n:
                return 0.0
            prev = closes[-(n + 1)]
            return (mid - prev) / prev * 10000 if prev > 0 else 0.0

        r5 = ret_bps(5)
        r15 = ret_bps(15)
        r30 = ret_bps(30)
        r60 = ret_bps(60)
        r120 = ret_bps(120)
        r300 = ret_bps(300)

        # ── EMA crossovers (bps) ──
        ema_cross_5_20 = (self._ema5 - self._ema20) / mid * 10000
        ema_cross_20_60 = (self._ema20 - self._ema60) / mid * 10000
        ema_cross_60_200 = (self._ema60 - self._ema200) / mid * 10000

        # ── OBI statistics ──
        obi_5 = obis[-5:]
        obi_mean_5 = float(np.mean(obi_5))

        obi_30 = np.array(obis[-30:])
        obi_mu_30 = float(np.mean(obi_30))
        obi_sigma_30 = float(np.std(obi_30))
        obi_zscore_30 = (obi_mean_5 - obi_mu_30) / max(obi_sigma_30, 1e-8)

        obi_120 = np.array(obis[-120:])
        obi_mu_120 = float(np.mean(obi_120))
        obi_sigma_120 = float(np.std(obi_120))
        obi_zscore_120 = (obi_mean_5 - obi_mu_120) / max(obi_sigma_120, 1e-8)

        # OBI acceleration: how fast is OBI changing?
        obi_mean_15 = float(np.mean(obis[-15:])) if len(obis) >= 15 else obi_mean_5
        obi_acceleration = obi_mean_5 - obi_mean_15

        # ── Order flow (aggTrade) ──
        bvr_5 = float(np.mean(bvrs[-5:]) - 0.5) * 2.0  # [-1, +1]
        bvr_30 = float(np.mean(bvrs[-30:]) - 0.5) * 2.0 if len(bvrs) >= 30 else bvr_5
        trade_dir_10 = float(np.mean(tdirs[-10:]))

        # VWAP deviation
        vwap_60 = float(np.mean(vwaps[-60:])) if len(vwaps) >= 60 else mid
        vwap_dev_60 = (mid - vwap_60) / max(vwap_60, 1e-8) * 10000

        # ── Spread ──
        spread_mean_5 = float(np.mean(spreads[-5:]))
        sp_60 = np.array(spreads[-60:])
        sp_mu = float(np.mean(sp_60))
        sp_sigma = float(np.std(sp_60))
        spread_zscore_60 = (spread_mean_5 - sp_mu) / max(sp_sigma, 1e-8)

        # ── Volatility ──
        c30 = np.array(closes[-31:])
        rets_30 = np.diff(c30) / c30[:-1] * 10000
        volatility_30 = float(np.std(rets_30)) if len(rets_30) > 1 else 0.0

        if len(closes) > 120:
            c120 = np.array(closes[-121:])
            rets_120 = np.diff(c120) / c120[:-1] * 10000
            volatility_120 = float(np.std(rets_120))
        else:
            volatility_120 = volatility_30

        vol_ratio = volatility_30 / max(volatility_120, 1e-8)

        # Volume trend
        vol_recent = float(np.mean(tick_counts[-5:]))
        vol_prev = float(np.mean(tick_counts[-10:-5])) if len(tick_counts) >= 10 else vol_recent
        vol_trend = (vol_recent - vol_prev) / max(vol_prev, 1) * 100

        # ── Market state ──
        c10 = np.array(closes[-11:])
        rets_10 = np.diff(c10) / c10[:-1] * 10000
        pos_sum = float(np.sum(rets_10[rets_10 > 0]))
        abs_sum = float(np.sum(np.abs(rets_10)))
        momentum_10 = pos_sum / max(abs_sum, 1e-8)

        c60 = closes[-60:]
        h60, l60 = max(c60), min(c60)
        price_position = (mid - l60) / max(h60 - l60, 1e-8) if h60 > l60 else 0.5

        c30_list = closes[-30:]
        h30, l30 = max(c30_list), min(c30_list)
        range_bps_30 = (h30 - l30) / max(mid, 1e-8) * 10000

        # ── Regime confirmation (NEW) ──
        # Trend consistency: what fraction of last 30 bars moved in same direction as r30?
        r30_sign = 1.0 if r30 > 0 else (-1.0 if r30 < 0 else 0.0)
        if len(rets_30) >= 30 and r30_sign != 0.0:
            same_dir = np.sum(np.sign(rets_30[-30:]) == r30_sign)
            trend_consistency = float(same_dir / 30)
        else:
            trend_consistency = 0.5

        # Flow-price agreement: do buyers dominate when price is rising?
        if (bvr_5 > 0 and r5 > 0) or (bvr_5 < 0 and r5 < 0):
            flow_price_agree = 1.0
        elif abs(bvr_5) < 0.05 or abs(r5) < 0.3:
            flow_price_agree = 0.5  # ambiguous
        else:
            flow_price_agree = 0.0  # disagreement

        # ── Assemble feature vector ──
        feat = np.array(
            [
                r5,
                r15,
                r30,
                r60,
                r120,
                r300,
                ema_cross_5_20,
                ema_cross_20_60,
                ema_cross_60_200,
                obi_mean_5,
                obi_zscore_30,
                obi_zscore_120,
                obi_acceleration,
                bvr_5,
                bvr_30,
                trade_dir_10,
                vwap_dev_60,
                spread_mean_5,
                spread_zscore_60,
                volatility_30,
                volatility_120,
                vol_ratio,
                vol_trend,
                momentum_10,
                price_position,
                range_bps_30,
                trend_consistency,
                flow_price_agree,
            ],
            dtype=np.float32,
        )

        np.nan_to_num(feat, nan=0.0, posinf=0.0, neginf=0.0, copy=False)
        return feat

    def get_regime_context(self) -> dict:
        """
        Provides current market regime information for companion signal.
        Called separately from features — not used by ML model.
        """
        if not self.is_warm:
            return {"regime": "UNKNOWN", "volatility": "UNKNOWN", "trend": "FLAT"}

        closes = list(self._closes)
        mid = closes[-1]

        # Trend: based on EMA alignment
        if self._ema5 > self._ema20 > self._ema60:
            trend = "BULL"
        elif self._ema5 < self._ema20 < self._ema60:
            trend = "BEAR"
        elif abs(self._ema5 - self._ema60) / mid * 10000 < 3:
            trend = "FLAT"
        else:
            trend = "MIXED"

        # Volatility regime
        c30 = np.array(closes[-31:])
        rets = np.diff(c30) / c30[:-1] * 10000
        vol = float(np.std(rets))

        if vol < 2.0:
            vol_state = "LOW"
        elif vol < 5.0:
            vol_state = "MEDIUM"
        else:
            vol_state = "HIGH"

        # Market regime (combining trend + vol)
        if vol_state == "LOW" and trend == "FLAT":
            regime = "DEAD"
        elif vol_state == "HIGH":
            regime = "VOLATILE"
        elif trend in ("BULL", "BEAR"):
            regime = "TRENDING"
        else:
            regime = "RANGING"

        return {
            "regime": regime,
            "volatility": vol_state,
            "trend": trend,
            "vol_30s": round(vol, 2),
            "ema_alignment_bps": round((self._ema5 - self._ema60) / mid * 10000, 2),
        }

    def export_state(self) -> dict[str, object]:
        return {
            "bar_count": int(self._bar_count),
            "closes": list(self._closes),
            "obis": list(self._obis),
            "spreads": list(self._spreads),
            "tick_counts": list(self._tick_counts),
            "buy_vol_ratios": list(self._buy_vol_ratios),
            "trade_dirs": list(self._trade_dirs),
            "vwaps": list(self._vwaps),
            "total_vols": list(self._total_vols),
            "ema5": float(self._ema5),
            "ema20": float(self._ema20),
            "ema60": float(self._ema60),
            "ema200": float(self._ema200),
        }

    def restore_state(self, state: dict[str, object] | None) -> None:
        if not state:
            return

        def _restore(target: deque, values: object) -> None:
            target.clear()
            if isinstance(values, list):
                target.extend(values[-(target.maxlen or len(values)) :])

        _restore(self._closes, state.get("closes"))
        _restore(self._obis, state.get("obis"))
        _restore(self._spreads, state.get("spreads"))
        _restore(self._tick_counts, state.get("tick_counts"))
        _restore(self._buy_vol_ratios, state.get("buy_vol_ratios"))
        _restore(self._trade_dirs, state.get("trade_dirs"))
        _restore(self._vwaps, state.get("vwaps"))
        _restore(self._total_vols, state.get("total_vols"))

        self._bar_count = int(state.get("bar_count", len(self._closes)) or len(self._closes))
        self._ema5 = float(state.get("ema5", self._closes[-1] if self._closes else 0.0) or 0.0)
        self._ema20 = float(state.get("ema20", self._ema5) or self._ema5)
        self._ema60 = float(state.get("ema60", self._ema20) or self._ema20)
        self._ema200 = float(state.get("ema200", self._ema60) or self._ema60)
