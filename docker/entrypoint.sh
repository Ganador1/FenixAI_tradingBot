#!/bin/sh
set -eu

umask 077

jwt_secret=${JWT_SECRET:-}
metrics_token=${FENIX_METRICS_TOKEN:-}

if [ "${#jwt_secret}" -lt 32 ]; then
  echo "JWT_SECRET must contain at least 32 characters" >&2
  exit 78
fi
if [ "${PROMETHEUS_ENABLED:-false}" = "true" ] &&
  [ "${#metrics_token}" -lt 32 ]; then
  echo "FENIX_METRICS_TOKEN must contain at least 32 characters" >&2
  exit 78
fi
if [ -n "${FENIX_MASTER_PASSWORD:-}" ] && [ "${#FENIX_MASTER_PASSWORD}" -lt 16 ]; then
  echo "FENIX_MASTER_PASSWORD must contain at least 16 characters" >&2
  exit 78
fi

exec "$@"
