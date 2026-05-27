# src/charts/strategy.py
"""
Chart Strategy ABC and shared data structures.

Defines the contract that every chart renderer must satisfy and
the unified ChartResult returned by all strategies.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & Data classes
# ---------------------------------------------------------------------------


class RenderBackend(Enum):
    """Available render backends."""

    PLOTLY = "plotly"
    MPLFINANCE = "mplfinance"
    PLAYWRIGHT = "playwright"
    PLACEHOLDER = "placeholder"


@dataclass
class ChartResult:
    """Unified result returned by every strategy."""

    image_b64: str | None = None
    filepath: str | None = None
    backend: RenderBackend = RenderBackend.PLACEHOLDER
    symbol: str = ""
    timeframe: str = ""
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    generation_ms: float = 0.0
    indicators_shown: list[str] = field(default_factory=list)
    indicators_summary: dict[str, Any] | None = None
    description: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.image_b64 is not None and len(self.image_b64) > 0

    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.generated_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_b64_len": len(self.image_b64) if self.image_b64 else 0,
            "filepath": self.filepath,
            "backend": self.backend.value,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "generated_at": self.generated_at.isoformat(),
            "generation_ms": round(self.generation_ms, 1),
            "indicators_shown": self.indicators_shown,
            "description": self.description,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Shared indicator helpers (deduplicated from chart_generator + pro gen)
# ---------------------------------------------------------------------------


def prepare_dataframe(kline_data: dict[str, list]) -> pd.DataFrame | None:
    """
    Convert raw kline dict to a DatetimeIndex DataFrame with OHLCV columns.

    Accepted key names: open/Open, close/Close, high/High, low/Low,
    volume/Volume, datetime/Date/timestamp.
    """
    try:
        df = pd.DataFrame(kline_data)
        col_map = {
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
            "datetime": "Date",
            "timestamp": "Date",
        }
        df.rename(
            columns={k: v for k, v in col_map.items() if k in df.columns},
            inplace=True,
        )
        if "Date" in df.columns:
            if pd.api.types.is_numeric_dtype(df["Date"]):
                df["Date"] = pd.to_datetime(df["Date"], unit="ms")
            else:
                df["Date"] = pd.to_datetime(df["Date"])
            df.set_index("Date", inplace=True)

        for col in ("Open", "High", "Low", "Close", "Volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)
        return df if len(df) >= 5 else None
    except Exception as e:
        logger.error("prepare_dataframe failed: %s", e)
        return None


def calculate_ema(close: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average."""
    alpha = 2.0 / (period + 1)
    ema = np.empty_like(close, dtype=float)
    ema[0] = close[0]
    for i in range(1, len(close)):
        ema[i] = alpha * close[i] + (1 - alpha) * ema[i - 1]
    return ema


def calculate_bollinger(close: np.ndarray, period: int = 20, std_dev: float = 2.0):
    """Returns (middle, upper, lower) Bollinger Bands."""
    middle = pd.Series(close).rolling(period).mean().values
    std = pd.Series(close).rolling(period).std().values
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return middle, upper, lower


