#!/usr/bin/env bash
# Local single-user launcher for the Streamlit UI.
#
#   ./scripts/run_local.sh            # http://127.0.0.1:5000
#   PORT=8501 ./scripts/run_local.sh
#
# Nothing in the app reads .env by itself, so this script exports it into the
# environment before Streamlit starts (values containing spaces, braces or
# quotes — e.g. TIER_LIMITS_JSON — must be single-quoted in .env). It binds to
# localhost by default: .streamlit/config.toml binds 0.0.0.0 for Replit, which
# on a laptop would expose your CV and applications to the whole LAN. Set
# STREAMLIT_ADDRESS=0.0.0.0 to opt back in.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/streamlit ]; then
  echo "Creating .venv (uv sync) ..."
  uv sync --python 3.11 --extra research --extra pdf
fi

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
else
  echo "No .env found: running in DEMO mode (sample data only)."
  echo "  cp .env.example .env   # then set ANTHROPIC_API_KEY or OPENAI_API_KEY"
fi

mkdir -p data

# Alembic-managed schema from day one: create_all never adds columns to an
# existing table, so a later `git pull` with a new model field would break an
# unmigrated database. A pre-existing create_all-built SQLite DB (no
# alembic_version table) is adopted with `stamp head` instead of re-created.
export USE_ALEMBIC=1
if [ -z "${DATABASE_URL:-}" ] && [ -f data/joboffer.db ] && \
   ! .venv/bin/python -c "import sqlite3,sys; c=sqlite3.connect('data/joboffer.db'); sys.exit(0 if c.execute(\"select 1 from sqlite_master where name='alembic_version'\").fetchone() else 1)"; then
  .venv/bin/alembic stamp head
fi
.venv/bin/alembic upgrade head

exec .venv/bin/streamlit run app.py \
  --server.address "${STREAMLIT_ADDRESS:-127.0.0.1}" \
  --server.port "${PORT:-5000}" \
  "$@"
