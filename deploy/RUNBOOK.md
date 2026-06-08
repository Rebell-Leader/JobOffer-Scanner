# Operations Runbook — JobOffer Scanner

Practical procedures for running the deployed app: backups, restore drills,
health checks, common incidents, and routine maintenance. Keep this current —
a runbook nobody trusts is worse than none.

> Scope: the Postgres-backed production deployment (Replit or Docker Compose).
> In dev/demo (SQLite, no keys) most of this is N/A — the "database" is the
> file `./data/joboffer.db`; copy it to back up.

---

## 1. Service map

| Component        | How it runs                                  | Health |
|------------------|----------------------------------------------|--------|
| Web UI           | `streamlit run app.py` (:5000)               | page loads, auth gate renders |
| REST API         | `python -m api.main` (:8000)                 | `GET /healthz` → `{"ok": true}` |
| Worker (opt.)    | `celery -A worker.celery_app:app worker`     | tasks leave `PENDING` |
| Reminders (cron) | `python -m worker.reminders`                 | exits 0 |
| Postgres         | managed / compose `postgres` service         | `pg_isready` |
| Redis (opt.)     | managed / compose `redis` service            | `redis-cli ping` → PONG |

Metrics (if `METRICS_ENABLED=1`): `GET /metrics` on the API (Prometheus text;
send `Authorization: Bearer $METRICS_TOKEN` when a token is set). Logs are
structured JSON on stdout when `LOG_FORMAT=json`.

---

## 2. Backups

### What & why
`scripts/backup_db.sh` writes a `pg_dump` **custom-format** archive (`-Fc`) —
compressed and restorable selectively/in parallel with `pg_restore`. The full
analysis blobs live in `Application.analysis_json`, so a DB backup is a
complete, self-contained snapshot (no separate object store to reconcile).

### Run it
```bash
DATABASE_URL=postgresql://user:pass@host:5432/joboffer \
  BACKUP_DIR=/var/backups/joboffer \
  BACKUP_RETENTION_DAYS=14 \
  ./scripts/backup_db.sh
```

Offsite copy (recommended) — set an upload command; the dump path is appended:
```bash
BACKUP_UPLOAD_CMD="aws s3 cp" ...        # → aws s3 cp <dump> s3://… (set dest in cmd)
BACKUP_UPLOAD_CMD="rclone copyto remote:joc/" ...
```

### Schedule
- **Docker Compose / VM:** cron, e.g. nightly at 03:17 UTC:
  ```cron
  17 3 * * * cd /srv/joboffer && DATABASE_URL=… ./scripts/backup_db.sh >> /var/log/joc-backup.log 2>&1
  ```
- **Replit:** a Scheduled Deployment running the same command.
- Keep ≥14 days local; rely on the offsite bucket's lifecycle for long-term.

### Verify (don't trust an unread backup)
- The script warns if a dump is < 1 KB (usually an empty/failed export).
- Confirm a recent file exists and is growing run-over-run:
  ```bash
  ls -lh "$BACKUP_DIR"/joboffer-*.dump | tail -3
  ```
- Actually exercise it monthly via the **restore drill** below — an untested
  backup is a hypothesis, not a backup.

---

## 3. Restore

### Restore drill (do this monthly; never first-time-in-prod)
Restore the latest dump into a **scratch** database and sanity-check it:
```bash
# 1. Make an empty scratch DB.
createdb joboffer_restore_test

# 2. Restore into it (no prod credentials here!).
DATABASE_URL=postgresql://user:pass@host:5432/joboffer_restore_test \
  ./scripts/restore_db.sh --force backups/joboffer-<latest>.dump

# 3. Smoke-check row counts.
psql "$DATABASE_URL" -c "SELECT count(*) FROM users;"
psql "$DATABASE_URL" -c "SELECT count(*) FROM applications;"

# 4. Point a throwaway app instance at it and confirm login + an app loads.

# 5. Drop the scratch DB.
dropdb joboffer_restore_test
```
Record the date + result somewhere durable. If the drill fails, treat it as a
SEV-2 until backups are proven again.

