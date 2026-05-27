# src/charts/plotly_strategy.py
"""
Plotly-based chart strategy — primary renderer.

Produces professional TradingView-style dark-theme charts with
candlesticks, volume, RSI, MACD, ATR, OBV sub-panels and configurable
overlays (EMA, BB, VWAP, SuperTrend, Ichimoku, HMA, Fisher, Pivots).

Extracted and deduplicated from professional_chart_generator.py — every
visual feature of the original is preserved.
"""

from __future__ import annotations

import base64
import logging
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from src.charts.strategy import (
    ChartResult,
    ChartStrategy,
    RenderBackend,
    calculate_atr,
    calculate_bollinger,
    calculate_ema,
    calculate_fisher_transform,
    calculate_hma,
    calculate_ichimoku,
    calculate_macd,
    calculate_obv,
    calculate_pivot_points,
    calculate_rsi,
    calculate_supertrend,
    calculate_vwap,
    generate_indicator_summary,
)

logger = logging.getLogger(__name__)

# Lazy-loaded Plotly
_plotly_available: bool | None = None
go = None  # type: ignore
make_subplots = None  # type: ignore


def _ensure_plotly():
    global _plotly_available, go, make_subplots
    if _plotly_available is None:
        try:
            import plotly.graph_objects as _go
            from plotly.subplots import make_subplots as _ms

            go = _go
            make_subplots = _ms
            _plotly_available = True
        except ImportError:
            _plotly_available = False
    return _plotly_available


# ---------- TradingView dark palette ----------

THEME = {
    "background": "#131722",
    "grid": "#1e222d",
    "text": "#d1d4dc",
    "text_secondary": "#787b86",
    "up_color": "#26a69a",
    "down_color": "#ef5350",
    "up_light": "rgba(38,166,154,0.5)",
    "down_light": "rgba(239,83,80,0.5)",
    "ema_9": "#2196f3",
    "ema_21": "#ff9800",
    "ema_50": "#9c27b0",
    "bb": "#42a5f5",
    "vwap": "#e91e63",
    "hma": "#00e5ff",
    "fisher_line": "#ffd700",
    "supertrend_up": "#00c853",
    "supertrend_down": "#ff1744",
    "support": "#00bcd4",
    "resistance": "#ff5722",
    "pivot": "#ffeb3b",
    "rsi": "#b388ff",
    "macd_line": "#2962ff",
    "signal_line": "#ff6d00",
    "hist_pos": "#26a69a",
    "hist_neg": "#ef5350",
    "atr": "#ff9800",
    "obv": "#9c27b0",
    "ichimoku_tenkan": "#2962ff",
    "ichimoku_kijun": "#b71c1c",
    "ichimoku_cloud_bull": "rgba(76,175,80,0.1)",
    "ichimoku_span_a": "rgba(76,175,80,0.3)",
    "ichimoku_span_b": "rgba(244,67,54,0.3)",
}


