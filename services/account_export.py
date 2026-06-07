"""GDPR-style "export all my data" — a single JSON bundle of everything a
user owns. Secrets (password hash, TOTP secret, token hashes, webhook
secrets) are deliberately excluded — this is the user's *data*, not their
credentials.

Reuses the existing per-domain list functions so the export stays correct as
those evolve. Returns a JSON string ready for a download button.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from services.audit import record as _audit


def _default(o: Any):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    return str(o)


def build_export(user_id: int) -> dict:
    """Assemble the full data bundle for ``user_id`` as a plain dict."""
    from services.api_tokens import list_for_user as list_tokens
    from services.applications import list_applications
    from services.auth import get_user
    from services.master_cv import get_master_cv, list_revisions
    from services.projects import list_projects
    from services.sharing import list_shares_for_application
    from services.stages import list_stages
    from services.tailoring import list_artifacts
    from services.telegram_link import get_link
    from services.webhooks import list_webhooks

    user = get_user(user_id)
    bundle: dict = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "account": {
            "user_id": user.id if user else user_id,
            "email": user.email if user else None,
        },
        "applications": [],
        "master_cv": None,
        "master_cv_revisions": [],
        "projects": [],
        "api_tokens": [],          # metadata only (no secret)
        "webhooks": [],            # metadata only (no secret)
        "telegram_link": None,
    }

    for app in list_applications(user_id):
        entry = {
            "id": app.id,
            "company_name": app.company_name,
            "job_title": app.job_title,
            "location": app.location,
            "status": app.status,
            "verdict": app.verdict,
            "verdict_light": app.verdict_light,
            "ats_score": app.ats_score,
            "notes": app.notes,
            "created_at": app.created_at,
            "updated_at": app.updated_at,
            "analysis": app.analysis_json,
            "stages": [
                {"kind": s.kind, "occurred_on": s.occurred_on,
                 "notes": s.notes, "at_pipeline_stage": s.at_pipeline_stage}
                for s in list_stages(user_id, app.id)
            ],
            "artifacts": [
                {"id": a.id, "kind": a.kind, "content": a.content,
                 "created_at": a.created_at, "meta": a.meta}
                for a in list_artifacts(user_id, app.id)
            ],
            "shares": [
                {"id": sh.id, "token": sh.token, "expires_at": sh.expires_at,
                 "revoked_at": sh.revoked_at, "view_count": sh.view_count,
                 "include_artifacts": sh.include_artifacts}
                for sh in list_shares_for_application(user_id, app.id)
            ],
        }
        bundle["applications"].append(entry)

    cv = get_master_cv(user_id)
    if cv is not None:
        bundle["master_cv"] = {
            "raw_text": cv.raw_text,
            "structured": cv.structured,
            "updated_at": cv.updated_at,
        }
        bundle["master_cv_revisions"] = [
            {"id": r.id, "reason": r.reason, "raw_text": r.raw_text,
             "created_at": r.created_at}
            for r in list_revisions(user_id)
        ]

    bundle["projects"] = [
        {"id": p.id, "title": p.title, "role": p.role, "tech_stack": p.tech_stack,
         "summary": p.summary, "highlights": p.highlights, "url": p.url}
        for p in list_projects(user_id)
    ]

    bundle["api_tokens"] = [
        {"id": t.id, "name": t.name, "prefix": t.prefix,
         "created_at": t.created_at, "last_used_at": t.last_used_at,
         "expires_at": t.expires_at, "revoked_at": t.revoked_at}
        for t in list_tokens(user_id)
    ]

    bundle["webhooks"] = [
        {"id": w.id, "url": w.url, "events": w.events, "active": w.active,
         "created_at": w.created_at}
        for w in list_webhooks(user_id)
    ]

    link = get_link(user_id)
    if link is not None:
        bundle["telegram_link"] = {
            "chat_id": link.chat_id,
            "chat_username": link.chat_username,
            "notify_on_stage": link.notify_on_stage,
            "inactive_reminder_days": link.inactive_reminder_days,
        }

    return bundle


def export_json(user_id: int) -> str:
    """Return the user's full data bundle as an indented JSON string."""
    bundle = build_export(user_id)
    _audit("user.account.export", user_id=user_id)
    return json.dumps(bundle, indent=2, default=_default, ensure_ascii=False)
