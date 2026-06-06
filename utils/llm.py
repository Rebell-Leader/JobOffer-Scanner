"""Provider-agnostic LLM client.

Supports Anthropic Claude, OpenAI, and any OpenAI-compatible endpoint
(e.g. Featherless). The active provider is auto-detected from whichever API
key is present, and can be forced with the ``LLM_PROVIDER`` env var.

Key behavioural guarantees (the Phase-0 fix):
  * When a provider key IS set, ``get_completion`` makes a REAL API call with
    retries. On persistent failure it RAISES — it never silently substitutes
    fabricated data for a real request. Failures surface to the user.
  * When NO provider key is set, the app runs in honest "demo mode" and returns
    clearly-bounded sample data so the pipeline can be showcased. The UI badge
    reflects this.
"""

from __future__ import annotations

import os
import re
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------

# Each provider exposes a "fast" and a "detailed" model tier. Callers pass a
# logical tier ("fast"/"detailed") and we resolve it for the active provider,
# so model selection in the UI stays meaningful regardless of provider.
TIER_MODELS = {
    "anthropic": {
        "fast": os.getenv("ANTHROPIC_FAST_MODEL", "claude-haiku-4-5-20251001"),
        "detailed": os.getenv("ANTHROPIC_DETAILED_MODEL", "claude-sonnet-4-6"),
    },
    "openai": {
        "fast": os.getenv("OPENAI_FAST_MODEL", "gpt-4o-mini"),
        "detailed": os.getenv("OPENAI_DETAILED_MODEL", "gpt-4o"),
    },
    "featherless": {
        "fast": os.getenv("FEATHERLESS_FAST_MODEL", "Qwen/Qwen3-32B"),
        "detailed": os.getenv("FEATHERLESS_DETAILED_MODEL", "deepseek-ai/DeepSeek-R1-0528"),
    },
}

# Provider -> (env var holding the API key, model-id prefixes that belong to it)
_PROVIDER_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "featherless": "FEATHERLESS_API_KEY",
}

# Order in which we auto-detect a provider when LLM_PROVIDER is unset.
_AUTODETECT_ORDER = ["anthropic", "openai", "featherless"]

_FEATHERLESS_BASE_URL = "https://api.featherless.ai/v1"

DEFAULT_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.4"))
DEFAULT_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2000"))
_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))


def get_active_provider() -> Optional[str]:
    """Return the active provider name, or ``None`` if no key is configured.

    Respects an explicit ``LLM_PROVIDER`` override; otherwise picks the first
    provider (in autodetect order) whose API key is present.
    """
    forced = os.getenv("LLM_PROVIDER", "").strip().lower()
    if forced:
        if forced not in _PROVIDER_KEYS:
            raise ValueError(
                f"LLM_PROVIDER={forced!r} is not supported. "
                f"Choose one of: {', '.join(_PROVIDER_KEYS)}."
            )
        return forced if os.getenv(_PROVIDER_KEYS[forced]) else None

    for provider in _AUTODETECT_ORDER:
        if os.getenv(_PROVIDER_KEYS[provider]):
            return provider
    return None


def is_demo_mode() -> bool:
    """True when no provider key is configured (sample data will be used)."""
    return get_active_provider() is None


def resolve_model(provider: str, requested: Optional[str]) -> str:
    """Map a requested tier or explicit model id to a concrete model id.

    Accepts logical tiers ("fast"/"detailed"), an explicit model id, or None.
    Explicit ids that plainly belong to a different provider are discarded in
    favour of the provider's "detailed" tier so legacy callers don't send, say,
    a DeepSeek id to Anthropic.
    """
    tiers = TIER_MODELS[provider]
    if not requested:
        return tiers["detailed"]

    req = requested.strip()
    if req in tiers:
        return tiers[req]

    # Explicit model id — keep it only if it plausibly matches this provider.
    if provider == "anthropic" and not req.startswith("claude"):
        return tiers["detailed"]
    if provider == "openai" and not (req.startswith("gpt") or req.startswith("o")):
        return tiers["detailed"]
    return req


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_completion(
    prompt: str,
    model: Optional[str] = None,
    system: Optional[str] = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """Get a completion from the active LLM provider.

    ``model`` may be a logical tier ("fast"/"detailed") or an explicit model id.
    In demo mode (no key configured) this returns bounded sample data. With a
    key configured it performs a real, retried API call and raises on failure.
    """
    provider = get_active_provider()

    if provider is None:
        print("[llm] Demo mode (no provider key set) — returning sample data.")
        return generate_sample_response(prompt)

    resolved_model = resolve_model(provider, model)
    system_prompt = system or _DEFAULT_SYSTEM_PROMPT

    # Local import keeps utils/timing -> utils/metrics -> utils/llm cycle-free
    # for the (uncommon) callers that import the LLM client transitively.
    from utils.timing import timed_block

    last_error: Optional[Exception] = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with timed_block(
                "llm.request",
                tags={"provider": provider, "model": resolved_model},
            ):
                if provider == "anthropic":
                    text = _complete_anthropic(
                        prompt, resolved_model, system_prompt, temperature, max_tokens
                    )
                else:  # openai or featherless share the OpenAI-compatible client
                    text = _complete_openai_compatible(
                        provider, prompt, resolved_model, system_prompt,
                        temperature, max_tokens,
                    )
            return _strip_reasoning_tokens(text)
        except Exception as exc:  # noqa: BLE001 - retried/surfaced below
            last_error = exc
            print(f"[llm] {provider} call failed (attempt {attempt}/{_MAX_RETRIES}): {exc}")
            if attempt < _MAX_RETRIES:
                time.sleep(2 ** (attempt - 1))  # 1s, 2s, 4s ...

    # Honest failure: do NOT fabricate data for a real request.
    raise RuntimeError(
        f"LLM request to provider '{provider}' (model '{resolved_model}') failed "
        f"after {_MAX_RETRIES} attempts: {last_error}"
    )


_DEFAULT_SYSTEM_PROMPT = (
    "You are a meticulous job-market analyst. Respond exactly in the format the "
    "user requests. When asked for JSON, return only valid JSON with no prose or "
    "markdown fences. Base your analysis strictly on the information provided; "
    "clearly label any figure you estimate as an estimate rather than a fact."
)


# ---------------------------------------------------------------------------
# Provider implementations (SDKs imported lazily so an unused provider's
# package being absent never breaks the active path).
# ---------------------------------------------------------------------------

def _complete_anthropic(prompt, model, system, temperature, max_tokens) -> str:
    from anthropic import Anthropic  # lazy import

    # The SDK reads ANTHROPIC_API_KEY and (optionally) ANTHROPIC_BASE_URL itself.
    client = Anthropic()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [block.text for block in resp.content if getattr(block, "type", None) == "text"]
    return "".join(parts).strip()


def _complete_openai_compatible(
    provider, prompt, model, system, temperature, max_tokens
) -> str:
    from openai import OpenAI  # lazy import

    if provider == "featherless":
        client = OpenAI(
            base_url=_FEATHERLESS_BASE_URL,
            api_key=os.getenv("FEATHERLESS_API_KEY"),
            timeout=60.0,
        )
    else:  # openai
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=60.0)

    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _strip_reasoning_tokens(text: str) -> str:
    """Remove <think>...</think> reasoning blocks emitted by some models."""
    if not text:
        return text
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


