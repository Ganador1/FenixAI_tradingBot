from src.tools.technical_tools import close_buf, high_buf, low_buf, open_buf
from src.trading.engine import TradingEngine


def _reset_buffers() -> None:
    open_buf.clear()
    close_buf.clear()
    high_buf.clear()
    low_buf.clear()


def test_rebound_guard_blocks_sell_bottom_rebound() -> None:
    _reset_buffers()
    open_buf.append(100.0)
    close_buf.append(100.5)
    high_buf.append(101.0)
    low_buf.append(98.0)  # strong lower wick

    engine = TradingEngine(symbol="BTCUSDT", timeframe="3m", paper_trading=True)
    lower_wick_ratio, _ = engine._get_last_wick_ratios()

    blocked = engine._should_block_sell_rebound(
        chop=60.0,
        chop_regime="CHOPPY",
        rsi=30.0,
        percent_b=0.05,
        lower_wick_ratio=lower_wick_ratio,
        obi=1.20,
        wdi=0.10,
        vol_imb=0.05,
    )
    assert blocked is True


def test_rebound_guard_does_not_block_without_bullish_microstructure() -> None:
    _reset_buffers()
    open_buf.append(100.0)
    close_buf.append(100.5)
    high_buf.append(101.0)
    low_buf.append(98.0)

    engine = TradingEngine(symbol="BTCUSDT", timeframe="3m", paper_trading=True)
    lower_wick_ratio, _ = engine._get_last_wick_ratios()

    blocked = engine._should_block_sell_rebound(
        chop=60.0,
        chop_regime="CHOPPY",
        rsi=30.0,
        percent_b=0.05,
        lower_wick_ratio=lower_wick_ratio,
        obi=0.95,
        wdi=-0.02,
        vol_imb=-0.01,
    )
    assert blocked is False


def test_rejection_guard_blocks_buy_top_rejection() -> None:
    _reset_buffers()
    open_buf.append(100.5)
    close_buf.append(100.0)
    high_buf.append(102.0)  # strong upper wick
    low_buf.append(99.5)

    engine = TradingEngine(symbol="BTCUSDT", timeframe="3m", paper_trading=True)
    _, upper_wick_ratio = engine._get_last_wick_ratios()

    blocked = engine._should_block_buy_rejection(
        chop=62.0,
        chop_regime="CHOPPY",
        rsi=70.0,
        percent_b=0.95,
        upper_wick_ratio=upper_wick_ratio,
        obi=0.80,
        wdi=-0.08,
        vol_imb=-0.05,
    )
    assert blocked is True


def test_rebound_guard_respects_chop_only_gate() -> None:
    _reset_buffers()
    open_buf.append(100.0)
    close_buf.append(100.5)
    high_buf.append(101.0)
    low_buf.append(98.0)

    engine = TradingEngine(symbol="BTCUSDT", timeframe="3m", paper_trading=True)
    lower_wick_ratio, _ = engine._get_last_wick_ratios()

    blocked = engine._should_block_sell_rebound(
        chop=30.0,
        chop_regime="TREND",
        rsi=30.0,
        percent_b=0.05,
        lower_wick_ratio=lower_wick_ratio,
        obi=1.30,
        wdi=0.20,
        vol_imb=0.08,
    )
    assert blocked is False
