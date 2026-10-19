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
from typing import List, Optional, Set

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
# Any plausible CV year (1900-2099). The previous range started at 1950, so a
# fabricated pre-1950 date slipped the check; lookarounds keep it from matching
# inside longer digit runs.
_YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_PCT_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?%)")

# Money / quantity claims: "$10M", "10M users", "1.5B revenue", "5x growth",
# "team of 8", "7+ years". The money branch is anchored with a lookbehind:
# a leading ``\b`` can never fire between a space and "$", which used to let
# every dollar figure through unflagged.
_QUANT_UNITS = (
    r"users|customers|clients|requests|MAU|DAU|revenue|ARR|stars|downloads|installs"
    r"|companies|years?|yrs?|engineers?|developers?|people|members?|reports?|countries"
    r"|markets|teams?|interns?|services|microservices|projects|languages|patents?"
)
_QUANT_RE = re.compile(
    r"(?<![\w$])\$\s?\d[\d,.]*\s?(?:[KMB]\b|(?:thousand|million|billion)\b)?"
    rf"(?:\s+(?:{_QUANT_UNITS})\b)?"
    rf"|\b\d[\d,.]*\+?\s?(?:[KMB]\+?\s+)?(?:{_QUANT_UNITS})\b"
    r"|\b(?:team|group|org|organization|staff) of \d+\b"
    r"|\b\d+(?:\.\d+)?x\s+(?:growth|faster|increase|reduction|improvement)\b",
    re.IGNORECASE,
)

# Seniority / title words. They are claims about the candidate, so they must
# trace to the master CV — previously they were ignored outright, which let
# "Software Engineer" be rewritten as "Principal Engineer" without a flag.
_SENIORITY_WORDS: Set[str] = {
    "senior", "sr", "junior", "jr", "lead", "staff", "principal", "manager",
    "director", "head", "chief", "vp", "intern",
}
# Salutation boilerplate that would otherwise surface "Manager" as a claim.
_SALUTATION_RE = re.compile(r"(?i)\bhiring\s+(?:manager|team)s?\b")
# A word broken across a line by PDF text extraction ("Kuber-\nnetes").
_HYPHEN_BREAK_RE = re.compile(r"(\w)-\n(\w)")

# Tokens we never treat as "facts" — section headers, generic role/job words,
# common English, and a handful of words our resume tools generate themselves.
COMMON_WORDS: Set[str] = {
    "summary", "skills", "experience", "education", "projects", "certifications",
    "candidate", "engineer", "engineers",
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


# Common tech skills/tools that are frequently written in lowercase. The
# capitalization heuristic in _TOKEN_RE only captures Capitalized tokens, so a
# fabricated skill written lowercase ("kubernetes") would otherwise never be
# extracted from the output — and a legitimately-lowercase skill in the source
# CV would never enter the allowed set (a false positive when the output
# capitalizes it). Scanning for these known terms case-insensitively on BOTH
# sides closes both holes without flagging ordinary lowercase prose.
_SKILL_LEXICON: Set[str] = {
    "python", "java", "javascript", "typescript", "golang", "rust", "ruby",
    "php", "scala", "kotlin", "swift", "perl", "haskell", "elixir", "clojure",
    "react", "angular", "vue", "svelte", "django", "flask", "fastapi", "rails",
    "spring", "express", "node", "nodejs", "deno", "nextjs", "nestjs",
    "tensorflow", "pytorch", "keras", "sklearn", "scikit-learn", "numpy",
    "pandas", "spark", "hadoop", "kafka", "airflow", "dbt", "snowflake",
    "postgres", "postgresql", "mysql", "sqlite", "mongodb", "redis",
    "cassandra", "elasticsearch", "dynamodb", "kubernetes", "docker",
    "terraform", "ansible", "jenkins", "gitlab", "github", "circleci",
    "prometheus", "grafana", "datadog", "kibana", "aws", "gcp", "azure",
    "linux", "kubernetes", "graphql", "grpc", "protobuf", "rabbitmq", "celery",
    "nginx", "caddy", "envoy", "istio", "helm", "openai", "anthropic",
    "langchain", "huggingface", "transformers",
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


def _prepare(text: str) -> str:
    text = _HYPHEN_BREAK_RE.sub(r"\1\2", text)
    return _SALUTATION_RE.sub(" ", text)


def _is_sentence_initial(text: str, start: int) -> bool:
    """True when the token at ``start`` opens a sentence, line, or bullet."""
    before = text[max(0, start - 40):start].rstrip(" \t*-•>#_|")
    before = re.sub(r"\d+[.)]$", "", before).rstrip()
    return not before or before[-1] in ".!?\n"


def _is_plain_word(token: str) -> bool:
    """A capitalized dictionary-looking word ("Thank", "Having") rather than a
    skill shape ("PyTorch", "AWS", "C++", "Node.js")."""
    return token[:1].isupper() and token[1:].islower() and token.isalpha()


def extract_proper_nouns(text: str, prose_filter: bool = False) -> Set[str]:
    """Return the case-folded set of skill/proper-noun-shaped tokens in ``text``.

    ``prose_filter`` skips capitalized plain words that merely open a sentence
    or bullet. Use it on generated output only: on the source side every token
    should count as an allowed fact.
    """
    out: Set[str] = set()
    if not text:
        return out
    text = _prepare(text)
    for m in _TOKEN_RE.finditer(text):
        token = m.group(1)
        norm = _normalize(token)
        # A capitalized plain word that merely opens a sentence or bullet
        # ("Thank you", "Led the migration") is ordinary prose. Seniority words
        # and known skills are still checked there — role headers start lines.
        if (
            prose_filter
            and _is_plain_word(token.rstrip(".,;:!?"))
            and norm not in _SENIORITY_WORDS
            and norm not in _SKILL_LEXICON
            and _is_sentence_initial(text, m.start())
        ):
            continue
        # "Python/Django" is two skills, not one new term.
        parts = norm.split("/") if "/" in norm and "://" not in norm else [norm]
        for piece in parts:
            if piece in COMMON_WORDS or len(piece) < 2:
                continue
            out.add(piece)
            # Also index each word of a multi-word phrase so "Distributed Systems"
            # in the master CV doesn't make a bare "Distributed" in tailored seem new.
            for part in piece.split():
                if part not in COMMON_WORDS and len(part) >= 2:
                    out.add(part)
    # Catch known skills written in lowercase (the capitalization gate above
    # misses them). Word-boundaried so "java" doesn't match "javascript" etc.
    low = text.casefold()
    for skill in _SKILL_LEXICON:
        if re.search(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])", low):
            out.add(skill)
    return out


def extract_years(text: str) -> Set[str]:
    return set(_YEAR_RE.findall(text or ""))


def extract_percentages(text: str) -> Set[str]:
    return set(_PCT_RE.findall(text or ""))


def extract_quantitative_claims(text: str) -> Set[str]:
    return {_normalize(m) for m in _QUANT_RE.findall(_prepare(text or ""))}


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
    # Subset of new_proper_nouns that appear in the job posting but not in the
    # candidate's sources: the classic way a tailored CV gets embellished.
    from_posting: List[str] = field(default_factory=list)
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
            "from_posting": self.from_posting,
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
            from_posting=list(d.get("from_posting") or []),
            severity=d.get("severity") or "clean",
        )


