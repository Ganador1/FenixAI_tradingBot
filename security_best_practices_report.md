# FenixAI Security Hardening Report

Date: 2026-07-29  
Branch: `codex/security-hardening`  
Scope: Python/FastAPI backend, trading execution, NanoFenix/MiniFenix, model
artifacts, local state, experiment tooling, React frontend, CI, and containers.

## Executive summary

This review treated FenixAI as a real-money trading system rather than a normal
web application. The most important result is that exchange mutation is now an
explicit capability and no longer follows implicitly from selecting testnet or
mainnet. Paper, observer, API, and experiment paths fail closed unless the
operator deliberately grants the appropriate capability.

The review also removed unauthenticated and duplicate legacy services, hardened
the supported API boundary, encrypted persisted secrets, authenticated
executable model artifacts, restricted outbound credential-bearing requests,
made sensitive files private and symlink-safe, and converted CI security checks
from advisory steps into blocking gates.

No real credential was identified in the reviewed working tree. Placeholder
values, test fixtures, pinned SHA-256 digests, and scanner test strings were
manually distinguished from credentials.

## Threat model and priorities

The review prioritized attackers or failures that could:

1. Place, cancel, or modify exchange orders without an explicit live/testnet
   mutation capability.
2. Reach control or sensitive read APIs without authentication and role checks.
3. steal credentials from source, browser storage, logs, subprocess
   environments, permissive files, redirects, proxies, or SSRF.
4. execute code through pickle/joblib artifacts, shell expansion, unsafe dotenv
   loading, path traversal, or symlink replacement.
5. corrupt risk, model, cache, or experiment state and cause unsafe decisions.
6. compromise builds through mutable CI actions, vulnerable dependencies,
   unpinned images, privileged containers, or silent security-test failures.

## Remediated findings

| Area | Previous risk | Remediation |
| --- | --- | --- |
| Exchange writes | Executor construction could imply write authority | Added an explicit `allow_mutations` capability and fail-closed guards on order placement, cancellation, protection replacement, and cleanup |
| Live trading | Live mode could be selected without independent deployment consent | Kept CLI acknowledgement and added the separate `FENIX_API_ALLOW_LIVE` capability; invalid risk/protection data blocks entry |
| API authentication | Sensitive reads and controls had inconsistent protection | Applied JWT authentication to sensitive reads, role-aware control/admin dependencies, strict issuer/audience/expiry validation, HS256-only decoding, generic login errors, and bounded rate limiting |
| JWT/passwords | Weak or absent secrets and legacy password hashing | Require a strong JWT secret for authenticated/control paths; use PBKDF2-SHA256 with 600,000 rounds and migrate bcrypt hashes after successful login |
| API exposure | Broad hosts/CORS, verbose health/docs, and legacy services | Added explicit Host/CORS allowlists, body limits, security headers, no-store responses, minimal public liveness, opt-in docs/reload/public bind, and removed the unsupported Flask/Express surfaces |
| Realtime control | Socket connections could bypass REST assumptions | Added authenticated Socket.IO connection validation and explicit loopback-development gating |
| Settings and vault | Notification credentials could be persisted in plaintext or protected by a machine-derived key | Added password-derived encryption with random salts, private atomic files, locking, migration, and no plaintext fallback |
| Dotenv files | Permissive/symlinked files and process-loader variables | Require an owner-controlled regular file with mode `0600`, bounded UTF-8 parsing without shell evaluation, and reject `PATH`, `PYTHONPATH`, `LD_PRELOAD`, `DYLD_*`, and related loader variables |
| Subprocesses | Child processes inherited unrelated credentials | Added allowlisted child environments, `FENIX_SKIP_DOTENV`, resolved executables, bounded plan data, no shell execution, process limits, and PID identity checks |
| Model artifacts | Pickle/joblib loads could execute modified content | Removed pickle-backed general cache use; require repository SHA-256 pins or HMAC signatures before model deserialization; signing keys and sidecars are private |
| Risk state | Corrupt, oversized, non-finite, or redirected state could affect live decisions | Added bounded strict parsing, finite/type/range checks, private atomic persistence, symlink rejection, and fail-closed integrity blocking |
| Runtime files | Logs, metrics, browser sessions, signals, caches, and prompt experiments used permissive or unbounded writes | Added reusable private-file primitives, atomic replacement, `0600`/`0700` modes, symlink/regular-file checks, bounded reads, log/cache caps, and compaction |
| Browser automation | Authenticated storage state and captured output could be overwritten or followed through symlinks | Browser state is loaded as bounded owner-private JSON and saved atomically; screenshots/chart caches use private writes and validated names |
| Outbound requests | SMTP/webhooks/cloud endpoints could expose credentials through SSRF, redirects, proxies, or insecure transport | Added scheme/host/port allowlists, DNS/IP validation and pinning where applicable, TLS requirements, redirect blocking, `trust_env=False`, bounded responses, and credential format validation |
| SQL and API input | Dynamic identifiers, unbounded queries, and permissive models | Added schema allowlists, SQLAlchemy expressions/parameters, strict Pydantic models, symbol/timeframe validation, finite numeric bounds, and result limits |
| Frontend tokens/XSS | Tokens persisted in browser storage and unsafe HTML/demo credentials remained | Keep bearer tokens in memory, removed unsafe HTML injection and bundled passwords, use same-origin routing, and enforce CSP/security headers |
| Dependencies | Known vulnerable or loosely resolved packages | Regenerated the frozen `uv.lock`, upgraded affected Python/frontend packages, selected the official CPU-only PyTorch index, and enabled blocking Python/npm audits |
| CI supply chain | Mutable action tags, broad permissions, and ignored failures | Pinned actions to commit SHAs, reduced permissions, added concurrency cancellation, removed `|| true`, and added blocking Ruff, pytest, Bandit, Semgrep, pip-audit, npm-audit, and Gitleaks gates |
| Containers | Root execution, mutable image tags, writable root filesystem, and default credentials | Pinned images by digest, use a non-root user, drop all capabilities, enable `no-new-privileges`, read-only roots, bounded PIDs, protected tmpfs, loopback publication, and mandatory independent secrets |
| Legacy configuration | A disabled legacy config still advertised debug mode, wildcard CORS, public bind, and a placeholder JWT secret | Changed it to debug-off, loopback bind, explicit development origins, and environment-only secrets |

