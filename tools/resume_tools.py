"""Resume ingestion + ATS/keyword-gap analysis.

Pipeline:
  1. ``extract_resume_text`` parses an uploaded resume (PDF / DOCX / plain text)
     into a normalized string. Optional deps (``pypdf``, ``python-docx``) are
     imported lazily so the rest of the app keeps working without them.
  2. ``ats_keyword_match`` computes a deterministic match score between the
     resume text and the required skills extracted from the job posting —
     pure logic, fully unit-testable.
  3. ``ats_format_checks`` flags formatting patterns that historically trip
     ATS parsers (tables, multi-column hints, excessive ALL-CAPS lines).
  4. ``analyze_resume`` ties these together and asks the LLM for the prose
     "gap analysis" section, with the resume text wrapped as untrusted data.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Dict, Iterable, List, Optional

from utils.cache import cache
from utils.llm import get_completion
from utils.security import wrap_untrusted

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Text extraction
# ---------------------------------------------------------------------------

def extract_resume_text(file_bytes: bytes, filename: str) -> str:
    """Extract plain text from a resume upload.

    Supports .pdf, .docx, .txt, .md. Raises ``ValueError`` for unsupported
    types or unreadable files so the caller can show a clear error rather
    than silently analyzing an empty document.
    """
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return _extract_pdf(file_bytes)
    if name.endswith(".docx"):
        return _extract_docx(file_bytes)
    if name.endswith((".txt", ".md")):
        return file_bytes.decode("utf-8", errors="replace")
    raise ValueError(f"Unsupported resume format: {filename!r}. Use PDF, DOCX, or TXT.")


def _extract_pdf(file_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader  # lazy import
    except ImportError as exc:
        raise ValueError(
            "PDF support requires the 'pypdf' package. Install it or upload a DOCX/TXT resume."
        ) from exc

    reader = PdfReader(io.BytesIO(file_bytes))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF page extraction failed: %s", exc)
    return "\n".join(pages).strip()


def _extract_docx(file_bytes: bytes) -> str:
    try:
        import docx  # python-docx, lazy import
    except ImportError as exc:
        raise ValueError(
            "DOCX support requires 'python-docx'. Install it or upload a PDF/TXT resume."
        ) from exc

    document = docx.Document(io.BytesIO(file_bytes))
    return "\n".join(p.text for p in document.paragraphs).strip()


# ---------------------------------------------------------------------------
# 2. Deterministic keyword match
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*")


def _normalize_skill(skill: str) -> str:
    """Lowercase and strip surrounding noise like '(Expert level)'."""
    base = re.sub(r"\([^)]*\)", "", skill).strip().lower()
    base = re.sub(r"\s+", " ", base)
    return base


def _skill_present(skill: str, resume_text_lower: str) -> bool:
    """Whole-token match for a skill in the resume.

    Boundary class includes ``+`` and ``#`` so a search for "C" doesn't match
    inside "C#" / "C++", and a search for "C++" doesn't match inside a
    hypothetical "C+++". ``.`` is intentionally NOT a boundary char so normal
    sentence punctuation ("…in Python.") doesn't block a real match.
    """
    skill = _normalize_skill(skill)
    if not skill:
        return False
    pattern = r"(?<![A-Za-z0-9+#])" + re.escape(skill) + r"(?![A-Za-z0-9+#])"
    return re.search(pattern, resume_text_lower) is not None


def ats_keyword_match(
    resume_text: str,
    required_skills: Iterable[str],
) -> Dict[str, object]:
    """Return matched / missing required skills and an overlap score 0-100."""
    resume_lower = resume_text.lower()
    required = [s for s in (required_skills or []) if s and isinstance(s, str)]

    matched: List[str] = []
    missing: List[str] = []
    for skill in required:
        (matched if _skill_present(skill, resume_lower) else missing).append(skill)

    score = round(100 * len(matched) / len(required)) if required else 0
    return {
        "score": score,
        "matched": matched,
        "missing": missing,
        "required_count": len(required),
    }


# ---------------------------------------------------------------------------
# 3. ATS format checks
# ---------------------------------------------------------------------------

def ats_format_checks(resume_text: str) -> List[str]:
    """Return a list of ATS-unfriendly patterns detected in the resume text."""
    issues: List[str] = []
    if not resume_text.strip():
        return ["Resume text is empty — ATS will read nothing."]

    # Tabs / pipe-heavy lines often indicate tables that ATS parsers mangle.
    lines = resume_text.splitlines()
    pipey = sum(1 for line in lines if line.count("|") >= 3)
    if pipey >= 2:
        issues.append("Multiple pipe-separated lines detected — tables often fail in ATS parsers.")

    if any("\t\t" in line for line in lines):
        issues.append("Multi-tab columns detected — column layouts often fail in ATS parsers.")

    # Excessive ALL-CAPS body lines (headers are fine; many in a row aren't).
    caps_lines = [
        line for line in lines
        if len(line) > 20 and line == line.upper() and any(c.isalpha() for c in line)
    ]
    if len(caps_lines) >= 3:
        issues.append("Many ALL-CAPS body lines — consider sentence case for readability and ATS.")

    # Common non-text markers smuggled into copy-pasted PDFs.
    if "" in resume_text or "" in resume_text:
        issues.append("Non-text glyphs detected — likely icons/decorations that ATS can't read.")

    word_count = len(_WORD_RE.findall(resume_text))
    if word_count < 150:
        issues.append(f"Resume is very short ({word_count} words) — ATS may rank it lower.")
    elif word_count > 1500:
        issues.append(f"Resume is very long ({word_count} words) — consider trimming.")

    return issues


# ---------------------------------------------------------------------------
# 4. Full resume analysis (deterministic score + LLM gap commentary)
# ---------------------------------------------------------------------------

def analyze_resume(
    resume_text: str,
    job_posting: str,
    required_skills: Iterable[str],
    model: str = "detailed",
) -> Dict[str, object]:
    """Produce the structured ATS analysis used by the report generator."""
    keyword_match = ats_keyword_match(resume_text, required_skills)
    format_issues = ats_format_checks(resume_text)

    # Cache the LLM commentary on (resume, posting, model) — the deterministic
    # parts are cheap and recomputed.
    cache_key = f"resume_gap_{hash(resume_text)}_{hash(job_posting)}_{model}"
    commentary: Optional[str] = cache.get(cache_key)
    if commentary is None:
        prompt = f"""
