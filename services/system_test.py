"""End-to-end system health check.

Runs a lightweight probe of every configured integration and returns a list of
:class:`TestResult` objects so the UI can render a clear pass/fail table.

The LLM probes make one *real* API call per provider (a 10-token "Reply OK"
prompt) so actual connectivity is verified without wasting significant tokens.
All other probes are connection-level only (no writes, no side-effects).
"""

from __future__ import annotations

import os
import smtplib
import time
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    name: str
    ok: bool
    message: str
    duration_ms: Optional[int] = None
    skipped: bool = False

    @property
    def icon(self) -> str:
        if self.skipped:
            return "⬜"
        return "✅" if self.ok else "❌"

    @property
    def duration_str(self) -> str:
        if self.duration_ms is None:
            return ""
        return f"{self.duration_ms} ms"


# ---------------------------------------------------------------------------
# Individual probes
# ---------------------------------------------------------------------------

def _probe_database() -> TestResult:
    t0 = time.time()
    try:
        from sqlalchemy import text as sa_text
        from db.session import get_session
        session = get_session()
        session.execute(sa_text("SELECT 1"))
        session.close()
        ms = int((time.time() - t0) * 1000)
        return TestResult("Database (PostgreSQL)", ok=True, message="Connected", duration_ms=ms)
    except Exception as exc:
        ms = int((time.time() - t0) * 1000)
        return TestResult("Database (PostgreSQL)", ok=False, message=str(exc), duration_ms=ms)


def _probe_llm(provider: str) -> TestResult:
    from utils.llm import _PROVIDER_KEYS, TIER_MODELS, ping_provider
    key_env = _PROVIDER_KEYS.get(provider, "")
    if not os.getenv(key_env):
        return TestResult(
            f"LLM · {provider}",
            ok=False,
            message="Not configured (no API key)",
            skipped=True,
        )
    model = TIER_MODELS[provider]["fast"]
    t0 = time.time()
    ok, msg = ping_provider(provider)
    ms = int((time.time() - t0) * 1000)
    return TestResult(f"LLM · {provider} [{model}]", ok=ok, message=msg, duration_ms=ms)


def _probe_email() -> TestResult:
    from services.email import email_configured
    if not email_configured():
        return TestResult("Email (SMTP)", ok=False, message="Not configured", skipped=True)
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    use_tls = os.getenv("SMTP_USE_TLS", "1") == "1"
    t0 = time.time()
    try:
        with smtplib.SMTP(host, port, timeout=8) as server:
            if use_tls:
                server.starttls()
            server.login(username, password)
        ms = int((time.time() - t0) * 1000)
        return TestResult("Email (SMTP)", ok=True, message=f"Auth OK — {host}:{port}", duration_ms=ms)
    except Exception as exc:
        ms = int((time.time() - t0) * 1000)
        return TestResult("Email (SMTP)", ok=False, message=str(exc), duration_ms=ms)


def _probe_telegram() -> TestResult:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return TestResult("Telegram bot", ok=False, message="Not configured", skipped=True)
    t0 = time.time()
    try:
        import requests
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=6,
        )
        resp.raise_for_status()
        data = resp.json()
        username = data.get("result", {}).get("username", "?")
        ms = int((time.time() - t0) * 1000)
        return TestResult("Telegram bot", ok=True, message=f"@{username}", duration_ms=ms)
    except Exception as exc:
        ms = int((time.time() - t0) * 1000)
        return TestResult("Telegram bot", ok=False, message=str(exc), duration_ms=ms)


def _probe_url_ingest() -> TestResult:
    t0 = time.time()
    try:
        from tools.url_ingest import fetch_job_posting
        text = fetch_job_posting("https://example.com")
        ms = int((time.time() - t0) * 1000)
        return TestResult("URL ingest", ok=True, message=f"Fetched {len(text)} chars from example.com", duration_ms=ms)
    except Exception as exc:
        ms = int((time.time() - t0) * 1000)
        return TestResult("URL ingest", ok=False, message=str(exc), duration_ms=ms)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_system_test() -> List[TestResult]:
    """Run all probes and return results in display order."""
    results: List[TestResult] = []

    results.append(_probe_database())

    for provider in ("openai", "anthropic", "featherless"):
        results.append(_probe_llm(provider))

    results.append(_probe_email())
    results.append(_probe_telegram())
    results.append(_probe_url_ingest())

    return results


# ---------------------------------------------------------------------------
# Pre-baked mock analysis for the demo display pane
# ---------------------------------------------------------------------------

