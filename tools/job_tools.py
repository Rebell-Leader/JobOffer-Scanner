"""Job-posting extraction tools.

Errors from the LLM call propagate (no silent fabricated success). JSON parse
failures DO fall back to a regex-based extraction, since malformed JSON is a
recoverable model issue rather than a system failure.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

try:
    from langchain_core.tools import Tool  # langchain >= 0.3
except ImportError:  # pragma: no cover - older langchain layout
    from langchain.tools import Tool

from utils.cache import cache
from utils.llm import get_completion, is_demo_mode
from utils.security import wrap_untrusted

logger = logging.getLogger(__name__)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # ```json ... ``` or ``` ... ```
        body = text.split("```", 2)
        if len(body) >= 2:
            inner = body[1]
            if inner.startswith("json"):
                inner = inner[4:]
            return inner.strip().rstrip("`").strip()
    return text


def _regex_extract(job_posting: str) -> Dict[str, Any]:
    """Last-ditch extraction when the LLM returns unparseable JSON."""
    lines = job_posting.splitlines()
    out = {
        "company_name": "Unknown",
        "job_title": "Unknown",
        "location": "Unknown",
        "experience_level": "Not specified",
        "required_skills": [],
        "compensation": "Not specified",
        "job_type": "Not specified",
        "responsibilities": [],
    }
    for line in lines:
        if "Company:" in line and out["company_name"] == "Unknown":
            out["company_name"] = line.split("Company:", 1)[1].strip()
        elif ("Title:" in line or "Position:" in line) and out["job_title"] == "Unknown":
            out["job_title"] = line.split(":", 1)[1].strip()
        elif "Location:" in line and out["location"] == "Unknown":
            out["location"] = line.split("Location:", 1)[1].strip()
    return out


def _coerce_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def extract_job_details(job_posting: str, model: str = "detailed") -> dict:
    """Extract key fields from a free-text job posting as a dict."""
    cache_key = f"job_details_{hash(job_posting)}_{model}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    prompt = f"""
Extract key information from this job posting and return STRICT JSON.

Job posting:
{wrap_untrusted(job_posting, "job_posting")}

Return JSON with exactly these keys (use null if unknown, never invent):
{{
  "company_name": "...",
  "job_title": "...",
  "location": "...",
  "experience_level": "...",
  "required_skills": ["..."],
  "compensation": "...",
  "job_type": "Full-time|Part-time|Contract|Internship|Unknown",
  "responsibilities": ["..."]
}}

Rules:
- "company_name" must match the posting text exactly. If absent, use null.
- Return ONLY JSON. No prose, no markdown fences.
"""
    response = get_completion(prompt, model)
    response = _strip_code_fence(response)

    try:
        parsed = json.loads(response)
    except json.JSONDecodeError as exc:
        logger.warning("LLM returned non-JSON for job details (%s); falling back to regex.", exc)
        parsed = _regex_extract(job_posting)

    # Backfill company name from a "Company:" header if the model missed it.
    if not parsed.get("company_name"):
        first_line = job_posting.splitlines()[0] if job_posting else ""
        if "Company:" in first_line:
            parsed["company_name"] = first_line.split("Company:", 1)[1].strip()

    parsed["required_skills"] = _coerce_list(parsed.get("required_skills"))
    parsed["responsibilities"] = _coerce_list(parsed.get("responsibilities"))

    cache.set(cache_key, parsed)
    return parsed


def analyze_requirements(job_posting: str, model: str = "detailed") -> dict:
    """Structured requirements analysis from a job posting."""
    cache_key = f"requirements_{hash(job_posting)}_{model}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    prompt = f"""
Analyze the requirements in this job posting and return STRICT JSON only:

{wrap_untrusted(job_posting, "job_posting")}

Return:
{{
  "technical_skills": ["skill (proficiency)"],
  "soft_skills": ["..."],
  "education": "...",
  "experience": "...",
  "unique_requirements": ["..."],
  "tools_and_technologies": ["..."]
}}

Rules:
- Only include items actually present in the posting. Do not invent.
- Return ONLY JSON. No prose, no markdown fences.
"""
    response = _strip_code_fence(get_completion(prompt, model))
    try:
        parsed = json.loads(response)
    except json.JSONDecodeError as exc:
        # With a real provider key set, non-JSON is a genuine call failure —
        # surface it rather than returning an empty analysis that looks
        # successful (the "raise, never fabricate" convention). The empty
        # skeleton is only a defensive path for demo mode (which returns valid
        # JSON anyway, so this is belt-and-suspenders).
        if not is_demo_mode():
            raise ValueError(
                f"Requirements analysis returned non-JSON from the provider: {exc}"
            ) from exc
        logger.warning("LLM returned non-JSON for requirements (%s); using empty fallback.", exc)
        parsed = {
            "technical_skills": [],
            "soft_skills": [],
            "education": "Not specified",
            "experience": "Not specified",
            "unique_requirements": [],
            "tools_and_technologies": [],
        }

    for key in ("technical_skills", "soft_skills", "unique_requirements", "tools_and_technologies"):
        parsed[key] = _coerce_list(parsed.get(key))

    cache.set(cache_key, parsed)
    return parsed


job_tools = [
    Tool(
        name="extract_job_details",
        func=extract_job_details,
        description="Extracts key details from a job posting",
    ),
    Tool(
        name="analyze_requirements",
        func=analyze_requirements,
        description="Analyzes job requirements and provides insights",
    ),
]
