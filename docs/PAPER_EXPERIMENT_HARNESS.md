# Isolated Paper Experiment Harness

The public harness runs one or more Fenix candidates with independent runtime
state. It supports two market-data venues:

- `testnet`: Binance Futures Testnet public streams.
- `mainnet-data`: Binance Futures Mainnet public streams with simulated orders.

The Mainnet-data option is deliberately separate from live trading. It requires
`--mode paper`, rejects `--allow-live`, strips Binance credentials from every
child process, and prevents each child from reloading those credentials from
`.env`.

No experiment results, prompts, databases, or private model assignments are
included in the repository.

## Start a clean experiment

Install the project first, then export any LLM-provider credentials needed by
your selected profile. By default the harness loads `.env` into its own
environment and removes all Binance credentials before launching children.
Set `FENIX_EXPERIMENT_LOAD_DOTENV=0` if you prefer to export variables
manually.

Testnet data:

```bash
FENIX_EXPERIMENT_VENUE=testnet \
FENIX_EXPERIMENT_TIMEFRAMES=5m,1h \
bash scripts/paper_experiment_harness.sh start
```

Mainnet public data with simulated orders:

```bash
FENIX_EXPERIMENT_VENUE=mainnet-data \
FENIX_EXPERIMENT_TIMEFRAMES=5m,1h \
bash scripts/paper_experiment_harness.sh start
```

Optional settings:

```bash
export FENIX_EXPERIMENT_SYMBOL=BTCUSDT
export FENIX_EXPERIMENT_INITIAL_BALANCE_USDT=10000
export FENIX_EXPERIMENT_FLOW_WINDOW_SEC=15
export FENIX_EXPERIMENT_INTERVAL_SEC=300
export FENIX_EXPERIMENT_TAKER_FEE_RATE=0.0004
export FENIX_EXPERIMENT_SLIPPAGE_BPS=1.0
export FENIX_EXPERIMENT_TEAM_MODELS="technical=model-a,qabba=model-b,decision=model-c"
export FENIX_EXPERIMENT_WITH_NANO=1
export FENIX_EXPERIMENT_NANO_MODEL="nanofenixv3/pretrained_btcusdt.pkl"
```

The harness intentionally does not publish tuned thresholds or model choices.
Users should choose those values for their own provider, latency, and risk
research.

## Monitor and stop

Use the same venue, symbol, and root variables for every command:

```bash
bash scripts/paper_experiment_harness.sh status
python scripts/inspect_paper_experiment.py \
  --root logs/paper_experiment_mainnet-data_btcusdt
bash scripts/paper_experiment_harness.sh stop
```

The inspector reports process health, aggregate log warning/error counts, and
database row counts. It does not print raw prompts or model responses. Add
`--json` for machine-readable output.

Starting again archives the prior runtime directory with a UTC timestamp. The
harness refuses to archive while any process it owns is still running.

## Isolation model

Each timeframe receives its own:

- SQLite database;
- process lock directory;
- risk-manager state;
- ReasoningBank directory;
- LLM-response log directory;
- operational state directory.

All children use a configured simulated balance and paper execution. Simulated
entries create persisted orders, trades, and positions; exits persist
fee-aware net PnL. Entry slippage and taker fees are explicit harness inputs.
The trade-flow window is also explicit so QABBA can be compared consistently
across venues.

## Safety boundary

This harness is for research, not evidence that a strategy is ready for real
money. Mainnet-data means production market data only. It never implies
Mainnet execution.
