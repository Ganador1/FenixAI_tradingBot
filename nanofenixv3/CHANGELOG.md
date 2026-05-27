# NanoFenix v3.x Changelog

## 2026-04-28: v2.5 release-prep notes

This changelog has been translated to English for the public v2.5 preparation pass. It documents the NanoFenix v3.5.1 tuning work that feeds the FenixAI v2.5 release candidate.

### Current live/paper hardening

- Added fee-aware trailing-stop protection so NanoFenix does not exit a trade as a "win" before estimated fees are covered.
- Added a minimum net-profit gate for trailing exits through `MIN_TRAILING_NET_PCT`.
- Kept companion mode enabled so NanoFenix can publish a sidecar signal for the larger Fenix runner without taking over consensus by itself.
- Runtime state files remain local and symbol/timeframe scoped.

## 2026-04-12: v3.5.1 overhaul

### Problems identified in the original v3 build

1. Adaptive fusion was disabled, forcing rigid consensus and creating excessive `HOLD` output.
2. Short-horizon retrains were rejected too often because `MAX_TRAIN_VAL_GAP=15%` was too strict.
3. Confidence calibration was conservative, with hard gates at 45% direction accuracy and 42% meta probability.
4. The executor filtered too aggressively through dead-market and order-flow guards.
5. `TIME_EXIT` closed trades at a loss after five minutes without enough trailing context.
6. Direction accuracy rarely calibrated because predictions below the threshold were never evaluated.
7. `LOW_VOL_MIN_BPS=2.0` killed signals in flat markets.
8. `COMPANION_MIN_DIR_SAMPLES=80` prevented companion readiness from activating.

### Implemented changes

#### Predictor (`predictor.py`)

- `ENABLE_ADAPTIVE_FUSION=1` by default.
- `ADAPTIVE_FUSION_BASE_THRESHOLD=0.35` instead of `0.52`.
- `ADAPTIVE_FUSION_MIN_MARGIN=0.08`.
- `MAX_TRAIN_VAL_GAP=0.30`, relaxed from `0.15`.
- Hard gates lowered to `dir_acc < 0.38` and `meta_prob < 0.35`.
- `BASE_MIN_BPS=0.5` and `LOW_VOL_MIN_BPS=0.5`.
- `COMPANION_MIN_DIR_ACC=0.42`.
- `COMPANION_MIN_DIRECTION_SAMPLES=10`.
- `COMPANION_MIN_CALIBRATION_SAMPLES=10`.
- `_queue_horizon_eval` threshold lowered from `BASE_MIN_BPS * 0.5` to `0.1` bps to break the direction-evaluation catch-22.
- `RAW_PRED` logging every 30 bars for diagnostics.
- `HORIZON_SHORT` and `HORIZON_LONG` configurable through environment variables.

#### Feature engine (`feature_engine.py`)

- No structural changes.
- `FEATURE_LOOKBACK=320` bars, equivalent to roughly 5.3 minutes on 1s bars or 26.7 minutes on 5s bars.

#### Executor (`executor.py`)

- `TRAILING_ACTIVATION=0.0000`, so trailing can activate from the first bar.
- `TRAILING_DISTANCE=0.0006`, a 6 bps trail.
- `TIGHT_TRAILING_AFTER=0.0015`, tightening after +15 bps.
- `TIGHT_TRAILING_DIST=0.0003`, a 3 bps tight trail.
- `MAX_HOLD_BARS=1800`, a 30-minute safety net.
- `COOLDOWN_BARS=30` and `COOLDOWN_AFTER_LOSS=60`.
- `EARLY_EXIT_BARS=120` and `EARLY_EXIT_THRESHOLD=-0.0015`.
- Dead-market filter relaxed to a range under 3 bps.
- Order-flow filter relaxed to 0.35/0.65.

#### Core (`core.py`)

- `NANOFENIXV3_BAR_INTERVAL` is configurable, with 1.0s as the default and 5.0s supported.
- Companion signal output is enabled through `NANOFENIX_SIGNAL_STATE_PATH`.
- Startup banner displays the active bar interval.

#### Pretraining

- Added `scripts/nanofenix_pretrain.py`.
- Downloads 24 hours of Binance Futures 1m klines.
- Converts them into pseudo-1s OHLC bars through interpolation.
- Produced 86,021 features versus roughly 500 on cold start.
- Initial pretrain result: short validation accuracy 83%, long validation accuracy 64.3%.

### Test results

| Metric | v3 original | v3.5 before pretrain | v3.5.1 after pretrain |
| --- | --- | --- | --- |
| Signals | 100% HOLD | 1 LONG every 30m | LONG/SHORT about every 30s |
| RAW prediction | 0.04-0.13 bps | 0.3-0.8 bps | 0.5-2.9 bps |
| Direction accuracy | 50%/0 | 3.4%/29 | 71.4%/42 to 63.5%/400 |
| Companion ready | Never | Never | True, utility=1.0 |
| Trades | 0 | 2, both losses | 2, both losses |
| Validation accuracy | 70.7% | 91.9%, overfit | 73.6%, more realistic |

### Early trade log

1. LONG at 71,153.05, prediction +1.0 bps, range 12 bps, `TIME_EXIT`, -$0.90 (-0.15%).
2. LONG at 71,097.20, prediction +1.7 bps, range 4 bps, `TRAILING_STOP`, -$0.47 (-0.078%).
3. SHORT at 71,022.60 on 5s, `TRAILING_STOP`, -$0.45 (-0.075%).
4. SHORT at 70,981.75 on 5s, `TRAILING_STOP`, -$0.74 (-0.123%).
5. LONG at 71,168.05 on 1s, `TRAILING_STOP`, +$0.56 (+0.093%), first recorded win.

### Current local runtime references

- 1s instance: screen `nanofenix_1s`, signal file `nanofenixv3/signals/btcusdt_1s.json`.
- 5s instance: screen `nanofenix_5s`, signal file `nanofenixv3/signals/btcusdt_5s.json`.
- Both instances use 24h pretraining, 6 bps trailing, and companion mode.
- Saved model: `nanofenixv3/pretrained_btcusdt.pkl`.
- Runtime states: `runtime_btcusdt_1s.pkl`, `runtime_btcusdt_5s.pkl`.
