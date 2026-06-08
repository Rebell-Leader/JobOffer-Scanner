#!/usr/bin/env bash
#
# restore_db.sh — restore a pg_dump custom-format archive into a database.
#
# DESTRUCTIVE: with --clean (the default) this drops and recreates objects in
# the target database before loading. Always restore into a scratch/staging DB
# first when validating a backup (the "restore drill" in deploy/RUNBOOK.md).
#
# Usage:
#   DATABASE_URL=postgresql://user:pass@host:5432/db \
#       ./scripts/restore_db.sh backups/joboffer-20260608T010203Z.dump
#
#   # Skip the interactive confirmation (for automated drills):
#   ./scripts/restore_db.sh --force <dump>
#
# Environment:
#   DATABASE_URL   Target Postgres connection string (required). The target is
#                  what gets overwritten — point it at staging, not prod,
#                  unless you mean it.
#
# Exit codes: 0 ok, 1 misconfig/usage, 2 aborted, 3 restore failed.
set -euo pipefail

FORCE=0
DUMP=""
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -*) echo "ERROR: unknown flag: $arg" >&2; exit 1 ;;
    *) DUMP="$arg" ;;
  esac
done

if [[ -z "$DUMP" ]]; then
  echo "Usage: $0 [--force] <dump-file>" >&2
  exit 1
fi
if [[ ! -f "$DUMP" ]]; then
  echo "ERROR: dump file not found: $DUMP" >&2
  exit 1
fi
if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERROR: DATABASE_URL (the RESTORE TARGET) is not set." >&2
  exit 1
fi

CONN="${DATABASE_URL/postgresql+psycopg2/postgresql}"
CONN="${CONN/postgresql+psycopg/postgresql}"
if [[ "$CONN" != postgres* ]]; then
  echo "ERROR: restore target must be a Postgres DATABASE_URL." >&2
  exit 1
fi

# Show the target host/db (without the password) so the operator can confirm.
SAFE_TARGET="$(printf '%s' "$CONN" | sed -E 's#(://[^:/@]+):[^@]*@#\1:***@#')"

if [[ "$FORCE" -ne 1 ]]; then
  echo "About to restore:"
  echo "    dump:   $DUMP"
  echo "    target: $SAFE_TARGET"
  echo "This will DROP and recreate objects in the target database."
  read -r -p "Type 'restore' to proceed: " CONFIRM
  if [[ "$CONFIRM" != "restore" ]]; then
    echo "Aborted." >&2
    exit 2
  fi
fi

echo "[restore] loading ${DUMP} into ${SAFE_TARGET} ..."
# --clean --if-exists makes the restore idempotent; --no-owner avoids needing
# the original role names. Errors during DROP of not-yet-existing objects are
# tolerated by --if-exists; a real failure still exits non-zero via pipefail.
if ! pg_restore --clean --if-exists --no-owner --no-privileges \
       --dbname="$CONN" "$DUMP"; then
  echo "ERROR: pg_restore reported errors." >&2
  exit 3
fi

echo "[restore] done. Verify with: deploy/RUNBOOK.md -> 'Restore drill'."
