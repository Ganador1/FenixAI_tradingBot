"""
MiniFenix Offline Pre-training Script.

Trains the SOTA model with historical Binance data before live trading
starts. This solves the "cold start" problem where the model needs
~5000 ticks before it trains correctly.

Flow:
1. Download historical candles (1m, 5m, 15m) from Binance
2. Synthesise LOB ticks from OHLCV (simulates the @bookTicker WebSocket)
3. Train the LightGBM model with dynamic class weighting
4. Save the pretrained model for live use

Usage:
    python -m minifenix.pretrain_offline --symbol BTCUSDT --days 7 --output minifenix/sota_model_pretrained.joblib
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from typing import List, Tuple

import numpy as np
import pandas as pd
import joblib

# Import local modules
from .feature_engine import FeatureEngine, LOBSnapshot
from .sota_predictor import SOTAPredictor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("MiniFenix.Pretrain")


def download_historical_klines(
    symbol: str,
    interval: str = "1m",
    days: int = 7,
) -> pd.DataFrame:
    """
    Download historical candles from Binance using the public API.

    Args:
        symbol: Trading pair (e.g. BTCUSDT)
        interval: Time interval (1m, 5m, 15m, 1h)
        days: How many days of history to download

    Returns:
        DataFrame with columns: timestamp, open, high, low, close, volume
    """
    import requests

    logger.info(f"[DOWN] Downloading {days} days of data {symbol} @ {interval}")

    # Compute timestamps
    end_time = int(time.time() * 1000)
    start_time = end_time - (days * 24 * 60 * 60 * 1000)

    all_klines = []
    current_start = start_time

    # Binance limits to 1000 candles per request
    while current_start < end_time:
        url = (
            f"https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}"
            f"&interval={interval}"
            f"&startTime={current_start}"
            f"&limit=1000"
        )

        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()

            if not data:
                break

            all_klines.extend(data)

            # Update start_time for the next iteration
            current_start = data[-1][0] + 1  # +1ms to avoid duplicates

            logger.info(f"   Downloaded {len(all_klines)} candles...")
            time.sleep(0.1)  # Rate limiting

        except Exception as e:
            logger.error(f"   Download error: {e}")
            break

    if not all_klines:
        raise ValueError("Could not download data")

    # Convert to DataFrame
    df = pd.DataFrame(all_klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
        'taker_buy_quote', 'ignore'
    ])

    # Convert types
    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.set_index('timestamp')[numeric_cols]

    logger.info(f"[OK] Downloaded {len(df)} candles ({df.index[0]} to {df.index[-1]})")
    return df


def synthesize_lob_ticks(
    df: pd.DataFrame,
    ticks_per_candle: int = 200,
) -> List[LOBSnapshot]:
    """
    Synthesise Limit Order Book ticks from OHLCV candles.

    Simulates the Binance @bookTicker stream by generating realistic
    bid/ask values based on each candle's open/high/low/close.

    Args:
        df: DataFrame with OHLCV candles
        ticks_per_candle: How many ticks to synthesise per candle

    Returns:
        List of synthetic LOBSnapshot objects
    """
    logger.info(f"[LAB] Synthesising ~{len(df) * ticks_per_candle:,} LOB ticks...")

    ticks = []

    for idx, (timestamp, row) in enumerate(df.iterrows()):
        open_p, high, low, close, volume = row

        # Estimate typical spread based on candle volatility
        price_range = high - low
        avg_price = (open_p + high + low + close) / 4

        # Typical spread: 0.5-2 bps depending on volatility
        base_spread_bps = 1.0 + (price_range / avg_price * 10000) * 0.1
        spread = avg_price * base_spread_bps / 10000

        # Generate ticks within the candle
        # Simulate the price path: open -> high/low -> close
        if high - open_p > open_p - low:
            # Up-trending candle
            price_path = np.concatenate([
                np.linspace(open_p, high, ticks_per_candle // 2),
                np.linspace(high, close, ticks_per_candle - ticks_per_candle // 2)
            ])
        else:
            # Down-trending candle
            price_path = np.concatenate([
                np.linspace(open_p, low, ticks_per_candle // 2),
                np.linspace(low, close, ticks_per_candle - ticks_per_candle // 2)
            ])

        # Add microstructure noise
        noise = np.random.normal(0, spread * 0.1, len(price_path))
        price_path = price_path + noise

        # Generate bid/ask and quantities
        for i, price in enumerate(price_path):
            # Spread varies with time inside the candle
            current_spread = spread * (1 + 0.3 * np.sin(i / ticks_per_candle * np.pi))

            bid = price - current_spread / 2
            ask = price + current_spread / 2

            # Quantities: simulate buy-side/sell-side pressure
            # If we are walking up the path, more buyers
            if i > 0 and price_path[i] > price_path[i-1]:
                bid_qty = np.random.exponential(2.0) * 0.5  # More buy-side
                ask_qty = np.random.exponential(1.0) * 0.5
            else:
                bid_qty = np.random.exponential(1.0) * 0.5
                ask_qty = np.random.exponential(2.0) * 0.5  # More sell-side

            # Incremental timestamp within the candle
            tick_ts = int(timestamp.timestamp() * 1000) + (i * 100)  # 100ms between ticks

            tick = LOBSnapshot(
                bid=round(bid, 2),
                bid_qty=round(bid_qty, 4),
                ask=round(ask, 2),
                ask_qty=round(ask_qty, 4),
                timestamp_ms=tick_ts
            )
            ticks.append(tick)

        if (idx + 1) % 100 == 0:
            logger.info(f"   Processed {idx + 1}/{len(df)} candles...")

    logger.info(f"[OK] Synthesised {len(ticks):,} LOB ticks")
    return ticks


def train_offline(
    symbol: str = "BTCUSDT",
    days: int = 7,
    interval: str = "1m",
    output_path: str = "minifenix/sota_model_pretrained.joblib",
    prediction_horizon: int = 200,
    move_threshold_bps: float = 2.0,
) -> dict:
    """
    Full offline pre-training pipeline.

    Args:
        symbol: Trading pair
        days: Days of history
        interval: Candle interval
        output_path: Where to save the model
        prediction_horizon: Prediction horizon in ticks
        move_threshold_bps: Movement threshold to label UP/DOWN

    Returns:
        Dictionary with training stats
    """
    logger.info("=" * 70)
    logger.info("[LEARN] MiniFenix Offline Pre-training")
    logger.info("=" * 70)

    # 1. Download historical data
    df = download_historical_klines(symbol, interval, days)

    # 2. Synthesise LOB ticks
    ticks = synthesize_lob_ticks(df, ticks_per_candle=200)

    # 3. Initialise feature engine and predictor
    feature_engine = FeatureEngine(lookback=200)

    # 4. Process ticks and accumulate features
    logger.info("[INIT] Extracting features...")
    X_buffer = []
    price_buffer = []

    for i, tick in enumerate(ticks):
        features = feature_engine.update(tick)
        if features is not None:
            X_buffer.append(features)
            price_buffer.append((tick.bid + tick.ask) / 2)

        if (i + 1) % 10000 == 0:
            logger.info(f"   Processed {i + 1:,}/{len(ticks):,} ticks...")

    logger.info(f"[OK] Features extracted: {len(X_buffer)} samples")

    # 5. Build labels
    X = np.array(X_buffer)
    prices = np.array(price_buffer)

    # Same parameters as SOTAPredictor
    horizon = prediction_horizon
    threshold = move_threshold_bps / 10000

    y = np.zeros(len(X) - horizon, dtype=np.int32)
    for i in range(len(y)):
        future_price = prices[i + horizon]
        current_price = prices[i]
        if current_price == 0:
            y[i] = 2  # HOLD
            continue
        change = (future_price - current_price) / current_price
        if change > threshold:
            y[i] = 1  # UP
        elif change < -threshold:
            y[i] = 0  # DOWN
        else:
            y[i] = 2  # HOLD

    X = X[:len(y)]  # Align lengths

    # 6. Distribution analysis
    unique, counts = np.unique(y, return_counts=True)
    class_dist = dict(zip(['DOWN', 'UP', 'HOLD'], counts))
    logger.info(f"[STATS] Class distribution: {class_dist}")

    # 7. Compute dynamic class weights
    total = len(y)
    class_weights = {
        0: total / (3 * counts[0]) if counts[0] > 0 else 1.0,
        1: total / (3 * counts[1]) if counts[1] > 0 else 1.0,
        2: total / (3 * counts[2]) if counts[2] > 0 else 1.0,
    }
    min_weight = min(class_weights.values())
    class_weights = {k: v/min_weight for k, v in class_weights.items()}
    logger.info(f"[BAL] Class weights: {class_weights}")

    # 8. Train model
    logger.info("[START] Training LightGBM model...")

    try:
        import lightgbm as lgb
    except ImportError:
        logger.error("[FAIL] LightGBM not installed. Install with: pip install lightgbm")
        raise

    # Temporal split (not random!)
    split_idx = int(len(X) * 0.8)
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]

    # Build model with class weights
    model = lgb.LGBMClassifier(
        n_estimators=200,  # More trees for offline
        max_depth=6,
        learning_rate=0.05,  # Slower for better convergence
        num_leaves=31,
        min_child_samples=20,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=0.1,
        class_weight=class_weights,
        n_jobs=-1,  # Use every core in offline mode
        verbose=-1,
        objective="multiclass",
        num_class=3,
        metric="multi_logloss",
        importance_type="gain",
    )

    # Train with early stopping
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[
            lgb.early_stopping(stopping_rounds=50, verbose=True),
            lgb.log_evaluation(period=10)
        ],
    )

    # 9. Evaluate
    train_preds = model.predict(X_train)
    val_preds = model.predict(X_val)

    train_acc = np.mean(train_preds == y_train)
    val_acc = np.mean(val_preds == y_val)

    logger.info("=" * 70)
    logger.info("[STATS] Training results:")
    logger.info(f"   Train Accuracy: {train_acc:.2%}")
    logger.info(f"   Val Accuracy:   {val_acc:.2%}")
    logger.info(f"   Best iteration: {model.best_iteration_}")

    # 10. Feature importance
    importances = model.feature_importances_
    feature_names = feature_engine.feature_names
    top_indices = np.argsort(importances)[-10:][::-1]
    logger.info("\n[TOP] Top 10 features:")
    for i in top_indices:
        logger.info(f"   {feature_names[i]}: {importances[i]:.2f}")

    # 11. Save model
    model_data = {
        "model": model,
        "accuracy": f"{val_acc:.2%}",
        "train_samples": len(X_train),
        "val_samples": len(X_val),
        "class_distribution": class_dist,
        "class_weights": class_weights,
        "feature_names": feature_names,
        "params": {
            "prediction_horizon": horizon,
            "move_threshold_bps": move_threshold_bps,
        }
    }

    joblib.dump(model_data, output_path)
    logger.info(f"\n[SAVE] Model saved to: {output_path}")

    return {
        "train_accuracy": train_acc,
        "val_accuracy": val_acc,
        "samples": len(X),
        "class_distribution": class_dist,
    }


def main():
    parser = argparse.ArgumentParser(description="MiniFenix Offline Pre-training")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading pair")
    parser.add_argument("--days", type=int, default=7, help="Days of history")
    parser.add_argument("--interval", default="1m", help="Interval (1m, 5m, 15m)")
    parser.add_argument("--output", default="minifenix/sota_model_pretrained.joblib",
                        help="Model output path")
    parser.add_argument("--horizon", type=int, default=200, help="Prediction horizon")
    parser.add_argument("--threshold", type=float, default=2.0,
                        help="Movement threshold in bps")

    args = parser.parse_args()

    stats = train_offline(
        symbol=args.symbol,
        days=args.days,
        interval=args.interval,
        output_path=args.output,
        prediction_horizon=args.horizon,
        move_threshold_bps=args.threshold,
    )

    logger.info("\n[OK] Pre-training completed successfully!")
    logger.info(f"   Validation accuracy: {stats['val_accuracy']:.2%}")
    logger.info(f"   Training samples: {stats['samples']:,}")


if __name__ == "__main__":
    main()
