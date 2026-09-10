#!/bin/sh
# Apply pending Alembic migrations, then exit.
#
# Runs as a one-shot service that the api and worker depend on, so neither
# starts against a database with no schema (audit finding C-5).
#
# This script only ever moves the schema FORWARD. It never creates tables
# outside Alembic, never downgrades, never stamps, never drops and never
# resets. A failure here is fatal by design: `set -e` plus the non-zero exit
# stops the dependent services from starting rather than letting them serve
# traffic against an unusable database.
set -eu

echo "[migrate] applying database migrations..."

# Fail loudly on the common misconfiguration rather than silently migrating the
# SQLite development fallback.
if [ -z "${DATABASE_URL:-}" ]; then
  echo "[migrate] ERROR: DATABASE_URL is not set." >&2
  exit 1
fi

case "${DATABASE_URL}" in
  sqlite*)
    if [ "${APP_ENV:-development}" = "production" ]; then
      echo "[migrate] ERROR: refusing to migrate a SQLite database with APP_ENV=production." >&2
      exit 1
    fi
    ;;
esac

python -m alembic upgrade head

echo "[migrate] migrations applied successfully; current revision:"
python -m alembic current
