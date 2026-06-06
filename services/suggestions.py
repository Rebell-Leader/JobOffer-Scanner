"""Soft suggestions derived from a constraint-check result.

When the checker flags a term in a tailored artifact, the user's two options
are usually: (a) it's a real fabrication — regenerate or edit the artifact,
or (b) the skill is real but missing from the master CV — add it there and
re-check clears the flag.

This module exists to make path (b) one click instead of a hunt-and-peck
edit. It produces actionable suggestions, and `apply_skill_addition` performs
the master-CV edit honestly: the addition is appended as a clearly-labelled
line so it's transparent what the user added, when, and from where.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from services.constraint_check import ConstraintCheck
from services.master_cv import MasterCVError, get_master_cv, save_master_cv


@dataclass
class Suggestion:
    """One actionable thing the user could do to clear a flag."""

    kind: str            # "skill" | "year" | "percentage" | "quantitative"
    term: str            # the flagged term verbatim (case-folded)
    title: str           # short label for the UI button
    explanation: str     # why this clears the flag
    auto_appliable: bool # True if we can perform the edit programmatically


def build_suggestions(check: ConstraintCheck) -> List[Suggestion]:
    """Produce a list of suggestions from a constraint check result.

    Only proper-noun flags are auto-appliable. Years and quantitative claims
    are intentionally NOT one-click — they're too specific to a real event in
    the user's history, and silently appending "2018" to the CV would invite
    the exact fabrication the checker exists to prevent.
    """
    out: List[Suggestion] = []
    for term in check.new_proper_nouns:
        out.append(Suggestion(
            kind="skill",
            term=term,
            title=f"Add “{term}” to my master CV skills",
            explanation=(
                f"Only do this if you genuinely have {term!r} experience. "
                "The detector flagged it because no occurrence appears in "
                "your master CV, project gallery, or the job context. "
                "Adding it here will clear the flag — it won't make the "
                "fabrication go away if you don't actually have it."
            ),
            auto_appliable=True,
        ))
    for term in check.new_years:
        out.append(Suggestion(
            kind="year",
            term=term,
            title=f"The year “{term}” isn't in your CV",
            explanation=(
                "Years usually mark a specific employment / education / "
                "project event. We don't auto-append years — open your "
                "master CV and add the relevant date in context if it's "
                "genuinely missing, then click Re-check."
            ),
            auto_appliable=False,
        ))
    for term in check.new_percentages:
        out.append(Suggestion(
            kind="percentage",
            term=term,
            title=f"The figure “{term}” isn't in your CV",
            explanation=(
                "Quantitative claims (percentages, dollar amounts, scale) "
                "need to trace back to a real achievement. We don't auto-add "
                "these — verify the claim and add it to your master CV in "
                "context, then click Re-check."
            ),
            auto_appliable=False,
        ))
    for term in check.new_quantitative_claims:
        out.append(Suggestion(
            kind="quantitative",
            term=term,
            title=f"The claim “{term}” isn't in your CV",
            explanation=(
                "Same reasoning as percentages — verify the claim is real, "
                "add it to your master CV in context, then Re-check."
            ),
            auto_appliable=False,
        ))
    return out


# ---------------------------------------------------------------------------
# Apply: append a skill to the master CV
# ---------------------------------------------------------------------------

# Section we manage automatically. Kept on its own line in the master CV so
# repeated additions accumulate without trampling the user's hand-written
# Skills section.
_AUTO_HEADER = "Additional skills (added in-app):"


def apply_skill_addition(user_id: int, skill: str) -> str:
    """Append a skill to a dedicated, in-app-managed section of the master CV.

    Returns the new raw_text. Raises ``MasterCVError`` if no master CV exists.

    Design note: we don't try to surgically edit the user's existing "Skills:"
    line — that's a recipe for mangling formatting. Instead we maintain a
    clearly-labelled separate section. Re-applying the same skill is a no-op.
    """
    skill = (skill or "").strip()
    if not skill:
        raise MasterCVError("Cannot add an empty skill.")

    cv = get_master_cv(user_id)
    if cv is None:
        raise MasterCVError("No master CV saved yet.")

    raw = cv.raw_text
    existing_section = _extract_auto_section(raw)
    items = _parse_auto_items(existing_section)

    if any(s.casefold() == skill.casefold() for s in items):
        return raw  # already there
    items.append(skill)

    new_section = _format_auto_section(items)
    new_raw = _replace_or_append_auto_section(raw, new_section)
    save_master_cv(user_id, new_raw, reason=f"added skill: {skill}")
    return new_raw


_SECTION_RE = re.compile(
    rf"\n*{re.escape(_AUTO_HEADER)}\n((?:- .+\n?)*)",
)


def _extract_auto_section(raw: str) -> str:
    m = _SECTION_RE.search(raw)
    return m.group(1) if m else ""


def _parse_auto_items(section_body: str) -> List[str]:
    items = []
    for line in section_body.splitlines():
        line = line.strip()
        if line.startswith("- "):
            items.append(line[2:].strip())
    return items


def _format_auto_section(items: List[str]) -> str:
    body = "\n".join(f"- {s}" for s in items)
    return f"\n\n{_AUTO_HEADER}\n{body}\n"


def _replace_or_append_auto_section(raw: str, new_section: str) -> str:
    if _SECTION_RE.search(raw):
        # Replace the whole header + body with the new one.
        # Strip the leading newlines from new_section so we don't double-space.
        return _SECTION_RE.sub(new_section.lstrip("\n").rstrip() + "\n", raw)
    return raw.rstrip() + new_section
