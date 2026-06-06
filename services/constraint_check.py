"""Deterministic post-check for tailored artifacts.

The no-fabrication rules in the tailoring prompts are a *hope*. This module
turns that into a *check*: after every generation we extract the concrete,
verifiable facts from the tailored output (skills/technology tokens, years,
percentages) and compare them against the union of facts present in the
master CV, the project gallery, and the job context. Anything new is surfaced
to the user as "review recommended".

This is intentionally a circuit breaker, not a proof of correctness:

  * It catches the *common* fabrication failure modes (invented technologies,
    made-up years, fake quantitative claims, new employers).
  * It cannot catch subtle reworded claims ("led a team" → "managed five
    engineers"); those still rely on the model honoring the prompt.
  * False positives are possible. We never silently delete content — we just
    flag it and let the user verify.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set

# ---------------------------------------------------------------------------
# Token-extraction patterns
# ---------------------------------------------------------------------------

# Skill-shaped tokens: a capitalized word plus optional skill-internal
# punctuation (``+ # . - /``) so "C++", "C#", "Node.js", "scikit-learn" survive
# whole. We deliberately stick to SINGLE words: a previous version matched
# 2-word capitalized phrases as well, but that flagged routine title text like
# "Staff ML" as a fabrication because no such *phrase* appeared in the master
# CV — even though both words individually did. Multi-word skills like
# "Apache Spark" still get caught because their component words ("apache",
# "spark") are extracted individually.
#
# Lookbehind/lookahead anchors replace ``\b`` because ``\b`` doesn't fire
# after non-word characters like ``+`` or ``%`` — that meant "C++" and "40%"
# weren't being captured at all.
_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Z][\w+#./-]{1,40})(?![A-Za-z0-9])"
)
_YEAR_RE = re.compile(r"(?<!\d)(19[5-9]\d|20\d{2})(?!\d)")
_PCT_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?%)")

# Money / quantity claims: "$10M", "10M users", "1.5B revenue", "5x growth".
_QUANT_RE = re.compile(
    r"\b(\$\s?\d[\d,.]*\s?[KMB]?(?:\s+(?:users|customers|requests|MAU|DAU|revenue|ARR))?"
    r"|\d[\d,.]*\s?[KMB]\s+(?:users|customers|requests|MAU|DAU|revenue|ARR|stars|downloads|installs|companies)"
    r"|\d+x\s+(?:growth|faster|increase|reduction|improvement))\b",
    re.IGNORECASE,
)

# Tokens we never treat as "facts" — section headers, generic role/job words,
# common English, and a handful of words our resume tools generate themselves.
COMMON_WORDS: Set[str] = {
    "summary", "skills", "experience", "education", "projects", "certifications",
    "candidate", "engineer", "engineers", "senior", "junior", "lead", "manager",
    "director", "principal", "staff", "intern",
    "company", "team", "role", "title", "year", "years", "month", "months",
    "include", "including", "such", "like", "example",
    "data", "system", "systems", "service", "services", "platform",
    "product", "design", "designed", "build", "built", "develop", "developed",
    "tech", "stack",
    "i", "we", "you", "our", "their", "his", "her", "the", "a", "an", "and", "or",
    "of", "to", "in", "at", "for", "with", "by", "from", "on", "as", "is", "was",
    "summary:", "skills:", "experience:", "education:",
    # Cover letter scaffolding the prompt encourages.
    "dear", "hiring", "sincerely", "regards", "best",
    # Common generic resume phrases.
    "github", "linkedin",  # platforms; if real, would appear in master too
}


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _normalize(token: str) -> str:
    """Case-fold + strip *trailing* sentence punctuation only.

    Trailing dots / commas / semicolons land inside the captured token because
    they're inside the skill-char class (``.`` is there for ``Node.js``), but
    they're usually sentence punctuation. Strip them so ``Python.`` matches
    ``Python``. Internal punctuation (``Node.js``, ``C++``) is preserved.
    """
    cleaned = token.strip().rstrip(".,;:!?")
    return re.sub(r"\s+", " ", cleaned).casefold()


def extract_proper_nouns(text: str) -> Set[str]:
    """Return the case-folded set of skill/proper-noun-shaped tokens in ``text``."""
    out: Set[str] = set()
    if not text:
        return out
    for m in _TOKEN_RE.findall(text):
        norm = _normalize(m)
        if norm in COMMON_WORDS or len(norm) < 2:
            continue
        out.add(norm)
        # Also index each word of a multi-word phrase so "Distributed Systems"
        # in the master CV doesn't make a bare "Distributed" in tailored seem new.
        for part in norm.split():
            if part not in COMMON_WORDS and len(part) >= 2:
                out.add(part)
    return out


def extract_years(text: str) -> Set[str]:
    return set(_YEAR_RE.findall(text or ""))


def extract_percentages(text: str) -> Set[str]:
    return set(_PCT_RE.findall(text or ""))


def extract_quantitative_claims(text: str) -> Set[str]:
    return {_normalize(m) for m in _QUANT_RE.findall(text or "")}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

@dataclass
class ConstraintCheck:
    """Result of checking a tailored artifact against its sources."""

    new_proper_nouns: List[str] = field(default_factory=list)
    new_years: List[str] = field(default_factory=list)
    new_percentages: List[str] = field(default_factory=list)
    new_quantitative_claims: List[str] = field(default_factory=list)
    severity: str = "clean"  # "clean" | "review_recommended"

    @property
    def is_clean(self) -> bool:
        return self.severity == "clean"

    @property
    def total_flags(self) -> int:
        return (
            len(self.new_proper_nouns)
            + len(self.new_years)
            + len(self.new_percentages)
            + len(self.new_quantitative_claims)
        )

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "new_proper_nouns": self.new_proper_nouns,
            "new_years": self.new_years,
            "new_percentages": self.new_percentages,
            "new_quantitative_claims": self.new_quantitative_claims,
            "total_flags": self.total_flags,
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "ConstraintCheck":
        if not d:
            return cls()
        return cls(
            new_proper_nouns=list(d.get("new_proper_nouns") or []),
            new_years=list(d.get("new_years") or []),
            new_percentages=list(d.get("new_percentages") or []),
            new_quantitative_claims=list(d.get("new_quantitative_claims") or []),
            severity=d.get("severity") or "clean",
        )


def check_tailored_output(
    master_cv_text: str,
    projects_text: str,
    tailored_text: str,
    job_context_text: str = "",
) -> ConstraintCheck:
    """Compare a tailored artifact against the sources it should be drawn from.

    The "allowed" set is the union of facts in master CV + project gallery +
    job context — the last one is important because a tailored output may
    legitimately echo company name / location / required skills from the
    posting itself.
    """
    source = "\n".join([master_cv_text or "", projects_text or "", job_context_text or ""])

    src_nouns = extract_proper_nouns(source)
    src_years = extract_years(source)
    src_pcts = extract_percentages(source)
    src_quant = extract_quantitative_claims(source)

    out_nouns = extract_proper_nouns(tailored_text or "")
    out_years = extract_years(tailored_text or "")
    out_pcts = extract_percentages(tailored_text or "")
    out_quant = extract_quantitative_claims(tailored_text or "")

    new_nouns = sorted(out_nouns - src_nouns)
    new_years = sorted(out_years - src_years)
    new_pcts = sorted(out_pcts - src_pcts)
    new_quant = sorted(out_quant - src_quant)

    severity = "clean" if not (new_nouns or new_years or new_pcts or new_quant) else "review_recommended"
    return ConstraintCheck(
        new_proper_nouns=new_nouns,
        new_years=new_years,
        new_percentages=new_pcts,
        new_quantitative_claims=new_quant,
        severity=severity,
    )


def summarize(check: ConstraintCheck) -> str:
    """Short human-readable summary for the UI."""
    if check.is_clean:
        return "✅ No new facts detected — output stays inside your master CV."
    parts = []
    if check.new_proper_nouns:
        sample = ", ".join(check.new_proper_nouns[:5])
        parts.append(f"{len(check.new_proper_nouns)} new skill/term ({sample}{'…' if len(check.new_proper_nouns) > 5 else ''})")
    if check.new_years:
        parts.append(f"{len(check.new_years)} new year ({', '.join(check.new_years)})")
    if check.new_percentages:
        parts.append(f"{len(check.new_percentages)} new percentage ({', '.join(check.new_percentages)})")
    if check.new_quantitative_claims:
        parts.append(f"{len(check.new_quantitative_claims)} new quantitative claim")
    return "⚠️ Review recommended — " + "; ".join(parts) + "."
