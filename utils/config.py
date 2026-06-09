
import logging
import os
from typing import Dict, List, Union

from utils.env import effective_config
from utils.llm import TIER_MODELS, get_active_provider, is_demo_mode

logger = logging.getLogger(__name__)


def check_environment_setup() -> Dict[str, Union[bool, str, None]]:
    """Check environment/configuration status.

    Demo mode is driven by the SAME logic the LLM layer uses, so the UI badge
    can never claim "Production" while the LLM layer is actually returning
    sample data (the previous OPENAI_API_KEY vs FEATHERLESS_API_KEY mismatch).
    """
    provider = get_active_provider()
    return {
        "llm_provider": provider,                 # e.g. "anthropic" or None
        "llm_configured": provider is not None,
        "database_url": bool(os.getenv("DATABASE_URL")),
        "demo_mode": is_demo_mode(),
    }


def log_effective_config() -> Dict[str, object]:
    """Log the resolved, NON-default configuration (from utils.env reads).

    Call once at process startup (api/main, app.py, worker). Makes a typo'd or
    unexpected override visible — secrets aren't read through utils.env, so this
    only surfaces tuning knobs (limits, timeouts, budgets, feature flags), never
    keys. Returns the dict it logged (handy for tests/admin views).
    """
    cfg = effective_config()
    if cfg:
        logger.info("Effective config (non-default): %s", cfg)
    else:
        logger.info("Effective config: all defaults (no env overrides read yet).")
    return cfg


def get_missing_configs() -> List[str]:
    """List configuration items missing for full production operation."""
    missing = []

    if is_demo_mode():
        missing.append(
            "LLM API key - set ANTHROPIC_API_KEY, OPENAI_API_KEY, or "
            "FEATHERLESS_API_KEY to enable real analysis"
        )

    if not os.getenv("DATABASE_URL"):
        missing.append("DATABASE_URL - required for persistence (Phase 3)")

    # External data sources still to be integrated (Phase 1).
    future_apis = [
        "NEWS_API_KEY - real company news & layoff signals",
        "Salary/COL data source - real benchmarking (e.g. Numbeo, levels.fyi)",
    ]
    missing.extend(future_apis)
    return missing


def print_environment_status():
    """Print current environment configuration status to the console."""
    print("\n" + "=" * 50)
    print("AI JOB ANALYSIS PLATFORM - ENVIRONMENT STATUS")
    print("=" * 50)

    status = check_environment_setup()

    if status["demo_mode"]:
        print("DEMO MODE: no LLM key configured — using sample data")
        print("Set ANTHROPIC_API_KEY / OPENAI_API_KEY / FEATHERLESS_API_KEY for real analysis")
    else:
        provider = status["llm_provider"]
        models = TIER_MODELS.get(provider, {})
        print(f"PRODUCTION MODE: provider='{provider}'")
        print(f"  fast='{models.get('fast')}'  detailed='{models.get('detailed')}'")

    print("\nConfiguration status:")
    for key, value in status.items():
        symbol = "OK" if value else "--"
        print(f"  [{symbol}] {key.replace('_', ' ').title()}: {value}")

    missing = get_missing_configs()
    if missing:
        print("\nMissing configurations for full production:")
        for item in missing:
            print(f"  - {item}")

    print("=" * 50 + "\n")
