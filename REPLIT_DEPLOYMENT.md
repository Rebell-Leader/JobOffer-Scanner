# Replit deployment — compatibility of this branch with `main`

`main` is configured to host on Replit via `.replit`, which runs a single
process:

```toml
[deployment]
run = ["sh", "-c", "streamlit run app.py"]
[[ports]]
localPort = 5000
externalPort = 80
```

**Short answer: yes, this branch merges and still deploys on Replit as a
Streamlit app — but two things MUST be set or the deployment breaks, and the
new non-Streamlit surfaces (REST API, Celery worker, Telegram bot) are not
started by Replit's single run command.**

This was verified against the live Replit docs (linked at the bottom) and by
actually resolving every `app.py` import in a clean environment.

---

## 1. CRITICAL — the lockfile was stale (fixed in this branch)

Replit installs Python deps with `uv` from `uv.lock`. The committed lock was
generated at the *prototype* stage and was missing ~14 of the dependencies we
added across phases 0–27:

```
✗ fastapi  ✗ pyotp  ✗ qrcode  ✗ uvicorn  ✗ alembic
✗ bcrypt   ✗ anthropic  ✗ pypdf  ✗ python-docx  …
```

With that stale lock, `streamlit run app.py` would have crashed at startup on
`import pyotp` / `import bcrypt`. **This branch regenerates `uv.lock`** (via
`uv lock`) so it matches `pyproject.toml` exactly — including the optional
extras (`celery`, `redis`, `playwright`, `fpdf2`, `python-telegram-bot`) and
dropping the now-unused `twilio`. After the fix, all 40 top-level `app.py`
imports resolve cleanly.

If you ever change deps again: run `uv lock` and commit the result, or delete
`uv.lock` so Replit resolves fresh from `pyproject.toml`.

## 2. CRITICAL — set `DATABASE_URL` to Replit PostgreSQL

The app defaults to **SQLite at `./data/joboffer.db`** (created on startup by
`init_db()`'s `create_all`). That's fine for local dev, but on Replit:

* **Autoscale deployments are stateless, ephemeral, and scale-to-zero** —
  the filesystem does not persist and multiple instances don't share it, so a
  SQLite file means data loss and split-brain. Replit explicitly warns against
  relying on the deployed filesystem for persistence.
* **Reserved VM deployments** have a persistent disk and a single instance, so
  SQLite would survive — but Postgres is still the better call.

Replit ships a **built-in PostgreSQL**. Create one and set the `DATABASE_URL`
secret (Replit → Secrets). The app picks it up automatically — `init_db()`
runs `create_all` against Postgres on first boot, creating all 15 tables. No
extra step needed for a single-instance start; for migration-managed
deploys set `USE_ALEMBIC=1` and run `alembic upgrade head` before serving.

## 3. Set an LLM provider key (else demo mode)

Without `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `FEATHERLESS_API_KEY`, the app
boots fine but runs in clearly-labelled **demo mode** (sample data). Add one as
a Replit Secret for real analysis. (Note: Replit's deployment network must
allow egress to the chosen provider.)

## 4. The non-Streamlit surfaces are NOT auto-started on Replit

Replit's deployment runs exactly one command (`streamlit run app.py`). These
optional components each need their own process and are therefore **not** part
of the Streamlit deployment:

| Component | How to run | Degrades to |
|-----------|-----------|-------------|
| REST API (FastAPI) | `python -m api.main` (separate service / port) | — (web UI unaffected) |
| Celery worker | `celery -A worker.celery_app:app worker` + Redis | background analyses disabled; everything runs inline |
| Telegram bot | `python -m bot.main` | bot just isn't available |
| Inactivity reminders | `python -m worker.reminders` via Scheduled Deployment | no reminder pings |

The app **degrades gracefully** for all of these: `async_enabled()` is False
without a broker (analysis runs inline), the browser scraper is off unless
`BROWSER_SCRAPER_ENABLED=1`, Telegram/email/webhooks are best-effort no-ops
when unconfigured. So the Streamlit UI is fully functional on a vanilla Replit
deployment; the extras require either a multi-service host (Docker Compose —
see `docker-compose.yml`) or additional Replit deployments (e.g. a Scheduled
Deployment for reminders, a Reserved VM for the worker + Redis).

## 5. Security headers (Phase 25) don't reach the Streamlit UI on Replit

The CSP/HSTS hardening assumes a reverse proxy you control (see
`deploy/Caddyfile.example`). On Replit you don't put nginx/Caddy in front, so
the web-UI CSP won't be applied there (Replit provides HTTPS, so transport is
still encrypted). The FastAPI `SecurityHeadersMiddleware` only applies if you
run the API. For a CSP-hardened public deployment, host behind Caddy/nginx on a
Reserved VM or external host instead of (or in front of) Replit.

---

## Bottom line

| Item | Status on merge |
|------|-----------------|
| Streamlit app boots on Replit | ✅ after the `uv.lock` regen in this branch |
| Data persistence | ⚠️ set `DATABASE_URL` to Replit Postgres (SQLite default is unsafe on Autoscale) |
| Real (non-demo) analysis | ⚠️ set a provider API key secret |
| REST API / worker / bot | ➖ not auto-started; run separately or use Docker Compose |
| CSP/HSTS on the web UI | ➖ needs a reverse proxy; not available on vanilla Replit |

**Net:** merging is safe. The one change that was strictly required to keep
Replit working — regenerating the stale `uv.lock` — is included in this
branch. The rest are deployment-time configuration (`DATABASE_URL` +
provider key as Secrets), not code changes.

### Sources
- [Replit — About Deployments](https://docs.replit.com/cloud-services/deployments/about-deployments)
- [Replit — Autoscale Deployments](https://docs.replit.com/cloud-services/deployments/autoscale-deployments)
- [Replit — Reserved VM Deployments](https://docs.replit.com/cloud-services/deployments/reserved-vm-deployments)
- [Replit — Announcing Autoscale and Static Deployments](https://blog.replit.com/autoscale)
