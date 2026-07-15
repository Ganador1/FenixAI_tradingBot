# Live Safety Hardening Review — 2026-07-10

## Scope

This review covered the local FenixAI code and runtime artifacts after the first
week of real-money testing with two Fenix instances. No bot, API server, or live
order was started during validation.

The inherited work already included useful improvements: exchange/local
position reconciliation, operational instance heartbeats, per-symbol runtime
state isolation, SQLite WAL configuration, ReasoningBank trade references,
protective-order verification, Reddit rate-limit backoff, NanoFenix runtime
state separation, and regression tests around phantom position closes.

This pass focused on failure modes that can create unintended exposure, corrupt
risk state, or make the dashboard report a different process than the one
holding real positions.

## Implemented Improvements

### Exchange-authoritative risk accounting

- Live risk balance now follows Binance futures equity (`marginBalance`), including
  unrealized losses. Wallet or available balance can no longer mask drawdown.
- Free collateral is queried separately and is used only for entry-margin checks.
- RuntimeRiskManager has an authoritative-balance mode. Realized PnL still updates
  daily performance, but it is not added to an exchange-refreshed balance a second time.
- The live engine refreshes exchange equity before recording a closed trade.

### Stop-based position sizing

- A live entry without a valid stop loss fails closed by default.
- Requested notional is capped so the estimated stop loss plus round-trip fees
  cannot exceed `FENIX_MAX_RISK_PER_TRADE` of current equity.
- Required margin is checked against available collateral, not total equity.
- The default per-process exposure limit was reduced from 50% to 5% of equity
  before the configured leverage multiplier.

### Multi-process portfolio protection

- A per-symbol process lock prevents accidentally starting two Fenix engines for
  the same symbol.
- Entry submissions across all local Fenix processes are serialized with a shared
  account lock.
- Immediately before submission, the executor reads Binance account equity and
  current initial margin. It blocks entries that would exceed
  `FENIX_MAX_ACCOUNT_MARGIN_PCT`.
- Existing quote-asset isolation remains in place for deployments such as
  `ETHUSDC` plus `SOLUSDT`.
- Pyramiding and generic same-side additions are disabled in the local live
  configuration until combined-position accounting is validated on testnet.

### Idempotent and reconciled order submission

- Market and limit entries use a unique Binance `newClientOrderId` and request a
  `RESULT` response.
- A timeout or missing order ID is reconciled by client order ID before any retry.
- An ambiguous market submission returns `ORDER_OUTCOME_UNCERTAIN`; it is never
  blindly resubmitted.
- An ambiguous limit submission is reconciled and cancelled before a market
  fallback. If cancellation cannot be confirmed, the fallback is blocked.

### Protective-order monitoring

- Successful protected entries are now registered in OrderMonitor.
- The executor returns `OrderResult.position_id`, and the engine persists that
  exact field instead of reading a non-existent `protection_position_id` attribute.
- Binance algo-order query and cancellation methods were added for migrated
  conditional orders.
- Monitor exchange calls run off the asyncio event loop.
- Exchange-wide cancellation errors now propagate instead of reporting false success.

### Position-state recovery

- Startup hydration and pre-entry checks use strict exchange snapshots. A failed
  query can no longer be interpreted as a flat position.
- If Binance has exposure but local tracking is absent, Fenix hydrates the local
  position and waits until the next cycle instead of entering immediately.

### Process and dashboard control

- SIGINT and SIGTERM now race the engine task and call `TradingEngine.stop()`.
- API observer mode no longer starts a duplicate AutoEvaluator.
- Observer-mode start, stop, and reconfiguration endpoints return HTTP 409.
- The dashboard displays `CLI managed` and disables its local engine button in
  observer mode.

### Persistence and evaluator reliability

- Unknown/non-directional ReasoningBank entries are marked `not_evaluable` before
  market data is requested, preventing repeated Binance REST traffic.
- ReasoningBank index and JSONL rewrites use flushed atomic replacement.
- NanoFenix model saves use flushed atomic replacement, so an interrupted save
  cannot truncate the active model.
- `.env` no longer overrides explicit process/test variables during config load.
- Test defaults disable live MTF, macro, and scorecard I/O.
- A pre-existing syntax error in the optional dual-key inference helper was
  repaired and covered by an import regression test. The helper is now tracked
  production source and no longer logs a key prefix.

## Live Configuration Applied Locally

The local `.env` now enforces the following safety posture:

```dotenv
FENIX_PYRAMID_ENABLE=0
FENIX_ALLOW_ADD_TO_POSITION=0
FENIX_ENFORCE_LLM_RISK=1
FENIX_REQUIRE_LIVE_STOP_LOSS=1
FENIX_MAX_RISK_PER_TRADE=0.01
FENIX_MAX_EXPOSURE_PCT=0.05
FENIX_GLOBAL_PORTFOLIO_GUARD=1
FENIX_MAX_ACCOUNT_MARGIN_PCT=0.50
```

`.env.example` documents the complete set of new controls in English.

## Validation

Validation is intentionally local and mocked. It does not connect to a private
Binance account or place orders.

Coverage added for:

- duplicate process locking;
- authoritative balance accounting;
- account-wide projected margin limits;
- ambiguous submission reconciliation;
- mandatory live stops;
- stop-distance notional caps;
- negative unrealized PnL in futures equity;
- client order IDs and `RESULT` responses;
- observer-mode API control rejection;
- terminal evaluator skips;
- ReasoningBank persistence of evaluator status.

Final local validation results:

```text
pytest -q --disable-warnings --maxfail=1
983 passed, 1 skipped

npm run check
passed

npm run lint
passed

npm run build
passed (with the existing large-chunk and stale browser-data warnings)
```

Python compilation and focused safety regressions also passed. The repository's
full Ruff/Black baseline is not clean: it still reports legacy typing-modernization,
import-order, whitespace, duplicate API function-name, and formatting findings in
files that predate this safety pass. No syntax or undefined-name failure was found,
and those unrelated mechanical rewrites were intentionally not mixed into the
live-risk patch.

## Remaining Risks and Recommended Next Steps

1. Run a longer canary for the new private user-data stream before reducing the
   five-second polling fallback. Testnet delivery is validated, but polling stays enabled.
2. Replace SQLite with PostgreSQL before scaling beyond the current local
   two-process topology. WAL reduces contention but does not provide a distributed
   transaction boundary.
3. Validate pyramiding only on testnet with blended entry, combined quantity,
   replacement SL/TP, partial fills, and restart recovery before re-enabling it.
4. Run a controlled testnet fault-injection session: submission timeout, delayed
   fill, protective rejection, WebSocket disconnect, process kill during model
   save, and simultaneous signals from both symbols.
5. Add external alerts for `ORDER_OUTCOME_UNCERTAIN`, `ACCOUNT_MARGIN_CAP`,
   `PROTECTION_NOT_VERIFIED`, reconciliation failures, and stale instance heartbeats.
6. Revisit the new 1% per-trade risk setting only after enough net-of-fee live
   samples exist. The account-wide margin cap is a separate guard and must not be
   treated as permission to increase loss risk per position.
