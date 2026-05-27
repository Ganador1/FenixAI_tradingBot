# NanoFenix — Evolution and Rationale

This document explains how NanoFenix grew from a quick microstructure
experiment into the v2.5 companion-signal layer, and why the FenixAI
project keeps it as a separate subsystem instead of folding it back
into the main LLM agent graph.

## The problem NanoFenix was built to solve

The main FenixAI engine reasons about the market with six LLM agents
(Technical, QABBA, Visual, Sentiment, Decision, Risk). That works well
at the timeframes the bot was designed for — 15 minutes and above —
but it has two structural limits:

1. **Latency.** Every short-timeframe tick that asks the LLMs to vote
   can take 10–60 seconds end to end. By the time the consensus is in,
   the microstructure opportunity is gone.
2. **Signal quality at very short horizons.** LLM agents reason about
   regime, structure, and risk. They do not see order-flow imbalance,
   queue dynamics, depth changes, or trade aggressor balance — the
   things that actually predict 30-second price moves.

NanoFenix was started in early 2026 as a way to test whether a
zero-LLM, purely numeric path could either replace Fenix on short
timeframes or, more conservatively, give Fenix a second opinion that
would catch obvious mistakes before they hit the exchange.

## v1 — Proof of concept

The first version was a one-file LightGBM model fed by hand-crafted
microstructure features. It tracked order-flow imbalance, depth
imbalance, mid-price returns, and trade aggressor ratio on a fixed
horizon. The launcher had a `--warmup` flag and a fixed position size
(`--position-pct`).

What it proved:

- A simple LightGBM trained online could detect direction on BTCUSDT
  with a directional accuracy that was meaningfully above chance.
- The cost of running a numeric predictor on every tick was
  insignificant compared to the LLM consensus cost.

What it did not prove:

- That the small per-trade edge survived fees. The first standalone
  paper runs of NanoFenix v1 (about 32 trades) closed at roughly 28%
  win rate and a negative PnL once fees were included. The direction
  forecast was right more often than not, but the trades NanoFenix
  triggered on its own were too small and too noisy.

This is what convinced the project to stop treating NanoFenix as a
standalone trader and start treating it as a **signal source for
Fenix**.

## v2 — Pretraining and serialization

The second version added two things the first did not have:

- A real pretraining pipeline (`python -m nanofenixv2.pretrain`) that
  generated a `pretrained_*.pkl` model from historical Binance Futures
  klines. Cold-start performance went from random to "already useful
  in the first hour."
- A `--pretrained` flag on the launcher and a `--live` toggle that
  swapped in a Binance live executor.

What it proved:

- A pretrained starter model removes the long warmup penalty. A bot
  that needs hours of live data to become useful is hard to evaluate.
- Saving and reloading the trained state between sessions was
  essential for any reproducible benchmarking.

What it did not prove:

- That the heavy v2 codebase was maintainable. The directory grew to
  hundreds of megabytes of cached data and intermediate pickles
  because pretraining wrote everything to disk by default. It became
  a research scratchpad more than a clean subsystem.

The v2.5 release ships the **pretrained-model** idea from v2 but not
the v2 codebase. Only the curated set of pretrained pickles from v3
ships publicly. v1 and v2 are kept locally as reproducibility
artifacts and are intentionally excluded from the release.

## v3 — Dual horizon and adaptive fusion

The third version is the one that ships in v2.5. The architecture
changed in three ways:

1. **Two horizons instead of one.** A short-horizon LightGBM
   (typically 30 seconds, configurable via `HORIZON_SHORT`) and a
   long-horizon LightGBM (typically 120 seconds, `HORIZON_LONG`) run
   in parallel. The earlier single-horizon model was forced to choose
   between catching the move and surviving the noise.
2. **Adaptive fusion.** Instead of requiring both horizons to agree,
   an `AdaptiveDualHorizonFusion` policy weighs the two horizon
   predictions based on recent calibration health, direction accuracy,
   and a configurable margin. When both horizons agree strongly the
   consensus is high-confidence; when they disagree the fusion can
   still emit a directional signal if one horizon is clearly better
   calibrated. This replaced the v3.0 rigid-consensus rule that had
   left the bot stuck at 100% HOLD for hours at a time.
3. **Confidence calibration as a first-class concept.** A
   `OnlineConfidenceCalibrator` watches recent forecasts vs realized
   outcomes and produces a calibrated probability. The companion
   signal exposes this as `calibration_health` so the consumer
   (Fenix or any downstream policy) can decide whether to trust the
   current signal at all.

The full set of v3.5.1 tuning changes is documented in
[CHANGELOG.md](./CHANGELOG.md). The short version: the previous
v3 build was too conservative everywhere — too strict on consensus,
too strict on the dead-market filter, too strict on calibration
gates, and too strict on the "minimum expected basis points" floor.
v3.5.1 relaxed every one of those gates and added a fee-aware
trailing stop and a minimum net-profit floor for trailing exits.

