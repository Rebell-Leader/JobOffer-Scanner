# Technical assessment: 2026-10-19

Scope: the whole repo at `main` @ `9862816`, plus the unmerged branch
`claude/founding-team-setup-b5t2ge` (`c1f9d6d`). The goal is to use the app for
a real job search, measure whether it helps, and only then sell it.

Where this document and the "Production-readiness roadmap" in `CLAUDE.md`
disagree, this document is current. Several items marked ✅ there were not
true at HEAD; they are listed below.

## TL;DR

- **The codebase is broad and well structured.** It is about 16.6k lines of
  app code across 3 channels, with 697 tests at baseline and real CI gates
  (ruff, mypy, bandit, coverage, Postgres migration round-trip).
- **The core value path had defects that silently misled the user.**
  - The fabrication check passed skills copied from the posting.
  - The ATS score under-counted real matches.
  - Reports were truncated at 2000 tokens, so the verdict block was cut off.
  - `.env` was never loaded, so the app ran in demo mode without saying so.
  - The whole analysis aborted when the posting didn't name the employer.
  - PDF export crashed the CV tab on "Ł", "→" or "●".
- **This pass fixes those** and a set of paid-launch blockers. Everything is
  on the working branch `fix/p0-launch-readiness`, uncommitted. See "Fixed in
  this pass".
- **Personal use:** ready to run locally (`./scripts/run_local.sh`). Treat the
  ATS score and the fabrication check as aids. Read every generated line
  before you send it.
- **Paid launch:** not ready yet. The remaining blockers are listed under
  "Remaining P0 before a paid launch". The biggest are: migrations on the
  live Replit deploy, OAuth, abuse controls, and Stripe state robustness.

## Branches

| Branch | State | Action |
|---|---|---|
| `main` @ 9862816 | Baseline: 697 tests pass, 7 live e2e tests skipped | — |
| `claude/elegant-knuth-aI7NB` | Merged via PR #3 | Can be deleted |
| `claude/founding-team-setup-b5t2ge` @ c1f9d6d | Never merged, no PR, CI never ran; it would have failed ruff and mypy | Its diff is applied in the working tree, with these fixes: unused import, `Optional[str].rstrip` type errors, `USE_ALEMBIC` on the scheduler and bot (a `create_all` vs `alembic` race), `pg_dump` missing from the image, the backups volume, and its incomplete bot quota fix |
| `fix/p0-launch-readiness` | The working branch: all changes below, uncommitted | Review, then commit and open a PR |

## Fixed in this pass

### Core analysis pipeline (P0 for personal use)

| Defect | Fix | Where |
|---|---|---|
| Nothing loads `.env` (no dotenv), so keys in `.env` were ignored and the app ran in demo mode | Local launcher that exports `.env`, runs migrations, binds to `127.0.0.1` (config.toml binds `0.0.0.0`, which exposes your CV on the LAN) | `scripts/run_local.sh` |
| `max_tokens=2000` on every call, with no stop-reason check: long reports lost the trailing `<verdict_json>`; gpt-5 / o-series reasoning could return empty text | Default raised to 16k. `max_tokens` / `length` / refusal / empty output now raise `LLMOutputError`, which is never retried | `utils/llm.py` |
| Default models were outdated. Current Claude models (Opus 5.5, Sonnet 5.5, Haiku 5.5) reject `temperature` with a 400, and the app always sent 0.4 | Defaults are `claude-haiku-5-5` / `claude-opus-5-5` and `gpt-5-mini` / `gpt-5`. `temperature` is sent only to models that accept it. Explicit timeouts. A single retry policy that skips 4xx errors other than 408/409/429. `ANTHROPIC_EFFORT` and `OPENAI_REASONING_EFFORT` knobs | `utils/llm.py` |
| No prices for the 5.5 models or gpt-5-mini/nano, so budgets used a $1/$3 placeholder (Opus 5.5 under-counted about 5×) | Pricing table updated | `services/usage.py` |
| Provider ping used a 10-token budget, so thinking/reasoning models always "failed" in the System test | 1024-token budget | `utils/llm.py` |
| A posting without a named employer (recruiter or agency listings) aborted the entire analysis | Company stage degrades with an explicit "not identified" note; salary, resume and report still run | `agents/company_analyzer.py` |
| Typing a company-name hint skipped LLM extraction, so location, seniority, skills and responsibilities vanished | Always extract; non-empty manual fields override | `agents/job_analyzer.py` |
| Salary stage ignored the posting's stated compensation | The posted range is passed in and compared against the market range | `tools/salary_tools.py`, `agents/salary_analyzer.py` |
| Salary prompt said "heuristic" even with real Adzuna data | Prompt matches the actual data source; the crude table is treated as a weak prior | same |
| Salary title matching depended on dict order ("Senior AI Engineer" → generic engineer; "Trainer" → AI) | Most specific title wins, matched on word boundaries | same |
| Report showed "Company: None" | Missing fields render as "Not specified" | `agents/report_generator.py` |

