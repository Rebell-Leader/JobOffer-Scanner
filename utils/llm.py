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

import logging
import os
import re
import time
from typing import Callable, Dict, List, Optional, Tuple

from utils.env import env_bool, env_float, env_int

logger = logging.getLogger(__name__)

# Usage type passed to observers: provider's token-usage dict, or None.
UsageDict = Optional[dict]
UsageRecorder = Callable[[str, str, UsageDict], None]

# Observers notified after each REAL completion. ``services.usage`` registers
# its ledger here (observer pattern) so this module stays a leaf — utils.llm
# must never import the services layer. Registration is idempotent and happens
# when ``services.usage`` is imported (guaranteed early via services/__init__).
_usage_recorders: List[UsageRecorder] = []


def register_usage_recorder(fn: UsageRecorder) -> None:
    """Register a callback invoked as ``fn(provider, model, usage)`` per call."""
    if fn not in _usage_recorders:
        _usage_recorders.append(fn)

# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------

# Each provider exposes a "fast" and a "detailed" model tier. Callers pass a
# logical tier ("fast"/"detailed") and we resolve it for the active provider,
# so model selection in the UI stays meaningful regardless of provider.
TIER_MODELS = {
    "anthropic": {
        "fast": os.getenv("ANTHROPIC_FAST_MODEL", "claude-haiku-5-5"),
        "detailed": os.getenv("ANTHROPIC_DETAILED_MODEL", "claude-opus-5-5"),
    },
    "openai": {
        "fast": os.getenv("OPENAI_FAST_MODEL", "gpt-5-mini"),
        "detailed": os.getenv("OPENAI_DETAILED_MODEL", "gpt-5"),
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

DEFAULT_TEMPERATURE = env_float("LLM_TEMPERATURE", 0.4)
# Output budget per call. Current Claude models think by default and gpt-5 /
# o-series models reason, and those tokens count against this cap — 2000 used
# to truncate full reports (dropping the trailing verdict block) and could
# leave reasoning models with an empty answer. 16k stays under SDK HTTP
# timeouts for non-streaming calls; you only pay for tokens actually produced.
DEFAULT_MAX_TOKENS = env_int("LLM_MAX_TOKENS", 16000)
_MAX_RETRIES = env_int("LLM_MAX_RETRIES", 3)
_TIMEOUT_SECONDS = env_float("LLM_TIMEOUT_SECONDS", 300.0)

# Claude models before Opus 4.7 accept sampling params; newer ones (Opus 4.7+,
# Sonnet 5+, Haiku 5.5, Fable) reject a non-default temperature with a 400.
_ANTHROPIC_SAMPLING_MODELS = re.compile(
    r"^claude-(?:3|(?:opus|sonnet|haiku)-4-(?:[0-6](?:-|$)|\d{8}))"
)


class LLMOutputError(RuntimeError):
    """The provider answered, but the output is unusable (truncated, refused,
    or empty). Deterministic for the same request, so it is never retried."""


def _record_usage(provider: str, model: str, usage: Optional[dict]) -> None:
    """Notify registered usage observers about a completed call.

    Best-effort: an observer failure is logged and never raised into the
    caller. No-op when nothing is registered.
    """
    for recorder in _usage_recorders:
        try:
            recorder(provider, model, usage)
        except Exception as exc:  # noqa: BLE001 - accounting is best-effort
            logger.warning("usage accounting skipped: %s", exc)


def _completion_cache_key(
    provider: str, model: str, system: str, temperature: float, max_tokens: int, prompt: str
) -> Optional[str]:
    """A stable cache key for an identical call, or None when caching is off."""
    if not env_bool("LLM_CACHE_COMPLETIONS", False):
        return None
    import hashlib

    payload = f"{provider}|{model}|{temperature}|{max_tokens}|{system}|{prompt}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"llm:completion:{digest}"


def _cache_get(key: str) -> Optional[str]:
    try:
        from utils.cache import cache

        value = cache.get(key)
        return value if isinstance(value, str) else None
    except Exception:  # noqa: BLE001 - cache must never break the call
        return None


def _cache_set(key: str, value: str) -> None:
    try:
        from utils.cache import cache

        cache.set(key, value)
    except Exception:  # noqa: BLE001
        pass


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
        logger.info("Demo mode (no provider key set) — returning sample data.")
        return generate_sample_response(prompt)

    resolved_model = resolve_model(provider, model)
    system_prompt = system or _DEFAULT_SYSTEM_PROMPT

    # Opt-in completion cache: identical (provider, model, system, params,
    # prompt) calls return the cached text for free — no tokens, no cost, no
    # ledger entry. Off by default (LLM_CACHE_COMPLETIONS=1 to enable) since
    # most prompts are unique; valuable for repeated/identical analyses.
    cache_key = _completion_cache_key(
        provider, resolved_model, system_prompt, temperature, max_tokens, prompt
    )
    if cache_key is not None:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

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
                    text, usage = _complete_anthropic(
                        prompt, resolved_model, system_prompt, temperature, max_tokens
                    )
                else:  # openai or featherless share the OpenAI-compatible client
                    text, usage = _complete_openai_compatible(
                        provider, prompt, resolved_model, system_prompt,
                        temperature, max_tokens,
                    )
            _record_usage(provider, resolved_model, usage)
            result = _strip_reasoning_tokens(text)
            if cache_key is not None:
                _cache_set(cache_key, result)
            return result
        except Exception as exc:  # noqa: BLE001 - retried/surfaced below
            last_error = exc
            logger.warning(
                "%s call failed (attempt %d/%d): %s", provider, attempt, _MAX_RETRIES, exc
            )
            if not _is_retryable(exc):
                break
            if attempt < _MAX_RETRIES:
                time.sleep(2 ** attempt)  # 2s, 4s, 8s ...

    if isinstance(last_error, LLMOutputError):
        raise last_error

    # Honest failure: do NOT fabricate data for a real request.
    raise RuntimeError(
        f"LLM request to provider '{provider}' (model '{resolved_model}') failed: "
        f"{last_error}"
    )


def _is_retryable(exc: Exception) -> bool:
    """Transient failures (network, timeouts, 408/409/429, 5xx) are worth a
    retry; a bad request, bad key, unknown model, or unusable output is not."""
    if isinstance(exc, LLMOutputError):
        return False
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 400 <= status < 500:
        return status in (408, 409, 429)
    return True


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

def _complete_anthropic(
    prompt: str, model: str, system: str, temperature: float, max_tokens: int,
) -> Tuple[str, UsageDict]:
    from anthropic import Anthropic  # lazy import

    # The SDK reads ANTHROPIC_API_KEY and (optionally) ANTHROPIC_BASE_URL itself.
    # Retries are owned by get_completion's loop, so the SDK's are disabled.
    client = Anthropic(timeout=_TIMEOUT_SECONDS, max_retries=0)
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    if _ANTHROPIC_SAMPLING_MODELS.match(model):
        kwargs["temperature"] = temperature
    effort = os.getenv("ANTHROPIC_EFFORT", "").strip().lower()
    if effort:
        kwargs["output_config"] = {"effort": effort}
    resp = _anthropic_create(client, kwargs)
    text = _anthropic_text(resp)
    usage = _anthropic_usage(resp)
    stop_reason = getattr(resp, "stop_reason", None)
    if stop_reason in ("max_tokens", "refusal") or not text:
        _record_usage("anthropic", model, usage)  # tokens were still billed
        raise LLMOutputError(_unusable_output_message(model, stop_reason, max_tokens))
    return text, usage


# Server-side refusal fallback: a request a safety classifier declines is
# re-run on Anthropic's recommended fallback model inside the same call.
# Haiku 5.5 has no fallback, so it is never sent there.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FALLBACK_MODEL_PREFIXES = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")
_fallbacks_rejected = False  # set once if this account/endpoint refuses the beta


def _anthropic_create(client, kwargs: dict):
    """``messages.create`` with refusal fallbacks where the model supports them.

    If the endpoint rejects the beta (e.g. a proxy via ANTHROPIC_BASE_URL),
    fall back to a plain request and stop asking for the rest of the process,
    so the opt-in can never take the app down. ANTHROPIC_REFUSAL_FALLBACK=0
    turns it off.
    """
    global _fallbacks_rejected
    use = (
        not _fallbacks_rejected
        and env_bool("ANTHROPIC_REFUSAL_FALLBACK", True)
        and kwargs["model"].startswith(_FALLBACK_MODEL_PREFIXES)
    )
    if not use:
        return client.messages.create(**kwargs)
    try:
        return client.beta.messages.create(
            **kwargs, betas=[_FALLBACK_BETA], extra_body={"fallbacks": "default"}
        )
    except Exception as exc:  # noqa: BLE001 - narrowed below
        status = getattr(exc, "status_code", None)
        if status == 400 and "fallback" in str(exc).lower():
            logger.warning("Refusal fallbacks unavailable here (%s); continuing without.", exc)
            _fallbacks_rejected = True
            return client.messages.create(**kwargs)
        raise


def _anthropic_text(resp) -> str:
    return "".join(
        getattr(block, "text", "") for block in resp.content
        if getattr(block, "type", None) == "text"
    ).strip()


def _anthropic_usage(resp) -> UsageDict:
    u = getattr(resp, "usage", None)
    if u is None:
        return None
    inp = getattr(u, "input_tokens", 0) or 0
    out = getattr(u, "output_tokens", 0) or 0
    usage: dict = {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out}
    server = getattr(u, "server_tool_use", None)
    searches = (getattr(server, "web_search_requests", 0) or 0) if server is not None else 0
    if searches:
        usage["web_search_requests"] = searches
        usage["extra_cost_usd"] = searches * _WEB_SEARCH_USD
    return usage


# ---------------------------------------------------------------------------
# Web research: Claude with server-side web search + fetch
# ---------------------------------------------------------------------------

# Anthropic web search is billed per search on top of tokens ($10 / 1,000).
_WEB_SEARCH_USD = env_float("WEB_SEARCH_USD_PER_CALL", 0.01)
# The dynamic-filtering tool versions (Claude writes code to filter results
# before they hit the context) run on the Opus / Sonnet 5.x line, so web
# research always uses the "detailed" tier rather than Haiku.
_WEB_TOOLS_VERSION = "20260209"
_MAX_PAUSE_CONTINUATIONS = 4


class WebResearchUnavailable(RuntimeError):
    """No provider with server-side web tools is configured (callers fall
    back to keyless search or skip the step)."""


def web_research_available() -> bool:
    return get_active_provider() == "anthropic" and env_bool("WEB_RESEARCH", True)


def web_research(
    prompt: str,
    system: Optional[str] = None,
    max_searches: int = 5,
    max_fetches: int = 3,
    model: str = "detailed",
) -> Tuple[str, List[dict]]:
    """Answer ``prompt`` with live web search/fetch. Returns ``(text, sources)``
    where sources are ``{"title", "url"}`` from search results and citations.

    Raises :class:`WebResearchUnavailable` when no Anthropic key is configured
    (or WEB_RESEARCH=0) and :class:`LLMOutputError` on truncated / refused
    output. Every response is ledgered, including the per-search fee.
    """
    if not web_research_available():
        raise WebResearchUnavailable("Web research needs ANTHROPIC_API_KEY (and WEB_RESEARCH not 0).")
    from anthropic import Anthropic  # lazy import

    resolved = resolve_model("anthropic", model)
    tools: list = [{
        "type": f"web_search_{_WEB_TOOLS_VERSION}", "name": "web_search", "max_uses": max_searches,
    }]
    if max_fetches:
        tools.append({
            "type": f"web_fetch_{_WEB_TOOLS_VERSION}", "name": "web_fetch", "max_uses": max_fetches,
        })
    # Server-tool turns are long; let the SDK retry transient errors itself.
    client = Anthropic(timeout=max(_TIMEOUT_SECONDS, 600.0), max_retries=2)
    messages: list = [{"role": "user", "content": prompt}]
    texts: List[str] = []
    sources: List[dict] = []
    from utils.timing import timed_block

    for _ in range(_MAX_PAUSE_CONTINUATIONS + 1):
        with timed_block("llm.web_research", tags={"model": resolved}):
            resp = _anthropic_create(client, {
                "model": resolved, "max_tokens": DEFAULT_MAX_TOKENS,
                "system": system or _DEFAULT_SYSTEM_PROMPT, "messages": messages, "tools": tools,
            })
        _record_usage("anthropic", resolved, _anthropic_usage(resp))
        texts.append(_anthropic_text(resp))
        sources.extend(_web_sources(resp))
        if getattr(resp, "stop_reason", None) != "pause_turn":
            break
        # The server-side tool loop hit its iteration cap: send the paused
        # turn back as-is and the API resumes it (no extra user message).
        messages = [{"role": "user", "content": prompt}, {"role": "assistant", "content": resp.content}]

    text = _strip_reasoning_tokens("\n".join(t for t in texts if t).strip())
    stop_reason = getattr(resp, "stop_reason", None)
    if stop_reason in ("max_tokens", "refusal") or not text:
        raise LLMOutputError(_unusable_output_message(resolved, stop_reason, DEFAULT_MAX_TOKENS))
    unique: Dict[str, dict] = {}
    for src in sources:
        unique.setdefault(src["url"], src)
    return text, list(unique.values())


def _web_sources(resp) -> List[dict]:
    out: List[dict] = []
    for block in resp.content:
        kind = getattr(block, "type", None)
        if kind == "web_search_tool_result":
            content = getattr(block, "content", None)
            for item in content if isinstance(content, list) else []:  # error = an object
                url = getattr(item, "url", None)
                if url:
                    out.append({"title": getattr(item, "title", "") or url, "url": url})
        elif kind == "text":
            for cite in getattr(block, "citations", None) or []:
                url = getattr(cite, "url", None)
                if url:
                    out.append({"title": getattr(cite, "title", "") or url, "url": url})
    return out


def _is_new_style_openai_model(model: str) -> bool:
    """Return True for models that use max_completion_tokens and reject temperature.

    Covers the o-series reasoning models and the gpt-5 family, which dropped
    the legacy max_tokens / temperature parameters.
    """
    prefixes = ("o1", "o3", "o4", "gpt-5")
    return any(model.startswith(p) for p in prefixes)


def _complete_openai_compatible(
    provider: str, prompt: str, model: str, system: str,
    temperature: float, max_tokens: int,
) -> Tuple[str, UsageDict]:
    from openai import OpenAI  # lazy import

    if provider == "featherless":
        client = OpenAI(
            base_url=_FEATHERLESS_BASE_URL,
            api_key=os.getenv("FEATHERLESS_API_KEY"),
            timeout=_TIMEOUT_SECONDS,
            max_retries=0,
        )
    else:  # openai
        client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"), timeout=_TIMEOUT_SECONDS, max_retries=0
        )

    # gpt-5 / o-series models use max_completion_tokens and reject temperature.
    if provider == "openai" and _is_new_style_openai_model(model):
        extra: dict = {}
        reasoning_effort = os.getenv("OPENAI_REASONING_EFFORT", "").strip().lower()
        if reasoning_effort:
            extra["reasoning_effort"] = reasoning_effort
        resp = client.chat.completions.create(
            model=model,
            max_completion_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            **extra,
        )
    else:
        resp = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
    usage = None
    u = getattr(resp, "usage", None)
    if u is not None:
        prompt_toks = getattr(u, "prompt_tokens", 0) or 0
        completion_toks = getattr(u, "completion_tokens", 0) or 0
        usage = {
            "prompt_tokens": prompt_toks,
            "completion_tokens": completion_toks,
            "total_tokens": getattr(u, "total_tokens", 0) or (prompt_toks + completion_toks),
        }
    choice = resp.choices[0]
    text = (choice.message.content or "").strip()
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason in ("length", "content_filter") or not text:
        _record_usage(provider, model, usage)  # tokens were still billed
        raise LLMOutputError(_unusable_output_message(model, finish_reason, max_tokens))
    return text, usage


def _unusable_output_message(model: str, reason: Optional[str], max_tokens: int) -> str:
    if reason in ("max_tokens", "length"):
        return (
            f"Model '{model}' hit the {max_tokens}-token output limit, so its answer "
            "was cut off. Raise LLM_MAX_TOKENS (reasoning/thinking tokens count "
            "toward it) and retry."
        )
    if reason in ("refusal", "content_filter"):
        return f"Model '{model}' declined to answer this request ({reason})."
    return f"Model '{model}' returned an empty response (stop reason: {reason})."


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


def ping_provider(provider: str) -> tuple[bool, str]:
    """Make a minimal real API call to verify a provider is reachable.

    Uses the provider's fast-tier model and a one-word prompt so the cost is
    negligible. The output cap leaves room for thinking/reasoning tokens, which
    count toward it on current models. Returns ``(ok, human-readable message)``.
    """
    key_env = _PROVIDER_KEYS.get(provider, "")
    if not os.getenv(key_env):
        return False, "Not configured (no API key)"

    model = TIER_MODELS[provider]["fast"]
    prompt = "Reply with exactly one word: OK"
    try:
        if provider == "anthropic":
            text, _ = _complete_anthropic(prompt, model, _DEFAULT_SYSTEM_PROMPT, 0.0, 1024)
        else:
            text, _ = _complete_openai_compatible(
                provider, prompt, model, _DEFAULT_SYSTEM_PROMPT, 0.0, 1024
            )
        return True, repr(text.strip()[:40])
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
