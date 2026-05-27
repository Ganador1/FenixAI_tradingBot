# src/charts/mpl_strategy.py
"""
mplfinance-based chart strategy — fallback renderer.

Produces dark-theme candlestick charts with overlays using
mplfinance. Used when Plotly is unavailable.
Extracted and deduplicated from chart_generator.py.
"""

from __future__ import annotations

import base64
import io
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from src.charts.strategy import (
    ChartResult,
    ChartStrategy,
    RenderBackend,
    calculate_bollinger,
    calculate_ema,
    calculate_rsi,
    calculate_supertrend,
)

logger = logging.getLogger(__name__)

# Lazy-loaded mplfinance
_mpf_available: bool | None = None
mpf = None  # type: ignore


def _ensure_mpf():
    global _mpf_available, mpf
    if _mpf_available is None:
        try:
            import mplfinance as _mpf

            mpf = _mpf
            _mpf_available = True
        except ImportError:
            _mpf_available = False
    return _mpf_available


# ---------- Dark style matching TradingView ----------

_UP = "#26a69a"
_DOWN = "#ef5350"
_BG = "#1e1e1e"
_GRID = "#333333"


def _get_style():
    mc = mpf.make_marketcolors(
        up=_UP,
        down=_DOWN,
        edge={"up": _UP, "down": _DOWN},
        wick={"up": _UP, "down": _DOWN},
        volume={"up": _UP + "80", "down": _DOWN + "80"},
    )
    return mpf.make_mpf_style(
        base_mpl_style="dark_background",
        marketcolors=mc,
        facecolor=_BG,
        edgecolor=_BG,
        figcolor=_BG,
        gridcolor=_GRID,
        gridstyle="--",
        y_on_right=True,
        rc={
            "axes.labelcolor": "#cccccc",
            "axes.edgecolor": _GRID,
            "xtick.color": "#cccccc",
            "ytick.color": "#cccccc",
            "font.size": 10,
        },
    )


class MplfinanceChartStrategy(ChartStrategy):
    """Render charts with mplfinance (matplotlib backend)."""

    def __init__(self, save_dir: str = "cache/charts"):
        self._save_dir = Path(save_dir)
        self._save_dir.mkdir(parents=True, exist_ok=True)

    @property
    def backend(self) -> RenderBackend:
        return RenderBackend.MPLFINANCE

    @property
    def available(self) -> bool:
        return _ensure_mpf()

    def render(
        self,
        df,
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
        indicators: list[str] | None = None,
        *,
        show_volume: bool = True,
        show_rsi: bool = True,
        show_macd: bool = False,
    ) -> ChartResult:
        if not self.available:
            return ChartResult(error="mplfinance not installed", backend=self.backend)

        t0 = time.time()
        indicators = indicators or ["ema_9", "ema_21", "bb_bands"]

        try:
            close = df["Close"].values.astype(float)
            high = df["High"].values.astype(float)
            low = df["Low"].values.astype(float)
            volume = df["Volume"].values.astype(float) if "Volume" in df.columns else None

            addplots: list = []
            shown: list[str] = []

            # EMAs
            for key, period, color in [
                ("ema_9", 9, "#2196f3"),
                ("ema_21", 21, "#ff9800"),
                ("ema_50", 50, "#9c27b0"),
            ]:
                if key in indicators:
                    s = pd.Series(calculate_ema(close, period), index=df.index)
                    addplots.append(mpf.make_addplot(s, color=color, width=1.5))
                    shown.append(key)

            # Bollinger Bands
            if "bb_bands" in indicators:
                mid, upper, lower = calculate_bollinger(close)
                addplots.append(
                    mpf.make_addplot(
                        pd.Series(upper, index=df.index),
                        color="#42a5f5",
                        width=1,
                        linestyle="dotted",
                    )
                )
                addplots.append(
                    mpf.make_addplot(
                        pd.Series(lower, index=df.index),
                        color="#42a5f5",
                        width=1,
                        linestyle="dotted",
                    )
                )
                shown.append("bb_bands")

            # SuperTrend
            if "supertrend" in indicators:
                st, _ = calculate_supertrend(high, low, close)
                addplots.append(
                    mpf.make_addplot(pd.Series(st, index=df.index), color="#00c853", width=2)
                )
                shown.append("supertrend")

            # RSI (as panel)
            if show_rsi:
                rsi = pd.Series(calculate_rsi(close), index=df.index)
                addplots.append(
                    mpf.make_addplot(rsi, panel=2, color="#b388ff", width=1.5, ylabel="RSI")
                )
                shown.append("rsi")

            style = _get_style()
            buf = io.BytesIO()

            kwargs: dict[str, Any] = dict(
                type="candle",
                style=style,
                volume=show_volume and volume is not None,
                addplot=addplots or None,
                figsize=(16, 10),
                title=f"\n{symbol} · {timeframe}",
                savefig=dict(fname=buf, dpi=150, bbox_inches="tight"),
                warn_too_much_data=500,
            )
            mpf.plot(df, **kwargs)
            plt.close("all")

            buf.seek(0)
            img_bytes = buf.read()
            img_b64 = base64.b64encode(img_bytes).decode()

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fp = self._save_dir / f"{symbol}_{timeframe}_{ts}_mpf.png"
            fp.write_bytes(img_bytes)

            elapsed = (time.time() - t0) * 1000
            return ChartResult(
                image_b64=img_b64,
                filepath=str(fp),
                backend=self.backend,
                symbol=symbol,
                timeframe=timeframe,
                generation_ms=elapsed,
                indicators_shown=shown,
                description=f"mplfinance chart {symbol} {timeframe} ({len(df)} candles)",
            )

        except Exception as e:
            logger.error("MplfinanceChartStrategy.render failed: %s", e, exc_info=True)
            return ChartResult(
                error=str(e), backend=self.backend, symbol=symbol, timeframe=timeframe
            )
