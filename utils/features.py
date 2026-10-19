"""Which optional subsystems this process runs.

**Core** (always on): the analysis pipeline, search profile + job discovery,
master CV / projects, tailoring + the no-fabrication check, application
tracking (stages, timeline, inactivity snooze), analytics, data export, and
the LLM cost ledger.

**Side features** exist for the hosted, multi-user product. ``PERSONAL_MODE=1``
turns all of them off: the web app doesn't import their modules or render
their UI, and service-level side effects (webhook dispatch, stage
notifications) are skipped.

Check a side feature with ``enabled("webhooks")``; an unknown name raises,
so a typo can't silently enable or disable anything.
"""

from __future__ import annotations

from utils.env import env_bool

SIDE_FEATURES = frozenset({
    "accounts",       # sign-up / login / password reset / OAuth / 2FA / email verification
    "billing",        # plans, Stripe checkout, tier quotas and model clamp
    "api_tokens",     # REST API bearer tokens (Chrome extension, scripts)
    "webhooks",       # outbound stage / application events
    "telegram",       # bot linking + stage notifications
    "notifications",  # email / Telegram notifications
    "sharing",        # public read-only share links
    "background",     # Celery-queued analyses
    "audit_log",      # "my recent activity" view
    "rate_limits",    # per-user request-count limits on analyses
})


def personal_mode() -> bool:
    return env_bool("PERSONAL_MODE")


def enabled(feature: str) -> bool:
    if feature not in SIDE_FEATURES:
        raise ValueError(f"Unknown feature {feature!r}; known: {sorted(SIDE_FEATURES)}")
    return not personal_mode()
