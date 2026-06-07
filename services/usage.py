"""LLM cost controls: per-call token accounting + per-user spend budgets.

The previous quota was a *request count* (``services/rate_limit`` ANALYSIS
limiter) — it can't tell a one-line extraction from a 50-stage report. This
module ledgers the actual token usage of every real provider call and turns it
into an estimated dollar cost, so an operator can cap spend per user.

How attribution works
---------------------
Threading a ``user_id`` through every agent and tool would be invasive, so we
use a ``contextvars`` scope instead. The request boundary opens an accounting
scope::

    with usage.account(user_id):
        run_analysis(...)          # every get_completion inside is attributed

``utils.llm.get_completion`` calls :func:`record_completion` after each real
call; it reads the current user from the scope (or takes an explicit
``user_id``). Calls made with no scope are still ledgered with a NULL owner for
global accounting.

Cost model
----------
Pricing is a per-model ``(input_per_1k, output_per_1k)`` table in USD,
overridable wholesale via the ``LLM_PRICING_JSON`` env var. Unknown models fall
back to ``_DEFAULT_RATE``. Figures are *estimates* for budgeting, not billing —
providers are the source of truth for actual charges.

Budget
------
``LLM_BUDGET_USD`` (default 0 = disabled) caps a user's spend over a rolling
``LLM_BUDGET_WINDOW_SECONDS`` window (default 30 days). :func:`check_budget`
raises :class:`BudgetExceeded` when the cap is hit; the analysis entry point
calls it before doing any work.

Everything here is best-effort for the *write* path: a ledger failure logs and
continues — accounting must never break the user's analysis.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Dict, Iterator, Optional, Tuple

from sqlalchemy import func, select

import utils.metrics as metrics
from db.models import LlmUsage
from db.session import get_session

logger = logging.getLogger(__name__)

# Estimated USD per 1,000 tokens, (input, output). Approximate list prices;
# override the whole table with LLM_PRICING_JSON='{"model": [in, out], ...}'.
_DEFAULT_PRICING: Dict[str, Tuple[float, float]] = {
    # Anthropic
    "claude-haiku-4-5-20251001": (0.001, 0.005),
    "claude-sonnet-4-6": (0.003, 0.015),
    # OpenAI
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "gpt-5": (0.00125, 0.01),
    # Featherless / open models are flat-rate hosted; treat as ~free per-token.
    "Qwen/Qwen3-32B": (0.0, 0.0),
    "deepseek-ai/DeepSeek-R1-0528": (0.0, 0.0),
}
# Fallback for any model not in the table (input, output) per 1k tokens.
_DEFAULT_RATE: Tuple[float, float] = (0.001, 0.003)

# contextvar holding the user_id the current LLM calls should be attributed to.
_current_user: ContextVar[Optional[int]] = ContextVar("usage_current_user", default=None)


class BudgetExceeded(Exception):
    """Raised when a user has spent their configured LLM budget for the window."""

    def __init__(self, spent_usd: float, budget_usd: float):
        self.spent_usd = spent_usd
        self.budget_usd = budget_usd
        super().__init__(
            f"LLM budget reached: ${spent_usd:.2f} of ${budget_usd:.2f} used "
            "for this period. Try again later or contact the operator."
        )


# ---------------------------------------------------------------------------
# Attribution scope
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def account(user_id: Optional[int]) -> Iterator[None]:
    """Attribute LLM calls made in this scope to ``user_id`` (None = unattributed)."""
    token = _current_user.set(user_id)
    try:
        yield
    finally:
        _current_user.reset(token)


def current_user() -> Optional[int]:
    return _current_user.get()


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def _pricing_table() -> Dict[str, Tuple[float, float]]:
    raw = os.getenv("LLM_PRICING_JSON")
    if not raw:
        return _DEFAULT_PRICING
    try:
        parsed = json.loads(raw)
        return {str(k): (float(v[0]), float(v[1])) for k, v in parsed.items()}
    except Exception as exc:  # noqa: BLE001 - bad override shouldn't break accounting
        logger.warning("Ignoring invalid LLM_PRICING_JSON (%s); using defaults.", exc)
        return _DEFAULT_PRICING


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimated USD cost for a call, from the pricing table (per-1k rates)."""
    in_rate, out_rate = _pricing_table().get(model, _DEFAULT_RATE)
    return (prompt_tokens / 1000.0) * in_rate + (completion_tokens / 1000.0) * out_rate


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def record_completion(
    provider: str,
    model: str,
    usage: Optional[dict],
    *,
    user_id: Optional[int] = None,
) -> None:
    """Ledger one real completion's token usage + estimated cost. Best-effort.

    ``usage`` is the provider's usage dict (``prompt_tokens`` /
    ``completion_tokens`` / ``total_tokens``); ``None`` (provider didn't report
    usage) records nothing but bumps a counter so the gap is visible.
    """
    if usage is None:
        metrics.increment("llm.completions_without_usage", tags={"provider": provider})
        return

    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or (prompt + completion))
    cost = estimate_cost_usd(model, prompt, completion)
    cost_micro = int(round(cost * 1_000_000))
    uid = user_id if user_id is not None else current_user()

    tags = {"provider": provider, "model": model}
    metrics.increment("llm.prompt_tokens", prompt, tags=tags)
    metrics.increment("llm.completion_tokens", completion, tags=tags)
    metrics.increment("llm.cost_micro_usd", cost_micro, tags=tags)

    try:
        with get_session() as session:
            session.add(LlmUsage(
                user_id=uid,
                provider=provider,
                model=model,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
                cost_micro_usd=cost_micro,
            ))
            session.commit()
    except Exception as exc:  # noqa: BLE001 - accounting must never break the call
        logger.warning("Failed to ledger LLM usage: %s", exc)


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

def _budget_usd() -> float:
    try:
        return float(os.getenv("LLM_BUDGET_USD", "0") or 0)
    except ValueError:
        return 0.0


def _budget_window_seconds() -> int:
    try:
        return int(os.getenv("LLM_BUDGET_WINDOW_SECONDS", str(30 * 24 * 3600)))
    except ValueError:
        return 30 * 24 * 3600


def spend_usd(user_id: int, window_seconds: Optional[int] = None) -> float:
    """Total estimated USD spend for a user over the rolling window."""
    window = window_seconds if window_seconds is not None else _budget_window_seconds()
    cutoff = datetime.utcnow() - timedelta(seconds=window)
    with get_session() as session:
        total_micro = session.execute(
            select(func.coalesce(func.sum(LlmUsage.cost_micro_usd), 0)).where(
                LlmUsage.user_id == user_id,
                LlmUsage.created_at >= cutoff,
            )
        ).scalar_one()
    return (total_micro or 0) / 1_000_000.0


def check_budget(user_id: Optional[int]) -> None:
    """Raise :class:`BudgetExceeded` if the user is at/over their LLM budget.

    No-ops when budgeting is disabled (``LLM_BUDGET_USD`` unset/0) or there's
    no user to attribute to (bot/anon flow).
    """
    budget = _budget_usd()
    if budget <= 0 or user_id is None:
        return
    spent = spend_usd(user_id)
    if spent >= budget:
        raise BudgetExceeded(spent, budget)
