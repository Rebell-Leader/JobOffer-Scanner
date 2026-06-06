"""Salary & cost-of-living tools.

Phase 0 honesty: the underlying numeric tables here are heuristic estimates,
not market data. We:
  * Prefix outputs as "ESTIMATE" so the downstream report cannot pass them
    off as benchmarked figures.
  * Stop catching LLM errors and returning a fabricated success report.
  * Keep the heuristics so the pipeline produces something useful while
    Phase 1 wires up a real salary/COL data source.
"""

from __future__ import annotations

import json
import logging
import re

try:
    from langchain_core.tools import Tool  # langchain >= 0.3
except ImportError:  # pragma: no cover - older langchain layout
    from langchain.tools import Tool

from tools.data_sources import fetch_salary_benchmark
from utils.cache import cache
from utils.llm import get_completion

logger = logging.getLogger(__name__)


def estimate_salary_range(job_title, location, experience_level, model="detailed"):
    print(
        f"Estimating salary for - Title: {job_title}, Location: {location}, "
        f"Experience: {experience_level}"
    )

    cache_key = f"salary_{job_title}_{location}_{experience_level}_{model}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    real_benchmark = fetch_salary_benchmark(job_title, location)
    heuristic_salary = _get_heuristic_salary_data(job_title, location, experience_level)
    heuristic_col = _get_heuristic_cost_of_living(location)

    if real_benchmark:
        salary_block = (
            f"Adzuna market data (REAL, treat as primary signal):\n{real_benchmark}\n\n"
            f"Heuristic estimate (internal model, for cross-check only — "
            f"prefer the Adzuna figures when they disagree):\n{heuristic_salary}"
        )
        primary_note = (
            "Primary salary figures are from live Adzuna postings. The heuristic "
            "below is a cross-check only — use Adzuna when they conflict."
        )
    else:
        salary_block = (
            f"Heuristic salary estimate (internal model, NOT market data — "
            f"label as ESTIMATE):\n{heuristic_salary}"
        )
        primary_note = (
            "No live salary feed configured (set ADZUNA_APP_ID + ADZUNA_APP_KEY). "
            "Figures below are heuristic estimates only — label them as ESTIMATE."
        )

    prompt = f"""
Produce a markdown salary analysis for:
- Job Title: {job_title}
- Location: {location}
- Experience Level: {experience_level or "not specified"}

{primary_note}

Salary signals:
{salary_block}

Heuristic cost-of-living estimate (internal model, not Numbeo data):
{heuristic_col}

In your report:
1. State up front that figures are heuristic estimates, not benchmarks.
2. Give a salary range in USD and, where it makes sense, local currency.
3. Explain the main factors driving the range (role, location, seniority).
4. Provide a cost-of-living adjusted assessment.
5. Suggest 3 concrete negotiation moves.

Use clear markdown headings.
"""
    response = get_completion(prompt, model)
    cache.set(cache_key, response)
    return response


def analyze_compensation_package(salary_details, model="detailed"):
    """Analyze the full compensation package including benefits."""
    prompt = f"""
Analyze the following compensation package: base, bonus, equity, benefits,
retirement, health, and perks.

Package details:
{salary_details}

Provide:
1. Total monetary value (label any figure you estimate as ESTIMATE).
2. Comparison to industry standards (qualitative — no fabricated benchmarks).
3. Strengths of the package.
4. Weaknesses or missing components.
5. Three concrete negotiation suggestions.

Format as a markdown report with clear section headings.
"""
    return get_completion(prompt, model)


# ---------------------------------------------------------------------------
# Heuristic estimators (internal, NOT market data)
# ---------------------------------------------------------------------------

def _get_heuristic_salary_data(job_title, location, experience_level):
    years = _parse_experience_level(experience_level)
    level = _map_experience_to_level(years)

    data = {
        "_disclaimer": "ESTIMATE from internal multiplier table, not market data.",
        "job_title": job_title,
        "location": location,
        "level": level,
        "salary_range": {
            "low": _simulated_salary(job_title, location, years, "low"),
            "median": _simulated_salary(job_title, location, years, "median"),
            "high": _simulated_salary(job_title, location, years, "high"),
        },
    }
    data["total_compensation"] = {
        "low": data["salary_range"]["low"] * 1.2,
        "median": data["salary_range"]["median"] * 1.3,
        "high": data["salary_range"]["high"] * 1.4,
    }
    return json.dumps(data, indent=2)


