# MiniFenix: Two-Speed Edge Trading Prototype

Status: research prototype for FenixAI v2.5. MiniFenix is not the primary public live runner.

MiniFenix is a minimal FenixAI prototype designed to test the **two-speed architecture**: a slow reasoning loop and a fast execution loop. It combines hybrid AI reasoning with quantitative market microstructure features.

## Why MiniFenix Exists

In the main FenixAI engine, LLM agents such as QABBA, Technical, and Decision can sit close to the critical decision path. That is acceptable for higher timeframes, but it becomes a bottleneck on 1-minute and tick-level market data. A microstructure opportunity can disappear while the model is still preparing or parsing a response.

MiniFenix separates the responsibilities:

1. **The Brain: Slow Loop (`brain.py`)**
   - Represents high-level LLM reasoning.
   - Runs periodically, usually every 5-30 seconds depending on the test.
   - Does not execute trades directly.
   - Publishes a short-lived trading regime such as: "market bias is LONG; only buy if OFI is above 0.5 and spread is acceptable."

2. **The Trigger: Fast Loop (`trigger.py`)**
   - Represents real-time execution timing.
   - Connects directly to Binance WebSocket data.
   - Does not call an LLM in the hot path.
   - Uses `quant_math.py`, `feature_engine.py`, and the online predictor to process Z-scores, order-flow imbalance, spread, and LOB features quickly.
   - Opens a paper/testnet position only when the fast quantitative signal agrees with the Brain regime.

## How to Run It

Activate the project environment and make sure the required runtime dependencies are installed.

```bash
python run_minifenix.py
```

## What to Watch

When MiniFenix is running:

- `[BRAIN]` logs show the slower market-regime analysis.
- `[TRIGGER]` logs show the WebSocket connection and fast tick processing.
- The trigger keeps running while the brain is thinking.
- When the mathematical trigger aligns with the Brain bias, the runner logs an execution event.

## Optimization Changelog (2026-02-26)

### Problems and Fixes

| Problem | Impact | Implemented fix |
| --- | --- | --- |
| Frequent LLM timeouts | Stale regime for 30+ seconds | Reduced primary timeout from 8s to 5s and fallback timeout from 6s to 4s in `brain.py` |
| Slow Brain updates | Regime became outdated | Reduced Brain interval from 30s to 15s |
| LightGBM overfitting | Unstable accuracy from 26% to 100% | Increased `min_training_samples` from 200 to 1000 in `sota_predictor.py` |
| Blocking retrain | 30-160ms latency spikes | Retrain now runs asynchronously with `run_in_executor` |
| Too conservative | Only 1 trade in 1.5 hours | Reduced `min_confidence` from 0.55 to 0.52 |
| Excessive cooldown | Missed opportunities | Reduced cooldown from 10s to 5s |

### Expected Results

| Metric | Before | After |
| --- | --- | --- |
| Maximum latency | About 160ms | Under 1ms for the fast path |
| Brain frequency | Every 30s | Every 15s |
| Model stability | High variance | More consistent |
| Estimated trade frequency | About 1 per 1.5h | About 3-5 per 1.5h |

## How MiniFenix Informed FenixAI v2.5

MiniFenix is important because it showed that Fenix needs separate layers for reasoning and timing:

1. LLMs are useful for context, regime, and synthesis.
2. Fast entries should rely on deterministic or ML-based logic that does not block on an LLM response.
3. A short-lived `TradingRegime` object is a cleaner interface between slow reasoning and fast execution.
4. Microstructure logic should be benchmarked independently from the full Fenix agent graph.

These lessons directly influenced NanoFenix v3.5 and the experimental slow-brain/fast-trigger runner in `fenix_experimental/`.

## Current Role in v2.5

MiniFenix should be described publicly as a research prototype and architectural proof of concept.

Use it to study:

- slow-brain / fast-trigger separation;
- LLM regime quality;
- fast WebSocket feature processing;
- online LightGBM behavior;
- model sweep comparisons for the slow Brain.

Do not present MiniFenix as the default live trading system.
