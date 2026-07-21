# FenixAI Current-State Review — 2026-07-18

## Scope and safety boundary

This review covers the local branch `fix/qabba-decision-orderflow-bias` at
`df1f1a6f`, the changes made after that commit, the currently running live
process, and controlled Binance Futures Testnet validation.

No Mainnet order, position, configuration, or process was mutated during this
review. The live Fenix and NanoFenix processes were inspected but not restarted.
One minimum-notional Testnet order was intentionally opened during the
protective-order failure scenario and was closed by the executor's real
fail-safe path. The Testnet account was flat after every scenario.

## Executive assessment

The recent CVD/OBI and agent-accuracy changes are directionally sound and have
focused the decision layer on more reliable evidence. The macro-news change
correctly narrowed the word `strike`, but needed the same treatment for the
generic word `attack`.

The most important regression was the inherited-drawdown grace period added to
the runtime risk manager. It allowed a fresh process to make one trade before
enforcing a persisted all-time drawdown. Repeated restarts could therefore
repeatedly bypass the all-time loss circuit breaker. The source now fails
closed: a restart never grants an extra trade.

The running live process was started before the uncommitted fixes in this
review. It therefore still has the old drawdown grace behavior and the old
Redis publishing behavior loaded in memory. A controlled restart is required
before the new protections are active in live execution.

## Live snapshot

- Process: Fenix `ETHUSDC` 15m with NanoFenix companion, running since
  2026-07-18 11:42 local time.
- Observed decisions at the review snapshot: 31 HOLD, 1 BUY, and 1 SELL.
- Both actionable decisions were blocked by downstream filters; no trade was
  executed.
- Mainnet inspection found no tracked position and no standard or algo orders.
- Redis was unavailable and generated 1,488 Socket.IO publish errors in the
  inspected log. Local event persistence continued.
- External Telegram and Discord safety alerts were not configured.
- Persisted all-time peak: `538.29151018`.
- Inspected current equity: `420.12848442`.

The difference between the persisted peak and current equity must be classified
before restarting live execution. If it represents trading loss, the all-time
drawdown breaker must remain anchored to the existing peak. If it represents an
intentional withdrawal or account transfer, the peak requires an explicit,
audited operator re-anchor. The application cannot safely infer the cause, and
the state must not be reset automatically.

## Corrections implemented

### Restart-safe all-time drawdown

`RuntimeRiskManager` now enforces persisted all-time drawdown independently of
the number of trades made by the current process. Tests cover a fresh process,
multiple restarts, persisted peaks, and genuine drawdown.

### Redis outage containment

The Redis bridge now performs a short, cached health check before attempting a
Socket.IO publish. During an outage it skips remote publishing, retains local
event persistence, emits one warning per outage, throttles retries, and resumes
publishing after recovery.

Environment controls:

- `FENIX_REDIS_HEALTH_TIMEOUT_SEC`
- `FENIX_REDIS_HEALTH_INTERVAL_SEC`
- `FENIX_REDIS_RETRY_INTERVAL_SEC`

### Macro-news severity

The generic word `attack` is no longer automatically severe. Severe
classification now requires a state or military actor, a systemic target, or a
more specific military/terror/cyber pattern. Generic attacks remain
high-impact rather than being discarded.

### Decision HOLD normalization

Compact HOLD responses from the decision model now receive safe defaults for
missing confidence and convergence fields. BUY and SELL responses remain
strict; missing execution-relevant fields still fail validation.

### Fault-injection validity

The runner no longer reports mocked behavior as real Testnet behavior:

- Mocks are scoped to individual scenarios.
- Submission timeout and delayed fill are explicitly labeled simulated exchange
  boundaries.
- Protective-order rejection uses a real Testnet entry and the real fail-safe
  close path.
- The authenticated user-data stream is really stopped and restarted.
- The save test kills a real subprocess with `SIGKILL` during a partial write.
- Simultaneous signals exercise the real file lock and verify that exchange
  submission never overlaps.
- Preflight rejects open positions and both standard and algo orders.
- Every scenario verifies cleanup and a zero position.

### Reproducible regression tests

Live-slot regression tests now use versioned fixtures under
`tests/fixtures/live_slot_regressions/` instead of volatile files under
`logs/`. The frontend lint script now invokes ESLint through Node, avoiding the
direct executable failure observed on the external workspace volume.

## Validation evidence

- Backend suite: `1069 passed, 1 skipped`.
- Frontend: type check, lint, and production build passed.
- Python lint on modified files: passed.
- Redis outage/recovery tests and isolated outage probe: passed.
- Fault injection: `6/6 passed`.
  - 2 simulated exchange-boundary scenarios.
  - 2 authenticated/real Testnet scenarios.
  - 1 real subprocess-kill scenario.
  - 1 real-lock scenario with a simulated exchange boundary.
- Integrated Testnet canary: completed cleanly in 134.68 seconds with 3 HOLD
  decisions, no order, no position, and no stream/handler error.

Artifacts:

- `logs/testnet_fault_injection_report_20260718_final.json`
- `logs/live_slot_summary_codex-testnet-20260718b_SOLUSDT_1m_20260718_post_integration_review_2m.json`

These results validate operational behavior and failure handling. They do not
establish strategy profitability or statistical edge.

## Remaining work, in priority order

1. Classify the `538.29151018` to `420.12848442` equity difference as trading
   loss or intentional capital movement.
2. Review and, only if justified, explicitly re-anchor persisted risk state.
3. Perform a controlled live restart so the drawdown and Redis fixes become
   active.
4. Configure at least one out-of-process safety alert channel.
5. Either run Redis as an intentional dependency or leave it absent with the
   new outage containment enabled.
6. Run a longer Testnet soak across actual entries, fills, protective orders,
   reconnects, and process restarts.
7. Evaluate HOLD labeling, per-agent credit assignment, regime decay, and
   retrieval bias in ReasoningBank before using memory scorecards as strong
   execution weights.

