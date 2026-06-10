# Go-to-market, analytics & lean-ops plan

Objective: **validate demand for ≤ $40/month fixed cost**, with a hard
decision gate at 60 days. This is a validation plan, not a growth plan — the
only question it answers is "will strangers pay for this?"

---

## 1. Positioning (the one sentence everywhere)

> **Stop applying to bad jobs.** Paste a posting, get a Green/Yellow/Red
> verdict — company stability, real salary benchmark, ATS fit — before you
> waste an evening on the application.

Secondary hook (unique, defensible, headline-able):
> The only AI resume tool that **provably can't lie** — every generated CV
> passes a deterministic fabrication check against your real experience.

Anti-positioning: we are NOT another auto-apply volume tool and NOT another
keyword stuffer. We're the *filter* that runs before all of that.

## 2. Audience & channels (zero-CAC only)

Primary: actively-searching tech workers (familiar with ATS pain, comfortable
pasting a posting, reachable online). Layoff waves continuously replenish it.

| Channel | Play | Cost |
|---|---|---|
| **Product Hunt** | Launch with the verdict-card demo GIF; the no-fabrication check is the differentiated story | 0 |
| **Hacker News** | "Show HN: I built a job-posting analyzer that tells you if the job is worth applying to" — lead with the deterministic-check engineering story | 0 |
| **Reddit** (r/jobsearchhacks, r/resumes, r/cscareerquestions per sub rules) | Genuinely useful verdict breakdowns of public postings; tool mention in context, never spam | 0 |
| **SEO content** (3–5 posts on the landing domain) | "Is this job offer worth it — checklist", "AI resume tools that don't hallucinate", "How to spot a dying company from the job ad" | 0 |
| **Chrome Web Store** | The extension is its own discovery surface ("job posting analyzer") | $5 one-time dev fee |
| **Demo mode as funnel** | The app already runs keyless with labelled sample data — let visitors try the full flow before signup | 0 |

Sequencing: week 1 soft-launch to 2 subreddits (message test) → week 2
Product Hunt + Show HN same week → weeks 3+ SEO posts + extension listing.

## 3. Product analytics (decide with data, spend nothing)

Two layers, both privacy-respecting (no ad trackers — it's also a privacy-
policy commitment):

1. **Marketing site:** Plausible (~$9/mo) or self-hosted Umami ($0 on the
   same box) — page views, referrers, CTA click-through. Cookieless.
2. **Product events:** we already own the data — no third-party needed:
   - `usage_events` = activation & engagement (analyses per user per week)
   - `subscriptions` = conversion & churn
   - `audit_events` = signups, logins, feature touches
   - `llm_usage` = COGS per user/tier (gross-margin truth)

   Ship a tiny `worker/funnel_report.py` (CLI, weekly cron → console/email)
   computing: visitors→signups (from Plausible), signups→first-analysis %,
   first→fifth-analysis % (the aha-metric), free→paid %, week-2 retention,
   COGS/user. ~100 lines against existing tables.

**North-star metric:** users who run ≥3 analyses in week 1 (proxy for "the
verdict is useful"). **Funnel targets for the 60-day gate:**

| Stage | Target |
|---|---|
| Visitor → signup | ≥ 5% |
| Signup → first analysis | ≥ 60% |
| First → ≥3 analyses (week 1) | ≥ 30% |
| Free → paid | ≥ 3% |
| **Gate: paying customers at day 60** | **≥ 10 → invest · 3–9 → iterate once · <3 → fold to portfolio piece** |

## 4. Run-as-cheap-as-possible architecture

Target ≤ $40/mo all-in at validation traffic (≤ ~200 signups):

| Component | Choice | $/mo |
|---|---|---|
| App (Streamlit + API in one box) | Single small VPS (Hetzner CX22 / Fly.io shared-1x) running docker-compose | ~$5–8 |
| Postgres | Same box via compose (backups already scripted + offsite to B2/R2 ~$1) | 0 |
| Redis | Same box (only needed for multi-instance — skip even that at first: in-process fallbacks are built in) | 0 |
| Edge/TLS | Caddy on the box (config in `deploy/`) + Cloudflare free in front | 0 |
| Landing | Cloudflare Pages (static, `landing/`) | 0 |
| LLM | **Free tier on haiku-class only** (~$0.01–0.04/analysis); completion cache ON | usage: ~$5–20 |
| Analytics | self-hosted Umami / Plausible | 0–9 |
| Email | Existing SMTP via Resend/Postmark free tier | 0 |
| Domain | .com | ~$1 |
| **Total fixed** | | **~$15–40** |

Cost discipline that's already built-in: per-tier LLM budgets fail-closed,
`LLM_CACHE_COMPLETIONS=1`, checkpoint resume (no double-spend on retries),
demo mode costs $0 per anonymous visitor. The single biggest lever:
**free tier = fast model only** (already enforced by `clamp_model`).

Deliberately deferred until revenue: managed Postgres, multi-instance +
Redis, Celery worker box, Browserbase subscription (paste-flow works without
it), paid monitoring (the `/metrics` endpoint + uptime-kuma on the same box
suffice).

## 5. Launch checklist (maps to existing repo pieces)

- [ ] Domain + Cloudflare; landing on Pages; app behind Caddy (`deploy/README.md`)
- [ ] Stripe live setup + dry run (`docs/STRIPE_SETUP.md` §3 all green)
- [ ] `/terms` + `/privacy` reviewed, [OPERATOR] placeholders filled
- [ ] `REQUIRE_EMAIL_VERIFICATION=1`, SSRF guard on (default), edge rate limit
- [ ] Backups cron + one restore drill (`deploy/RUNBOOK.md`)
- [ ] Analytics live; funnel report cron
- [ ] Demo GIF recorded (paste → verdict card) for PH/HN/Reddit
- [ ] 3 SEO posts drafted; PH listing + Show HN text written
- [ ] Support address wired (hello@) + 24h response habit

## 6. Risks to the plan itself

- **Churn-by-success** is structural (users cancel when hired): push annual
  on the pricing page, measure LTV honestly from month 2, treat cohort
  replenishment (layoff news cycles) as the real engine.
- **Marketplace ToS** (LinkedIn et al.): lead with paste + extension (user's
  own session); hosted-browser ingest stays a Pro nicety, never the headline.
- **One-founder bus factor:** the RUNBOOK + backups + boring single-box infra
  are the mitigation — keep it boring until money says otherwise.
