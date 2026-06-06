"""SQLAlchemy models.

Schema kept narrow on purpose: just enough to persist users and the
applications they've analyzed/tracked. The full analysis blob lives in
``Application.analysis_json`` so we can render historical reports without a
new LLM call.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# Canonical application status values. Free-text would be tempting but breaks
# filtering and analytics later.
APPLICATION_STATUSES = (
    "saved",       # analyzed but not yet applied
    "applied",
    "interviewing",
    "offer",
    "rejected",
    "withdrawn",
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    applications: Mapped[list["Application"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class PasswordResetToken(Base):
    """Short-lived password-reset token.

    We store the bcrypt hash of the token, never the raw value, so a DB leak
    can't be turned into account takeovers.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="reset_tokens")


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    job_title: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(255))

    status: Mapped[str] = mapped_column(String(32), default="saved", nullable=False)
    verdict: Mapped[Optional[str]] = mapped_column(String(64))
    verdict_light: Mapped[Optional[str]] = mapped_column(String(16))
    ats_score: Mapped[Optional[int]] = mapped_column(Integer)

    notes: Mapped[Optional[str]] = mapped_column(Text)
    # Full result dict (job_details, company_analysis, salary_analysis,
    # resume_analysis, final_report, verdict) — JSON column works on both
    # SQLite and Postgres.
    analysis_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="applications")
    stages: Mapped[list["ApplicationStage"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="ApplicationStage.occurred_on, ApplicationStage.id",
    )

    __table_args__ = (
        # Quick "my applications, newest first" filter.
        Index("ix_applications_user_created", "user_id", "created_at"),
        # We don't enforce uniqueness on (user, company, title) — a user may
        # re-analyze the same posting and we want both records.
    )


# Canonical pipeline stages. Order here = funnel order for analytics. The
# terminal stages (rejected/withdrew/ghosted) close an application but don't
# advance the funnel; ``offer_accepted`` is the success terminal.
PIPELINE_STAGES = (
    "applied",
    "recruiter_screen",
    "phone_screen",
    "technical_interview",
    "take_home",
    "onsite",
    "offer_received",
    "offer_accepted",
)
TERMINAL_NEGATIVE_STAGES = ("rejected", "withdrew", "ghosted")
ALL_STAGE_KINDS = PIPELINE_STAGES + TERMINAL_NEGATIVE_STAGES


class ApplicationStage(Base):
    """One milestone in an application's lifecycle.

    Each row is an *event that happened*: ``applied`` on a date, ``phone_screen``
    on another, ``rejected`` on another. Multiple events of the same kind are
    fine (e.g. several technical interviews) — sort order is by ``occurred_on``.
    """

    __tablename__ = "application_stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # For terminal stages (rejected/withdrew/ghosted): which pipeline stage was
    # the application in when it ended? Useful for "rejected after onsite" stats.
    at_pipeline_stage: Mapped[Optional[str]] = mapped_column(String(32))

    # Optional structured payload — captures, e.g., a verbatim recruiter
    # feedback quote, the offer compensation, or a rejection reason — without
    # forcing a single rigid shape across stages.
    extra: Mapped[Optional[dict]] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    application: Mapped["Application"] = relationship(back_populates="stages")
