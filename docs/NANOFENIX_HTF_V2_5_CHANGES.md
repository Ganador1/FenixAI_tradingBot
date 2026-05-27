# NanoFenix HTF v2.5 Changes

Date: 2026-04-28  
Status: release-candidate documentation, local only

## Executive summary

NanoFenix v3 originally used a rigid dual-horizon requirement: the short horizon and long horizon had to fully agree before a directional signal could pass. Live and paper testing showed that this created too many `HOLD` decisions and filtered out useful partial-consensus signals.

The v2.5 preparation work keeps the dual-horizon idea but changes how it is used. Instead of perfect agreement, NanoFenix uses adaptive weighted fusion, companion signaling, and fee-aware execution checks.

## Root issue

The previous short/long consensus design was too strict for fast crypto markets:

- short-horizon signals reacted faster but were easy to veto;
- long-horizon signals provided stability but lagged;
- the system did not learn enough from partial agreement;
- trailing exits could close before estimated fees were covered.

## Current direction

### Adaptive dual-horizon fusion

NanoFenix now uses weighted fusion across market regimes:

| Regime | Short horizon | Long horizon | Intent |
| --- | ---: | ---: | --- |
| `TRENDING` | 35% | 65% | Favor the more stable horizon. |
| `CHOP` | 70% | 30% | Favor faster range reactions. |
| `DEAD` | 50% | 50% | Avoid overcommitting in low range. |
| `VOLATILE` | 60% | 40% | React faster while keeping context. |

The fusion score is normalized by horizon confidence and reliability. If the final score is below the configured threshold, NanoFenix returns `HOLD`.

### Companion mode

NanoFenix can publish a companion signal file for the larger Fenix runner. This lets the main engine use NanoFenix as a market microstructure input without letting it override the complete Fenix decision stack by itself.

### Fee-aware trailing exits

NanoFenix estimates net PnL before allowing trailing exits to lock in profit. This is intended to prevent small gross wins from becoming net losses after fees.

Relevant runtime parameter:

```bash
MIN_TRAILING_NET_PCT=0.0002
```

The exact value should remain conservative until more live data proves that a tighter threshold is reliable.

## Implemented components

- `nanofenixv3/adaptive_fusion.py`: adaptive dual-horizon fusion, associative pattern memory, online confidence calibration.
- `nanofenixv3/executor.py`: fee-aware trailing-stop behavior and runtime trade management.
- `nanofenixv3/core.py`: configurable bar interval and companion signal output.
- `scripts/nanofenix_pretrain.py`: Binance Futures pretraining data preparation.
- `scripts/run_hybrid_live_paper.py`: hybrid live/paper comparison with staged TP/SL handling.

## Current release-candidate stance

This is not a claim that NanoFenix is profitable. The current v2.5 goal is narrower: make the system safer to test, easier to reason about, and less likely to produce misleading paper/live results.

## Testing checklist

- Run NanoFenix in paper mode long enough to include both low-range and high-volatility periods.
- Compare companion signals against Fenix decisions before allowing them to affect live entries.
- Confirm trailing-stop exits show positive estimated net PnL when marked as profitable.
- Confirm runtime state reloads correctly after stopping and restarting the process.
- Review signal JSON files for stale timestamps before using them in the main runner.

## Known open questions

- Whether the current timeframe mix is the main bottleneck or only one contributor.
- Whether small local models are strong enough for final decision agents in live conditions.
- Whether NanoFenix should remain a companion signal or become a weighted agent inside the main decision graph.
- Whether staged TP/SL behavior improves outcomes after fees across enough real sessions.