class PlotlyChartStrategy(ChartStrategy):
    """
    Render charts with Plotly — full TradingView professional style.

    Supports all overlays and sub-panels from ProfessionalChartGenerator:
    EMA 9/21/50, Bollinger Bands, VWAP, HMA, SuperTrend, Ichimoku Cloud,
    Pivot Points, Fisher Transform, plus Volume/RSI/MACD/ATR/OBV panels.
    """

    def __init__(
        self,
        save_dir: str = "cache/charts",
        width: int = 1600,
        height: int = 900,
    ):
        self._save_dir = Path(save_dir)
        self._save_dir.mkdir(parents=True, exist_ok=True)
        self._width = width
        self._height = height

    # -- ABC --

    @property
    def backend(self) -> RenderBackend:
        return RenderBackend.PLOTLY

    @property
    def available(self) -> bool:
        return _ensure_plotly()

    # -- Render --

    def render(
        self,
        df,
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
        indicators: list[str] | None = None,
        *,
        show_volume: bool = True,
        show_rsi: bool = True,
        show_macd: bool = True,
        show_atr: bool = False,
        show_obv: bool = False,
        **kwargs,
    ) -> ChartResult:
        if not self.available:
            return ChartResult(error="Plotly not installed", backend=self.backend)

        t0 = time.time()
        indicators = indicators or [
            "ema_9",
            "ema_21",
            "ema_50",
            "bb_bands",
            "supertrend",
            "vwap",
            "hma",
            "ichimoku",
            "pivots",
        ]

        try:
            close = df["Close"].values.astype(float)
            high = df["High"].values.astype(float)
            low = df["Low"].values.astype(float)
            volume = df["Volume"].values.astype(float) if "Volume" in df.columns else None

            # ---------- subplot layout ----------
            rows = 1
            row_h = [0.55]
            if show_volume and volume is not None:
                rows += 1
                row_h.append(0.12)
            if show_rsi:
                rows += 1
                row_h.append(0.10)
            if show_macd:
                rows += 1
                row_h.append(0.10)
            if show_atr:
                rows += 1
                row_h.append(0.08)
            if show_obv and volume is not None:
                rows += 1
                row_h.append(0.05)
            total = sum(row_h)
            row_h = [h / total for h in row_h]

            fig = make_subplots(
                rows=rows,
                cols=1,
                shared_xaxes=True,
                vertical_spacing=0.02,
                row_heights=row_h,
            )

            n = len(df)
            use_num_x = n < 30
            x = list(range(n)) if use_num_x else df.index

            # Tick labels for numeric X (few candles)
            x_tickvals, x_ticktext = None, None
            if use_num_x:
                x_tickvals = list(range(0, n, max(1, n // 10)))
                x_ticktext = [
                    df.index[i].strftime("%H:%M")
                    if hasattr(df.index[i], "strftime")
                    else str(df.index[i])
                    for i in x_tickvals
                ]

            cur = 1  # current row

            # ---------- candlestick ----------
            fig.add_trace(
                go.Candlestick(
                    x=x,
                    open=df["Open"],
                    high=df["High"],
                    low=df["Low"],
                    close=df["Close"],
                    increasing=dict(
                        line=dict(color=THEME["up_color"], width=1), fillcolor=THEME["up_color"]
                    ),
                    decreasing=dict(
                        line=dict(color=THEME["down_color"], width=1), fillcolor=THEME["down_color"]
                    ),
                    name="Price",
                    showlegend=False,
                ),
                row=cur,
                col=1,
            )

            shown: list[str] = []

            # -- EMAs --
            for ema_key, period, color in [
                ("ema_9", 9, THEME["ema_9"]),
                ("ema_21", 21, THEME["ema_21"]),
                ("ema_50", 50, THEME["ema_50"]),
            ]:
                if ema_key in indicators:
                    vals = calculate_ema(close, period)
                    fig.add_trace(
                        go.Scatter(
                            x=x, y=vals, line=dict(color=color, width=1.5), name=f"EMA {period}"
                        ),
                        row=cur,
                        col=1,
                    )
                    shown.append(ema_key)

            # -- Bollinger Bands --
            if "bb_bands" in indicators:
                mid, upper, lower = calculate_bollinger(close)
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=upper,
                        line=dict(color=THEME["bb"], width=1, dash="dot"),
                        name="BB Upper",
                        showlegend=False,
                    ),
                    row=cur,
                    col=1,
                )
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=lower,
                        line=dict(color=THEME["bb"], width=1, dash="dot"),
                        fill="tonexty",
                        fillcolor="rgba(66,165,245,0.1)",
                        name="BB",
                    ),
                    row=cur,
                    col=1,
                )
                shown.append("bb_bands")

            # -- VWAP --
            if "vwap" in indicators and volume is not None:
                vw = calculate_vwap(high, low, close, volume)
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=vw,
                        line=dict(color=THEME["vwap"], width=1.5, dash="dash"),
                        name="VWAP",
                    ),
                    row=cur,
                    col=1,
                )
                shown.append("vwap")

            # -- HMA (Hull Moving Average) --
            if "hma" in indicators:
                hma = calculate_hma(close, period=16)
                valid = ~np.isnan(hma)
                if np.any(valid):
                    fig.add_trace(
                        go.Scatter(
                            x=np.array(x)[valid] if use_num_x else df.index[valid],
                            y=hma[valid],
                            line=dict(color=THEME["hma"], width=2),
                            name="HMA 16",
                        ),
                        row=cur,
                        col=1,
                    )
                    shown.append("hma")

            # -- SuperTrend --
            if "supertrend" in indicators:
                st, d = calculate_supertrend(high, low, close)
                colors = [
                    THEME["supertrend_up"] if di == 1 else THEME["supertrend_down"] for di in d
                ]
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=st,
                        mode="lines+markers",
                        marker=dict(color=colors, size=3),
                        line=dict(width=2),
                        name="SuperTrend",
                    ),
                    row=cur,
                    col=1,
                )
                shown.append("supertrend")

            # -- Ichimoku Cloud --
            if "ichimoku" in indicators:
                ichi = calculate_ichimoku(high, low, close)
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=ichi["tenkan"],
                        line=dict(color=THEME["ichimoku_tenkan"], width=1.5),
                        name="Tenkan",
                    ),
                    row=cur,
                    col=1,
                )
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=ichi["kijun"],
                        line=dict(color=THEME["ichimoku_kijun"], width=1.5),
                        name="Kijun",
                    ),
                    row=cur,
                    col=1,
                )
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=ichi["senkou_a"],
                        line=dict(color=THEME["ichimoku_span_a"], width=0),
                        fill=None,
                        name="Cloud A",
                        showlegend=False,
                    ),
                    row=cur,
                    col=1,
                )
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=ichi["senkou_b"],
                        line=dict(color=THEME["ichimoku_span_b"], width=0),
                        fill="tonexty",
                        fillcolor=THEME["ichimoku_cloud_bull"],
                        name="Cloud",
                    ),
                    row=cur,
                    col=1,
                )
                shown.append("ichimoku")

            # -- Fisher Transform (overlay on price) --
            if "fisher" in indicators:
                fisher = calculate_fisher_transform(high, low, period=10)
                valid = ~np.isnan(fisher)
                if np.any(valid):
                    fig.add_trace(
                        go.Scatter(
                            x=np.array(x)[valid] if use_num_x else df.index[valid],
                            y=fisher[valid],
                            line=dict(color=THEME["fisher_line"], width=1.5),
                            name="Fisher",
                        ),
                        row=cur,
                        col=1,
                    )
                    fig.add_hline(
                        y=2.0,
                        line=dict(color="red", width=1, dash="dash"),
                        annotation_text="+2.0",
                        row=cur,
                        col=1,
                    )
                    fig.add_hline(
                        y=-2.0,
                        line=dict(color="green", width=1, dash="dash"),
                        annotation_text="-2.0",
                        row=cur,
                        col=1,
                    )
                    shown.append("fisher")

            # -- Pivot Points (horizontal lines) --
            if "pivots" in indicators:
                pivots = calculate_pivot_points(high, low, close)
                for name, val in [
                    ("R3", pivots["r3"]),
                    ("R2", pivots["r2"]),
                    ("R1", pivots["r1"]),
                    ("PP", pivots["pp"]),
                    ("S1", pivots["s1"]),
                    ("S2", pivots["s2"]),
                    ("S3", pivots["s3"]),
                ]:
                    c = (
                        THEME["resistance"]
                        if "R" in name
                        else THEME["support"]
                        if "S" in name
                        else THEME["pivot"]
                    )
                    fig.add_hline(
                        y=val,
                        line=dict(color=c, width=1, dash="dot"),
                        annotation_text=name,
                        annotation_position="right",
                        row=cur,
                        col=1,
                    )
                shown.append("pivots")

            price_row = cur
            cur += 1

            # ---------- Volume ----------
            if show_volume and volume is not None:
                vc = [
                    THEME["up_light"]
                    if df["Close"].iloc[i] >= df["Open"].iloc[i]
                    else THEME["down_light"]
                    for i in range(n)
                ]
                fig.add_trace(
                    go.Bar(x=x, y=volume, marker_color=vc, name="Volume", showlegend=False),
                    row=cur,
                    col=1,
                )
                fig.update_yaxes(title_text="Vol", row=cur, col=1)
                shown.append("volume")
                cur += 1

            # ---------- RSI ----------
            if show_rsi:
                rsi = calculate_rsi(close)
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=rsi,
                        line=dict(color=THEME["rsi"], width=1.5),
                        name="RSI",
                        showlegend=False,
                    ),
                    row=cur,
                    col=1,
                )
                for lvl in (30, 50, 70):
                    fig.add_hline(
                        y=lvl, line=dict(color="rgba(255,255,255,0.3)", dash="dash"), row=cur, col=1
                    )
                fig.update_yaxes(title_text="RSI", range=[0, 100], row=cur, col=1)
                shown.append("rsi")
                cur += 1

            # ---------- MACD ----------
            if show_macd:
                ml, sl, hist = calculate_macd(close)
                hc = [THEME["hist_pos"] if v >= 0 else THEME["hist_neg"] for v in hist]
                fig.add_trace(
                    go.Bar(x=x, y=hist, marker_color=hc, name="Hist", showlegend=False),
                    row=cur,
                    col=1,
                )
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=ml,
                        line=dict(color=THEME["macd_line"], width=1.5),
                        name="MACD",
                        showlegend=False,
                    ),
                    row=cur,
                    col=1,
                )
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=sl,
                        line=dict(color=THEME["signal_line"], width=1.5),
                        name="Signal",
                        showlegend=False,
                    ),
                    row=cur,
                    col=1,
                )
                fig.update_yaxes(title_text="MACD", row=cur, col=1)
                shown.append("macd")
                cur += 1

            # ---------- ATR ----------
            if show_atr:
                atr = calculate_atr(high, low, close)
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=atr,
                        line=dict(color=THEME["atr"], width=1.5),
                        name="ATR",
                        showlegend=False,
                    ),
                    row=cur,
                    col=1,
                )
                fig.update_yaxes(title_text="ATR", row=cur, col=1)
                shown.append("atr")
                cur += 1

            # ---------- OBV ----------
            if show_obv and volume is not None:
                obv = calculate_obv(close, volume)
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=obv,
                        line=dict(color=THEME["obv"], width=1.5),
                        name="OBV",
                        showlegend=False,
                    ),
                    row=cur,
                    col=1,
                )
                fig.update_yaxes(title_text="OBV", row=cur, col=1)
                shown.append("obv")
                cur += 1

            # ---------- Layout ----------
            pchange = ((close[-1] - close[0]) / close[0]) * 100
            arrow = "▲" if pchange >= 0 else "▼"
            cc = THEME["up_color"] if pchange >= 0 else THEME["down_color"]
            title = (
                f"<b>{symbol}</b> · {timeframe} · "
                f"<span style='color:{cc}'>{arrow} {abs(pchange):.2f}%</span> · "
                f"Last: {close[-1]:,.2f}"
            )
            fig.update_layout(
                title=dict(
                    text=title, font=dict(size=16, color=THEME["text"]), x=0.01, xanchor="left"
                ),
                plot_bgcolor=THEME["background"],
                paper_bgcolor=THEME["background"],
                font=dict(color=THEME["text"], size=11),
                xaxis_rangeslider_visible=False,
                showlegend=True,
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.01,
                    xanchor="left",
                    x=0,
                    bgcolor="rgba(0,0,0,0)",
                    font=dict(size=10),
                ),
                margin=dict(l=60, r=30, t=60, b=30),
                width=self._width,
                height=self._height,
            )
            for i in range(1, rows + 1):
                xkw: dict = dict(gridcolor=THEME["grid"], showgrid=True, zeroline=False)
                if use_num_x and x_tickvals:
                    xkw.update(tickvals=x_tickvals, ticktext=x_ticktext, tickangle=-45)
                else:
                    xkw.update(type="date", nticks=min(n, 20))
                fig.update_xaxes(**xkw, row=i, col=1)
                fig.update_yaxes(
                    gridcolor=THEME["grid"],
                    showgrid=True,
                    zeroline=False,
                    side="right",
                    row=i,
                    col=1,
                )
            fig.update_yaxes(title_text="Price", row=price_row, col=1)
            fig.update_xaxes(rangeslider_visible=False)

            # ---------- Export ----------
            img_bytes = fig.to_image(format="png", scale=2)
            img_b64 = base64.b64encode(img_bytes).decode()

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fp = self._save_dir / f"{symbol}_{timeframe}_{ts}_pro.png"
            fp.write_bytes(img_bytes)

            elapsed = (time.time() - t0) * 1000

            # Indicator summary for visual agent
            summary = generate_indicator_summary(close, high, low, volume, indicators)

            return ChartResult(
                image_b64=img_b64,
                filepath=str(fp),
                backend=self.backend,
                symbol=symbol,
                timeframe=timeframe,
                generation_ms=elapsed,
                indicators_shown=shown,
                description=f"Plotly pro chart {symbol} {timeframe} ({n} candles)",
                indicators_summary=summary,
            )

        except Exception as e:
            logger.error("PlotlyChartStrategy.render failed: %s", e, exc_info=True)
            return ChartResult(
                error=str(e), backend=self.backend, symbol=symbol, timeframe=timeframe
            )
