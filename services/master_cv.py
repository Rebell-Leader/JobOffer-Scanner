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
from typing import List, Optional

from sqlalchemy import desc, select

from db.models import MasterCV, MasterCVRevision
from db.session import get_session
from services._ownership import require_owned
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


@dataclass
class RevisionRecord:
    id: int
    user_id: int
    raw_text: str
    structured: Optional[dict]
    reason: Optional[str]
    created_at: datetime


def _to_record(cv: MasterCV) -> MasterCVRecord:
    return MasterCVRecord(
        user_id=cv.user_id,
        raw_text=cv.raw_text,
        structured=cv.structured,
        created_at=cv.created_at,
        updated_at=cv.updated_at,
    )


def _to_revision(rev: MasterCVRevision) -> RevisionRecord:
    return RevisionRecord(
        id=rev.id,
        user_id=rev.user_id,
        raw_text=rev.raw_text,
        structured=rev.structured,
        reason=rev.reason,
        created_at=rev.created_at,
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
    reason: Optional[str] = None,
) -> MasterCVRecord:
    """Create-or-update the user's master CV.

    Setting ``structured`` to None leaves the existing structured projection
    alone (so a text edit doesn't wipe a previous parse); pass an explicit
    empty dict to clear it.

    On UPDATE, the previous version is snapshotted as a ``MasterCVRevision``
    *only if the content actually changed* — purely-metadata calls (e.g. a
    re-parse that returns the same structured payload) don't pollute history.
    ``reason`` is a short tag stored on the snapshot (``manual edit``,
    ``parsed``, ``skill added``, ``restored from #N``, etc.).
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
            content_changed = (
                raw_text != cv.raw_text
                or (structured is not None and structured != cv.structured)
            )
            if content_changed:
                # Snapshot the prior version BEFORE mutating.
                session.add(
                    MasterCVRevision(
                        master_cv_id=cv.id,
                        user_id=user_id,
                        raw_text=cv.raw_text,
                        structured=cv.structured,
                        reason=reason or "manual edit",
                    )
                )
            cv.raw_text = raw_text
            if structured is not None:
                cv.structured = structured
        session.commit()
        session.refresh(cv)
        return _to_record(cv)


def list_revisions(user_id: int) -> List[RevisionRecord]:
    """All saved revisions for the user, newest first."""
    with get_session() as session:
        rows = session.execute(
            select(MasterCVRevision)
            .where(MasterCVRevision.user_id == user_id)
            .order_by(desc(MasterCVRevision.created_at))
        ).scalars().all()
        return [_to_revision(r) for r in rows]


def restore_revision(user_id: int, revision_id: int) -> MasterCVRecord:
    """Promote a saved revision to the active master CV.

    Saves the *current* version as a new snapshot first (so restore is itself
    reversible), then writes the revision's content as the current master CV.
    """
    with get_session() as session:
        rev = require_owned(session, MasterCVRevision, revision_id, user_id, MasterCVError, "Revision not found.")
        cv = session.execute(
            select(MasterCV).where(MasterCV.user_id == user_id)
        ).scalar_one_or_none()
        if cv is None:
            raise MasterCVError("No active master CV to restore over.")
        # Snapshot the current as "before-restore" so the user can undo.
        session.add(
            MasterCVRevision(
                master_cv_id=cv.id,
                user_id=user_id,
                raw_text=cv.raw_text,
                structured=cv.structured,
                reason=f"before restore of #{revision_id}",
            )
        )
        cv.raw_text = rev.raw_text
        cv.structured = rev.structured
        session.commit()
        session.refresh(cv)
        return _to_record(cv)


def delete_revision(user_id: int, revision_id: int) -> None:
    with get_session() as session:
        rev = require_owned(session, MasterCVRevision, revision_id, user_id, MasterCVError, "Revision not found.")
        session.delete(rev)
        session.commit()


def diff_revision_against_current(user_id: int, revision_id: int) -> str:
    """Return a unified diff: ``revision_id`` -> current master CV.

    The "before" side is the revision (the older state); the "after" side is
    the current master CV. Empty string when they're identical. Ownership-
    checked: a revision owned by another user raises ``MasterCVError``.
    """
    from utils.diff import unified_diff  # local import — keeps the module light

    with get_session() as session:
        rev = require_owned(session, MasterCVRevision, revision_id, user_id, MasterCVError, "Revision not found.")
        cv = session.execute(
            select(MasterCV).where(MasterCV.user_id == user_id)
        ).scalar_one_or_none()
        if cv is None:
            raise MasterCVError("No active master CV to diff against.")
        return unified_diff(
            rev.raw_text, cv.raw_text,
            before_label=f"revision #{rev.id}",
            after_label="current",
        )


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

    save_master_cv(user_id, cv.raw_text, structured=structured, reason="parsed")
    return structured
