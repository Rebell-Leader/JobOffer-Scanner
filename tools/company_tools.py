"""Company-research tools.

Phase 0 honesty rules:
  * We do NOT invent employee reviews. The previous ``get_company_reviews``
    asked the LLM to fabricate reviews "based on typical patterns". That
    presents hallucination as evidence and is removed. A clearly-labelled
    *culture-signals* prompt replaces it; the user sees that it's an
    LLM-derived inference, not real review data.
  * Layoffs / news stubs return EXPLICIT "not yet integrated" sentinels so
    the downstream LLM never treats them as confirmed facts.
  * Errors are no longer swallowed into fake success — they raise.
"""

from __future__ import annotations

import logging

try:
    from langchain_core.tools import Tool  # langchain >= 0.3
except ImportError:  # pragma: no cover - older langchain layout
    from langchain.tools import Tool

from utils.cache import cache
from utils.llm import get_completion
from utils.security import sanitize_untrusted, wrap_untrusted
from tools.data_sources import fetch_company_news, fetch_layoffs

logger = logging.getLogger(__name__)


_LAYOFFS_PLACEHOLDER = (
    "LAYOFFS DATA NOT AVAILABLE: no layoffs dataset configured "
    "(set LAYOFFS_DATASET_URL). Do not assume the absence of layoffs."
)
_NEWS_PLACEHOLDER = (
    "RECENT NEWS NOT AVAILABLE: no news source configured (set NEWS_API_KEY). "
    "Do not invent news items; reason only from the job posting itself."
)


def check_layoffs_data(company_name: str) -> str:
    """Real layoffs lookup when configured, else an explicit sentinel."""
    return fetch_layoffs(company_name) or _LAYOFFS_PLACEHOLDER


def get_company_news(company_name: str) -> str:
    """Real news lookup when configured, else an explicit sentinel."""
    return fetch_company_news(company_name) or _NEWS_PLACEHOLDER


def analyze_company_stability(company_name: str, model: str = "detailed") -> str:
    """Synthesize a stability assessment from whatever signals we have.

    With external data sources still pending, the LLM is told explicitly that
    layoffs/news are unknown and instructed to label inferences as inferences.
    """
    company_name = sanitize_untrusted(company_name, max_chars=200)
    cache_key = f"stability_{company_name}_{model}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    # Fetched news/layoffs are external & untrusted — wrap as inert data.
    layoffs_info = wrap_untrusted(check_layoffs_data(company_name), "layoff_signal")
    news_summary = wrap_untrusted(get_company_news(company_name), "news_signal")

    prompt = f"""
Assess the stability and growth prospects of **{company_name}** for a job seeker.

External signals available right now:

Layoff signal:
{layoffs_info}

News signal:
{news_summary}

Constraints:
- Where a signal is marked "NOT AVAILABLE", do NOT assume good or bad news.
  Say the signal is unavailable and reason about general industry context only.
- Clearly label any specific figures (revenue, headcount, funding) as
  "ESTIMATE" unless they come from the signals above.
- Do not fabricate news headlines, executive names, or financial events.

Produce a concise markdown report with these sections:
1. Current stability (what we can and cannot say)
2. Market position (industry-level reasoning only if company-specific data is missing)
3. Growth trajectory outlook
4. Risk factors for the job seeker
5. Overall stability score (1-10) with a one-sentence justification
"""
    response = get_completion(prompt, model)
    cache.set(cache_key, response)
    return response


def analyze_culture_signals(company_name: str, model: str = "detailed") -> str:
    """LLM-derived inference about culture, explicitly labelled as inference.

    Replaces the old ``get_company_reviews`` which fabricated reviews. This
    version makes the LLM reason about *what to look for* and known
    industry-level patterns, without inventing quotes or ratings.
    """
    company_name = sanitize_untrusted(company_name, max_chars=200)
    cache_key = f"culture_{company_name}_{model}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    prompt = f"""
You have NO access to Glassdoor, Indeed, Blind, or similar review platforms.
Do NOT invent employee quotes, ratings, or specific incidents.

For **{company_name}**, produce a markdown briefing titled
"Culture Signals (Inferred — Not From Reviews)" with these sections:
1. Public-knowledge context — only widely-known, verifiable facts about the
   company / its sector. If you are unsure of a fact, omit it.
2. Questions the candidate should research themselves (with concrete sources
   to check: Glassdoor URL pattern, LinkedIn employee posts, news search terms).
3. Red-flag patterns to watch for in interviews, given the company's sector.
4. Green-flag patterns to watch for, given the company's sector.

Every paragraph that is an inference must start with "Inference:".
"""
    response = get_completion(prompt, model)
    cache.set(cache_key, response)
    return response


company_tools = [
    Tool(
        name="analyze_company_stability",
        func=analyze_company_stability,
        description="Analyzes company stability and growth prospects",
    ),
    Tool(
        name="analyze_culture_signals",
        func=analyze_culture_signals,
        description="Inferred culture briefing (not real reviews)",
    ),
]
