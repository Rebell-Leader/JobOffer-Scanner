"""Bulk import of projects and past applications.

Two ingest paths per kind:

  * **Structured** — a CSV with predictable headers parsed deterministically.
    Used when the user already has a clean export (their own backup, or a
    CSV the app produced).
  * **Free-form** — paste anything, the LLM parses it into the same shape.
    Used when the user just has a portfolio dump or a list of "places I
    applied to".

Both paths return *preview* dicts that the caller renders for the user to
edit / approve before calling the corresponding ``save_*`` function. We
intentionally never persist directly from an LLM parse — the user is the
final arbiter of what lands in their data.

The "no fabrication" discipline from Phase 9/10 carries over: the LLM is
told that any field not present in the input must be ``null`` / omitted.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date as date_cls
from datetime import datetime
from typing import List, Optional

from db.models import APPLICATION_STATUSES
from services.applications import save_analysis
from services.projects import ProjectRecord, create_project
from services.stages import add_stage
from utils.llm import get_completion
from utils.security import wrap_untrusted
from utils.text import strip_code_fence


class BulkImportError(ValueError):
    """User-facing failure (bad CSV, model returned non-JSON, etc.)."""


# ---------------------------------------------------------------------------
# Projects — CSV path
# ---------------------------------------------------------------------------

_PROJECT_CSV_FIELDS = ("title", "role", "tech_stack", "summary", "highlights", "url")


def parse_projects_csv(csv_text: str) -> List[dict]:
    """Parse a project CSV into preview dicts. Required header: ``title``."""
    if not (csv_text or "").strip():
        return []
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
    except csv.Error as exc:
        raise BulkImportError(f"CSV parse failed: {exc}") from exc
    if not reader.fieldnames or "title" not in reader.fieldnames:
        raise BulkImportError(
            "CSV must include a 'title' column. Other supported columns: "
            + ", ".join(_PROJECT_CSV_FIELDS)
        )
    out = []
    for row in reader:
        title = (row.get("title") or "").strip()
        if not title:
            continue
        out.append(
            {
                "title": title,
                "role": (row.get("role") or "").strip() or None,
                "tech_stack": (row.get("tech_stack") or "").strip() or None,
                "summary": (row.get("summary") or "").strip() or None,
                "highlights": _split_highlights(row.get("highlights")),
                "url": (row.get("url") or "").strip() or None,
            }
        )
    return out


def _split_highlights(value) -> List[str]:
    if not value:
        return []
    # Allow either newline-separated or pipe-separated bullets in a CSV cell.
    parts = re.split(r"\n|\s*\|\s*", value)
    return [p.strip(" -•\t") for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# Projects — LLM free-form path
# ---------------------------------------------------------------------------

_PROJECT_PROMPT = """\
Parse the projects described in the text below into STRICT JSON.

CRITICAL: Do NOT invent any project, technology, or fact not present in the
text. If a project lacks a field, set that field to null. Return ONLY the JSON
array, no prose, no markdown code fences.

Schema (one entry per project):
[
  {{
    "title": "...",
    "role": "..." | null,
    "tech_stack": "..." | null,
    "summary": "..." | null,
    "highlights": ["..."],
    "url": "..." | null
  }}
]

Input:
{wrapped}
"""


def parse_projects_freeform(text: str, model: str = "detailed") -> List[dict]:
    """Use the LLM to extract projects from a free-form paste."""
    if not (text or "").strip():
        return []
    prompt = _PROJECT_PROMPT.format(wrapped=wrap_untrusted(text, "projects_dump"))
    response = get_completion(prompt, model)
    parsed = _strict_json_list(response)
    out = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        out.append(
            {
                "title": title,
                "role": (entry.get("role") or None) or None,
                "tech_stack": (entry.get("tech_stack") or None) or None,
                "summary": (entry.get("summary") or None) or None,
                "highlights": _coerce_str_list(entry.get("highlights")),
                "url": (entry.get("url") or None) or None,
            }
        )
    return out


def save_projects(user_id: int, previews: List[dict]) -> List[ProjectRecord]:
    """Persist a list of preview dicts (typically after the user approved them)."""
    saved: List[ProjectRecord] = []
    for p in previews:
        saved.append(
            create_project(
                user_id,
                title=p["title"],
                role=p.get("role"),
                tech_stack=p.get("tech_stack"),
                summary=p.get("summary"),
                highlights=p.get("highlights"),
                url=p.get("url"),
            )
        )
    return saved


# ---------------------------------------------------------------------------
# Applications — CSV path
# ---------------------------------------------------------------------------

_APPLICATION_CSV_FIELDS = (
    "company_name", "job_title", "location", "applied_on",
    "status", "verdict", "notes",
)


def parse_applications_csv(csv_text: str) -> List[dict]:
    """Parse an application CSV. Required headers: ``company_name``, ``job_title``."""
    if not (csv_text or "").strip():
        return []
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames or not {"company_name", "job_title"}.issubset(reader.fieldnames):
        raise BulkImportError(
            "CSV must include 'company_name' and 'job_title' columns. Other "
            "supported columns: " + ", ".join(_APPLICATION_CSV_FIELDS)
        )
    out = []
    for row in reader:
        company = (row.get("company_name") or "").strip()
        title = (row.get("job_title") or "").strip()
        if not company or not title:
            continue
        status = (row.get("status") or "").strip() or "applied"
        if status not in APPLICATION_STATUSES:
            # Don't reject the whole row — fall back to a safe default so
            # imports from other tools' free-form statuses still work.
            status = "applied"
        out.append(
            {
                "company_name": company,
                "job_title": title,
                "location": (row.get("location") or "").strip() or None,
                "applied_on": _parse_date(row.get("applied_on")),
                "status": status,
                "verdict": (row.get("verdict") or "").strip() or None,
                "notes": (row.get("notes") or "").strip() or None,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Applications — LLM free-form path
# ---------------------------------------------------------------------------

_APPLICATION_PROMPT = """\
Parse the job applications described in the text below into STRICT JSON.