def calculate_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder RSI."""
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = pd.Series(gain).ewm(alpha=1 / period, min_periods=period).mean().values
    avg_loss = pd.Series(loss).ewm(alpha=1 / period, min_periods=period).mean().values
    rs = np.divide(avg_gain, avg_loss, out=np.ones_like(avg_gain), where=avg_loss != 0)
    return 100 - (100 / (1 + rs))


def calculate_macd(
    close: np.ndarray,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
):
    """Returns (macd_line, signal_line, histogram)."""
    ema_fast = calculate_ema(close, fast)
    ema_slow = calculate_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_supertrend(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 10,
    multiplier: float = 3.0,
):
    """Returns (supertrend_values, direction) arrays."""
    n = len(close)
    atr = np.zeros(n)
    tr = np.maximum(high - low, np.abs(high - np.roll(close, 1)))
    tr = np.maximum(tr, np.abs(low - np.roll(close, 1)))
    tr[0] = high[0] - low[0]
    for i in range(n):
        if i < period:
            atr[i] = np.mean(tr[: i + 1])
        else:
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    mid = (high + low) / 2
    up = mid - multiplier * atr
    dn = mid + multiplier * atr
    st = np.zeros(n)
    direction = np.ones(n)

    for i in range(1, n):
        if close[i - 1] > dn[i - 1]:
            dn[i] = max(dn[i], dn[i - 1])
        if close[i - 1] < up[i - 1]:
            up[i] = min(up[i], up[i - 1])
        if close[i] > dn[i - 1]:
            st[i] = up[i]
            direction[i] = 1
        elif close[i] < up[i - 1]:
            st[i] = dn[i]
            direction[i] = -1
        else:
            st[i] = st[i - 1]
            direction[i] = direction[i - 1]
    return st, direction


def calculate_vwap(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> np.ndarray:
    """Volume-weighted average price."""
    tp = (high + low + close) / 3
    cum_tpv = np.cumsum(tp * volume)
    cum_vol = np.cumsum(volume)
    return np.divide(cum_tpv, cum_vol, out=np.zeros_like(cum_tpv), where=cum_vol != 0)


def calculate_sma(close: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average."""
    sma = np.convolve(close, np.ones(period) / period, mode="valid")
    return np.concatenate([np.full(period - 1, np.nan), sma])


def calculate_hma(close: np.ndarray, period: int = 16) -> np.ndarray:
    """
    Hull Moving Average — ultra-fast, minimal lag.

    HMA = WMA(2 * WMA(n/2) − WMA(n), sqrt(n))
    """

    def _wma(data: np.ndarray, window: int) -> np.ndarray:
        weights = np.arange(1, window + 1, dtype=float)
        wma_vals = np.convolve(data, weights / weights.sum(), mode="valid")
        return np.concatenate([np.full(window - 1, np.nan), wma_vals])

    n = len(close)
    if n < period:
        return np.full(n, np.nan)

    half = period // 2
    sqrt_p = int(np.sqrt(period))

    wma_half = _wma(close, half)
    wma_full = _wma(close, period)

    min_len = min(len(wma_half), len(wma_full))
    raw = 2 * wma_half[-min_len:] - wma_full[-min_len:]
    hma = _wma(raw, sqrt_p)

    pad = n - len(hma)
    return np.concatenate([np.full(pad, np.nan), hma]) if pad > 0 else hma


def calculate_fisher_transform(
    high: np.ndarray,
    low: np.ndarray,
    period: int = 10,
) -> np.ndarray:
    """Fisher Transform — values > 2 or < -2 signal extreme reversals."""
    n = len(high)
    if n < period:
        return np.full(n, np.nan)

    midprices = (high + low) / 2
    fisher = np.full(n, np.nan)

    for i in range(period - 1, n):
        window = midprices[i - period + 1 : i + 1]
        hh, ll = np.max(window), np.min(window)
        if hh == ll:
            continue
        value = 0.33 * 2 * ((midprices[i] - ll) / (hh - ll) - 0.5)
        value = max(min(value, 0.999), -0.999)
        fisher[i] = 0.5 * np.log((1 + value) / (1 - value))

    return fisher