MOCK_ANALYSIS: dict = {
    "job_details": {
        "extracted_details": {
            "company_name": "Acme Technologies (demo)",
            "job_title": "Senior Software Engineer",
            "location": "Remote, United States",
            "experience_level": "5+ years",
            "required_skills": ["Python", "React", "PostgreSQL", "Docker", "AWS"],
            "compensation": "$140 000 – $180 000 + equity",
            "job_type": "Full-time",
            "responsibilities": [
                "Design and build scalable backend services",
                "Collaborate with product and design on new features",
                "Mentor junior engineers and contribute to hiring",
                "Own reliability and observability of core systems",
            ],
        },
        "requirements_analysis": {
            "technical_skills": [
                "Python (expert — 5 yrs+ production)",
                "React / TypeScript (intermediate)",
                "PostgreSQL & Redis",
                "Docker / Kubernetes",
                "AWS (EC2, RDS, S3, Lambda)",
            ],
            "soft_skills": ["Strong communicator", "High ownership", "Remote-first mindset"],
            "education": "BS/MS Computer Science or equivalent experience",
            "experience": "5+ years shipping production software at scale",
            "unique_requirements": ["Open-source contributions a plus", "Startup experience preferred"],
            "tools_and_technologies": ["GitHub Actions", "Terraform", "Datadog", "PagerDuty"],
        },
    },
    "company_analysis": {
        "stability_analysis": (
            "## Stability (demo)\n\n"
            "**Score: 7 / 10**\n\n"
            "Acme Technologies raised a $60 M Series B in Q1 and has 18 months runway. "
            "No recent layoffs. Headcount grew 40 % YoY according to LinkedIn signals. "
            "Sector (developer tooling) remains resilient despite broader tech headwinds.\n\n"
            "**Risk factors:** pre-IPO equity illiquidity; CEO transition in 2023 (new CEO now 1 yr in)."
        ),
        "culture_signals": (
            "## Culture signals (demo)\n\n"
            "Public engineering blog updated monthly — signals active technical community. "
            "Glassdoor composite 4.2 / 5 (78 reviews). Top themes: autonomy, good tooling, "
            "async-friendly. Concerns: fast pace, occasional priority shifts.\n\n"
            "**Green flags in JD:** explicit async-first, no on-call rotation for this role, "
            "documented promotion criteria.\n\n"
            "**Red flags to probe:** 'startup pace' and 'wear many hats' in JD text."
        ),
    },
    "salary_analysis": {
        "estimated_range": (
            "## Compensation (demo)\n\n"
            "**Posted range:** $140 000 – $180 000 base + equity\n\n"
            "| Percentile | Base | Total comp (incl. equity) |\n"
            "|---|---|---|\n"
            "| p25 | $138 000 | $155 000 |\n"
            "| p50 | $162 000 | $195 000 |\n"
            "| p75 | $185 000 | $240 000 |\n\n"
            "Market data: Levels.fyi / Glassdoor composite for Senior SWE, Remote US, 2024.\n\n"
            "**CoL note:** posted range is remote-agnostic; SF / NYC candidates may find this "
            "~10 % below local norms. For LCOL locations it is above market.\n\n"
            "**Negotiation moves:** anchor at $175 000 base; push for refresher cliff at 18 months; "
            "ask for sign-on if equity vest start is delayed."
        ),
    },
    "final_report": (
        "# Job Analysis — Senior Software Engineer @ Acme Technologies (demo)\n\n"
        "## Verdict: **Apply** · Confidence 8 / 10\n\n"
        "### Why Apply\n"
        "- Compensation is at / above market for remote Senior SWE\n"
        "- Company is well-funded with positive headcount trajectory\n"
        "- Role aligns well with a strong Python + cloud background\n"
        "- Async-first culture reduces sync meeting overhead\n\n"
        "### Watch-outs\n"
        "- Probe 'startup pace' during interviews — get specifics on incident cadence\n"
        "- Equity is Series B preferred; understand liquidation preferences\n"
        "- CEO transition: ask about strategic continuity in first-round screen\n\n"
        "### Suggested next steps\n"
        "1. Apply within the week — role is 14 days old\n"
        "2. Customise CV: highlight async/distributed experience\n"
        "3. Prepare a system-design example (high-throughput Python service)"
    ),
    "verdict": {
        # Canonical verdict shape used everywhere else in the app
        # (utils/verdict.py + render_result): verdict / light / reasons /
        # confidence. Keeping the mock on the same schema means the System
        # Test tab renders the verdict badge identically to a real analysis.
        "verdict": "Recommended",
        "light": "green",
        "confidence": 8,
        "source": "structured",
        "reasons": [
            "Compensation at / above market",
            "Well-funded (Series B, 18-month runway)",
            "Async-first, remote-friendly culture",
        ],
        "cautions": [
            "Probe 'startup pace' specifics",
            "Understand equity liquidation preferences",
        ],
    },
    "error": None,
}
