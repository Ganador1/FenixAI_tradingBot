# Community Deployment Fixes

This change set addresses deployment and dashboard defects reported by a
community paper-trading user.

## Installation

The core runtime now declares `langchain-core` and `langgraph`. Provider
adapters are available through the `llm` extra, while Playwright and Kaleido
are part of the `vision` extra:

```bash
pip install -e ".[dev,llm,vision,monitoring]"
python -m playwright install chromium
```

The second command installs the browser binary used by Playwright. Kaleido 1.x
also requires Chrome or Chromium to be available on the host.

## API Ports

Local Python runs bind to port 8000 by default. `--port` overrides the command
line value and `FENIX_API_PORT` changes the default.

Docker Compose keeps the container API on port 8000 and publishes it on
`127.0.0.1:8001` by default. Use `FENIX_API_HOST_PORT` to change only the host
port.

## Paper Balances

Paper mode no longer calls Binance's signed Futures balance endpoint. The API
reports paper equity from the configured initial balance plus the persisted
realized and unrealized PnL ledger. The engine also feeds simulated/cached
balance into risk analysis without private-account I/O.

`FENIX_BALANCE_FALLBACK_USDT` sets the initial simulated balance.

## System Settings

Settings are validated and atomically persisted to
`data/system_settings.json`. Override this location with
`FENIX_SYSTEM_SETTINGS_PATH`.

All settings and connection-test routes use the control-access guard. Saved
notification secrets are never returned by the API. Database and SMTP tests
perform real, non-destructive checks; unsupported SMS tests return an explicit
error instead of a false success.

The Settings page manages administrative/dashboard policy. It intentionally
does not hot-reconfigure a running trading engine. Execution and risk changes
must use the dedicated engine controls or deployment configuration.

## Frontend

The tab components now share active state, expose correct accessibility
attributes, react to clicks, and hide inactive panels. API calls obtain the JWT
from the persisted Zustand authentication store through one shared helper,
eliminating conflicting local-storage keys.
