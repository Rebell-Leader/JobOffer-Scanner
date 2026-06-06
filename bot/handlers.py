"""Telegram bot handlers — pure logic, no telegram dependency at import time.

Handlers are split out so they can be unit-tested without booting the
``python-telegram-bot`` runtime. Each handler takes a small ``Reply`` callable
(``async def(text: str) -> None``) instead of a real ``Update`` so tests can
capture what would have been sent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from agents.orchestrator import run_analysis
from tools.url_ingest import fetch_job_posting, is_url

logger = logging.getLogger(__name__)

# Telegram caps messages at 4096 chars. We split on paragraph boundaries
# whenever possible to keep markdown readable across chunks.
_TELEGRAM_LIMIT = 4000


Reply = Callable[[str], Awaitable[None]]


@dataclass
class AnalyzeInput:
    """What the user typed after `/analyze`."""

    url: Optional[str]
    text: str


def parse_analyze_args(raw: str) -> AnalyzeInput:
    """Pull a URL (if present) out of the message; the rest is posting text."""
    raw = (raw or "").strip()
    if not raw:
        return AnalyzeInput(url=None, text="")
    first, _, rest = raw.partition("\n")
    first = first.strip()
    if is_url(first):
        return AnalyzeInput(url=first, text=rest.strip())
    if is_url(raw):
        return AnalyzeInput(url=raw, text="")
    return AnalyzeInput(url=None, text=raw)


def chunk_for_telegram(text: str, limit: int = _TELEGRAM_LIMIT) -> list[str]:
    """Split a long message into Telegram-sized pieces on paragraph breaks."""
    if not text:
        return []
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        # Prefer the last paragraph break inside the limit, then last newline,
        # then a hard cut.
        cut = remaining.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def format_summary(result: dict) -> str:
    """Render a compact bot-friendly summary of an analysis result."""
    if result.get("error"):
        return f"❌ Analysis failed: {result['error']}"

    job = (result.get("job_details") or {}).get("extracted_details") or {}
    verdict = result.get("verdict") or {}
    light = verdict.get("light", "yellow")
    light_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(light, "⚪")
    label = verdict.get("verdict", "Consider with Caution")
    confidence = verdict.get("confidence")

    lines = [
        f"{light_emoji} *Verdict:* {label}"
        + (f" (confidence {confidence}/10)" if confidence is not None else ""),
        "",
        f"*Company:* {job.get('company_name', '—')}",
        f"*Title:* {job.get('job_title', '—')}",
        f"*Location:* {job.get('location', '—')}",
    ]
    reasons = verdict.get("reasons") or []
    if reasons:
        lines.append("")
        lines.append("*Top reasons:*")
        for r in reasons[:3]:
            lines.append(f"• {r}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "I analyze job postings end-to-end and return a Green/Yellow/Red verdict.\n\n"
    "*Usage:*\n"
    "`/analyze <url>` — fetch a posting URL and analyze it\n"
    "`/analyze\\n<paste posting text here>` — analyze pasted text\n"
    "`/help` — show this message\n\n"
    "Note: JS-heavy job boards (LinkedIn / Indeed / Glassdoor) often won't fetch — paste the text instead."
)


async def handle_start(reply: Reply, _args: str = "") -> None:
    await reply(
        "Hi! I'm the JobOffer Scanner bot. Send `/analyze` followed by a "
        "URL or pasted job description to get a verdict.\n\n" + HELP_TEXT
    )


async def handle_help(reply: Reply, _args: str = "") -> None:
    await reply(HELP_TEXT)


async def handle_analyze(reply: Reply, args: str) -> None:
    parsed = parse_analyze_args(args)
    if not parsed.url and not parsed.text:
        await reply("Please include a URL or pasted posting text. " + HELP_TEXT)
        return

    posting_text = parsed.text
    if parsed.url:
        try:
            await reply(f"Fetching {parsed.url} …")
            posting_text = fetch_job_posting(parsed.url)
        except ValueError as exc:
            await reply(f"⚠️ {exc}")
            if not parsed.text:
                return

    await reply("Analyzing — this can take 30-60 seconds…")
    try:
        result = run_analysis(
            posting_text,
            manual_inputs=None,
            model="fast",  # Telegram users want quick replies.
            progress_callback=None,
        )
    except Exception as exc:  # noqa: BLE001 - reported to user
        logger.exception("Telegram analyze failed.")
        await reply(f"❌ Analysis crashed: {exc}")
        return

    await reply(format_summary(result))
    final = result.get("final_report") or ""
    for chunk in chunk_for_telegram(final):
        await reply(chunk)