### CV / tailoring (P0 for personal use)

| Defect | Fix |
|---|---|
| **The no-fabrication check whitelisted the posting's required skills.** A CV claiming "Kubernetes, Terraform" (from the posting, not your CV) showed ✅ "No new facts" | A CV may only use facts from the master CV and projects. A cover letter may also echo the target company, title and location. Flagged terms lifted from the posting are labelled "copied from the job posting" (`from_posting`) |
| Title inflation passed: senior, staff, principal, lead, manager and director were ignored | Seniority words must trace to your CV. Two tests that encoded the bug were corrected, e.g. "Staff ML Engineer" for a Senior was expected to be clean |
| `$` amounts were never caught (`\b` before `$`); counts such as "team of 8" and "7+ years" passed | Fixed regex; count claims added |
| Noise: sentence-initial words ("Thank", "Having") were flagged, and one-click "add 'thank' to my skills" polluted the master CV | Prose filter on generated output only. `python/django` split; PDF hyphenation joined |
| Tailoring prompt never saw responsibilities or the posting itself | Responsibilities, tools, education and a 6k-character excerpt of the posting are added; a new rule forbids inventing facts about the employer |
| Model preamble or code fences could land in the PDF | Output cleaned |
| **ATS score:** "Containerization (Docker, Kubernetes)" was reduced to "containerization"; no aliases; "Go" matched "go hiking" | Alternatives inside parentheses, a qualifier stripper, an alias map (K8s, Postgres, Golang, …), capitalized-only matching for ambiguous names, generic-tail handling ("RESTful APIs"). Scored against technical skills **and** tools |
| "Non-text glyphs detected" warning on every resume (`"" in text` is always true) | Real private-use-area glyph check |
| Corrupt or encrypted PDF/DOCX crashed the page; DOCX tables and headers (contact details) were dropped; scanned PDFs said "CV cannot be empty" | Clear `ValueError` messages; tables, headers and footers extracted |
| **PDF export crashed the CV tab** on non-Latin-1 text (Ł, Cyrillic, →, ●, €); the branch's fix printed "?ukasz" | Bundled DejaVu Unicode font (`assets/fonts/`, license included), with the branch's error wrapping kept. CJK and emoji glyphs are still dropped (logged, no crash) |

### UI and tracking

| Defect | Fix |
|---|---|
| A failed URL fetch silently analyzed whatever was in the Paste tab (an older posting, or the sample) and saved it under the wrong company | Stops with a clear message |
| Stage results rendered as JSON dumps (`st.write(dict)`) | Rendered as markdown |
| Status dropdown wrote only `Application.status`, but analytics and reminders read stages: a "rejected" application stayed Active and kept being nagged | A status change records the matching stage once. Save-with-status does too, and so do undated bulk-import rows |
| Save failed when the posting didn't name the employer, and fixing it meant re-running the paid analysis | Editable Company / Title fields on the Save form |
| CSV import: "Rejected" imported as "applied"; header case and Excel BOM broke detection | Case-insensitive statuses plus synonyms; normalized headers |
| URL ingestion discarded JSON-LD (the complete posting on Greenhouse, Lever, Ashby and Workday pages) and picked the first "content" div, often a cookie banner | JSON-LD `JobPosting` first (title, company, location, salary, description); otherwise the longest content container |
| "Start 2FA setup" crashed: a nested expander the smoke test's static scan missed | Checkbox instead of the nested expander; the guard now also catches `st.sidebar.expander` |
| Tailoring and parsing provider errors surfaced as raw tracebacks | Caught and shown as errors |

### Paid-launch blockers fixed

