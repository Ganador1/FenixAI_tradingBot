from datetime import datetime, timedelta

import numpy as np
import pytest

from src.tools import professional_chart_generator as pcg


def _sample_kline_data(n: int = 80, start_price: float = 100.0) -> dict[str, list]:
    now = datetime(2026, 7, 2, 12, 0, 0)
    timestamps = [int((now - timedelta(minutes=15 * (n - i))).timestamp() * 1000) for i in range(n)]
    closes = [start_price + (i * 0.25) + (np.sin(i / 4) * 0.6) for i in range(n)]
    opens = [closes[i - 1] if i else start_price for i in range(n)]
    highs = [max(open_, close) + 0.8 for open_, close in zip(opens, closes)]
    lows = [min(open_, close) - 0.8 for open_, close in zip(opens, closes)]
    volumes = [1000 + (i * 7) for i in range(n)]

    return {
        "timestamp": timestamps,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }


def test_supertrend_warmup_uses_nan_instead_of_zero_price_anchor():
    close = np.linspace(100.0, 120.0, 40)
    high = close + 1.0
    low = close - 1.0

    supertrend, direction = pcg.calculate_supertrend(high, low, close, period=10)

    assert np.all(np.isnan(supertrend[:10]))
    assert np.all(np.isfinite(supertrend[10:]))
    assert np.nanmin(supertrend) > 0
    assert set(np.unique(direction)).issubset({-1.0, 1.0})


def test_plotly_professional_honors_engine_visual_indicator_contract(monkeypatch, tmp_path):
    if not pcg.PLOTLY_AVAILABLE:
        pytest.skip("Plotly is not installed")

    captured: dict[str, list] = {"traces": [], "hlines": []}

    class FakeFigure:
        def add_trace(self, trace, *args, **kwargs):
            captured["traces"].append(trace)

        def add_hline(self, *args, **kwargs):
            captured["hlines"].append(kwargs)

        def update_layout(self, *args, **kwargs):
            return None

        def update_xaxes(self, *args, **kwargs):
            return None

        def update_yaxes(self, *args, **kwargs):
            return None

        def to_image(self, *args, **kwargs):
            return b"fake-png"

    monkeypatch.setattr(pcg, "make_subplots", lambda *args, **kwargs: FakeFigure())

    generator = pcg.ProfessionalChartGenerator(save_path=str(tmp_path))
    result = generator.generate_chart(
        kline_data=_sample_kline_data(),
        symbol="ETHUSDC",
        timeframe="15m",
        show_indicators=[
            "ema_9",
            "ema_21",
            "ema_50",
            "bb_bands",
            "supertrend",
            "vwap",
            "pivots",
        ],
        show_volume=True,
        show_rsi=False,
        show_macd=False,
    )

    trace_names = [getattr(trace, "name", None) for trace in captured["traces"]]
    assert result["generator"] == "plotly_professional"
    assert result["timeframe"] == "15m"
    assert "EMA 9" in trace_names
    assert "EMA 21" in trace_names
    assert "EMA 50" in trace_names
    assert "SMA 50" not in trace_names
    assert "BB Lower" in trace_names
    assert "VWAP" in trace_names
    assert "SuperTrend" in trace_names
    assert "Volume" in trace_names

    pivot_labels = [line.get("annotation_text") for line in captured["hlines"]]
    assert pivot_labels == ["R3", "R2", "R1", "PP", "S1", "S2", "S3"]

    summary = result["indicators_summary"]
    assert "ema_50" in summary
    assert "vwap" in summary
    assert "supertrend" in summary
    assert set(summary["pivots"]) == {"r3", "r2", "r1", "pp", "s1", "s2", "s3"}
