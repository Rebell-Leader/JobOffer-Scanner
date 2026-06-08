"""Project gallery — user-curated portfolio entries.

Tailored CVs may select, reorder, and emphasize these; they may NOT invent new
projects. Each project carries title, role, tech stack, summary, and a list of
highlight bullets. ``highlights`` is stored as JSON so per-bullet metrics
(impact, link) can be added later without a schema change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from sqlalchemy import asc, select

from db.models import Project
from db.session import get_session
from services._ownership import require_owned


class ProjectError(ValueError):
    """User-facing failure (missing title, ownership violation, etc.)."""


@dataclass
class ProjectRecord:
    id: int
    user_id: int
    title: str
    role: Optional[str]
    tech_stack: Optional[str]
    summary: Optional[str]
    highlights: List[str]
    url: Optional[str]
    created_at: datetime
    updated_at: datetime


def _to_record(p: Project) -> ProjectRecord:
    return ProjectRecord(
        id=p.id,
        user_id=p.user_id,
        title=p.title,
        role=p.role,
        tech_stack=p.tech_stack,
        summary=p.summary,
        highlights=list(p.highlights or []),
        url=p.url,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _coerce_highlights(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        # One bullet per non-empty line.
        return [line.strip(" -•\t") for line in value.splitlines() if line.strip()]
    raise ProjectError("Highlights must be a list or newline-separated text.")


def create_project(
    user_id: int,
    title: str,
    role: Optional[str] = None,
    tech_stack: Optional[str] = None,
    summary: Optional[str] = None,
    highlights=None,
    url: Optional[str] = None,
) -> ProjectRecord:
    title = (title or "").strip()
    if not title:
        raise ProjectError("Project title is required.")
    with get_session() as session:
        p = Project(
            user_id=user_id,
            title=title,
            role=(role or "").strip() or None,
            tech_stack=(tech_stack or "").strip() or None,
            summary=(summary or "").strip() or None,
            highlights=_coerce_highlights(highlights),
            url=(url or "").strip() or None,
        )
        session.add(p)
        session.commit()
        session.refresh(p)
        return _to_record(p)


def list_projects(user_id: int) -> List[ProjectRecord]:
    with get_session() as session:
        rows = session.execute(
            select(Project)
            .where(Project.user_id == user_id)
            .order_by(asc(Project.created_at))
        ).scalars().all()
        return [_to_record(r) for r in rows]


def update_project(
    user_id: int,
    project_id: int,
    **fields,
) -> ProjectRecord:
    if "title" in fields:
        new_title = (fields["title"] or "").strip()
        if not new_title:
            raise ProjectError("Project title is required.")
        fields["title"] = new_title
    if "highlights" in fields:
        fields["highlights"] = _coerce_highlights(fields["highlights"])

    with get_session() as session:
        p = require_owned(session, Project, project_id, user_id, ProjectError, "Project not found.")
        for k, v in fields.items():
            if hasattr(p, k):
                # Normalize empty-string strings to None for nullable columns.
                if isinstance(v, str) and k != "title" and not v.strip():
                    v = None
                setattr(p, k, v)
        session.commit()
        session.refresh(p)
        return _to_record(p)


def delete_project(user_id: int, project_id: int) -> None:
    with get_session() as session:
        p = require_owned(session, Project, project_id, user_id, ProjectError, "Project not found.")
        session.delete(p)
        session.commit()


def projects_as_text(user_id: int) -> str:
    """Render every project for inclusion in a tailoring prompt."""
    projects = list_projects(user_id)
    if not projects:
        return "(no projects in gallery)"
    blocks = []
    for p in projects:
        block = [f"### {p.title}"]
        if p.role:
            block.append(f"- Role: {p.role}")
        if p.tech_stack:
            block.append(f"- Tech: {p.tech_stack}")
        if p.summary:
            block.append(f"- Summary: {p.summary}")
        if p.highlights:
            block.append("- Highlights:")
            block.extend(f"  - {h}" for h in p.highlights)
        if p.url:
            block.append(f"- Link: {p.url}")
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)
