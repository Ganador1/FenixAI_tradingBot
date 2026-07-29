<!-- DEVELOPMENT.md: Developer onboarding & runbook for FenixAI -->

# Development Guide

Short developer guide for running and contributing to FenixAI locally. Contains recommended environment variables, security habits and development commands.

## Quick Start

1. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,llm,vision,monitoring]"
python -m playwright install chromium
plotly_get_chrome -y
```

1. Copy `.env.example` to `.env` and update keys

```bash
cp .env.example .env
chmod 600 .env
# Edit .env - do not commit .env
```

1. Start the backend

```bash
# Default binds to 127.0.0.1 by design
python run_fenix.py --api
```

1. Start the frontend

```bash
cd frontend && npm install && npm run client:dev
```

## Docker

The Docker stack uses Python 3.12 by default, matching the current local virtualenv
series. Docker has no credential fallbacks: configure independent values of at
least 32 characters for `JWT_SECRET`, `FENIX_METRICS_TOKEN`, `REDIS_PASSWORD`,
and `GRAFANA_ADMIN_PASSWORD`.

```bash
cp .env.example .env
chmod 600 .env
# Edit .env with real secrets before live/testnet use.

# API + Redis only
docker compose up -d --build

# API + Redis + Prometheus + Grafana
docker compose --profile monitoring up -d --build
```

Default local endpoints:

- API: `http://127.0.0.1:8001`
- Prometheus, with monitoring profile: `http://127.0.0.1:9090`
- Grafana, with monitoring profile: `http://127.0.0.1:3001`

Redis is intentionally not published to the host by default; the API reaches it on
the internal Compose network.

## Environment Variables (Important)

- `ALLOW_EXPOSE_API` (default: false) — Set to `true` explicitly to bind to `0.0.0.0` (external exposure). Only enable if intentionally exposing.
- `FENIX_REQUIRE_SHARED_LOGIN_RATE_LIMIT` — Require the Redis-backed login limiter. It defaults to enabled whenever `ALLOW_EXPOSE_API=true`; externally reachable and multi-worker deployments must provide a working `REDIS_URL`.
- `FENIX_API_ALLOW_LIVE` (default: false) — Separate deployment capability required before the API may start a non-paper engine.
- `CREATE_DEMO_USERS` (default: false) — Only enable in local dev/testing to auto-create demo accounts.
- `DEFAULT_ADMIN_PASSWORD` / `DEFAULT_DEMO_PASSWORD` — Required, independently chosen values of at least 16 characters when demo users are explicitly enabled.
- `FENIX_MASTER_PASSWORD` — Required for encrypted settings/vault persistence; use at least 16 characters and never reuse the JWT secret.
- `FENIX_MODEL_SIGNING_KEY_FILE` — Optional private key path used to authenticate mutable pickle/joblib model artifacts. The generated key and signatures must remain local.
- `FENIX_HF_LOG_RESPONSES`, `FENIX_HF_LOG_CONTENT`, and `FENIX_DEBUG_VISUAL_RAW` — Keep disabled except during short, controlled diagnostics because provider content can be sensitive.
- `OPENAI_API_KEY`, `GROQ_API_KEY`, `BINANCE_API_KEY`, `BINANCE_API_SECRET`, etc — set these in `.env` for runtime but keep them private and do not commit.

## Recommended Local Security Practices

- Never commit `.env`, browser storage-state/cookie files, signing keys, or local venvs.
- Run `chmod 600 .env` and `chmod 600 tradingview_session_state.json` when that browser state is used. Fenix refuses permissive credential files.
- Leave API docs, public binding, unauthenticated loopback control, and raw provider logging disabled outside isolated development.
- Use `scripts/release_cleanup.sh` before creating a release.
- Add `pre-commit` and `detect-secrets` in your local environment.

## Linters, formatting & pre-commit

- Use prettier and eslint for frontend. Use black/isort for Python code.
- Pre-commit hooks are configured in `.pre-commit-config.yaml` (detect-secrets, black, flake8).

## Testing

- Run unit tests: `pytest` at repo root
- Integration tests in `tests/` and end-to-end tests are also available.

## Add new features

- Create a new branch from the base branch
- Add tests for new logic
- Run pre-commit and linters locally
- Make a PR targeting the main branch for review

## Releasing

- Use `scripts/release_cleanup.sh` and `RELEASE_CHECKLIST.md` to ensure security and compliance.

---

## Troubleshooting

- If the server binds publicly when you don't expect it, check `ALLOW_EXPOSE_API`.
- If authentication returns 503 in an exposed deployment, verify the private Redis connection used by the shared login limiter.
- If the demo accounts exist unexpectedly, check `CREATE_DEMO_USERS` and `DEFAULT_DEMO_PASSWORD`.
