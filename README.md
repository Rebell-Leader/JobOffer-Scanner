
# AI Job Analysis Platform

An AI-powered platform that helps job seekers analyze job postings, evaluate company stability, and make informed career decisions by combining multiple data sources and AI analysis.

## 🚀 Current Status: Phase 0 — real LLM, honest gaps

The pipeline now performs **real LLM calls** against whichever provider key is
present (Anthropic / OpenAI / Featherless — auto-detected, overridable with
`LLM_PROVIDER`). Without a key, the app runs in clearly-labelled demo mode and
returns sample data — never silently. See `.env.example`.

### What Works Now
- ✅ Real LLM calls (Anthropic / OpenAI / Featherless) with retries
- ✅ Demo mode is visibly labelled (no fake "Production Mode" badge)
- ✅ Job posting requirement extraction
- ✅ Company stability briefing with explicit "data not available" labels
- ✅ Heuristic salary + cost-of-living estimate, labelled as ESTIMATE
- ✅ Final recommendation report
- ✅ Streamlit UI, progress callbacks, thread-safe cache
- ✅ End-to-end demo-mode smoke test

### Phase 1 (shipped)
- ✅ Prompt-injection hardening — untrusted job text & company names are
  sanitized and wrapped as inert data (`utils/security.py`)
- ✅ Pluggable real company-news feed via `NEWS_API_KEY` (newsapi.org), with
  honest "NOT AVAILABLE" fallback when unconfigured (`tools/data_sources.py`)
- ✅ Optional layoffs dataset via `LAYOFFS_DATASET_URL`
- ✅ Company + salary stages run concurrently (thread pool) for faster results

