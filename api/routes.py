"""REST endpoints — keeps the route code in one file for now.

Endpoint catalogue (all under ``/v1``):

  * ``GET /me`` — returns the authenticated user's id + email.
  * ``POST /analyze`` — run the analysis pipeline synchronously.
  * ``GET /applications`` — list saved applications.
  * ``GET /applications/{id}`` — fetch one.
  * ``DELETE /applications/{id}`` — delete one.
  * ``GET /cv`` — fetch the master CV raw text.
  * ``PUT /cv`` — replace the master CV raw text.
  * ``GET /analytics`` — dashboard payload.

All endpoints require ``Authorization: Bearer jos_…``.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.auth import require_user
from services.analysis_runner import check_user_quota, run_analysis_sync
from services.analytics import compute_dashboard
from services.applications import (
    ApplicationError,
    delete_application,
    get_application,
    list_applications,
    save_analysis,
)
from services.auth import get_user
from services.master_cv import MasterCVError, get_master_cv, save_master_cv
from services.rate_limit import RateLimitExceeded
from services.usage import BudgetExceeded

router = APIRouter(prefix="/v1")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class MeResponse(BaseModel):
    user_id: int
    email: str


class AnalyzeRequest(BaseModel):
    job_posting: str = Field(..., min_length=1)
    company_name: Optional[str] = None
    job_title: Optional[str] = None
    location: Optional[str] = None
    compensation: Optional[str] = None
    resume_text: Optional[str] = None
    model: str = "detailed"
    save: bool = False
    save_status: str = "saved"
    save_notes: Optional[str] = None


class AnalyzeResponse(BaseModel):
    saved_application_id: Optional[int] = None
    verdict: Optional[dict] = None
    final_report: Optional[str] = None
    job_details: Optional[dict] = None
    company_analysis: Optional[dict] = None
    salary_analysis: Optional[dict] = None
    resume_analysis: Optional[dict] = None


class ApplicationListItem(BaseModel):
    id: int
    company_name: str
    job_title: str
    location: Optional[str]
    status: str
    verdict: Optional[str]
    verdict_light: Optional[str]
    ats_score: Optional[int]


class ApplicationFull(ApplicationListItem):
    notes: Optional[str]
    analysis_json: dict
    created_at: str
    updated_at: str


class MasterCVResponse(BaseModel):
    raw_text: Optional[str]
    updated_at: Optional[str]


class MasterCVUpdate(BaseModel):
    raw_text: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# /me
# ---------------------------------------------------------------------------

@router.get("/me", response_model=MeResponse)
def get_me(user_id: int = Depends(require_user)) -> MeResponse:
    user = get_user(user_id)
    if user is None:
        # Token resolved to a user that no longer exists — surface 401 so
        # API clients re-authenticate cleanly.
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    return MeResponse(user_id=user.id, email=user.email)


# ---------------------------------------------------------------------------
# /analyze
# ---------------------------------------------------------------------------

@router.post("/analyze", response_model=AnalyzeResponse)
def post_analyze(
    body: AnalyzeRequest, user_id: int = Depends(require_user),
) -> AnalyzeResponse:
    manual = {
        "company_name": body.company_name or "",
        "job_title": body.job_title or "",
        "location": body.location or "",
        "compensation": body.compensation or "",
    }

    # Enforce the same cost controls as the web/worker paths BEFORE any tokens
    # are spent: request-count rate limit + rolling spend budget. Map to the
    # conventional HTTP statuses (429 too-many, 402 payment-required).
    try:
        check_user_quota(user_id)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except BudgetExceeded as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    # run_analysis_sync opens the usage-accounting scope so this run's LLM
    # tokens/cost are attributed to the caller (the direct run_analysis call
    # would ledger them with a NULL owner and skip the budget).
    result = run_analysis_sync(
        body.job_posting,
        manual_inputs=manual,
        model=body.model,
        resume_text=body.resume_text,
        user_id=user_id,
    )

    if result.get("error"):
        raise HTTPException(status_code=502, detail=result["error"])

    saved_id: Optional[int] = None
    if body.save:
        # Backfill manual fields from extraction so save_analysis (which
        # requires company + title) doesn't reject.
        extracted = (result.get("job_details") or {}).get("extracted_details") or {}
        for field in ("company_name", "job_title", "location", "compensation"):
            if not manual.get(field):
                manual[field] = extracted.get(field) or ""
        try:
            rec = save_analysis(
                user_id, manual, result,
                status=body.save_status, notes=body.save_notes,
            )
            saved_id = rec.id
        except ApplicationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Strip non-serialisable cruft (progress_callback) before responding.
    safe_result = {k: v for k, v in result.items() if not callable(v)}
    return AnalyzeResponse(
        saved_application_id=saved_id,
        verdict=safe_result.get("verdict"),
        final_report=safe_result.get("final_report"),
        job_details=safe_result.get("job_details"),
        company_analysis=safe_result.get("company_analysis"),
        salary_analysis=safe_result.get("salary_analysis"),
        resume_analysis=safe_result.get("resume_analysis"),
    )


# ---------------------------------------------------------------------------
# /applications
# ---------------------------------------------------------------------------

@router.get("/applications", response_model=list[ApplicationListItem])
def get_applications(user_id: int = Depends(require_user)):
    rows = list_applications(user_id)
    return [
        ApplicationListItem(
            id=r.id, company_name=r.company_name, job_title=r.job_title,
            location=r.location, status=r.status, verdict=r.verdict,
            verdict_light=r.verdict_light, ats_score=r.ats_score,
        )
        for r in rows
    ]


@router.get("/applications/{application_id}", response_model=ApplicationFull)
def get_one_application(
    application_id: int, user_id: int = Depends(require_user),
):
    try:
        r = get_application(user_id, application_id)
    except ApplicationError:
        raise HTTPException(status_code=404, detail="Not found.")
    return ApplicationFull(
        id=r.id, company_name=r.company_name, job_title=r.job_title,
        location=r.location, status=r.status, verdict=r.verdict,
        verdict_light=r.verdict_light, ats_score=r.ats_score,
        notes=r.notes, analysis_json=r.analysis_json,
        created_at=r.created_at.isoformat(),
        updated_at=r.updated_at.isoformat(),
    )


@router.delete(
    "/applications/{application_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_one_application(
    application_id: int, user_id: int = Depends(require_user),
):
    try:
        delete_application(user_id, application_id)
    except ApplicationError:
        raise HTTPException(status_code=404, detail="Not found.")
    return None


# ---------------------------------------------------------------------------
# /cv
# ---------------------------------------------------------------------------

@router.get("/cv", response_model=MasterCVResponse)
def get_cv(user_id: int = Depends(require_user)):
    cv = get_master_cv(user_id)
    if cv is None:
        return MasterCVResponse(raw_text=None, updated_at=None)
    return MasterCVResponse(
        raw_text=cv.raw_text, updated_at=cv.updated_at.isoformat(),
    )


@router.put("/cv", response_model=MasterCVResponse)
def put_cv(body: MasterCVUpdate, user_id: int = Depends(require_user)):
    try:
        cv = save_master_cv(user_id, body.raw_text)
    except MasterCVError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MasterCVResponse(
        raw_text=cv.raw_text, updated_at=cv.updated_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# /analytics
# ---------------------------------------------------------------------------

@router.get("/analytics")
def get_analytics(user_id: int = Depends(require_user)):
    dash = compute_dashboard(user_id)
    return {
        "overview": asdict(dash.overview),
        "funnel": [asdict(r) for r in dash.funnel],
        "time_in_stage": [asdict(t) for t in dash.time_in_stage],
        "verdict_outcomes": [asdict(v) for v in dash.verdict_outcomes],
        "rejection_stage_distribution": dash.rejection_stage_distribution,
        "volume_by_week": dash.volume_by_week,
    }
