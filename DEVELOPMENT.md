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
series. Copy `.env.example` to `.env` and set `JWT_SECRET`; for non-local use,
also replace the Redis and Grafana fallback passwords.

```bash
cp .env.example .env
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
- `CREATE_DEMO_USERS` (default: false) — Only enable in local dev/testing to auto-create demo accounts.
- `DEFAULT_ADMIN_PASSWORD` / `DEFAULT_DEMO_PASSWORD` — Optionally define demo passwords in your local `.env` for ease of use.
- `OPENAI_API_KEY`, `GROQ_API_KEY`, `BINANCE_API_KEY`, `BINANCE_API_SECRET`, etc — set these in `.env` for runtime but keep them private and do not commit.

## Recommended Local Security Practices

- Never commit `.env` or local venvs to git. They should be ignored by `.gitignore`.
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
- If the demo accounts exist unexpectedly, check `CREATE_DEMO_USERS` and `DEFAULT_DEMO_PASSWORD`.