## Operationally breaking security changes

Operators must account for the following intentional fail-closed behavior:

- Run `chmod 600 .env` before starting Fenix. Symlinked, oversized, foreign-owned,
  or group/world-readable dotenv files are rejected.
- Docker requires independent values of at least 32 characters for
  `JWT_SECRET`, `FENIX_METRICS_TOKEN`, `REDIS_PASSWORD`, and
  `GRAFANA_ADMIN_PASSWORD`.
- Persisted settings/vault secrets require `FENIX_MASTER_PASSWORD` with at least
  16 characters.
- API live mode requires `FENIX_API_ALLOW_LIVE=true` in addition to the normal
  live acknowledgement and risk safeguards.
- Exchange-writing unit/integration callers must construct `OrderExecutor` with
  `allow_mutations=True`; the default is read-only.
- Mutable pickle/joblib artifacts require a valid local signing key/signature.
  A modified or unsigned runtime model is rejected.
- TradingView session files must be owned by the current user and private. Use
  `chmod 600 tradingview_session_state.json` for existing state.
- Raw visual/provider response logging is disabled. Temporary diagnostic
  enablement can still record sensitive model input/output and must be handled
  as confidential data.
- Corrupt risk state blocks new trading until the integrity issue is
  investigated; it is not silently overwritten.
- The old Express and Flask APIs are retired and must not be re-enabled.

## Verification evidence

Completed locally:

- Ruff security baseline: passed.
- Bandit, medium/high severity: zero findings.
- Semgrep Python/TypeScript ERROR rules: zero blocking findings. The large
  trading engine was also scanned separately with an extended timeout.
- Python dependency audit from the frozen production graph: no known
  vulnerabilities.
- Frontend `npm audit`: zero known vulnerabilities.
- Frontend type-check, lint, and production build: passed; lint retains two
  non-security React refresh warnings.
- Docker Compose validation for the main and monitoring files: passed.
- Shell syntax and entrypoint fail-closed secret checks: passed.
- Secret scan review: no real credential identified.
- `git diff --check`: passed.

- Full pytest suite: **1,104 passed, 4 skipped**. The skips are one existing
  optional path plus legacy `nanofenix` modules that are not part of the
  supported distribution.

## Residual risks and follow-up

These are not known bypasses of the controls above, but they remain relevant:

1. FenixAI is a high-risk, real-money system. Security hardening does not prove
   strategy correctness, exchange availability, or profitability.
2. JWT login rate limiting is in-memory and per process. A public multi-worker
   deployment should use a shared limiter at the reverse proxy or Redis layer.
3. Dashboard tokens remain accessible to JavaScript for API calls. In-memory
   storage reduces persistence but cannot protect against a successful same-
   origin XSS. Keep CSP strict and avoid third-party scripts.
4. Local signing/HMAC keys protect against accidental or untrusted artifact
   replacement by users without key access; they do not protect against an
   attacker who already controls the same OS account.
5. Legacy pickle/joblib models remain executable formats after successful
   authentication. Prefer `safetensors` or a non-executable model format when
   the model pipeline supports it.
6. The application currently has simple local user provisioning and no MFA.
   Externally exposed deployments should place the API behind a mature identity
   provider/reverse proxy and TLS termination.
7. Some non-sensitive research/report/cache writers still use conventional
   file APIs. Credential, browser, model, live-state, risk, audit, and primary
   experiment paths were prioritized and hardened.
8. CI registry rules and vulnerability databases change over time. Keep lock
   files, action SHAs, image digests, and scanner versions under scheduled
   review.
9. Docker Desktop completed dependency installation and all runtime image
   layers, but local `containerd` returned an I/O error while exporting the
   final image. Compose syntax and Dockerfile stages are validated; a successful
   image export/runtime smoke test still requires healthy Docker storage.
