import pytest


def _book(bids, asks):
    return {
        "bids": [[float(p), float(q)] for p, q in bids],
        "asks": [[float(p), float(q)] for p, q in asks],
    }


def test_ofi_qi_mlofi_and_normalization_bid_adds_liquidity():
    from src.trading.market_data import MarketDataManager

    md = MarketDataManager(symbol="BTCUSDT", timeframe="1m", use_testnet=True)
    md._book_levels = 2

    md._update_orderbook(_book(bids=[(99.0, 2.0), (98.0, 1.0)], asks=[(101.0, 1.0), (102.0, 1.0)]))
    md._update_orderbook(_book(bids=[(99.0, 3.0), (98.0, 1.0)], asks=[(101.0, 1.0), (102.0, 1.0)]))

    m = md.get_microstructure_metrics()

    assert m.ofi == pytest.approx(1.0)
    assert m.mlofi == pytest.approx(1.0)
    assert m.tob_liquidity == pytest.approx(4.0)  # 3 + 1
    assert m.ofi_norm == pytest.approx(0.25)
    assert m.mlofi_norm == pytest.approx(1.0 / 6.0)
    assert m.qi == pytest.approx((3.0 - 1.0) / 4.0)
    assert m.volume_imbalance == pytest.approx((4.0 - 2.0) / 6.0)


def test_ofi_turns_negative_when_ask_liquidity_adds_at_same_price():
    from src.trading.market_data import MarketDataManager

    md = MarketDataManager(symbol="BTCUSDT", timeframe="1m", use_testnet=True)
    md._book_levels = 1

    md._update_orderbook(_book(bids=[(99.0, 2.0)], asks=[(101.0, 1.0)]))
    md._update_orderbook(_book(bids=[(99.0, 2.0)], asks=[(101.0, 2.0)]))

    m = md.get_microstructure_metrics()

    assert m.ofi == pytest.approx(-1.0)
    assert m.tob_liquidity == pytest.approx(4.0)  # 2 + 2
    assert m.ofi_norm == pytest.approx(-0.25)
    assert m.qi == pytest.approx((2.0 - 2.0) / 4.0)

