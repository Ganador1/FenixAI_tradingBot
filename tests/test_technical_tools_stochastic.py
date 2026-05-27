import logging
import warnings
from types import SimpleNamespace

import numpy as np

from src.tools import technical_tools


def _reset_buffers() -> None:
    technical_tools.open_buf.clear()
    technical_tools.close_buf.clear()
    technical_tools.high_buf.clear()
    technical_tools.low_buf.clear()
    technical_tools.vol_buf.clear()
    technical_tools.timestamp_buf.clear()
    technical_tools._latest_indicators_cache.clear()


def test_manual_stochastic_does_not_log_empty_slice_errors(caplog, monkeypatch) -> None:
    _reset_buffers()
    monkeypatch.setattr(technical_tools, "talib", None)

    base = 100.0
    for idx in range(20):
        open_price = base + idx * 0.5
        close_price = open_price + 0.2
        technical_tools.open_buf.append(open_price)
        technical_tools.close_buf.append(close_price)
        technical_tools.high_buf.append(close_price + 0.4)
        technical_tools.low_buf.append(open_price - 0.4)
        technical_tools.vol_buf.append(1000.0 + idx)
        technical_tools.timestamp_buf.append(1_700_000_000_000 + idx * 60_000)

    with caplog.at_level(logging.WARNING):
        with technical_tools._buffer_lock:
            technical_tools._calculate_and_store_all_indicators()

    assert "stoch_k" in technical_tools._latest_indicators_cache
    assert "stoch_d" in technical_tools._latest_indicators_cache
    assert not any("Error calculating Stochastic" in rec.message for rec in caplog.records)


def test_garch_fit_suppresses_convergence_warning(monkeypatch) -> None:
    _reset_buffers()

    seen_kwargs = {}

    class FakeArchModel:
        def fit(self, **kwargs):
            seen_kwargs.update(kwargs)
            if kwargs.get("show_warning", True):
                warnings.warn("The optimizer returned code 9.", UserWarning, stacklevel=2)
            return SimpleNamespace(
                forecast=lambda horizon: SimpleNamespace(variance=SimpleNamespace(values=np.array([[1.25]])))
            )

    monkeypatch.setattr(technical_tools, "arch_available", True)
    monkeypatch.setattr(technical_tools, "arch_model", lambda *args, **kwargs: FakeArchModel())
    monkeypatch.setattr(technical_tools, "ArchConvergenceWarning", UserWarning)

    base = 100.0
    for idx in range(35):
        close_price = base + np.sin(idx / 3) * 0.2 + idx * 0.01
        technical_tools.open_buf.append(close_price - 0.05)
        technical_tools.close_buf.append(close_price)
        technical_tools.high_buf.append(close_price + 0.2)
        technical_tools.low_buf.append(close_price - 0.2)
        technical_tools.vol_buf.append(1000.0 + idx)
        technical_tools.timestamp_buf.append(1_700_000_000_000 + idx * 60_000)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with technical_tools._buffer_lock:
            technical_tools._calculate_and_store_all_indicators()

    assert seen_kwargs["show_warning"] is False
    assert "garch_volatility_forecast" in technical_tools._latest_indicators_cache
    assert not any("optimizer returned code" in str(w.message) for w in caught)