CRITICAL: Do NOT invent any application, employer, title, or date not
present in the text. If a field isn't in the text, set it to null. Return
ONLY the JSON array, no prose, no markdown code fences.

Each entry must have at minimum a company_name and job_title.

Schema:
[
  {{
    "company_name": "...",
    "job_title": "...",
    "location": "..." | null,
    "applied_on": "YYYY-MM-DD" | null,
    "status": "saved" | "applied" | "interviewing" | "offer" | "rejected" | "withdrawn",
    "verdict": "..." | null,
    "notes": "..." | null
  }}
]

Input:
{wrapped}
"""


def parse_applications_freeform(text: str, model: str = "detailed") -> List[dict]:
    if not (text or "").strip():
        return []
    prompt = _APPLICATION_PROMPT.format(wrapped=wrap_untrusted(text, "applications_dump"))
    response = get_completion(prompt, model)
    parsed = _strict_json_list(response)
    out = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        company = (entry.get("company_name") or "").strip()
        title = (entry.get("job_title") or "").strip()
        if not company or not title:
            continue
        status = (entry.get("status") or "applied").strip()
        if status not in APPLICATION_STATUSES:
            status = "applied"
        out.append(
            {
                "company_name": company,
                "job_title": title,
                "location": (entry.get("location") or None) or None,
                "applied_on": _parse_date(entry.get("applied_on")),
                "status": status,
                "verdict": (entry.get("verdict") or None) or None,
                "notes": (entry.get("notes") or None) or None,
            }
        )
    return out


# How an imported "status" maps to the stage events we create. The first
# stage in the list is ``applied`` on the user-supplied applied_on date; the
# second (when present) is on the same date as a best-effort approximation
# of "this is where the application currently sits". Users can refine the
# stage timeline later.
_IMPORT_STAGE_PLAN = {
    "saved": (),
    "applied": ("applied",),
    "interviewing": ("applied", "phone_screen"),
    "offer": ("applied", "offer_received"),
    "rejected": ("applied", "rejected"),
    "withdrawn": ("applied", "withdrew"),
}


def save_applications(user_id: int, previews: List[dict]) -> List[int]:
    """Persist preview dicts; create stage events matching each imported status.

    The legacy ``Application.status`` field is auto-synced from stages, so we
    must materialise stages that reproduce the user's stated status — adding
    just ``applied`` would silently downgrade an imported ``rejected`` to
    ``applied``. We use the same ``applied_on`` date for the secondary stage
    when we don't have a better one; that's an approximation the user can
    refine later, never a fabricated independent date.

    Returns the saved application ids.
    """
    saved_ids = []
    for p in previews:
        # Build the minimal analysis_json shape that other parts of the app
        # expect (verdict + extracted_details, both with the imported values).
        analysis = {
            "verdict": {
                "verdict": p.get("verdict") or "",
                "light": None,
                "reasons": [],
            },
            "job_details": {
                "extracted_details": {
                    "company_name": p["company_name"],
                    "job_title": p["job_title"],
                    "location": p.get("location") or "",
                },
                "requirements_analysis": {},
            },
            "final_report": "",
            "_imported": True,
        }
        status = p.get("status", "applied")
        rec = save_analysis(
            user_id,
            {
                "company_name": p["company_name"],
                "job_title": p["job_title"],
                "location": p.get("location") or "",
                "compensation": "",
            },
            analysis,
            status=status,
            notes=p.get("notes"),
        )
        applied_on = p.get("applied_on")
        # Materialise stages matching the imported status so auto-status-sync
        # produces the right Application.status.
        if applied_on and status in _IMPORT_STAGE_PLAN:
            for kind in _IMPORT_STAGE_PLAN[status]:
                try:
                    add_stage(user_id, rec.id, kind, occurred_on=applied_on)
                except Exception:  # noqa: BLE001 - import is best-effort
                    pass
        saved_ids.append(rec.id)
    return saved_ids


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strict_json_list(text: str):
    """Parse a JSON array from an LLM response, stripping any code fences."""
    text = strip_code_fence(text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BulkImportError(f"Model did not return valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise BulkImportError("Expected a JSON array; got something else.")
    return parsed


def _coerce_str_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [line.strip(" -•\t") for line in value.splitlines() if line.strip()]
    return []


_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%d %b %Y", "%b %d, %Y")


def _parse_date(value) -> Optional[date_cls]:
    if value is None:
        return None
    if isinstance(value, date_cls):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None  # silent skip — imports stay best-effort, not fragile
