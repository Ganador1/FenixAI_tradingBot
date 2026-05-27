from datetime import datetime, timedelta, timezone

import pytest

from src.trading.market_data import MarketDataManager, OrderBookSnapshot


def test_microprice_leans_toward_liquidity_side():
    md = MarketDataManager(symbol="BTCUSDT", timeframe="1m", use_testnet=True)
    md.orderbook = OrderBookSnapshot(
        bids=[[99.0, 2.0]],
        asks=[[101.0, 1.0]],
        timestamp=datetime.now(timezone.utc),
    )

    metrics = md.get_microstructure_metrics()

    # microprice = (bid_p * ask_q + ask_p * bid_q) / (bid_q + ask_q)
    expected = (99.0 * 1.0 + 101.0 * 2.0) / 3.0
    assert metrics.mid_price == pytest.approx(100.0)
    assert metrics.microprice == pytest.approx(expected)
    assert metrics.microprice > metrics.mid_price
    assert metrics.microprice_bps > 0


def test_trade_imbalance_uses_recent_window():
    md = MarketDataManager(symbol="BTCUSDT", timeframe="1m", use_testnet=True)
    md._trade_imbalance_window_sec = 10.0

    now = datetime.now(timezone.utc)
    md.trade_buffer.clear()
    md.trade_buffer.append({"qty": 1.0, "side": "buy", "price": 100.0, "timestamp": now - timedelta(seconds=20)})
    md.trade_buffer.append({"qty": 1.0, "side": "sell", "price": 100.0, "timestamp": now - timedelta(seconds=1)})
    md.trade_buffer.append({"qty": 4.0, "side": "buy", "price": 100.0, "timestamp": now - timedelta(seconds=1)})

    metrics = md.get_microstructure_metrics()

    # Only the 2 recent trades should count: buy=4, sell=1 => (4-1)/5 = 0.6
    assert metrics.trade_count_5s == 2
    assert metrics.trade_volume_5s == pytest.approx(5.0)
    assert metrics.trade_imbalance_5s == pytest.approx(0.6)
    assert metrics.trade_buy_vol_5s == pytest.approx(4.0)
    assert metrics.trade_sell_vol_5s == pytest.approx(1.0)
    assert metrics.cvd_delta_5s == pytest.approx(3.0)
    assert metrics.trade_intensity_5s == pytest.approx(0.2)
    assert metrics.avg_trade_size_5s == pytest.approx(2.5)


def test_futures_trade_stream_uses_raw_trade_feed():
    md = MarketDataManager(symbol="SOLUSDT", timeframe="15m", use_testnet=False)

    assert md.trade_ws_url == "wss://fstream.binance.com/public/ws/solusdt@trade"


def test_futures_streams_use_binance_routed_endpoints():
    md = MarketDataManager(symbol="SOLUSDT", timeframe="15m", use_testnet=False)

    assert md.kline_ws_url == "wss://fstream.binance.com/market/ws/solusdt@kline_15m"
    assert md.depth_ws_url == "wss://fstream.binance.com/public/ws/solusdt@depth20@100ms"
    assert md.trade_ws_url == "wss://fstream.binance.com/public/ws/solusdt@trade"


@pytest.mark.asyncio
async def test_process_kline_accepts_combined_stream_envelope():
    md = MarketDataManager(symbol="SOLUSDT", timeframe="15m", use_testnet=False)
    seen = []
    md.on_kline(lambda kline: seen.append(kline))

    await md._process_kline(
        {
            "stream": "solusdt@kline_15m",
            "data": {
                "e": "kline",
                "s": "SOLUSDT",
                "k": {
                    "t": 1778160600000,
                    "T": 1778161499999,
                    "s": "SOLUSDT",
                    "i": "15m",
                    "o": "88.10",
                    "c": "88.55",
                    "h": "88.80",
                    "l": "87.90",
                    "v": "12345.67",
                    "x": True,
                },
            },
        }
    )

    assert len(seen) == 1
    assert seen[0]["symbol"] == "SOLUSDT"
    assert seen[0]["timeframe"] == "15m"
    assert seen[0]["is_closed"] is True
    assert seen[0]["close"] == pytest.approx(88.55)


def test_wdi_and_liquidity_gap_metrics():
    md = MarketDataManager(symbol="BTCUSDT", timeframe="1m", use_testnet=True)
    md.orderbook = OrderBookSnapshot(
        bids=[[99.0, 2.0], [98.0, 1.0]],
        asks=[[101.0, 1.0], [102.0, 1.0]],
        timestamp=datetime.now(timezone.utc),
    )

    metrics = md.get_microstructure_metrics()

    # WDI uses weights=1/(level+1): here 2 levels.
    # bid_w = 2*1 + 1*0.5 = 2.5
    # ask_w = 1*1 + 1*0.5 = 1.5
    # wdi = (2.5-1.5)/(2.5+1.5) = 0.25
    assert metrics.wdi == pytest.approx(0.25)

    bid_gap = (99.0 - 98.0) / 99.0 * 100.0
    ask_gap = (102.0 - 101.0) / 101.0 * 100.0
    assert metrics.liquidity_gap_pct == pytest.approx(max(bid_gap, ask_gap))
