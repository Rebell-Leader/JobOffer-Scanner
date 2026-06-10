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
  url_ingest, browser_scraper (Playwright, optional), html_extract (shared),
  company_research (agentic keyless DuckDuckGo + Browserbase fallback)
services/               Business logic (34 modules) — the bulk of the app
  auth, totp, oauth, api_tokens, rate_limit, audit          (identity/security)
  applications, stages, analytics, timeline, background_analysis  (tracking)
  master_cv, projects, tailoring, constraint_check, suggestions, pdf_export  (CV/artifacts)
  sharing, webhooks, telegram_link, notifications, email, reminders  (integrations)
  checkpoint, bulk_import, system_test, analysis_runner, usage (LLM cost),
  billing (tiers/Stripe), funnel (operator metrics), waitlist,
  account_export, email_verify, _ownership (require_owned)
db/                     SQLAlchemy 2.0 models + session (StaticPool-aware)
api/                    FastAPI app: routes, bearer auth, security headers
bot/                    Telegram bot: handlers (pure) + main (runtime)
worker/                 Celery app + tasks + CLI runners (reminders, metrics_dump)
utils/                  LEAF layer: llm, config, env, crypto, security, diff,
                        verdict, text, logging_setup, metrics, timing, cache
migrations/             Alembic (19 revisions, 23 tables)
chrome-extension/       MV3 extension calling the REST API (JS, Node-tested)
deploy/                 Caddy/nginx reverse-proxy examples (CSP/HSTS) +
                        README (edge topology) + RUNBOOK (backups/incidents)
scripts/                backup_db.sh / restore_db.sh (Postgres ops)
landing/                static marketing site + waitlist form (deploy at edge)
docs/                   STRIPE_SETUP, HARDENING, GTM (founder/ops guides)
tests/                  46 files, 697 Python tests (7 live e2e, skipped
                        unless RUN_E2E=1) + 9 JS (extract.test.mjs)
```

## Architecture & layering (keep this clean)

Strict one-directional dependency flow — never import upward:

```
app.py / api/ / bot/        entry points (UI, REST, Telegram)
        │
        ▼
   services/                business logic (owns the DB, side-effects, policy)
        │
        ▼
      db/                    SQLAlchemy models + session

agents/  (LangGraph pipeline)  ─┐
tools/   (LLM-facing tools)     ├─▶  utils/   (LEAF — depends on nothing internal)
                                ─┘
