# Community Bug Fixes: Round 2

This update addresses the second community report received after the
`community-deployment-fixes-v2` release. The changes intentionally use real
application state instead of placeholders or fabricated data.

## Trading event semantics

Same-side fills are now classified as position additions regardless of whether
they were authorized by the strict pyramid gate, the consensus override, or the
legacy add-to-position flag. They emit `position:pyramid_added` instead of
`position:opened`, and include the added quantity, total quantity, average entry
price, and entry count.

The persisted open-position row now contains the aggregate quantity and blended
entry price after an addition. Ledger records use `position_added`, while a true
fresh entry continues to use `position_opened`.

## User administration

User creation, editing, deletion, and password reset now operate on the SQLAlchemy
`User` table. Account-management mutations require administrator
reauthentication. New accounts and password recovery use 15-minute, single-use
tokens stored only as hashes; each user chooses their own password on the public
`/reset-password` page. Password changes immediately invalidate previously
issued access tokens.

The API prevents an administrator from deleting or deactivating their own
account and prevents removal of the final active admin. Deployments using
Alembic must apply the included migration before serving account-management
requests.

The dashboard form now exposes only roles and statuses supported by the backend.
Accounts created through the UI persist across application restarts.

## Metrics and alerts

The dashboard system chart now reads timestamped samples from
`/api/system/metrics/history`. The backend keeps sampled history and filters it by
the requested real time window; the frontend no longer extrapolates fake points
from a single snapshot.

The engine event bridge now creates dashboard alerts for position closures,
stop-loss and take-profit exits, execution failures, risk-control blocks, and
market-data watchdog warnings. Alerts are broadcast over Socket.IO and persisted
for retrieval after a page reload. Repetitive error categories use bounded
cooldowns, payload lengths are constrained, and insert/retention work is committed
in one database transaction.

## Frontend behavior

- Dashboard quick actions navigate to Trading, Agents, and System Monitor.
- ReasoningBank entries use incremental pagination instead of an unreachable
  20-row hard cap.
- The market banner derives its icon from the configured or reported symbol.
- The sidebar reports the actual shared WebSocket connection state.
- System Monitor reads the backend's `python_version` field.
- Persisted alerts are loaded when the dashboard opens.
- Non-functional remember-me, social-login, and sign-up controls were removed.
- Four verified unused legacy components were removed.

## Verification

Regression coverage includes database-backed user mutations, active-admin safety,
alert generation, real metrics-window filtering, same-side fill event/persistence
semantics, and frontend source wiring. Recommended verification commands are:

```bash
pytest tests/test_community_bugfixes_round2.py \
  tests/test_engine_position_management.py::test_execute_trade_allows_add_when_flag_enabled
cd frontend && npm run check && npm run build
```
