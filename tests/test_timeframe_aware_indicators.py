from src.indicators.timeframe_aware_indicators import get_optimal_indicators, MarketRegime


def test_technical_1m_trending_keeps_minimum_primary_indicators():
    suite = get_optimal_indicators(
        timeframe="1m",
        available_feeds=["ohlcv", "volume"],
        available_indicators=["hull_ma", "vwap", "fisher_transform", "atr", "sar", "rsi"],
        chop=30.0,  # TRENDING
    )

    assert suite.market_regime == MarketRegime.TRENDING
    assert len(suite.primary_indicators) >= 3
    assert "fisher_transform" in suite.primary_indicators


def test_qabba_1m_has_richer_microstructure_primary_set_when_feeds_available():
    suite = get_optimal_indicators(
        timeframe="1m",
        available_feeds=["orderbook", "trades"],
        chop=30.0,  # TRENDING
    )

    # Expect key microstructure indicators to be selected when feeds allow it.
    assert "obi" in suite.primary_indicators
    assert any(x in suite.primary_indicators for x in ["ofi", "mlofi", "volume_imbalance"])


def test_microstructure_3m_prefers_orderbook_and_trade_signals_when_feeds_available():
    suite = get_optimal_indicators(
        timeframe="3m",
        available_feeds=["orderbook", "trades"],
        chop=30.0,  # TRENDING
    )

    assert suite.market_regime == MarketRegime.TRENDING
    assert "obi" in suite.primary_indicators
    assert "cvd" in suite.primary_indicators