# ---------------------------------------------------------------------------
# Demo-mode sample data (used ONLY when no provider key is configured).
# Kept structurally valid so the end-to-end pipeline renders for showcasing.
# ---------------------------------------------------------------------------

def generate_sample_response(prompt: str) -> str:
    """Return bounded sample data for demo mode. Not used when a key is set."""
    prompt_lower = prompt.lower()

    if "extract key information" in prompt_lower or "extract key" in prompt_lower:
        return """{
            "company_name": "Sample Corp (DEMO)",
            "job_title": "Senior AI Engineer",
            "location": "San Francisco, CA (Remote Friendly)",
            "experience_level": "5+ years in ML/AI",
            "required_skills": ["Python", "TensorFlow", "PyTorch", "AWS", "Docker", "Kubernetes"],
            "compensation": "$140k - $180k + equity",
            "job_type": "Full-time",
            "responsibilities": [
                "Develop and deploy machine learning models",
                "Build scalable AI infrastructure",
                "Collaborate with cross-functional teams",
                "Mentor junior engineers"
            ]
        }"""

    if "analyze the requirements" in prompt_lower:
        return """{
            "technical_skills": [
                "Python (Expert level required)",
                "Machine Learning Frameworks (TensorFlow/PyTorch)",
                "Cloud Platforms (AWS/GCP preferred)",
                "Containerization (Docker, Kubernetes)",
                "Version Control (Git)",
                "API Development (REST/GraphQL)"
            ],
            "soft_skills": [
                "Strong communication skills",
                "Team collaboration",
                "Problem-solving mindset",
                "Mentoring abilities"
            ],
            "education": "Bachelor's or Master's in Computer Science, AI, or related field",
            "experience": "5+ years in AI/ML with production deployment experience",
            "unique_requirements": [
                "Experience with real-time ML systems",
                "Previous startup experience preferred"
            ],
            "tools_and_technologies": [
                "Jupyter Notebooks", "MLflow", "Airflow", "Redis", "PostgreSQL"
            ]
        }"""

    if "company" in prompt_lower and ("financial" in prompt_lower or "stability" in prompt_lower):
        return """## Company Stability Analysis (DEMO DATA)

> **Demo mode** — sample data, not a real assessment. Configure an API key for live analysis.

**Overall Assessment: POSITIVE (sample)**

### Financial Health
- Revenue growth and funding figures shown here are illustrative placeholders.

### Risk Factors
- This section would summarise real layoffs, news, and market signals in live mode.

**Stability score: 7/10 (sample)**"""

    if "salary" in prompt_lower or "compensation" in prompt_lower:
        return """## Compensation Analysis (DEMO DATA)

> **Demo mode** — sample data, not a real benchmark. Configure an API key for live analysis.

- **Base Salary Range:** $140k - $180k (illustrative)
- **Total Compensation:** $160k - $220k including equity (illustrative)
- **Cost-of-living adjustment:** placeholder figures only

**Overall rating:** sample output for demonstration."""

    if "comprehensive" in prompt_lower or "recommendation" in prompt_lower:
        return """# Job Opportunity Analysis Report (DEMO DATA)

> **Demo mode** — this report uses sample data and does not reflect the posting you submitted.
> Configure an LLM API key to generate a real, posting-specific analysis.

## Executive Summary
This is illustrative output showing the report structure end to end.

## Final Recommendation
**Recommended (sample verdict).** In live mode this verdict is derived from the
real job, company, and compensation analysis.

**Confidence Level: demo**"""

    return (
        "**Demo mode** — no LLM provider key is configured, so this is sample "
        "output rather than a real analysis. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, "
        "or FEATHERLESS_API_KEY to enable live analysis."
    )


# Backwards-compatibility alias for any callers/tests referencing the old name.
generate_mock_response = generate_sample_response


def get_llm_client():
    """Deprecated: kept for backwards compatibility.

    The provider client is now created per-request inside ``get_completion`` so
    that provider/model selection is resolved at call time. Returns None in
    demo mode.
    """
    return None
