import pytest

from nanofenixv3.executor import PaperExecutor, Position


def test_short_trailing_stop_does_not_close_when_net_after_fees_is_negative():
    executor = PaperExecutor(balance=10_000.0)
    executor._position = Position(
        direction="SHORT",
        entry_price=100.0,
        quantity=10.0,
        entry_bar=0,
    )
    executor._position.peak_price = 99.75
    executor._position.trailing_active = True

    reason = executor.on_bar(
        bar_idx=120,
        close=99.96,
        signal="SHORT",
        pred_bps=-1.0,
        ema_trend=-1.0,
        direction_accuracy=0.60,
        companion_ready=True,
        volatility_state="LOW",
    )

    assert reason is None
    assert executor.position is not None
    assert executor.n_trades == 0


def test_short_trailing_stop_closes_when_net_after_fees_is_positive():
    executor = PaperExecutor(balance=10_000.0)
    executor._position = Position(
        direction="SHORT",
        entry_price=100.0,
        quantity=10.0,
        entry_bar=0,
    )
    executor._position.peak_price = 99.70
    executor._position.trailing_active = True

    reason = executor.on_bar(
        bar_idx=120,
        close=99.91,
        signal="SHORT",
        pred_bps=-1.0,
        ema_trend=-1.0,
        direction_accuracy=0.60,
        companion_ready=True,
        volatility_state="LOW",
    )

    assert reason == "TRAILING_STOP"
    assert executor.position is None
    assert executor.n_trades == 1
    assert executor._trades[-1].pnl_net > 0