worker/  (Celery) consumes services; runs the same pipeline off-process.
```

- **`utils/` is a LEAF.** It must NOT import from `services/agents/tools/api/
  bot/worker`. When a leaf needs a higher-layer behaviour (e.g. the LLM client
  notifying the usage ledger), use an **observer**: `utils/llm` exposes
  `register_usage_recorder`; `services/usage` registers `record_completion`
  into it on import (wired via `services/__init__`). Don't reach back up.
- **`db/` is below services** — it may import `utils.env` (config) but nothing
  else internal. No business logic lives in `db/` or `utils/`.
- **`tools/` and `agents/` depend DOWN on `utils/` only** — the one accepted
  exception is `agents/orchestrator` importing `services.checkpoint` (resume
  persistence) + `services.usage` (per-thread attribution). Provider/transport
  code lives only in `utils/llm`; a service calling `get_completion` to run a
  domain prompt is correct (business orchestration), not a layer break.
- **`services ↔ worker` is the one accepted bidirectional seam** (producer in
  services, consumer in worker). The `services → worker` direction is ALWAYS a
  lazy in-function import to avoid an import-time cycle; keep it that way.
- **Import full submodule paths** — `from utils.cache import cache`, not
  `from utils import cache`. The `utils/` and `tools/` `__init__.py` are
  intentionally empty (no symbol re-exports) so a submodule is never shadowed
  by a same-named re-exported value.

### Shared helpers — use these, don't re-roll them

- **Config:** `utils/env.env_{str,bool,int,float}` — never `os.getenv(...) ==
  "1"` (env_bool accepts `1/true/yes/on`) or ad-hoc `int(os.getenv(...))`.
  Reads are recorded; `utils/config.log_effective_config()` logs non-default
  config at startup (don't read secrets through this).
- **Ownership:** `services/_ownership.require_owned(session, Model, id,
  user_id, exc, msg)` for every owned fetch — don't inline the
  `is None or .user_id != user_id` check.
- **LLM-output text:** `utils/text.strip_code_fence` (fenced JSON) and
  `tools/html_extract.extract_job_text` (posting HTML → text) are the single
  implementations — reuse them.

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
python -m unittest discover -s tests      # ~6 min, 697 tests (7 e2e skipped)
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
  raise into the user action that triggered them. The `stage.added` /
  `application.saved` webhooks dispatch from the **service layer**
  (`services/stages`, `services/applications`) so every entry point (UI, API,
  bot) fires them; `dispatch_event_durable` skips work entirely when no active
  subscriber exists, else enqueues a retrying Celery task (or a daemon thread
  without a broker).
- **Secrets are bcrypt-hashed at rest**: passwords, reset tokens, 2FA backup
  codes, API tokens (with an indexed 8-char prefix for fast lookup). The TOTP
  secret is envelope-encrypted (`utils/crypto`, `enc:v1:…`) when
  `SECRETS_ENCRYPTION_KEY` is set, else plaintext (dev/demo).
- **OAuth auto-link requires a provider-VERIFIED email.** `services/oauth`
  only links an external identity to a pre-existing local account when the
  provider asserts the email is verified (Google OIDC claim / GitHub verified
  primary) — otherwise it refuses (anti-account-takeover).
- **Billing never constrains self-hosters.** `services/billing`: with no
  `STRIPE_SECRET_KEY`, every user is on the `unlimited` tier and all quota
  checks are no-ops. With a key, no-subscription = `free` tier; quotas
  (analyses/window, artifacts/window, tier LLM budget, detailed-model,
  API access) are enforced inside `check_user_quota`, `tailoring`, and
  `api/auth` — entry points only catch `TierLimitExceeded`. Tier limits
  live in code (`TIER_LIMITS_JSON` to override), the user's tier in the
  `subscriptions` table (mirrored from Stripe via `POST /v1/billing/webhook`).
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

### P0 — ✅ DONE (all four shipped)
1. ✅ **Horizontal-scaling state.** `utils/cache` and `services/checkpoint`
   gained Redis backends behind `REDIS_URL` (same detection + graceful
   fallback as rate-limiting). Metrics aggregation across instances is still
   per-process — that's folded into P1 "observability shipping".
2. ✅ **`use_container_width` → `width="stretch"`** (all 5 sites); the
   AppTest smoke test confirms no deprecation warnings.
3. ✅ **Account lifecycle:** email verification on signup (soft banner;
   `REQUIRE_EMAIL_VERIFICATION=1` to hard-gate), `delete_account`
   (password-gated, cascades — SQLite FK pragma now ON so cascade matches
   Postgres), full data export (`services/account_export`, secrets excluded),
   and reset-token logging now gated behind `RESET_TOKEN_SURFACE_IN_UI`.
4. ✅ **Schema-drift guard** (`tests/test_phase29_schema_drift.py`): applies
   all migrations and asserts no add/remove table-or-column drift vs the
   models. Runs in CI via the unittest job.

### P1 — ✅ DONE (hardening + confidence)
5. ✅ **CI quality gates:** ruff (lint) + mypy (type) + bandit (hard gate) +
   pip-audit (advisory) + coverage floor 80% + JS tests in CI + Node-24 opt-in.
   Config in `pyproject.toml`; jobs in `.github/workflows/tests.yml`.
6. ✅ **Secrets at rest:** the TOTP secret is envelope-encrypted via
   `utils/crypto` (Fernet, key derived from `SECRETS_ENCRYPTION_KEY`) — stored
   as `enc:v1:…`, transparently decrypted on read, pass-through plaintext when
   unkeyed (dev/demo), and legacy plaintext rows re-encrypt opportunistically
   on the next successful verify. Column widened 64→255 (migration
   `5d2f8a1c4e7b`). Same primitive is reusable for OAuth-linked emails.
7. ✅ **LLM cost controls:** `services/usage` ledgers every real completion's
   tokens + estimated USD cost (`llm_usage` table, migration `7c1a9e3b5f02`),
   attributed to a user via a `contextvars` scope (`usage.account(user_id)`,
   re-applied across the company‖salary thread pool). `check_user_quota` now
   enforces a rolling-window spend budget (`LLM_BUDGET_USD`) on top of the
   request-count limiter. Pricing table is env-overridable (`LLM_PRICING_JSON`).
   Opt-in identical-completion cache (`LLM_CACHE_COMPLETIONS=1`) returns free
   hits with no token/cost re-charge.
8. ✅ **Observability shipping:** `utils/metrics.render_prometheus` emits the
   in-process registry in Prometheus text format; the REST API serves it at
   `GET /metrics` (gated by `METRICS_ENABLED`, optional `METRICS_TOKEN` bearer).
   Per-process is Prometheus's model — it scrapes each instance and aggregates
   server-side. `worker/metrics_dump --prometheus|--push` adds a Pushgateway
   stop-gap for cron-only deploys. Logs are already structured JSON (stdout
   drain).
9. ✅ **Live provider e2e** (`tests/test_e2e_live.py`, `RUN_E2E=1`): real
   round-trip per provider, real DuckDuckGo search, the agentic company
   fallback with no news/COL keys, URL ingest, optional Browserbase, and a
   full real-posting pipeline run. Skipped by default (egress-restricted CI).

**Agentic fallback (shipped, P1):** when `NEWS_API_KEY` is absent but an LLM
key is set, `tools/company_research.agentic_company_research` does an
LLM-directed **keyless DuckDuckGo** search ([research] extra: ddgs) and
synthesises a briefing under the no-fabrication rule, optionally deep-fetching
a top result via a **headless agentic browser** (Browserbase hosted, or the
local Playwright scraper). Wired into `fetch_company_news` as tier 2.

### P2 — ✅ DONE (all four shipped)
10. ✅ **JS-board scraping:** `url_ingest` now routes known JS boards
    (LinkedIn/Indeed/Glassdoor/ZipRecruiter via `is_js_board`) and any
    too-thin plain fetch through a headless browser — the local Playwright
    scraper OR a **hosted Browserbase** session (`deep_fetch`, no local
    chromium, so it works on a vanilla deploy). Honest paste-hint error when no
    backend is configured. The Chrome extension remains the auth'd-board path.
11. ✅ **Webhook delivery durability:** `dispatch_event_durable` enqueues a
    per-delivery Celery task (`worker.tasks.deliver_webhook_task`) that retries
    with exponential backoff (`WEBHOOK_MAX_ATTEMPTS`/`WEBHOOK_RETRY_BACKOFF`)
    for at-least-once delivery; degrades to the daemon-thread path when no
    broker. `attempt_delivery(delivery_id)` is the re-runnable single-attempt
    unit (rebuilds the signed body from the stored row).
12. ✅ **Backups + runbook:** `scripts/backup_db.sh` (pg_dump custom format,
    retention pruning, optional offsite upload hook) + `scripts/restore_db.sh`
    (confirmation-gated `pg_restore --clean`), documented in `deploy/RUNBOOK.md`
    (backup schedule, monthly restore drill, incidents, escalation). Scripts
    are syntax/guard-tested in `tests/test_phase38_ops_backup.py`.
13. ✅ **Reverse proxy on the real deployment:** `deploy/README.md` documents
    the edge topology (TLS + CSP/HSTS + Secure cookies for the Streamlit UI,
    pass-through for the self-hardening API) and how to apply the Caddy/nginx
    examples or an equivalent edge (Cloudflare) on Replit. The header set is
    guarded by `tests/test_phase40_edge_proxy.py`.

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
