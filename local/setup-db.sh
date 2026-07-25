#!/usr/bin/env bash
#
# Create the local 'endurain' role + database to match local/.env.
# Assumes a Postgres running on localhost reachable as the 'postgres' superuser
# (peer/trust auth, as on this machine). Idempotent.
#
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ENV_FILE="${1:-$HERE/.env}"
[ -f "$ENV_FILE" ] || { echo "ERROR: $ENV_FILE not found. Copy env.local.example -> .env first."; exit 1; }

# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

DB_USER="${DB_USER:-endurain}"
DB_DATABASE="${DB_DATABASE:-endurain}"
DB_PASSWORD="${DB_PASSWORD:?DB_PASSWORD must be set in $ENV_FILE}"
PGSUPER="${PGSUPER:-postgres}"

echo "==> Ensuring role '$DB_USER'"
psql -h localhost -U "$PGSUPER" -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 \
  && psql -h localhost -U "$PGSUPER" -c "ALTER ROLE \"$DB_USER\" WITH LOGIN PASSWORD '$DB_PASSWORD';" \
  || psql -h localhost -U "$PGSUPER" -c "CREATE ROLE \"$DB_USER\" WITH LOGIN PASSWORD '$DB_PASSWORD';"

echo "==> Ensuring database '$DB_DATABASE'"
psql -h localhost -U "$PGSUPER" -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_DATABASE'" | grep -q 1 \
  || psql -h localhost -U "$PGSUPER" -c "CREATE DATABASE \"$DB_DATABASE\" OWNER \"$DB_USER\";"

echo "==> Done. Endurain will create its schema on first start."