def calculate_atr(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """Average True Range — volatility measure."""
    n = len(close)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    return pd.Series(tr).rolling(window=period).mean().values


def calculate_obv(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    """On-Balance Volume."""
    obv = np.zeros(len(close))
    obv[0] = volume[0]
    for i in range(1, len(close)):
        if close[i] > close[i - 1]:
            obv[i] = obv[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            obv[i] = obv[i - 1] - volume[i]
        else:
            obv[i] = obv[i - 1]
    return obv


def calculate_ichimoku(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_period: int = 52,
) -> dict[str, np.ndarray]:
    """Ichimoku Cloud — Tenkan, Kijun, Senkou A/B, Chikou."""
    n = len(close)

    def _period_hl(arr_h, arr_l, p):
        out = np.zeros(n)
        for i in range(p - 1, n):
            out[i] = (np.max(arr_h[i - p + 1 : i + 1]) + np.min(arr_l[i - p + 1 : i + 1])) / 2
        return out

    tenkan = _period_hl(high, low, tenkan_period)
    kijun = _period_hl(high, low, kijun_period)

    senkou_a = np.zeros(n)
    for i in range(n):
        if i + kijun_period < n:
            senkou_a[i] = (tenkan[i] + kijun[i]) / 2

    senkou_b = _period_hl(high, low, senkou_period)

    chikou = np.zeros(n)
    for i in range(kijun_period, n):
        chikou[i] = close[i - kijun_period]

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b,
        "chikou": chikou,
    }


def calculate_pivot_points(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
) -> dict[str, float]:
    """Classic Pivot Points — PP, R1-R3, S1-S3."""
    pp = (high[-1] + low[-1] + close[-1]) / 3
    r1, s1 = 2 * pp - low[-1], 2 * pp - high[-1]
    hl = high[-1] - low[-1]
    r2, s2 = pp + hl, pp - hl
    r3 = high[-1] + 2 * (pp - low[-1])
    s3 = low[-1] - 2 * (high[-1] - pp)
    return {"r3": r3, "r2": r2, "r1": r1, "pp": pp, "s1": s1, "s2": s2, "s3": s3}


def generate_indicator_summary(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray | None,
    indicators: list[str],
) -> dict[str, Any]:
    """Generate a dict summarising current indicator values for the visual agent."""
    summary: dict[str, Any] = {
        "price": {
            "current": float(close[-1]),
            "open": float(close[0]),
            "high": float(high.max()),
            "low": float(low.min()),
            "change_pct": float(((close[-1] - close[0]) / close[0]) * 100),
        }
    }
    if "ema_9" in indicators:
        v = calculate_ema(close, 9)
        summary["ema_9"] = {
            "value": float(v[-1]),
            "position": "above" if close[-1] > v[-1] else "below",
        }
    if "ema_21" in indicators:
        v = calculate_ema(close, 21)
        summary["ema_21"] = {
            "value": float(v[-1]),
            "position": "above" if close[-1] > v[-1] else "below",
        }
    if "bb_bands" in indicators:
        mid, upper, lower = calculate_bollinger(close)
        pos = (
            "overbought"
            if close[-1] > upper[-1]
            else ("oversold" if close[-1] < lower[-1] else "neutral")
        )
        summary["bollinger"] = {
            "upper": float(upper[-1]) if not np.isnan(upper[-1]) else None,
            "middle": float(mid[-1]) if not np.isnan(mid[-1]) else None,
            "lower": float(lower[-1]) if not np.isnan(lower[-1]) else None,
            "position": pos,
        }
    rsi = calculate_rsi(close)
    summary["rsi"] = {
        "value": float(rsi[-1]) if not np.isnan(rsi[-1]) else None,
        "condition": "overbought" if rsi[-1] > 70 else ("oversold" if rsi[-1] < 30 else "neutral"),
    }
    ml, sl, hist = calculate_macd(close)
    summary["macd"] = {
        "macd": float(ml[-1]) if not np.isnan(ml[-1]) else None,
        "signal": float(sl[-1]) if not np.isnan(sl[-1]) else None,
        "histogram": float(hist[-1]) if not np.isnan(hist[-1]) else None,
        "trend": "bullish" if hist[-1] > 0 else "bearish",
    }
    return summary


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class ChartStrategy(ABC):
    """
    Abstract chart rendering strategy.

    Every concrete strategy must implement ``render()`` which receives a
    prepared DataFrame (DatetimeIndex, OHLCV columns) and returns a
    ``ChartResult``.
    """

    @abstractmethod
    def render(
        self,
        df: pd.DataFrame,
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
        indicators: list[str] | None = None,
        *,
        show_volume: bool = True,
        show_rsi: bool = True,
        show_macd: bool = True,
    ) -> ChartResult:
        """Render a chart and return a ChartResult."""
        ...

    @property
    @abstractmethod
    def backend(self) -> RenderBackend:
        """Return which backend this strategy uses."""
        ...

    @property
    def available(self) -> bool:
        """Whether the required libraries are installed."""
        return True