### Phase 2 (shipped)
- ✅ **Resume / ATS analysis** — upload PDF/DOCX/TXT; deterministic
  keyword-match score against required skills (boundary-aware: C# ≠ C),
  ATS formatting checks (tables, ALL-CAPS body, etc.), plus LLM gap
  commentary (`tools/resume_tools.py`)
- ✅ **Optional job-URL ingestion** — paste a URL or the description text;
  honest error when JS-rendered pages don't yield enough content
  (`tools/url_ingest.py`)
- ✅ **Structured Green / Yellow / Red verdict** with machine-readable JSON
  sidecar and an inference fallback (`utils/verdict.py`); rendered as a
  colored badge above the report

### Phase 3 (shipped)
- ✅ **Persistence layer** — SQLAlchemy models (`User`, `Application`);
  SQLite by default at `./data/joboffer.db`, drop in `DATABASE_URL` to point
  at Postgres (`db/`)
- ✅ **Email/password auth** with bcrypt hashing, normalized emails, identical
  error messages for unknown-user vs wrong-password (no enumeration leak)
  (`services/auth.py`)
- ✅ **Application tracking** — save any analysis to your dashboard, set
  status (saved / applied / interviewing / offer / rejected / withdrawn),
  add notes, update, delete, view the historical report without re-running
  the LLM (`services/applications.py`)
- ✅ **Streamlit UI**: auth gate, tabbed layout (Analyze / My Applications),
  Save form with status + notes, per-row update/delete forms
- ✅ 36 unit tests total (15 new for auth & applications: ownership
  isolation, password hashing, duplicate detection, callable-stripping)

### Phase 4 (shipped)
- ✅ **Password reset & change-password** — bcrypt-hashed one-shot reset
  tokens with 1-hour expiry, no user-enumeration leak (`services/auth.py`);
  raw token never stored. UI exposes Forgot Password, Use Reset Token, and
  a Change Password sidebar form. Token delivery left pluggable (printed to
  server logs by default; set `RESET_TOKEN_SURFACE_IN_UI=1` for self-hosted
  single-operator deployments).
- ✅ **Real salary benchmarks via Adzuna** — when `ADZUNA_APP_ID` + 
  `ADZUNA_APP_KEY` are set, the salary stage pulls live posting data
  (median, p10/p90, predicted-share) and tells the LLM to treat it as the
  primary signal; the heuristic table becomes a cross-check only. Routes
  to the right country feed automatically. Degrades to labelled-ESTIMATE
  heuristics when unconfigured.
- ✅ **Telegram bot channel** (`bot/`) — the second entry point from the
  vision doc, sharing the analysis pipeline. `/start`, `/help`, `/analyze
  <url-or-text>`. Long reports auto-chunked on paragraph boundaries to fit
  Telegram's 4096-char limit. Run with `python -m bot.main` after setting
  `TELEGRAM_BOT_TOKEN`. `python-telegram-bot` is an optional extra
  (`pip install -e ".[telegram]"`).
- ✅ **CI** (`.github/workflows/tests.yml`) — full unittest suite runs on
  push and pull_request against Python 3.11 and 3.12.
- ✅ 59 unit tests total (23 new for reset/change-password, Adzuna summary,
  Telegram parsing/chunking/formatting/handler logic).

### Phase 5 (shipped)
- ✅ **Real cost-of-living data** via configurable `COL_DATASET_URL` — same
  pluggable pattern as layoffs; degrades to labelled-ESTIMATE heuristic when
  unconfigured
- ✅ **Salary cache-key bug fix** — cache key now includes which data sources
  contributed, so configuring Adzuna/COL after a heuristic-only run no
  longer serves stale heuristic output. Covered by a regression test.
- ✅ **Alembic migrations** — `alembic upgrade head` produces the same schema
  as `create_all`. Opt in with `USE_ALEMBIC=1` for migration-managed
  deployments; zero-config setups still use `create_all`. Test runs the
  real migration against a temp SQLite and asserts every table exists.
- ✅ **Application export** — Download buttons in My Applications for CSV
  (pinned column order) and JSON (full backup including `analysis_json`).
  User-scoped: one account never sees another's rows.
- ✅ 68 unit tests total (9 new for Phase 5).

### Phase 23–27 (shipped) — integrations, federated auth, hardening, extension
- ✅ **Phase 23** — outbound **webhooks** (HMAC-signed POSTs on `stage.added`
  / `application.saved` / `verdict.changed`), delivery log + redelivery,
  background dispatch so the UI never blocks on a receiver.
- ✅ **Phase 24** — **OAuth login** (Google / GitHub) over the auth-code flow;
  identity resolution links by email, creates OAuth users with unusable
  passwords; CSRF-state-verified callback.
- ✅ **Phase 25** — **security headers**: pure-ASGI middleware adds CSP /
  X-Frame-Options / Referrer-Policy / opt-in HSTS to every API response
  (incl. errors); Streamlit XSRF pinned on; `deploy/Caddyfile.example` +
  `deploy/nginx.conf.example` add web-UI CSP/HSTS + Secure cookies.
- ✅ **Phase 26** — **Postgres-tested migrations in CI**: a service-container
  job runs `alembic upgrade head → downgrade base → upgrade head` on real
  Postgres and asserts all 15 tables exist (SQLite's permissive DDL hid this
  class of bug).
- ✅ **Phase 27** — **Chrome extension** (MV3) that reads the rendered job
  page and calls `/v1/analyze` with the user's API token — works on JS-heavy
  boards the server can't fetch. Pure helpers unit-tested in Node.
- ✅ **Deployment**: `REPLIT_DEPLOYMENT.md` analyses Replit compatibility;
  `uv.lock` regenerated to match `pyproject.toml` (the stale lock would have
  crashed the Replit boot). 462 tests total (Python) + 9 JS helper tests.

### Phase 19–22 (shipped) — async UX, hardened auth, sharing, public API
- ✅ **Phase 19** — **background analyses** via Celery survive container
  restarts and cross-session resume. `@st.fragment(run_every="5s")` card
  polls Celery state without re-running the form. Terminal-state short-
  circuit so polling stops once SUCCESS/FAILURE is reached.
- ✅ **Phase 20** — **TOTP-based 2FA** with QR-code setup, 10 one-shot
  backup codes (canonical form hashed, formatted XXXXX-XXXXX for display —
  the bug fix that made tests catch the type of mistake the feature exists
  to prevent), password-gated disable, full audit + rate-limit hooks.
- ✅ **Phase 21** — **public read-only share links**. Owner picks expiry,
  optionally bundles tailored artifacts, gets a URL. Anyone with the URL
  sees a read-only view without an account. View counter + audit row on
  every access so the owner sees who accessed when.
- ✅ **Phase 22** — **REST API** at `/v1/*` via FastAPI: `/me`,
  `/analyze`, `/applications`, `/cv`, `/analytics`, all bearer-token
  authenticated. Tokens (`jos_…`) are bcrypt-hashed with an indexed 8-char
  prefix for fast lookup. Cross-user reads return 404 (not 403) so the
  API doesn't leak which IDs exist. `python -m api.main` runs uvicorn;
  `/healthz` open without auth for liveness probes. **405 tests total** —
  31 new in Phase 22 including all auth + scoping cases via
  `fastapi.testclient.TestClient`.

### Phase 16–18 (shipped) — timelines, resumability, observability
- ✅ **Phase 16** — `utils/diff.inline_diff_html` word-level diff with HTML
  ins/del spans (XSS-safe — user content escaped before insertion).
  `services/timeline` builds per-application + cross-application swimlane
  data; UI renders both via Altair with palette-colored dots and connecting
  lines. Inline/Unified toggle on every diff surface.
- ✅ **Phase 17** — `services/checkpoint` lets the orchestrator skip
  already-completed stages on retry. When the salary stage 502s, the user
  re-submits and only the missing stages re-run (verified end-to-end).
  `services/reminders` + `worker/reminders` send a Telegram summary of
  applications that haven't moved within the user's threshold; per-app
  snooze + per-user threshold settings.
- ✅ **Phase 18** — observability stack:
  - **Structured JSON logging** (`utils/logging_setup.py`) with
    contextvar-based `request_id` that threads through nested calls.
    `LOG_FORMAT=json` switches to machine-readable; default is human-
    friendly. Configurable in `app.py`, `bot/main.py`, `worker/*`.
  - **Metrics registry** (`utils/metrics.py`) — in-process counters +
    histograms with snapshot dump. Bounded memory (1000 samples per
    histogram). CLI `python -m worker.metrics_dump [--json]`.
  - **Timing wrapper** (`utils/timing.timed_block`) emits one log line and
    one histogram observation per operation; auto-tags errors. Wired into
    the LLM client (`provider`, `model` tags) and every pipeline stage
    (`stage` tag), so error rate per provider and p95 stage latency are
    derivable from a snapshot.
  - **Audit log** (`db.models.AuditEvent` + `services/audit.py`,
    migration `f7cd19cbca6e`) records security-sensitive events:
    register / login (success+failure) / password change / reset
    request+complete / application delete / artifact delete / telegram
    bind+unbind. Captures the contextvar request_id so audit rows join
    cleanly to log lines. `audit.record` never raises into the caller.
  - **Per-user audit viewer** in the sidebar.
- ✅ 76 new tests across the three phases. Full suite **314/314 green**.

### Phase 14–15 (shipped) — diff views, PDF for master CV, Telegram linking + notifications
- ✅ **Phase 14** — `utils/diff.py` produces unified diffs that
  ``st.code(language="diff")`` syntax-highlights. The master CV revision
  history grew a `🔀 Diff against current` expander per entry. Per-application
  artifact list grew a `🆚 Compare two artifacts` toggle for side-by-side
  view with optional unified diff. Master CV also exports as PDF (reuses
  Phase 12's `markdown_to_pdf`).
- ✅ **Phase 15** — Telegram account linking + auto-notify:
  - `TelegramLink` + `TelegramLinkBindingToken` tables, migration
    `3ba1c52f7b93`. Binding tokens are bcrypt-hashed and 15-min one-shot
    (same security pattern as password reset). Single linked chat per user.
  - Bot grew `/bind <token>`, `/unbind`, `/me` commands. All run via
    `asyncio.to_thread` so DB work doesn't block the event loop.
  - When a stage event is added through the web UI, ``notify_stage_added``
    sends a glanceable Telegram message (emoji + label + title + company +
    date + truncated notes) to the linked chat, **best-effort** — never
    raises into the UI, silently no-ops if unlinked / token unset.
  - Outbound send hits the public Telegram HTTP API directly, so the web
    container doesn't need to share state with the running bot process.
  - Sidebar "📲 Link Telegram" panel issues tokens, shows the linked chat,
    and toggles per-user notification preference.
  - Caught a real test-infrastructure bug: tests that hop threads (the bot
    handlers via `asyncio.to_thread`) saw an empty `:memory:` SQLite
    database because each new connection gets a fresh one. Fixed by using
    `StaticPool` in `reset_engine_for_testing` so all threads share one
    connection. Every multi-threaded test now actually exercises the schema.
- ✅ 38 new unit tests (10 in Phase 14, 28 in Phase 15: binding token
  one-shot/expiry/rebind, hash-not-raw, chat lookup, unlink idempotency,
  notify preference, send_to_chat conditional/HTTP-error paths,
  notify_stage_added all branches incl. truncation, all 5 bot handlers,
  migration). Full suite **247/247 green**.

### Phase 11–13 (shipped) — closing the loop + onboarding speed-ups
- ✅ **Phase 11** — soft suggestions on flagged tailored artifacts
  (`services/suggestions.py`): each skill flag offers a one-click "Add to my
  master CV" that performs the edit through `apply_skill_addition` and
  re-runs the check in one go. Year / percentage / quantitative flags are
  deliberately NOT one-click (auto-appending those would invite the exact
  fabrication the checker exists to prevent). Master CV gains versioned
  revisions (`MasterCVRevision`): every content change snapshots the prior
  version with a reason tag; restore is itself reversible. Migration
  `a3b401ffdd58`.
- ✅ **Phase 12** — PDF export for tailored CVs / cover letters
  (`services/pdf_export.py`). Pure-Python via `fpdf2` (`[pdf]` extra); UI
  surfaces `.md` and `.pdf` download buttons in parallel. Em dashes / smart
  quotes / emoji map to ASCII so the built-in Helvetica font renders cleanly.
  Two real fpdf2 quirks caught during development and documented inline:
  `multi_cell(0,...)` leaves cursor at the right margin (so reset X every
  line) and mixing `write()` with `multi_cell()` breaks across versions
  (so inline `**bold**` is stripped — headings still bold).
- ✅ **Phase 13** — bulk import for projects + past applications
  (`services/bulk_import.py`). CSV path for structured data; LLM-parsed
  free-form path with the same "do not invent" constraint and untrusted
  wrapping as the tailoring prompts. Imports never auto-persist — always a
  preview-then-approve step. **Caught a real bug** during the e2e: importing
  a `rejected`/`offer`/`interviewing` application would silently downgrade
  to `applied` because the stage-event auto-sync overwrote the imported
  status. Fixed by materialising the *correct* stage events that match the
  imported status (e.g. `interviewing` → `applied` + `phone_screen`,
  `offer` → `applied` + `offer_received`). End-to-end verified: importing
  4 past applications immediately lights up the funnel, verdict→outcome
  correlation, and rejection-stage charts.
- ✅ Phase 11–13 add 47 unit tests; full suite **209/209 green**.

### Phase 10 (shipped) — deterministic constraint-violation detector + tone presets
- ✅ **Constraint checker** (`services/constraint_check.py`) — runs after every
  tailored generation. Extracts skill-shaped tokens, years, percentages, and
  quantitative claims from the output; flags anything not present in the union
  of master CV + project gallery + job context.
  - Single-word extraction with character-class boundaries so `C++` / `C#` /
    `Node.js` / `40%` actually match (the previous `\b` approach silently
    missed them).
  - Trailing sentence punctuation stripped so `Python.` matches `Python`.
  - Multi-word phrase matching deliberately dropped — it falsely flagged
    routine title text like `Staff ML` because the *phrase* wasn't in the
    master CV. Individual word matching catches genuine invented skills like
    `TensorFlow` or `Apache Spark` (both components flagged).
- ✅ **Auto-check on every generation** — `generate_tailored_cv` and
  `generate_cover_letter` run the check and persist the result in
  `artifact.meta.constraint_check`. The UI badges each artifact as ✅ clean or
  ⚠️ review-recommended and lists the specific terms / years / percentages
  that warrant a look.
- ✅ **Re-check button** — `recheck_artifact` re-runs against the *current*
  master CV + projects so previously-flagged items can clear after a CV
  update, without regenerating the artifact.
- ✅ **Cover-letter tone presets** — `COVER_LETTER_TONES = (professional, warm,
  direct, enthusiastic, concise)`, exposed as a per-application Streamlit
  selectbox that persists in session state.
- ✅ 162 unit tests total (22 new: token extraction across punctuation /
  percentages / quant-claim patterns, clean+flagged+job-context-whitelisting
  for the checker, serialization round-trip, auto-check-on-generation,
  re-check end-to-end including master-CV-update clearing, ownership
  isolation on recheck, tone-preset propagation into the prompt).

### Phase 9 (shipped) — master CV + project gallery + tailored artifacts
- ✅ **Master CV** (`db.models.MasterCV`, `services/master_cv.py`) — one per
  user, stored as raw text plus an optional LLM-derived structured projection
  (Summary / Skills / Experience / Education / Certifications). Tailoring reads
  the raw text directly, so a parse step can never drop a fact silently.
- ✅ **Project gallery** (`db.models.Project`, `services/projects.py`) — many
  per user, each with title / role / tech / summary / highlights / link. The
  ``projects_as_text`` renderer prepares them for inclusion in tailoring prompts.
- ✅ **Tailored CV & cover-letter generation** (`services/tailoring.py`) with
  hard "tailor, don't invent" enforcement:
  - A ``NO_FABRICATION_RULES`` block is included verbatim in every generation
    prompt (tests assert the critical phrases stay there).
  - Allowed: rephrase, reorder, select, emphasize, match terminology.
  - Forbidden: invent skills, employers, dates, degrees, quantitative claims,
    or projects the user doesn't have.
  - Adjacent-experience framing is explicitly permitted when a requirement
    isn't met — pretending is not.
  - User-supplied content (CV + projects) goes through the existing
    ``wrap_untrusted`` injection-hardening helper.
- ✅ **ApplicationArtifact** table (`db.models`) versioned per application —
  multiple drafts accumulate so the user can iterate without losing previous
  output. Newest-first listing; download as markdown.
- ✅ **UI**: new "📝 CV & Projects" top-level tab with Master CV + Project
  gallery sub-tabs (upload PDF/DOCX/TXT or paste, optional structured parse,
  two-step delete). Per-application "🎯 Tailored artifacts" section with
  generate buttons, in-place preview, download, and delete.
- ✅ Alembic migration ``83518f3daa22`` adds ``master_cvs``, ``projects``,
  ``application_artifacts``.
- ✅ 140 unit tests total (22 new: CV CRUD with structured-preservation,
  one-CV-per-user, parse persists, project CRUD + cross-user isolation,
  ``projects_as_text`` rendering, missing-CV guard on tailoring, **constraint
  text presence in both prompts**, history accumulation, artifact ownership,
  preview-mode non-persistence, migration applies cleanly).

### Phase 8 (shipped) — pipeline tracking + analytics
- ✅ **Application stage tracker** (`db.models.ApplicationStage`,
  `services/stages.py`) — every milestone (applied, recruiter / phone /
  technical screen, take-home, onsite, offer, accept, reject, withdraw,
  ghost) lives as its own dated row with optional notes and a structured
  ``extra`` payload (offer comp, verbatim feedback, rejection reason).
- ✅ **Auto status sync** — the legacy ``Application.status`` field stays
  consistent with the latest stage event automatically, so the existing
  filters and badges keep working. Deleting the latest stage reverts status.
- ✅ **Analytics dashboard** (`services/analytics.py`, new "📊 Analytics"
  tab) — derives funnel counts + stage-over-stage conversion rates,
  per-pair time-in-stage averages with sample sizes, verdict→outcome
  correlation table, rejection-stage histogram, applications-per-week
  volume. Zero-reach pipeline stages are skipped so the displayed funnel
  reflects real signal. Empty state for new users.
- ✅ **Per-application stages UI** — chronological timeline inside each
  application expander, one-click quick-action buttons for the common
  transitions, and a detailed add-stage form (date + notes +
  at_pipeline_stage for terminal events).
- ✅ Alembic migration ``c067abb9272c`` adds ``application_stages``.
- ✅ 118 unit tests total (18 new: stage CRUD + ownership isolation,
  auto status sync (including by-date ordering and delete-revert),
  funnel counts & conversion rates, time-in-stage with real per-app
  walks, verdict outcomes, rejection-stage distribution, empty state,
  migration applies cleanly).

### Phase 7 (shipped)
- ✅ **Rate limiting** (`services/rate_limit.py`) — sliding-window limiter
  with thread-safe in-memory backend (default) and Redis backend
  (auto-selected when `REDIS_URL` or `CELERY_BROKER_URL` is set). Pre-wired
  limiters: login (10/5min), register (5/hour), reset-request (5/hour),
  analysis (30/hour). Successful login clears the failure counter. All limits
  env-configurable via `RL_*_MAX` / `RL_*_WINDOW`.
- ✅ **Telegram bot uses the async queue** — when a Celery broker is
  configured the bot enqueues + polls the worker (off the event loop, so
  other commands stay responsive); falls back to in-thread execution when
  no queue is available. Timeout and failure-state reporting included.
- ✅ **UI/UX overhaul**:
  - **Required-field trap fixed** — Company/Title/Location are now optional;
    they auto-populate from extraction. Save still requires them, but
    extraction fills in for users who leave fields blank.
  - **Posting input split** into "🔗 From URL" / "📝 Paste text" tabs.
  - **Auth tabs collapsed** from 4 → 3 (Forgot + Reset merged into a single
    "Recover password" two-step flow).
  - **Two-step delete** in My Applications — first click arms, second
    confirms. No more one-click data loss.
  - **Search + status filter** on My Applications; user-friendly empty
    state for new accounts.
  - **Resume ATS visual upgrade** — big colored score banner, matched skills
    in green ✓, missing skills in red ✗, count metrics.
  - Sign-out moved to the bottom of the sidebar so it stops being the
    second-most-clickable button.
  - Optional Telegram-bot link in sidebar (`TELEGRAM_BOT_USERNAME`).
- ✅ 100 unit tests total (15 new for Phase 7).

### Phase 6 (shipped)
- ✅ **Browser scraper** (`tools/browser_scraper.py`) for resources whose API
  is paid/unavailable: headless Playwright rendering of JS job boards
  (LinkedIn-class) and Numbeo cost-of-living, wired as automatic fallbacks
  in `url_ingest` and `data_sources`. Optional `[browser]` extra, disabled
  unless `BROWSER_SCRAPER_ENABLED=1`. Parsers split from the browser so they
  test offline; price parser handles US (`1,234.56`) and EU (`1.234,56`)
  number formats.
- ✅ **Async job queue** (`worker/`, `services/analysis_runner.py`) — Celery +
  Redis (`[worker]` extra). `submit()` enqueues when a broker is configured,
  else runs in-process; the interactive UI stays synchronous for live
  progress. Import-cycle-safe app factory.
- ✅ **Email delivery** (`services/email.py`, `services/notifications.py`) —
  SMTP password-reset emails; best-effort (logs + returns False when
  unconfigured, never breaks the flow). Renders a full link when
  `APP_BASE_URL` is set, else the raw token.
- ✅ **Docker** — `Dockerfile` + `docker-compose.yml` bring up web + Celery
  worker + Redis + Postgres (Telegram bot behind a `bot` profile); app
  container runs `alembic upgrade head` before serving.
- ✅ 85 unit tests total (17 new: Numbeo/job HTML parsing, US/EU price
  parsing, browser fallback wiring, async-runner sync fallback, SMTP send +
  reset-email rendering).

### Validation note (sandbox networking)
This repo was built in a host-allowlisted sandbox where only `api.anthropic.com`
is reachable; every external data host returns a proxy `403 host_not_allowed`.
Connection code was verified to **attempt real requests and degrade gracefully**
(e.g. Adzuna correctly routes by country, then returns `None` on the 403);
**parsing** is verified against real-shaped HTML/JSON fixtures. A live data
round-trip requires running outside the allowlist (or adding these hosts to it).

### Honest Gaps (next)
- ❌ layoffs.fyi scraping is fragile (Airtable embed) — dataset-URL path
  preferred; no robust browser parser yet
- ❌ Bot/queue integration is available via `submit()` but the Telegram bot
  still runs analysis inline (fine at low volume)
- ❌ No rate limiting / abuse protection on auth or analysis endpoints

## 🎯 Roadmap: Production-Ready Features

### Phase 1: Real Data Integration
- [ ] **Company Financial Data**: Integrate with APIs like Alpha Vantage, Yahoo Finance, or SEC filings
- [ ] **Salary Benchmarking**: Connect to Glassdoor, PayScale, or Levels.fyi APIs
- [ ] **Cost of Living**: Integrate Numbeo, BestPlaces, or similar APIs
- [ ] **Company Reviews**: Access Glassdoor, Indeed, or Blind APIs
- [ ] **News & Layoffs**: Integrate news APIs and layoff tracking services

### Phase 2: Enhanced Analysis
- [ ] **CV Tailoring**: Auto-generate customized resumes based on job requirements
- [ ] **Cover Letter Generation**: Create personalized cover letters
- [ ] **Interview Preparation**: Generate potential interview questions and answers
- [ ] **Skills Gap Analysis**: Identify missing skills and suggest learning resources

### Phase 3: Application Tracking
- [ ] **Application Database**: Track where and when users applied
- [ ] **Status Management**: Monitor application progress (applied, interview, rejection, offer)
- [ ] **Follow-up Reminders**: Automated reminders for application follow-ups
- [ ] **Analytics Dashboard**: Personal job search analytics and insights
- [ ] **Document Management**: Store tailored CVs and cover letters per application

### Phase 4: Advanced Features  
- [ ] **Job Alert System**: Automated job matching and notifications
- [ ] **Network Analysis**: LinkedIn integration for connection insights
- [ ] **Market Trends**: Industry-specific hiring trends and forecasts
- [ ] **Negotiation Assistant**: Salary negotiation strategies and talking points

## 🛠️ Technology Stack

- **Frontend**: Streamlit
- **Backend**: Python with LangChain/LangGraph
- **AI Models**: Support for multiple LLMs (OpenAI, DeepSeek, Qwen)
- **Caching**: In-memory caching system
- **Architecture**: Agent-based orchestration pattern

## 🚀 Quick Start

1. **Clone and Setup**:
   ```bash
   # The project runs on Replit - click "Run" to start
   # or manually run:
   streamlit run app.py
   ```

2. **Environment Variables**:
   ```bash
   # Add your API keys to .env file:
   OPENAI_API_KEY=your_openai_key_here
   # Add other API keys as you integrate real data sources
   ```

3. **Usage**:
   - Select analysis model (Fast or Detailed)
   - Fill in basic job details
   - Paste the full job description
   - Click "Analyze Job" and wait for results

## 📁 Project Structure

```
├── agents/                 # AI agents for different analysis tasks
│   ├── job_analyzer.py     # Job posting analysis
│   ├── company_analyzer.py # Company research
│   ├── salary_analyzer.py  # Compensation analysis
│   └── report_generator.py # Final recommendations
├── tools/                  # External API integrations (currently mock)
│   ├── job_tools.py        # Job parsing tools
│   ├── company_tools.py    # Company data tools  
│   └── salary_tools.py     # Salary benchmark tools
├── utils/                  # Utility functions
│   ├── llm.py             # LLM interaction
│   └── cache.py           # Caching system
├── app.py                 # Main Streamlit application
└── README.md              # This file
```

## 🔒 Security & Best Practices

- ✅ No hardcoded API keys or secrets
- ✅ Environment variable usage for configuration
- ✅ Input validation and error handling
- ✅ Modular, maintainable code structure
- ✅ Comprehensive logging for debugging

## 🤝 Contributing

This project is ready for collaboration and open-source contributions:

1. **Current State**: Fork and improve the mock data implementations
2. **API Integration**: Help integrate real external APIs
3. **UI/UX**: Enhance the Streamlit interface
4. **Testing**: Add comprehensive test coverage
5. **Documentation**: Improve code documentation and user guides

## 📄 License

MIT License - feel free to use this project as a foundation for your own job analysis tools.

## 🔮 Vision

Transform job searching from a manual, time-consuming process into an AI-powered, data-driven experience that helps candidates:
- Make informed career decisions
- Stand out with tailored applications  
- Track and optimize their job search process
- Negotiate better compensation packages
- Build long-term career strategies

---

**Ready to contribute or integrate real APIs?** Check out the issues tab or reach out to discuss collaboration opportunities!
