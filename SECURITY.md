# Security Policy

## Reporting a vulnerability

Do not disclose suspected vulnerabilities, credentials, exploit details, or
production configuration in public issues, discussions, or pull requests.

Use GitHub's private vulnerability reporting form:

<https://github.com/Ganador1/FenixAI_tradingBot/security/advisories/new>

Include only the information needed to reproduce the issue. Remove credentials,
account data, trading records, local paths, and other personal or operational
data before submitting a report.

## Scope and response

Security reports concerning the supported Python API, trading safeguards,
credential handling, dependency chain, frontend, and container deployment are
in scope. Legacy or archived components should not be deployed.

The maintainers will validate the report privately, coordinate remediation,
and publish a concise advisory when disclosure is safe.

## Supported versions

Only the latest release and the current `main` branch receive security fixes.
Paper trading remains the default. Live trading must be enabled deliberately
and should use restricted exchange credentials without withdrawal permission.
