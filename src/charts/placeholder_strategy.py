# src/charts/placeholder_strategy.py
"""
Placeholder chart strategy — emergency fallback.

Generates a simple text-only image when no real charting library
is available. Keeps the pipeline working even in minimal environments.
"""

from __future__ import annotations

import base64
import io
import logging
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from src.charts.strategy import ChartResult, ChartStrategy, RenderBackend

logger = logging.getLogger(__name__)


class PlaceholderChartStrategy(ChartStrategy):
    """Render a minimal text-only placeholder image."""

    @property
    def backend(self) -> RenderBackend:
        return RenderBackend.PLACEHOLDER

    @property
    def available(self) -> bool:
        return True  # matplotlib is always present

    def render(
        self,
        df=None,
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
        indicators: list[str] | None = None,
        *,
        show_volume: bool = True,
        show_rsi: bool = True,
        show_macd: bool = False,
        message: str = "No chart data available",
    ) -> ChartResult:
        t0 = time.time()
        fig, ax = plt.subplots(figsize=(10, 6), facecolor="#1e1e1e")
        ax.set_facecolor("#1e1e1e")
        ax.text(
            0.5,
            0.5,
            f"{symbol} · {timeframe}\n{message}",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=18,
            color="#cccccc",
        )
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        img_b64 = base64.b64encode(buf.read()).decode()

        elapsed = (time.time() - t0) * 1000
        return ChartResult(
            image_b64=img_b64,
            backend=self.backend,
            symbol=symbol,
            timeframe=timeframe,
            generation_ms=elapsed,
            description=f"Placeholder: {message}",
        )
