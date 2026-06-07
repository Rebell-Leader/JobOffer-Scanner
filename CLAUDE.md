# CLAUDE.md — JobOffer Scanner

Guide for AI agents (and humans) working in this repo. Read this first.

## What this is

An AI-assisted **job-offer analysis platform**. A user pastes a job posting
(or a URL); the system runs a multi-stage LLM pipeline that extracts
requirements, assesses company stability, benchmarks compensation, optionally
scores a resume against the posting (ATS), and emits a structured
Green/Yellow/Red verdict + report. Around that core it tracks applications
through pipeline stages, derives analytics, maintains a master CV + project
gallery, and generates **tailored CVs / cover letters that cannot fabricate**
(a deterministic post-check verifies every generated artifact stays within the
user's real CV facts).

Three channels: **Streamlit web UI** (`app.py`), a **Telegram bot** (`bot/`),
and a **REST API** (`api/`). Deployed on **Replit** (Streamlit + Replit
Postgres); a `docker-compose.yml` runs the full multi-service stack.

## Repo layout

```
app.py                  Streamlit web UI (single entry; all tabs + sidebar)
agents/                 LangGraph pipeline: orchestrator + per-stage agents
  orchestrator.py       Checkpoint-aware graph: job -> (company || salary) -> resume? -> report
  job_/company_/salary_/resume_analyzer.py, report_generator.py
tools/                  LLM-facing tools + ingestion
  job_tools, company_tools, salary_tools, resume_tools, data_sources,
  url_ingest, browser_scraper (Playwright, optional)
services/               Business logic (28 modules) — the bulk of the app
  auth, totp, oauth, api_tokens, rate_limit, audit          (identity/security)
  applications, stages, analytics, timeline, background_analysis  (tracking)
  master_cv, projects, tailoring, constraint_check, suggestions, pdf_export  (CV/artifacts)
  sharing, webhooks, telegram_link, notifications, email, reminders  (integrations)
  checkpoint, bulk_import, system_test, analysis_runner
db/                     SQLAlchemy 2.0 models + session (StaticPool-aware)
api/                    FastAPI app: routes, bearer auth, security headers
bot/                    Telegram bot: handlers (pure) + main (runtime)
worker/                 Celery app + tasks + CLI runners (reminders, metrics_dump)
utils/                  llm, config, security, diff, verdict, logging_setup, metrics, timing, cache
migrations/             Alembic (14 revisions, 18 tables)
chrome-extension/       MV3 extension calling the REST API (JS, Node-tested)
deploy/                 Caddyfile + nginx reverse-proxy examples (CSP/HSTS)
tests/                  28 files, 468 Python tests + 9 JS (extract.test.mjs)
```

## How to run

```bash
# Web UI (what Replit runs)
streamlit run app.py                      # :5000

# REST API
python -m api.main                        # :8000 ; /healthz, /v1/*

# Telegram bot   (needs TELEGRAM_BOT_TOKEN + [telegram] extra)
python -m bot.main

# Celery worker  (needs CELERY_BROKER_URL + [worker] extra)
celery -A worker.celery_app:app worker

# Scheduled reminders (cron / Scheduled Deployment)
python -m worker.reminders

# Tests
python -m unittest discover -s tests      # ~5 min, 468 tests
node chrome-extension/extract.test.mjs    # 9 JS tests

# Migrations
USE_ALEMBIC=1 alembic upgrade head        # prod; else create_all on boot
```

Config is via env vars / `.env` (see `.env.example`). No key set ⇒ **demo
mode** (sample data, clearly labelled). `DATABASE_URL` unset ⇒ SQLite at
`./data/joboffer.db`.

## Conventions that matter (follow these)

- **Demo mode is a first-class state.** `utils/llm.get_completion` returns
  bounded sample data when no provider key is set, and **raises** (never
  fabricates) when a key is set but the call fails. The UI badge reflects it.
- **No fabrication in generated artifacts.** `services/tailoring` injects
  `NO_FABRICATION_RULES` into every prompt AND `services/constraint_check`
  deterministically flags any skill/year/percentage/claim in the output that
  isn't in the master CV + projects + job context. Keep both halves.
- **Ownership scoping everywhere.** Every service read/write that takes a
  `user_id` checks it; cross-user access raises. The REST API returns **404
  (not 403)** for another user's resource so IDs can't be enumerated.
- **Best-effort side-effects never break the main flow.** Notifications
  (Telegram/email/webhooks) and audit writes log-and-continue; they must not
  raise into the user action that triggered them. Webhooks dispatch on a
  daemon thread.
- **Secrets are bcrypt-hashed at rest**: passwords, reset tokens, 2FA backup
  codes, API tokens (with an indexed 8-char prefix for fast lookup). TOTP
  secrets are stored plaintext by design (documented; envelope-encrypt for
  hardened prod).
- **Migrations:** add a model field ⇒ add an Alembic migration. CI runs the
  full `up → down → up` on real Postgres. `db.session.reset_engine_for_testing`
  uses **StaticPool** so thread-hopping tests (bot handlers, Streamlit
  `AppTest`) share one in-memory connection.
- **Streamlit forbids nested `st.expander`.** Inner collapsibles use
  `st.checkbox` toggles. `tests/test_phase28_ui_smoke.py` guards this both
  dynamically (AppTest render) and statically (source scan).

## Testing approach

- Services are unit-tested directly against in-memory SQLite.
- Network is always mocked (LLM, HTTP, SMTP, Telegram). No test hits a real
  provider — this is deliberate (the sandbox is egress-restricted; CI is too).
- `tests/test_phase28_ui_smoke.py` renders the real `app.py` via Streamlit
  `AppTest` (auth gate, dense authenticated UI, public share view) and clicks
  the analyze-submit + a stage quick-action — this is the only layer that
  exercises the Streamlit render/callback path, and it exists because a
  nested-expander bug once shipped through a fully-green service suite.

## Recent improvements (high level)

Built across ~28 increments from a non-functional prototype (it returned the
same canned "TechCorp" text regardless of input) to the current product:

- **Core made real:** provider-agnostic LLM (Anthropic/OpenAI/Featherless,
  incl. GPT-5 / o-series `max_completion_tokens` handling), prompt-injection
  hardening, parallel company‖salary stages, resumable pipeline checkpoints.
- **Product:** resume/ATS analysis, URL ingestion, structured verdict, stage
  tracking + analytics + timelines, master CV (with revisions + diff),
  project gallery, tailored CV/cover-letter generation with the deterministic
  no-fabrication check + one-click "add skill to CV" suggestions, PDF export,
  bulk import.
- **Platform:** email/password + **OAuth (Google/GitHub)** + **TOTP 2FA**,
  rate limiting, audit log, structured JSON logging, in-process metrics +
  timing, background analyses (Celery), inactivity reminders, public share
  links, webhooks, REST API + bearer tokens, Chrome extension, system-test tab.
- **Ops:** Alembic migrations (Postgres-tested in CI), Docker Compose,
  reverse-proxy CSP/HSTS configs, Replit deployment (live).

## Production-readiness roadmap (what's left)

The app is feature-complete and deployed, but several things stand between it
and "production-grade for real multi-user traffic." Prioritized:

### P0 — do before scaling past one instance / real users
1. **State that's in-process won't survive horizontal scaling.** `utils/cache.SimpleCache`,
   `services/checkpoint`, `utils/metrics`, and the default rate-limit backend
   are per-process. Rate-limit already supports Redis; do the same for the
   cache + checkpoint store (or accept single-instance Reserved-VM only).
2. **`use_container_width` is deprecated and past its removal date
   (2025-12-31).** 5 call sites in `app.py`. Migrate to `width="stretch"`
   before a Streamlit bump breaks the UI.
3. **Account lifecycle gaps:** no email verification on signup, no "delete my
   account / export all my data" (GDPR). FK cascades exist, but expose the
   action. `RESET_TOKEN_SURFACE_IN_UI` and reset-token logging must be off in
   prod.
4. **Schema drift guard.** App boots with `create_all` by default; prod uses
   Alembic. Add a CI check (`alembic check` / autogenerate-diff) so models and
   migrations can't silently diverge.

### P1 — hardening + confidence
5. **CI quality gates:** add lint (ruff), type-check (mypy), security scan
   (bandit + pip-audit), and a coverage floor. Run the JS tests in CI
   (add setup-node). Bump `actions/checkout`/`setup-python` off Node 20.
6. **Secrets at rest:** envelope-encrypt the TOTP secret (KMS-derived key);
   consider the same for OAuth-linked emails.
7. **LLM cost controls:** token accounting + per-user budget (today's quota is
   a request count, not spend). Cache identical analyses (the cache exists; wire
   COL/news/LLM through it consistently).
8. **Observability shipping:** logs are structured JSON and metrics exist, but
   nothing exports them. Wire an OTLP/Prometheus exporter or a log drain;
   metrics are currently per-process snapshot-only.
9. **Live provider e2e:** every test mocks the LLM. Add a gated,
   key-required smoke test (skipped without a key) that does one real
   round-trip per provider so model/param regressions (like the GPT-5
   `max_completion_tokens` change) are caught.

### P2 — robustness + reach
10. **JS-board scraping:** `browser_scraper` (Playwright) exists but isn't
    deployed; LinkedIn/Indeed/Glassdoor need it (or the Chrome extension).
11. **Webhook delivery durability:** deliveries are best-effort daemon
    threads; move to the Celery queue with retry/backoff for at-least-once.
12. **Backups + runbook:** automated Postgres backups, a restore drill, and a
    documented incident/runbook. None exist yet.
13. **Reverse proxy on the real deployment:** the CSP/HSTS configs in `deploy/`
    aren't applied on vanilla Replit; a hardened public deploy should sit
    behind Caddy/nginx (or an equivalent edge).

## Gotchas

- The sandbox/CI is **egress-restricted** — only allowlisted hosts resolve.
  Don't write tests that hit real network; mock it.
- `uv.lock` must match `pyproject.toml` — regenerate with `uv lock` after any
  dependency change, or Replit's `uv sync` installs the wrong set.
- This is an **application, not a library**: `pyproject.toml` sets
  `[tool.setuptools] py-modules = []` so `pip install -e .` only pulls deps;
  source is imported from the repo root (on `sys.path` in every run context).
- `fpdf2 >= 2.8` imports `cryptography` eagerly; if a sandbox has broken Rust
  bindings, pin to `2.7.x` locally (prod/CI are fine).
