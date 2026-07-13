# Binance Futures Testnet Validation — 2026-07-10

## Objective

Validate the new live-safety controls against Binance Futures Testnet using the
real Fenix runtime and a controlled minimum-notional order lifecycle. Production
credentials and mainnet endpoints were not used.

## Environment

- Symbol: `SOLUSDT`
- Analysis timeframe: `1m`
- Exchange: Binance USD-M Futures Testnet
- Local model: `qwen2.5:3b` through Ollama
- Testnet leverage: `10x`
- Canary risk: `0.1%` per trade
- Pyramiding and same-side additions: disabled
- Account margin cap during canary: `10%`

## Controlled Order Lifecycle

The reusable runner is `scripts/testnet_order_lifecycle_smoke.py`. It is
testnet-only, requires dedicated testnet credentials, refuses pre-existing
symbol exposure/orders, submits approximately 1.25 times Binance's minimum
notional, verifies protection, and always attempts to cancel and flatten.

### First run: defect discovery

- Entry: `BUY 0.09 SOLUSDT`, approximately `7.03 USDT` notional.
- Binance accepted the entry and two algo protections.
- OrderMonitor registered the position.
- Binance's immediate `FILLED` payload reported `avgPrice=0`.
- The monitor queried the standard order endpoint before the algo endpoint,
  producing repeated `-2013 Order does not exist` noise.
- Cleanup succeeded: both algo orders cancelled, reduce-only close filled,
  final position `0`, and zero remaining orders.

### Fixes made from the first run

- A zero-price `FILLED` response is now requeried and resolved from order/trade,
  position, or ticker evidence; an unusable fill fails closed.
- Protective IDs are queried and cancelled through the algo API first.
- The monitor stores the effective normalized SL/TP prices instead of the
  pre-clamp proposal.

### Repeated run after fixes

- Entry fill resolved correctly at `78.01`.
- Reduce-only close resolved correctly at `78.00`.
- Two algo protections were visible and registered.
- No standard-order `-2013` noise occurred.
- Final position: `0`.
- Remaining standard orders: `0`.
- Remaining algo orders: `0`.

The machine-readable result is in
`logs/testnet_order_lifecycle_smoke_20260710_after_fix.json`.

## Private User-Data Stream

A reconnecting authenticated Futures user-data stream was added while retaining
the five-second polling watchdog as fallback. A third controlled lifecycle
verified the stream against Testnet:

- Connected successfully.
- Events received: `12`.
- Reconnects: `0`.
- Last error: none.
- Received entry and exit `ORDER_TRADE_UPDATE` events.
- Received entry and exit `ACCOUNT_UPDATE` events.
- Received `ALGO_UPDATE` events for both creation and cancellation of SL/TP.
- Final position and remaining orders were again zero.

The corresponding report is
`logs/testnet_order_lifecycle_smoke_20260710_user_stream.json`.

## Full Fenix Runtime Comparison

Two authenticated finite slots ran with real Testnet market data, private
account access, depth/trade/kline WebSockets, live reconciliation, and safe
cleanup.

### Before compact-response normalization

- Duration: 3 minutes.
- Decisions: 3 HOLD, 0 BUY, 0 SELL.
- Technical and QABBA each exhausted four attempts per cycle because the local
  model returned compact HOLD schemas.
- Completed cycle times: `39.21s`, `34.79s`, and `28.28s`.
- Average completed cycle: approximately `34.09s`.
- Safety behavior: fail-safe HOLD; no order submitted.

### After compact-response normalization

- Duration: 2 minutes.
- Decisions: 2 HOLD, 0 BUY, 0 SELL.
- Technical and QABBA validated on attempt 1.
- Completed cycle times: `12.71s` and `12.19s`.
- Average completed cycle: approximately `12.45s`.
- Cycle latency improved by approximately `63.5%`.
- Directional responses still require explicit reasoning; only abstaining HOLD
  responses receive conservative aliases/defaults.
- Safety behavior remained HOLD; no unapproved order was submitted.

Runtime summaries:

- `logs/live_slot_summary_codex-testnet-canary_SOLUSDT_1m_20260710_testnet_canary.json`
- `logs/live_slot_summary_codex-testnet-after-normalization_SOLUSDT_1m_20260710_testnet_after_normalization.json`

### Final integrated canary

A final authenticated one-minute slot exercised the private stream inside the
complete TradingEngine after shutdown diagnostics were retained:

- Status: completed without an error.
- Decisions: 2 HOLD, 0 BUY, 0 SELL.
- Technical and QABBA responses validated on attempt 1.
- Completed cycle times: `13.39s` and `11.91s`.
- Private stream: connected to Testnet, 0 reconnects, and no error.
- Private event count: 0, as expected because the canary submitted no order and
  the account did not change during the slot.
- Accounting status: OK, with no active trade or unexplained position delta.
- Post-run exchange check: position `0.0`, standard orders `0`, algo orders `0`.

The summary is
`logs/live_slot_summary_codex-testnet-final-canary_SOLUSDT_1m_20260710_testnet_final_canary.json`.

## Automated Validation

```text
pytest -q --disable-warnings --maxfail=1
983 passed, 1 skipped

npm run check
passed

npm run lint
passed

npm run build
passed (with existing stale browser-data and large-chunk warnings)
```

Python compilation, focused Ruff checks, and `git diff --check` also passed.

## Result

The new execution, protection, fill reconciliation, process cleanup, compact
agent validation, and private event-stream paths all produced observable Testnet
behavior. Every controlled run ended flat with no remaining SOLUSDT orders, and
no Fenix test process was left running.

Pyramiding remains intentionally disabled. PostgreSQL migration, external alert
delivery, and a longer production canary remain separate rollout tasks; none is
required to preserve the current fail-safe Testnet behavior.