## What v3 added that earlier versions did not have

- **Companion-signal JSON output.** v3 writes a small JSON file with
  the current direction, confidence, expected basis points, net
  actionable edge after fees, uncertainty, calibration health, and a
  set of policy hints (`allow_execute`, `allow_add_to_position`,
  `size_multiplier_hint`). The main Fenix engine reads this file when
  `FENIX_ENABLE_NANOFENIX_COMPANION=1`. The coupling is one-way and
  file-based — Fenix and NanoFenix can run, restart, or fail
  independently.
- **Observer-only mode.** `NANOFENIXV3_COMPANION_OBSERVER_ONLY=1`
  publishes the signal but never executes paper trades itself. This
  is the mode the v2.5 live canary runs in.
- **Configurable hard-veto reasons.** The Fenix engine can be told
  which NanoFenix reasons should hard-block an entry vs which should
  only warn. The current production setting limits hard vetoes to
  `direction_mismatch`, `high_uncertainty`, `stale_signal`, and the
  file-integrity errors (`symbol_mismatch`, `signal_file_missing`,
  `signal_file_empty`, `signal_parse_error`,
  `missing_or_invalid_timestamp`). Soft reasons like
  `companion_not_ready` and `low_actionable_edge` are observed but
  not treated as hard blockers, because making them hard blockers
  blocked almost every entry during testing.
- **Fee-aware trailing.** `MIN_TRAILING_NET_PCT` adds a minimum
  net-profit floor before a trailing exit is considered acceptable.
  The earlier versions could close a winning trade at trailing-stop
  with negative net PnL because the gross move did not cover fees.

## Why NanoFenix is not the main trader

The honest summary of NanoFenix's standalone trading record:

- v1 paper: about 32 trades, roughly 28% win rate, negative PnL.
- v3 early standalone tests: 2 trades, both losses; 1 first win on a
  1s instance with a small positive PnL after fees.
- v3.5 with pretraining: directional accuracy moved into the 60-70%
  range on validation, but real paper executions still produce
  net-negative or near-zero PnL because the per-trade edge is small
  relative to fees, and the executor's trailing logic can exit before
  the move completes.

The conclusion the project reached: NanoFenix predicts direction
better than chance, but it does not, on its own, make money. It is
shipped in v2.5 as a **companion and calibration layer** that helps
Fenix Core avoid bad entries, not as a separate live trader. The
public documentation should never imply otherwise.

## How NanoFenix and Fenix Core are connected today

```
Fenix Core (LangGraph + LLM agents)
   │
   │  reads JSON companion file
   ▼
NanoFenix v3.5 companion signal
   ▲
   │  writes JSON every bar
   │
Binance bookTicker + aggTrade WebSocket
```

The connection is intentionally loose. NanoFenix does not call into
Fenix and Fenix does not call into NanoFenix. They communicate
through a small JSON file under `logs/nanofenixv3_companion_*.json`.
This means either side can be restarted, swapped, or disabled
without breaking the other. It also means NanoFenix can be benchmarked
separately from Fenix Core and vice versa.

## What v2.5 ships

- The `nanofenixv3/` source code as a clean Python package.
- The dual-horizon predictor, the adaptive fusion policy, the
  confidence calibrator, the paper executor with fee-aware trailing,
  and the multi-scale feature engine.
- A small set of curated pretrained pickles for BTCUSDC, BTCUSDT,
  BTCUSDT 5s bars, ETHUSDT, and SOLUSDT, so users do not need to
  pretrain before the first run.
- A standalone launcher (`run_nanofenixv3.py`) with companion-mode
  flags.
- The `scripts/nanofenix_pretrain.py` tool used to regenerate the
  pretrained models from public Binance klines.
- The full `CHANGELOG.md` and this history file so that users can
  see what changed and why.

## What v2.5 does not ship

- The v1 and v2 codebases, the unsuffixed `nanofenix/` intermediate
  build, and their launchers.
- Local runtime state pickles (`runtime_*.pkl`), backup pickles
  (`*_backup.pkl`, `*.bak`), benchmark pickles, broken or in-flight
  temp files, and the local `signals/` directory generated during
  development. Each release should regenerate runtime state from
  the pretrained models on first run.

## Open questions for v2.6 / v3.0

- Whether to move some Fenix execution-timing logic out of the LLM
  graph and into a NanoFenix-style fast path. The
  `fenix_experimental/` package is an early sketch of this idea.
- Whether the calibration layer should be shared with the main
  Fenix risk manager, so that Fenix can size positions based on
  the same calibration health that NanoFenix already tracks.
- Whether NanoFenix should expose a multi-symbol mode by default
  instead of one symbol per process.
- Whether the pretraining pipeline should produce models that are
  small enough to ship and good enough to be useful without per-user
  retraining.

These are not v2.5 release blockers. They are noted here as the
current direction of work.
