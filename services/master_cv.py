"""Master CV — the user's source-of-truth document.

Stored as raw text (always). Optional structured projection (Summary / Skills /
Experience / Education / Certifications) can be derived on demand via the LLM
when the user opts in; it's a convenience for the UI, not a requirement for
tailoring. Tailoring reads from ``raw_text`` directly so it can never miss a
fact a parse step happened to drop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from db.models import MasterCV
from db.session import get_session
from tools.resume_tools import extract_resume_text  # PDF/DOCX/TXT parser
from utils.llm import get_completion
from utils.security import wrap_untrusted


class MasterCVError(ValueError):
    """User-facing failure (missing CV, parse error, etc.)."""


@dataclass
class MasterCVRecord:
    user_id: int
    raw_text: str
    structured: Optional[dict]
    created_at: datetime
    updated_at: datetime


def _to_record(cv: MasterCV) -> MasterCVRecord:
    return MasterCVRecord(
        user_id=cv.user_id,
        raw_text=cv.raw_text,
        structured=cv.structured,
        created_at=cv.created_at,
        updated_at=cv.updated_at,
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def get_master_cv(user_id: int) -> Optional[MasterCVRecord]:
    with get_session() as session:
        cv = session.execute(
            select(MasterCV).where(MasterCV.user_id == user_id)
        ).scalar_one_or_none()
        return _to_record(cv) if cv else None


def save_master_cv(
    user_id: int,
    raw_text: str,
    structured: Optional[dict] = None,
) -> MasterCVRecord:
    """Create-or-update the user's master CV.

    Setting ``structured`` to None leaves the existing structured projection
    alone (so a text edit doesn't wipe a previous parse); pass an explicit
    empty dict to clear it.
    """
    raw_text = (raw_text or "").strip()
    if not raw_text:
        raise MasterCVError("Master CV cannot be empty.")

    with get_session() as session:
        cv = session.execute(
            select(MasterCV).where(MasterCV.user_id == user_id)
        ).scalar_one_or_none()
        if cv is None:
            cv = MasterCV(
                user_id=user_id,
                raw_text=raw_text,
                structured=structured,
            )
            session.add(cv)
        else:
            cv.raw_text = raw_text
            if structured is not None:
                cv.structured = structured
        session.commit()
        session.refresh(cv)
        return _to_record(cv)


def save_master_cv_from_upload(
    user_id: int, file_bytes: bytes, filename: str
) -> MasterCVRecord:
    """Convenience: parse a PDF/DOCX/TXT upload and save its text."""
    text = extract_resume_text(file_bytes, filename)
    return save_master_cv(user_id, text)


def delete_master_cv(user_id: int) -> None:
    with get_session() as session:
        cv = session.execute(
            select(MasterCV).where(MasterCV.user_id == user_id)
        ).scalar_one_or_none()
        if cv is not None:
            session.delete(cv)
            session.commit()


# ---------------------------------------------------------------------------
# Optional LLM parse — turns the raw text into a structured projection
# ---------------------------------------------------------------------------

_PARSE_PROMPT = """\
Parse this CV into STRICT JSON. Do NOT invent any field — if something isn't
in the text, set the field to null or an empty list.

CV text:
{cv_wrapped}

Return JSON with EXACTLY these keys:
{{
  "name": "...",
  "headline": "...",
  "summary": "...",
  "skills": ["..."],
  "experience": [
    {{"title":"...","company":"...","start":"...","end":"...","bullets":["..."]}}
  ],
  "education": [
    {{"degree":"...","institution":"...","year":"..."}}
  ],
  "certifications": ["..."],
  "links": ["..."]
}}

Rules:
- Use only what's in the CV. Do not invent dates, employers, or degrees.
- Output JSON ONLY. No prose, no markdown fences.
"""


def parse_master_cv(user_id: int, model: str = "detailed") -> dict:
    """Parse the saved master CV into a structured dict and persist it.

    Raises ``MasterCVError`` if no CV is saved or parsing fails.
    """
    cv = get_master_cv(user_id)
    if cv is None:
        raise MasterCVError("No master CV saved yet.")

    prompt = _PARSE_PROMPT.format(cv_wrapped=wrap_untrusted(cv.raw_text, "master_cv"))
    response = get_completion(prompt, model)
    # The model occasionally wraps JSON in a markdown fence even when told not to.
    text = response.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()

    try:
        structured = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MasterCVError(f"Could not parse model response as JSON: {exc}") from exc

    save_master_cv(user_id, cv.raw_text, structured=structured)
    return structured