def _get_heuristic_cost_of_living(location):
    parts = (location or "").split(",")
    city = parts[0].strip() if parts else location
    cost_index = _simulated_cost_index(city)
    rent_index = _simulated_rent_index(city)

    return json.dumps(
        {
            "_disclaimer": "ESTIMATE from internal index table, not Numbeo.",
            "city": city,
            "cost_of_living_index": cost_index,
            "rent_index": rent_index,
        },
        indent=2,
    )


def _parse_experience_level(experience_level):
    if not experience_level:
        return 3

    match = re.search(r"(\d+)(?:\+)?\s*(?:year|yr)", experience_level.lower())
    if match:
        return int(match.group(1))

    text = experience_level.lower()
    if "entry" in text or "junior" in text:
        return 0
    if "mid" in text:
        return 3
    if "senior" in text:
        return 5
    if "lead" in text or "manager" in text:
        return 7
    if "director" in text:
        return 10
    if "executive" in text or "vp" in text:
        return 15
    return 3


def _map_experience_to_level(years):
    if years < 1:
        return "IC1"
    if years < 3:
        return "IC2"
    if years < 6:
        return "IC3"
    if years < 9:
        return "IC4"
    if years < 12:
        return "IC5"
    return "IC6+"


_BASE_SALARIES = {
    "software engineer": 80000, "data scientist": 85000, "product manager": 90000,
    "designer": 70000, "marketing": 65000, "sales": 60000, "analyst": 65000,
    "manager": 100000, "director": 130000, "engineer": 75000, "developer": 80000,
    "ml": 90000, "ai": 95000,
}

_LOCATION_MULTIPLIERS = {
    "san francisco": 1.5, "new york": 1.4, "seattle": 1.3, "boston": 1.25,
    "los angeles": 1.3, "chicago": 1.2, "austin": 1.15, "remote": 1.0,
    "london": 1.2, "berlin": 0.9, "paris": 0.9, "toronto": 0.85,
    "sydney": 0.95, "singapore": 1.1, "tokyo": 1.0, "zurich": 1.4, "prague": 0.7,
}

_PERCENTILE_MULTS = {"low": 0.8, "median": 1.0, "high": 1.2}


def _simulated_salary(job_title, location, years, percentile):
    base = 70000
    if job_title:
        for title, salary in _BASE_SALARIES.items():
            if title in job_title.lower():
                base = salary
                break

    loc_mult = 1.0
    if location:
        for loc, mult in _LOCATION_MULTIPLIERS.items():
            if loc in location.lower():
                loc_mult = mult
                break

    exp_mult = 1.0 + (years * 0.06)
    return round(base * loc_mult * exp_mult * _PERCENTILE_MULTS[percentile], -3)


_CITY_COST_INDEX = {
    "new york": 100, "san francisco": 95, "london": 83, "tokyo": 86,
    "paris": 80, "berlin": 65, "singapore": 83, "sydney": 80, "toronto": 73,
    "chicago": 70, "seattle": 85, "austin": 65, "boston": 82,
    "los angeles": 77, "zurich": 123, "geneva": 108, "dublin": 75, "prague": 50,
}

_CITY_RENT_INDEX = {
    "new york": 100, "san francisco": 108, "london": 87, "tokyo": 60,
    "paris": 70, "berlin": 50, "singapore": 78, "sydney": 75, "toronto": 68,
    "chicago": 60, "seattle": 80, "austin": 55, "boston": 78,
    "los angeles": 85, "zurich": 90, "geneva": 85, "dublin": 80, "prague": 40,
}


def _simulated_cost_index(city):
    if city:
        for known, index in _CITY_COST_INDEX.items():
            if known in city.lower():
                return index
    return 65


def _simulated_rent_index(city):
    if city:
        for known, index in _CITY_RENT_INDEX.items():
            if known in city.lower():
                return index
    return 50


salary_tools = [
    Tool(
        name="estimate_salary_range",
        func=estimate_salary_range,
        description="Estimates salary range for a given job (heuristic, not benchmark)",
    ),
    Tool(
        name="analyze_compensation_package",
        func=analyze_compensation_package,
        description="Analyzes full compensation package",
    ),
]