| Defect | Fix |
|---|---|
| Telegram `/analyze` ran unlimited, unmetered analyses for anyone; the branch fix was bypassable with `/unbind` | Requires a linked chat (`TELEGRAM_ALLOW_UNLINKED_ANALYZE=1` for private bots), checked before any URL fetch |
| **Stripe webhook crashed on every real event** (stripe 15 `Event` has no `.get`), so paying users were never upgraded | Normalized to a dict, plus a test through the real `construct_event` |
| CV parsing, bulk import and tailoring called the LLM with no budget check, on the detailed model, with spend logged to no user | One `metered_llm(user_id, model)` gate: spend budget, plan allowance, model clamp, usage attribution |
| SSRF: redirects to internal hosts were followed; the body cap read the whole body first; CGNAT range allowed | Every redirect hop re-validated; streamed capped read with a deadline; `not is_global` |
| HTML injection: posting-derived skills were rendered with `unsafe_allow_html` | Escaped |
| Sign-out left API tokens, the Stripe portal link and 2FA backup codes in the tab's session | `session_state.clear()` |
| System test tab was open to every user, and a failed Telegram probe printed the bot token | Operator-only (`ADMIN_EMAILS`, or everyone when billing is off); secrets redacted |
| Compose: worker used the factory function as the `-A` app; the image lacked worker/billing/research extras; no `pg_dump`; no restart policies | Fixed; trixie base image (pg_dump 17 ≥ server 16); backups volume. **Not build-verified here.** |
| No `pool_pre_ping` (Replit Postgres drops idle connections); `postgres://` URLs rejected | Fixed in the app and in Alembic |

## Remaining P0 before a paid launch

1. **The live Replit deploy runs no migrations.** `.replit` sets `USE_ALEMBIC=1`
   (so `create_all` is skipped) but runs plain `streamlit run app.py`. Any
   migration not applied by hand is missing in prod (billing, waitlist, …).
   I did **not** change `.replit`: if that DB was built with `create_all`,
   `alembic upgrade head` fails and the deploy would stop booting. Procedure:
   1. Back up first (`scripts/backup_db.sh`).
   2. `SELECT version_num FROM alembic_version;` in the Replit DB shell.
   3. If the table is missing, compare the schema with
      `tests/test_phase29_schema_drift.py`'s approach. Then
      `alembic stamp <revision matching prod>` (`head` only if the schema
      fully matches) and `alembic upgrade head`.
   4. Set `run = ["sh", "-c", "alembic upgrade head && streamlit run app.py"]`.
2. **OAuth (Google/GitHub) doesn't work and bypasses 2FA.** The state nonce
   lives in `st.session_state`, but the callback arrives in a new session;
   OAuth login also skips the TOTP step. Leave the OAuth env vars unset until
   the state lives server-side and OAuth logins go through 2FA.
3. **Abuse economics.** The register limiter is keyed by email and email
   verification is only a banner, so unlimited free accounts each get the
   free-tier allowance. Before charging: an IP rate limit or captcha, and
   `REQUIRE_EMAIL_VERIFICATION=1` whenever billing is on.
4. **Stripe state mirroring.** No event-ID idempotency or ordering. A late
   `checkout.session.completed` can re-activate a cancelled plan.
   `payment_status` is ignored. `subscription.updated` before the row exists
   is dropped with a 200. Fix by retrieving the Subscription and mirroring
   its status, and by storing processed event IDs.
5. **Sessions don't survive a refresh** (no cookie); every reload logs the
   user out. That is acceptable for you, not for customers.
6. **Webhook SSRF:** URLs are validated only at registration (DNS can be
   re-pointed later), and unresolvable hosts are allowed. Re-check at
   delivery and pin the resolved IP, or use an egress proxy.
7. **PII posture:** CVs and applications are personal data. Set
   `SECRETS_ENCRYPTION_KEY`, define retention, confirm your LLM provider's
   data terms, and publish them.

## P1: quality and scale

- **`app.py` (2.7k lines) runs every tab on every click.** That means account
  export, PDF render, and per-application timeline, stage and artifact
  queries. At about 150 applications, that's hundreds of queries per click.
  Split into `st.navigation` pages (`ui/pages/*`); show applications as a
  table with a single detail panel.
- **Analytics math:** the funnel is per-stage rather than cumulative (it can
  exceed 100%); there is no response rate; funnel bars sort alphabetically;
  weekly volume uses the save date.
