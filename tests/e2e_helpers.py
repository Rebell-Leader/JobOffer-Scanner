"""Shared helpers for use-case (user-journey) e2e tests.

NOT a test module (no ``test`` prefix → unittest won't collect it). The suite
runs under ``unittest`` (not pytest), so this is a plain importable module of
context managers + factories rather than pytest fixtures.

The headline piece is :func:`mock_llm`: it makes the REAL analysis pipeline run
end-to-end without a provider, by setting a provider key (so the code takes the
non-demo path — usage accounting, model resolution, caching all execute) and
stubbing the provider call to return **stage-appropriate** structured output.
We reuse ``utils.llm.generate_sample_response`` for the body because it already
emits valid, stage-shaped JSON/markdown for every pipeline stage (it's what
demo mode runs on), so the shapes are guaranteed to match what the agents
expect — while the surrounding real code (which demo mode skips) still runs.

Optional external fetchers (news/COL/salary/agentic search) are disabled via
env so nothing touches the network; individual tests can re-enable + mock a
specific one (e.g. the agentic fallback journey).
"""

from __future__ import annotations

import contextlib
import os
from typing import Callable, Iterator, Optional
from unittest import mock

# Env that gates optional network fetchers — cleared inside mock_llm so the
# pipeline relies solely on the stubbed LLM and never egresses.
_NETWORK_ENV = (
    "NEWS_API_KEY", "LAYOFFS_DATASET_URL", "COL_DATASET_URL",
    "ADZUNA_APP_ID", "ADZUNA_APP_KEY",
)

_STUB_USAGE = {"prompt_tokens": 50, "completion_tokens": 50, "total_tokens": 100}


def fresh_db() -> None:
    """Reset to a clean in-memory SQLite DB (StaticPool, shared across threads).

    Also clears the process-wide rate-limit + checkpoint singletons so journeys
    don't leak counters into each other (registering the same email across
    classes would otherwise trip the register limiter)."""
    from db.session import reset_engine_for_testing
    from services.rate_limit import reset_backend_for_testing
    reset_engine_for_testing("sqlite:///:memory:")
    reset_backend_for_testing()
    try:
        from services.checkpoint import reset_store_for_testing
        reset_store_for_testing()
    except Exception:  # noqa: BLE001 - checkpoint reset is best-effort
        pass


def make_user(email: str = "journey@example.com", password: str = "Sup3rSecret!"):
    """Register and return an AuthedUser."""
    from services.auth import register_user
    return register_user(email, password)


def issue_api_token(user_id: int, label: str = "e2e") -> str:
    from services.api_tokens import issue
    return issue(user_id, label, ttl_days=30).raw_token


@contextlib.contextmanager
def mock_llm(handler: Optional[Callable[[str], str]] = None) -> Iterator[None]:
    """Run the real pipeline with a stubbed LLM provider (no network, no key).

    ``handler(prompt) -> text`` lets a test override the response; by default we
    delegate to the demo sample generator, which returns valid stage-shaped
    output for every pipeline prompt. A non-demo provider key is set so usage
    accounting and the non-demo code paths actually execute.
    """
    import utils.llm as llm

    def _text(prompt: str) -> str:
        if handler is not None:
            out = handler(prompt)
            if out is not None:
                return out
        return llm.generate_sample_response(prompt)

    def _openai(provider, prompt, model, system, temperature, max_tokens):
        return _text(prompt), dict(_STUB_USAGE)

    def _anthropic(prompt, model, system, temperature, max_tokens):
        return _text(prompt), dict(_STUB_USAGE)

    env = {"OPENAI_API_KEY": "sk-e2e-test", "LLM_PROVIDER": "openai",
           # Disable the agentic web fallback so the company stage never egresses.
           "COMPANY_RESEARCH_FALLBACK": "0"}
    with mock.patch.dict(os.environ, env):
        for k in _NETWORK_ENV:
            os.environ.pop(k, None)
        with mock.patch.object(llm, "_complete_openai_compatible", _openai), \
             mock.patch.object(llm, "_complete_anthropic", _anthropic):
            # The completion cache would mask per-call stubbing across tests.
            from utils.cache import cache
            cache.clear()
            yield