def check_tailored_output(
    master_cv_text: str,
    projects_text: str,
    tailored_text: str,
    job_context_text: str = "",
    posting_text: str = "",
) -> ConstraintCheck:
    """Compare a tailored artifact against the sources it should be drawn from.

    The "allowed" set is master CV + project gallery + ``job_context_text``.
    Pass only what the artifact may legitimately echo back as-is — for a cover
    letter that's the target company / title / location; for a CV, nothing
    (every CV fact is a claim about the candidate). Never pass the posting's
    required skills here: that whitelisted exactly the skills a model is most
    tempted to copy into the CV. Pass the posting via ``posting_text`` instead;
    it is used only to label flagged terms that were lifted from it.
    """
    source = "\n".join([master_cv_text or "", projects_text or "", job_context_text or ""])

    src_nouns = extract_proper_nouns(source)
    src_years = extract_years(source)
    src_pcts = extract_percentages(source)
    src_quant = extract_quantitative_claims(source)

    out_nouns = extract_proper_nouns(tailored_text or "", prose_filter=True)
    out_years = extract_years(tailored_text or "")
    out_pcts = extract_percentages(tailored_text or "")
    out_quant = extract_quantitative_claims(tailored_text or "")

    new_nouns = sorted(out_nouns - src_nouns)
    new_years = sorted(out_years - src_years)
    new_pcts = sorted(out_pcts - src_pcts)
    new_quant = sorted(out_quant - src_quant)
    posting_nouns = extract_proper_nouns(posting_text) if posting_text else set()
    from_posting = [n for n in new_nouns if n in posting_nouns]

    severity = "clean" if not (new_nouns or new_years or new_pcts or new_quant) else "review_recommended"
    return ConstraintCheck(
        new_proper_nouns=new_nouns,
        new_years=new_years,
        new_percentages=new_pcts,
        new_quantitative_claims=new_quant,
        from_posting=from_posting,
        severity=severity,
    )


def summarize(check: ConstraintCheck) -> str:
    """Short human-readable summary for the UI."""
    if check.is_clean:
        return "✅ No new facts detected — output stays inside your master CV."
    parts = []
    if check.from_posting:
        sample = ", ".join(check.from_posting[:5])
        parts.append(
            f"{len(check.from_posting)} term(s) copied from the job posting that your "
            f"CV doesn't contain ({sample}{'…' if len(check.from_posting) > 5 else ''})"
        )
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
