# Contributing to FenixAI

Thank you for contributing to FenixAI! This document provides basic instructions to help you get started.

## Getting Started

1. Fork the repository and create a feature branch.
2. Ensure tests are passing locally before creating a PR.
3. Use `pre-commit` for formatting and secret detection.

## Code Standards

- Python: `black`, `flake8`, `isort`
- TypeScript: `eslint`, `prettier`
- Keep commit messages short and meaningful.

## Security and Secrets

- Never commit `.env` or private keys.
- Use `detect-secrets` locally.
- Submit suspected vulnerabilities through the private process in
  [SECURITY.md](./SECURITY.md), not through a public issue or pull request.

## Creating a Pull Request

- Describe the problem and the proposed solution.
- Add tests for critical functionality.
- Request reviewers and provide reproduction steps if needed.