### Real restore (recovery)
1. **Stop writers** (web + worker) so nothing races the restore.
2. Restore into the target (interactive confirm unless `--force`):
   ```bash
   DATABASE_URL=postgresql://user:pass@host:5432/joboffer \
     ./scripts/restore_db.sh backups/joboffer-<chosen>.dump
   ```
   The script masks the password and asks you to type `restore` to proceed.
3. Bring the schema to head if the dump predates a migration:
   ```bash
   USE_ALEMBIC=1 alembic upgrade head
   ```
4. Restart web + worker; verify `GET /healthz` and a login.

> `restore_db.sh` uses `--clean --if-exists`, so it drops and recreates objects
> in the target. **The target is overwritten** — double-check `DATABASE_URL`
> points where you intend.

---

## 4. Migrations

- Production runs Alembic: `USE_ALEMBIC=1 alembic upgrade head` at deploy time.
- Always **back up before** applying a migration in prod.
- CI runs the full `up → down → up` on real Postgres and a schema-drift guard
  (`tests/test_phase29_schema_drift.py`); a red bar there means a model changed
  without a migration.
- Rollback one step: `alembic downgrade -1` (review the migration's
  `downgrade()` first — some are intentionally one-way).

---

## 5. Common incidents

**API/UI 5xx or won't boot**
- Check logs for a stack trace. Most boot failures are config: missing/invalid
  `DATABASE_URL`, or a provider key set but unreachable.
- `GET /healthz` isolates API-vs-DB: if it 500s, the DB connection is the
  suspect (`pg_isready`, credentials, connection limit).

**"Demo mode" unexpectedly in prod**
- No provider key is resolving. Confirm `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`
  / `FEATHERLESS_API_KEY` (or `LLM_PROVIDER`) is set in the runtime env, not
  just `.env.example`.

**Analyses rejected with a budget error**
- A user hit `LLM_BUDGET_USD`. Inspect spend: `SELECT user_id, sum(cost_micro_usd)/1e6
  FROM llm_usage WHERE created_at > now() - interval '30 days' GROUP BY 1;`
  Raise the budget or wait out the window.

**Webhooks not arriving**
- Deliveries are durable when a Celery broker is set: check the worker is up
  and `webhook_deliveries` rows (`success`, `attempts`, `error`). Without a
  broker they're best-effort daemon threads (no retry). Owners can re-send a
  failed delivery from the UI.

**Background analyses stuck `PENDING`**
- Worker down or broker unreachable. Check the `worker` process and
  `CELERY_BROKER_URL`. Without a broker, analysis runs inline (no async).

**Rate-limit / lockout complaints**
- Tunables: `RL_LOGIN_*`, `RL_ANALYSIS_*`. In multi-instance deployments these
  only coordinate when `REDIS_URL` is set; otherwise each instance counts
  independently.

---

## 6. Routine maintenance

- **Secrets rotation:** rotating `SECRETS_ENCRYPTION_KEY` invalidates existing
  encrypted TOTP secrets — those users must re-enrol 2FA. Rotate only with a
  re-encryption plan. App/API bearer tokens and webhook secrets can be rotated
  per-record from the UI/API.
- **TLS / headers:** terminate TLS and apply CSP/HSTS at the edge — see
  `deploy/Caddyfile.example` / `deploy/nginx.conf.example`. The API also sets
  defensive headers itself (`api/security.py`); enable HSTS there only behind
  TLS (`API_ENABLE_HSTS=1`).
- **Dependency hygiene:** `uv.lock` must match `pyproject.toml` (`uv lock`
  after any change). CI runs `pip-audit` (advisory) — review findings.
- **Capacity:** metrics expose LLM latency/cost and pipeline timings; watch
  `llm.request` p95 and per-window `llm_usage` spend.

---

## 7. Escalation checklist (data-loss event)

1. Stop writers immediately (prevent further divergence).
2. Identify the last-known-good dump (`ls -lt "$BACKUP_DIR"`).
3. Run the **restore drill** against a scratch DB to confirm the dump is good
   BEFORE touching prod.
4. Restore to prod (§3), `alembic upgrade head`, restart, verify.
5. Write a short post-incident note: trigger, blast radius, recovery time,
   follow-ups.
