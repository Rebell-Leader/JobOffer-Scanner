#!/usr/bin/env bash
#
# backup_db.sh — take a compressed, restorable Postgres backup.
#
# Produces a pg_dump custom-format archive (-Fc), which pg_restore can load
# selectively and in parallel. Designed to run from cron / a Scheduled
# Deployment. Best-effort uploads and retention pruning are built in.
#
# Usage:
#   DATABASE_URL=postgresql://user:pass@host:5432/db ./scripts/backup_db.sh
#
# Environment:
#   DATABASE_URL            Postgres connection string (required). A
#                           "+driver" suffix (e.g. postgresql+psycopg) is
#                           stripped automatically.
#   BACKUP_DIR              Where to write dumps (default ./backups).
#   BACKUP_RETENTION_DAYS   Delete local dumps older than this (default 14).
#   BACKUP_UPLOAD_CMD       Optional command run per new dump for offsite
#                           copy; the dump path is appended as the last arg.
#                           e.g. BACKUP_UPLOAD_CMD="aws s3 cp" or
#                                BACKUP_UPLOAD_CMD="rclone copyto remote:joc/"
#
# Exit codes: 0 ok, 1 misconfig, 2 dump failed.
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERROR: DATABASE_URL is not set." >&2
  exit 1
fi

# pg_dump doesn't understand SQLAlchemy's "+driver" suffix.
CONN="${DATABASE_URL/postgresql+psycopg2/postgresql}"
CONN="${CONN/postgresql+psycopg/postgresql}"

if [[ "$CONN" != postgres* ]]; then
  echo "ERROR: backups require a Postgres DATABASE_URL (got: ${CONN%%:*}...)." >&2
  echo "       SQLite is just a file — copy ./data/joboffer.db instead." >&2
  exit 1
fi

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
mkdir -p "$BACKUP_DIR"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTFILE="${BACKUP_DIR}/joboffer-${TIMESTAMP}.dump"

echo "[backup] dumping to ${OUTFILE} ..."
if ! pg_dump --format=custom --no-owner --no-privileges --file="$OUTFILE" "$CONN"; then
  echo "ERROR: pg_dump failed." >&2
  rm -f "$OUTFILE"
  exit 2
fi

SIZE="$(wc -c < "$OUTFILE" | tr -d ' ')"
echo "[backup] wrote ${OUTFILE} (${SIZE} bytes)"

# A dump under ~1KB almost certainly means an empty/failed export — flag it.
if [[ "$SIZE" -lt 1024 ]]; then
  echo "WARNING: backup is suspiciously small (${SIZE} bytes)." >&2
fi

if [[ -n "${BACKUP_UPLOAD_CMD:-}" ]]; then
  echo "[backup] uploading via: ${BACKUP_UPLOAD_CMD} ${OUTFILE}"
  # shellcheck disable=SC2086 # intentional word-split of the command
  if ${BACKUP_UPLOAD_CMD} "$OUTFILE"; then
    echo "[backup] upload ok"
  else
    echo "WARNING: upload command failed (local copy retained)." >&2
  fi
fi

# Prune old local dumps (offsite copies are governed by their own lifecycle).
echo "[backup] pruning local dumps older than ${RETENTION_DAYS} day(s) ..."
find "$BACKUP_DIR" -name 'joboffer-*.dump' -type f -mtime "+${RETENTION_DAYS}" -print -delete || true

echo "[backup] done."
