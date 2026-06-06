
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

### Honest Gaps (next)
- ❌ No async job queue for large workloads (still in-process)
- ❌ JS-heavy job boards (LinkedIn / Indeed / Glassdoor) need a real headless
  scraper; the generic URL ingest is best-effort only
- ❌ Email delivery for reset tokens not bundled — by design (pluggable)

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