- **Quota is charged before the run** and on failure. A failed company or
  salary branch hides the job details already computed. Show partial results
  and charge on success.
- **Second-order prompt injection:** posting-derived fields go into the
  report prompt unfenced. Markdown images in reports load external URLs.
- **The fabrication check is still lexical.** It misses synonyms, spelled-out
  numbers and per-role date shifts. The real fix is a structured,
  user-confirmed CV schema (roles, dates, bullets) with fact-level checks.
- **Auth hardening:** the login limiter is keyed by email only (lockout and
  spraying); bcrypt raises on passwords over 72 bytes; `chat_id` is not
  unique; `/bind` bcrypt-scans every token; a password change doesn't revoke
  API tokens; `/metrics` uses a non-constant-time token comparison.
- **Tracker model:** no job URL, applied date, contact or next-action date;
  saving twice creates duplicates.
- **Channels:** the extension sends `save:false`, so a paid analysis isn't
  tracked. The REST API has no stages or tailoring endpoints. None of the API,
  bot or extension runs on the Replit deploy.
- `use_container_width` is still used at 5 sites (CLAUDE.md says it was
  removed). It works on the pinned Streamlit 1.42 but breaks on upgrade.
- `deploy/Caddyfile.fullstack` uses `handle_path /app/*`, which strips the
  prefix Streamlit's `baseUrlPath` needs. Use `handle`.
- **CI gaps:** no Docker build or `docker compose config`, no
  `uv lock --check`, an incomplete Postgres table list, `[worker]` and
  `[billing]` extras not installed in the test job.

## Is it effective for a job search? A pilot protocol

**Use it for:** triage (verdict, red flags, missing-employer warnings), a
salary sanity check (estimates unless Adzuna keys are set), first drafts of
tailored CVs and cover letters, and the tracker.

**Don't trust blindly:**
- the ATS score as an absolute number (it's keyword overlap, now realistic);
- salary figures without Adzuna;
- company research on small or private companies (DuckDuckGo snippets);
- the "no new facts" badge. It now catches the common embellishments, but
  read the output anyway.

**2–4 week pilot (this is what decides whether it's sellable):**
1. **Baseline:** import your past applications (CSV: company_name, job_title,
   status, applied_on) to get your historical response rate.
2. **Per posting:** write down your own go/no-go *before* reading the verdict.
   Track how often you agree, and when you disagree, who was right.
3. **Per application:** tailored versions are stored per application, so you
   can compare response rate for tailored vs. master-CV applications.
4. **Per generated document:** count the edits needed, and count the
   fabrication flags that were true vs. false positives.
5. **Cost:** the `llm_usage` table ledgers real tokens and estimated USD per
   analysis. Rough estimate for about 9 calls per analysis:
   - **Fast** (Haiku 5.5): about $0.01;
   - **Detailed** (Opus 5.5): about $0.30–0.50;
   - Sonnet 5.5 (`ANTHROPIC_DETAILED_MODEL=claude-sonnet-5-5`): about half of
     Opus. Check these against the ledger.
6. **Go/no-go for selling:** a better response rate (or the same rate with
   much less time per application), verdict agreement you'd pay for, and
   per-user cost well under your planned price.

**Biggest product gaps for an active job seeker:**
- No job discovery. Adzuna is already wired up for salaries; reuse it for
  search, saved searches and alerts, and watch target companies'
  Greenhouse/Lever/Ashby boards.
- Follow-up dates and email reminders.
- A kanban view.
- Interview prep from posting + CV.
- Offer comparison.
- DOCX export.
- Inbox integration to log replies automatically.

## Verification done

- **Tests:** baseline 697 tests OK; the working tree passes the full suite
  (**723 tests**, 7 live e2e skipped). New regression tests cover each bug
  fixed above (fabrication holes, ATS realism, JSON-LD, SSRF redirect,
  Unicode PDF, the Stripe real-SDK path, status → stage, CSV import, the bot
  link requirement).
- **Static checks:** ruff, mypy (85 files) and bandit clean; 15 JS tests pass.
- **Local launch:** fresh SQLite, all 19 migrations applied, health endpoint
  OK.
- **Not verified:**
  - real LLM calls (no key was available in this session; run one analysis
    to confirm);
  - the Docker image build;
  - the Replit deploy.