You are an ATS-optimization coach. Compare the candidate's resume against the
job posting and produce a concise markdown briefing.

Required skills already detected in the resume (deterministic): {keyword_match['matched']}
Required skills NOT detected in the resume (deterministic): {keyword_match['missing']}
Detected ATS formatting issues: {format_issues}

Job posting:
{wrap_untrusted(job_posting, "job_posting")}

Resume:
{wrap_untrusted(resume_text, "resume")}

Produce these sections (markdown):
1. **Overall fit** — one paragraph, grounded in the deterministic match above.
2. **Strengths to lead with** — 3-5 bullets the candidate should emphasize.
3. **Gaps and how to address them** — for each missing required skill, one
   concrete action (transferable experience to highlight, course to take,
   project to add). Do not invent skills the candidate doesn't have.
4. **Resume changes** — concrete edits (re-phrasings, sections to add/cut)
   that would improve the ATS score, including formatting fixes.

Rules:
- Do not fabricate facts about the candidate.
- Do not output `<think>` blocks or markdown code fences around the whole report.
"""
        commentary = get_completion(prompt, model)
        cache.set(cache_key, commentary)

    return {
        "ats_score": keyword_match["score"],
        "matched_skills": keyword_match["matched"],
        "missing_skills": keyword_match["missing"],
        "format_issues": format_issues,
        "commentary": commentary,
    }
