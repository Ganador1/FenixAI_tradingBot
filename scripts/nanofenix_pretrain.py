#!/usr/bin/env python3
"""
NanoFenix V3.5 Pre-training Script
===================================
Downloads historical 1-second bars from Binance Futures and
pre-trains both horizon models with massive data.

Usage:
    python scripts/nanofenix_pretrain.py --symbol BTCUSDT --hours 24
    python scripts/nanofenix_pretrain.py --symbol BTCUSDT --hours 168  # 1 week

This creates a pretrained model file:
    nanofenixv3/pretrained_btcusdt.pkl

With enough data, the models learn:
- Direction patterns across multiple market regimes
- Volatility-dependent signal strength
- When to predict SMALL moves (market is dead) vs LARGE moves (volatile)

Key insight: With only ~300 bars of live data, the model sees maybe
1 regime change. With 24h of data (86400 bars), it sees dozens.
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import requests

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from nanofenixv3.feature_engine import FEATURE_LOOKBACK, Bar, MultiScaleFeatureEngine
from nanofenixv3.predictor import DualHorizonPredictor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("NanoFenixPretrain")


def download_futures_aggtrades(
    symbol: str, start_ms: int, end_ms: int, batch_size: int = 1000
) -> list[dict]:
    """Download aggTrades from Binance Futures in batches."""
    base_url = "https://fapi.binance.com/fapi/v1/aggTrades"
    all_trades = []
    current_start = start_ms

    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": batch_size,
        }
        try:
            resp = requests.get(base_url, params=params, timeout=30)
            resp.raise_for_status()
            trades = resp.json()
            if not trades:
                break
            all_trades.extend(trades)
            # Move to next batch (last trade time + 1)
            current_start = trades[-1]["T"] + 1
            if len(trades) < batch_size:
                break
        except Exception as e:
            logger.warning(f"Error downloading trades: {e}")
            time.sleep(1)

    return all_trades


def download_futures_klines(symbol: str, interval: str = "1m", hours: int = 24) -> list[list]:
    """Download klines (OHLCV) from Binance Futures."""
    base_url = "https://fapi.binance.com/fapi/v1/klines"
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - hours * 3600 * 1000
    all_klines = []
    current_start = start_ms

    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": 1500,
        }
        try:
            resp = requests.get(base_url, params=params, timeout=30)
            resp.raise_for_status()
            klines = resp.json()
            if not klines:
                break
            all_klines.extend(klines)
            # Move to next batch
            current_start = klines[-1][0] + 1  # open time + 1ms
            if len(klines) < 1500:
                break
        except Exception as e:
            logger.warning(f"Error downloading klines: {e}")
            time.sleep(1)

    return all_klines


def aggtrades_to_bars(trades: list[dict], interval_sec: float = 1.0) -> list[Bar]:
    """Convert aggTrades into 1-second bars compatible with NanoFenix feature engine."""
    if not trades:
        return []

    bars = []
    bar_start_ms = None
    bar_mid_sum = 0
    bar_mid_count = 0
    bar_buy_vol = 0.0
    bar_sell_vol = 0.0
    bar_buy_count = 0
    bar_sell_count = 0

    for trade in trades:
        price = float(trade["p"])
        qty = float(trade["q"])
        ts = int(trade["T"])
        is_sell = trade["m"]  # m=True means buyer is maker → sell

        # Mid price approximation (bookTicker not available in historical)
        mid = price
        obi = -1.0 if is_sell else 1.0  # Simplified OBI from trade direction
        spread_bps = 1.0  # Default spread

        if bar_start_ms is None:
            bar_start_ms = ts - (ts % 1000)  # Align to second boundary

        bar_mid_sum += mid
        bar_mid_count += 1

        if is_sell:
            bar_sell_vol += qty
            bar_sell_count += 1
        else:
            bar_buy_vol += qty
            bar_buy_count += 1

        # Check if we've moved to a new second
        next_second = bar_start_ms + int(interval_sec * 1000)
        if ts >= next_second or trade == trades[-1]:
            if bar_mid_count > 0:
                avg_mid = bar_mid_sum / bar_mid_count
                total_vol = bar_buy_vol + bar_sell_vol
                buy_vol_ratio = bar_buy_vol / max(total_vol, 0.001)
                trade_direction = (bar_buy_count - bar_sell_count) / max(
                    bar_buy_count + bar_sell_count, 1
                )
                vwap = avg_mid  # Simplified

                bar = Bar(
                    timestamp=bar_start_ms / 1000.0,
                    open=avg_mid,
                    high=avg_mid * 1.00001,  # Approximate
                    low=avg_mid * 0.99999,
                    close=avg_mid,
                    tick_count=bar_mid_count,
                    obi_sum=obi * bar_mid_count,
                    spread_sum=spread_bps * bar_mid_count,
                    buy_vol=bar_buy_vol,
                    sell_vol=bar_sell_vol,
                    buy_count=bar_buy_count,
                    sell_count=bar_sell_count,
                    vwap_num=avg_mid * (bar_buy_vol + bar_sell_vol),
                    vwap_den=max(bar_buy_vol + bar_sell_vol, 0.001),
                )
                bars.append(bar)

            # Reset for next bar
            bar_start_ms = next_second
            bar_mid_sum = 0
            bar_mid_count = 0
            bar_buy_vol = 0.0
            bar_sell_vol = 0.0
            bar_buy_count = 0
            bar_sell_count = 0

    return bars


def klines_to_bars(klines: list[list]) -> list[Bar]:
    """Convert 1-minute klines into 1-second pseudo-bars for feature computation.

    This is less ideal than real 1s bars but much faster to download.
    We split each 1m candle into 60 pseudo-bars with interpolated prices.
    """
    bars = []

    for k in klines:
        open_time = k[0] / 1000.0
        open_p = float(k[1])
        high_p = float(k[2])
        low_p = float(k[3])
        close_p = float(k[4])
        volume = float(k[5])
        tbbv = float(k[9]) if len(k) > 9 else volume * 0.5  # Taker buy vol
        count = int(k[8]) if len(k) > 8 else 10

        buy_vol_ratio = tbbv / max(volume, 0.001)
        trade_direction = (buy_vol_ratio - 0.5) * 2.0  # [-1, +1]

        # Split 1m candle into 60 pseudo-1s bars
        for sec in range(60):
            # Linear interpolation: open → high(1/3) → low(2/3) → close
            t = sec / 59.0
            if t < 0.33:
                # Open to High phase
                local_t = t / 0.33
                mid = open_p + (high_p - open_p) * local_t
            elif t < 0.67:
                # High to Low phase
                local_t = (t - 0.33) / 0.34
                mid = high_p + (low_p - high_p) * local_t
            else:
                # Low to Close phase
                local_t = (t - 0.67) / 0.33
                mid = low_p + (close_p - low_p) * local_t

            obi = trade_direction  # Simplified
            spread_bps = 1.0

            bar = Bar(
                timestamp=open_time + sec,
                open=mid,
                high=mid * 1.000005,
                low=mid * 0.999995,
                close=mid,
                tick_count=max(1, count // 60),
                obi_sum=obi,
                spread_sum=spread_bps,
                buy_vol=volume * buy_vol_ratio / 60.0,
                sell_vol=volume * (1 - buy_vol_ratio) / 60.0,
                buy_count=max(1, int(count * buy_vol_ratio) // 60),
                sell_count=max(1, int(count * (1 - buy_vol_ratio)) // 60),
                vwap_num=mid * volume / 60.0,
                vwap_den=max(volume / 60.0, 0.001),
            )
            bars.append(bar)

    return bars


def pretrain_with_bars(
    symbol: str,
    bars: list[Bar],
    output_path: str,
    min_samples: int = 200,
    retrain_every: int = 300,
) -> DualHorizonPredictor:
    """Feed bars through feature engine and pre-train the predictor."""

    # Override params for pretraining
    os.environ["NANOFENIXV3_MIN_SAMPLES"] = str(min_samples)
    os.environ["NANOFENIXV3_RETRAIN_EVERY"] = str(retrain_every)

    predictor = DualHorizonPredictor(model_path="")  # No existing model
    feature_engine = MultiScaleFeatureEngine()

    warmup_needed = FEATURE_LOOKBACK
    features_collected = 0
    last_log = 0

    logger.info(f"Processing {len(bars)} bars through feature engine...")
    logger.info(f"Need {warmup_needed} bars for warmup, then collecting features for training...")

    for i, bar in enumerate(bars):
        feature_engine.add_bar(bar)

        if not feature_engine.is_warm:
            if i - last_log >= 5000:
                logger.info(f"  Warmup: {feature_engine.bar_count}/{warmup_needed} bars")
                last_log = i
            continue

        # Compute features
        features = feature_engine.compute_features()
        if features is None:
            continue

        close = feature_engine.current_mid()
        predictor.store(features, close)
        features_collected += 1

        # Attempt retrain
        if predictor.should_retrain():
            predictor.retrain()

        # Evaluate direction accuracy with available bars
        if feature_engine.bar_count % 30 == 0:
            predictor.evaluate_direction(feature_engine.bar_count, close)

        # Progress logging
        if features_collected - last_log >= 5000:
            last_log = features_collected
            short_trained = "✅" if predictor._short.trained else "❌"
            long_trained = "✅" if predictor._long.trained else "❌"
            logger.info(
                f"  Features: {features_collected} | "
                f"Short: {short_trained} (acc={predictor._short.val_accuracy * 100:.0f}%) | "
                f"Long: {long_trained} (acc={predictor._long.val_accuracy * 100:.0f}%)"
            )

    # Final training attempt
    if predictor.should_retrain():
        predictor.retrain()

    # Save model
    predictor.save_model(output_path)

    # Summary
    short_trained = predictor._short.trained
    long_trained = predictor._long.trained
    logger.info("=" * 65)
    logger.info("PRE-TRAINING COMPLETE")
    logger.info(f"  Bars processed: {len(bars)}")
    logger.info(f"  Features collected: {features_collected}")
    logger.info(
        f"  Short model (30s): {'✅ Trained' if short_trained else '❌ Failed'} "
        f"(val_acc={predictor._short.val_accuracy * 100:.1f}%)"
    )
    logger.info(
        f"  Long model (120s): {'✅ Trained' if long_trained else '❌ Failed'} "
        f"(val_acc={predictor._long.val_accuracy * 100:.1f}%)"
    )
    logger.info(f"  Model saved to: {output_path}")
    logger.info("=" * 65)

    return predictor


def main():
    parser = argparse.ArgumentParser(description="NanoFenix V3.5 Pre-training")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading pair")
    parser.add_argument("--hours", type=int, default=24, help="Hours of history to download")
    parser.add_argument(
        "--source",
        choices=["klines", "aggtrades"],
        default="klines",
        help="Data source (klines=fast, aggtrades=precise)",
    )
    parser.add_argument("--min-samples", type=int, default=200, help="Min samples for training")
    parser.add_argument("--output", default=None, help="Output model path")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    hours = args.hours
    output_path = args.output or f"nanofenixv3/pretrained_{symbol.lower()}.pkl"

    logger.info(f"🚀 NanoFenix V3.5 Pre-training: {symbol} ({hours}h)")

    if args.source == "klines":
        logger.info(f"Downloading {hours}h of 1m klines from Binance Futures...")
        klines = download_futures_klines(symbol, "1m", hours)
        logger.info(f"Got {len(klines)} 1m candles → ~{len(klines) * 60} pseudo-1s bars")
        bars = klines_to_bars(klines)
    else:
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - hours * 3600 * 1000
        logger.info(f"Downloading aggTrades from {hours}h ago...")
        trades = download_futures_aggtrades(symbol, start_ms, end_ms)
        logger.info(f"Got {len(trades)} aggTrades → converting to 1s bars")
        bars = aggtrades_to_bars(trades)

    if not bars:
        logger.error("No bars downloaded. Exiting.")
        sys.exit(1)

    logger.info(f"Processing {len(bars)} bars for pre-training...")
    pretrain_with_bars(
        symbol=symbol,
        bars=bars,
        output_path=output_path,
        min_samples=args.min_samples,
    )


if __name__ == "__main__":
    main()
