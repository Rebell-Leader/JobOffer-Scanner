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
    BigInteger as sa_BigInt,
    Boolean,
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
    two_factor: Mapped[Optional["UserTwoFactor"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    oauth_identities: Mapped[list["OAuthIdentity"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    master_cv: Mapped[Optional["MasterCV"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    projects: Mapped[list["Project"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    telegram_link: Mapped[Optional["TelegramLink"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )


class UserTwoFactor(Base):
    """TOTP-based second factor for one user.

    ``secret`` is the base32 TOTP shared secret. It is stored unencrypted
    here — for production-grade key protection, layer in an envelope-
    encryption step using a KMS-derived key. TOTP is secondary defence;
    the primary protection against credential stuffing is the bcrypt
    password hash, which remains intact under a DB-only compromise.

    ``backup_codes`` is the list of bcrypt-hashed one-time recovery codes
    the user can use instead of an OTP. Used codes are removed from the
    list. The ``verified`` flag tracks the setup ceremony — a row exists
    after ``start_setup`` but ``verified=False`` until the user proves
    they've successfully scanned the secret by entering a live OTP.
    """

    __tablename__ = "user_two_factor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    secret: Mapped[str] = mapped_column(String(64), nullable=False)
    verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    backup_codes: Mapped[Optional[list]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="two_factor")


class OAuthIdentity(Base):
    """Links an external OAuth identity (Google / GitHub) to a local user.

    One user can have multiple identities (sign in with Google AND GitHub
    that both resolve to the same account by email). The (provider,
    provider_user_id) pair is globally unique — an external identity maps to
    exactly one local account.
    """

    __tablename__ = "oauth_identities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="oauth_identities")

    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_oauth_provider_subject"),
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

    # Reminder muting — set to a future date to silence the inactivity
    # notifier for this specific application. The notifier respects this
    # field; the per-user threshold lives on TelegramLink.
    snooze_reminders_until: Mapped[Optional[date]] = mapped_column(Date)

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
    shares: Mapped[list["ApplicationShare"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
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


# ---------------------------------------------------------------------------
# Master CV / project gallery / per-application artifacts
# (tailored CVs + cover letters generated from the user's source-of-truth)
# ---------------------------------------------------------------------------

class MasterCV(Base):
    """The user's long-form source-of-truth CV — every tailored output reads
    from this and is forbidden to add facts it doesn't contain."""

    __tablename__ = "master_cvs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional structured projection (Summary, Skills, Experience, Education,
    # Certifications) — populated by an LLM parse step the user opts into.
    structured: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="master_cv")
    revisions: Mapped[list["MasterCVRevision"]] = relationship(
        back_populates="master_cv",
        cascade="all, delete-orphan",
        order_by="desc(MasterCVRevision.created_at)",
    )


class MasterCVRevision(Base):
    """A snapshot of a previous master-CV version.

    We snapshot before every overwrite so the user can compare and revert. Kept
    deliberately simple — full raw_text + structured + reason, not a diff —
    because tens-of-kilobytes-per-row is fine at the scale of a personal CV.
    """

    __tablename__ = "master_cv_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    master_cv_id: Mapped[int] = mapped_column(
        ForeignKey("master_cvs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalized for fast ownership checks across all revisions.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    structured: Mapped[Optional[dict]] = mapped_column(JSON)
    # Short tag describing the change that triggered the snapshot — "manual
    # edit", "parsed", "skill added", "restored", etc.
    reason: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    master_cv: Mapped["MasterCV"] = relationship(back_populates="revisions")


class Project(Base):
    """One entry in the user's project gallery. Tailored CVs may select +
    reframe these, but must not invent new ones."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Optional[str]] = mapped_column(String(255))
    tech_stack: Mapped[Optional[str]] = mapped_column(String(500))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    highlights: Mapped[Optional[list]] = mapped_column(JSON)
    url: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="projects")


# Artifact kinds the system can generate for a saved application.
ARTIFACT_KINDS = ("tailored_cv", "cover_letter")


class ApplicationArtifact(Base):
    """A generated artifact (tailored CV / cover letter) versioned per
    application. We keep every version so the user can compare and roll back."""

    __tablename__ = "application_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalized for fast per-user listings and ownership checks.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Free-form metadata about how it was generated (model, tone, instruction).
    meta: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_artifacts_app_kind_created", "application_id", "kind", "created_at"),
    )


class ApiToken(Base):
    """A long-lived bearer token for the REST API.

    We never store the raw token — only its bcrypt hash. To make per-request
    auth fast (without bcrypt-ing every row), we also store the first 8 chars
    of the token as a public ``prefix`` column with an index. Auth flow:

      1. Split the incoming token on its first 8 chars.
      2. Look up by ``prefix``. Multiple rows possible in theory (collision
         odds: ~1 in 64^8 ≈ 1 in 2.8e14) so we iterate the matches.
      3. bcrypt-compare the full incoming token against each row's
         ``token_hash``. First match wins.

    A 8-char prefix keeps the rendering UI useful too — the "Active tokens"
    list shows ``jos_AbCdEfGh…`` so the user can recognise which token to
    revoke. ``last_used_at`` updates on successful auth so abandoned tokens
    are easy to spot.
    """

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Webhooks — outbound HMAC-signed POSTs on user-subscribed events
# ---------------------------------------------------------------------------

# The event kinds a webhook can subscribe to. Kept narrow + explicit so the
# subscription UI is a fixed checklist and payload shapes stay documented.
WEBHOOK_EVENTS = (
    "stage.added",
    "application.saved",
    "verdict.changed",
)


class Webhook(Base):
    """A user's outbound webhook subscription.

    ``secret`` signs every delivery (HMAC-SHA256) so the receiver can verify
    authenticity. ``events`` is the JSON list of subscribed event kinds.
    ``active`` lets the owner pause deliveries without losing the config.
    """

    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    secret: Mapped[str] = mapped_column(String(128), nullable=False)
    events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class WebhookDelivery(Base):
    """One delivery attempt record — the receipt log for a webhook POST.

    Persisted so the owner can see what fired, whether it succeeded, and the
    response/error. Failed deliveries can be re-sent via the service.
    """

    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    webhook_id: Mapped[int] = mapped_column(
        ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status_code: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ApplicationShare(Base):
    """A read-only sharing token for one ``Application``.

    Tokens are opaque random strings (URL-safe) and unique. Optional
    ``expires_at`` lets the owner cap how long the link works; ``revoked_at``
    is set when the owner revokes manually. View counters help the owner
    notice unexpected access. ``include_artifacts`` controls whether the
    public view also exposes tailored CVs / cover letters.
    """

    __tablename__ = "application_shares"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    include_artifacts: Mapped[bool] = mapped_column(default=False, nullable=False)
    view_count: Mapped[int] = mapped_column(default=0, nullable=False)
    last_viewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    application: Mapped["Application"] = relationship(back_populates="shares")


# ---------------------------------------------------------------------------
# Audit log — security-sensitive events worth a paper trail
# ---------------------------------------------------------------------------

# Canonical kinds. New entries should be added explicitly (don't accept free
# strings) so the dashboards stay parseable.
AUDIT_KINDS = (
    "user.register",
    "user.login.success",
    "user.login.failure",
    "user.password.change",
    "user.password.reset.request",
    "user.password.reset.complete",
    "user.2fa.enable",
    "user.2fa.disable",
    "user.2fa.verify.success",
    "user.2fa.verify.failure",
    "user.2fa.backup_code.used",
    "application.delete",
    "telegram.bind",
    "telegram.unbind",
    "artifact.delete",
    "share.create",
    "share.revoke",
    "share.view",
    "api_token.create",
    "api_token.revoke",
    "api_token.used",
    "webhook.create",
    "webhook.delete",
    "user.oauth.login",
    "user.oauth.register",
    "user.oauth.link",
)


class AuditEvent(Base):
    """One row per security-relevant action.

    ``user_id`` is nullable so failed-login attempts against unknown emails
    can still be recorded for rate-monitoring purposes. ``details`` is a
    JSON column for kind-specific context (the email that was tried, the
    application_id, the artifact_id, etc.). ``ip`` is reserved for future
    use; Streamlit doesn't expose client IP cleanly, but behind a reverse
    proxy a sidecar can populate it.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    details: Mapped[Optional[dict]] = mapped_column(JSON)
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    request_id: Mapped[Optional[str]] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True,
    )


# ---------------------------------------------------------------------------
# Telegram link — pairs a web user to a Telegram chat for notifications
# ---------------------------------------------------------------------------

class TelegramLinkBindingToken(Base):
    """Short-lived token a user pastes into the bot to bind their chat.

    Like password-reset tokens: stored as a bcrypt hash so a DB leak can't be
    turned into account takeovers. One open token per user; superseded by
    re-issuance.
    """

    __tablename__ = "telegram_link_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class TelegramLink(Base):
    """An active web-user ↔ Telegram-chat binding.

    One row per user; we don't support multiple chats per account because the
    UX of "which chat got the notification?" doesn't justify the complexity.
    """

    __tablename__ = "telegram_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    # Telegram chat_id is a 64-bit signed integer — fits in BigInteger.
    chat_id: Mapped[int] = mapped_column(sa_BigInt, nullable=False, index=True)
    chat_username: Mapped[Optional[str]] = mapped_column(String(64))
    notify_on_stage: Mapped[bool] = mapped_column(default=True, nullable=False)
    # The inactivity-reminder threshold. Set to 0 to disable inactivity
    # reminders entirely without losing per-stage notifications.
    inactive_reminder_days: Mapped[int] = mapped_column(default=7, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="telegram_link")


# ---------------------------------------------------------------------------
# Background analyses — long-running pipeline jobs submitted to Celery
# ---------------------------------------------------------------------------

# Lifecycle states. ``PENDING`` and ``STARTED`` map to live Celery states; the
# others mirror Celery's terminal states so we can stop polling once written.
BACKGROUND_STATES = ("PENDING", "STARTED", "SUCCESS", "FAILURE", "REVOKED")
BACKGROUND_TERMINAL_STATES = {"SUCCESS", "FAILURE", "REVOKED"}


class BackgroundAnalysis(Base):
    """One queued analysis, addressable by ``task_id`` so a user can leave
    the page and come back later to see the result.

    ``result_json`` is only populated once the task reaches ``SUCCESS`` —
    until then, callers should poll ``state`` and ask the broker. We cache
    the terminal state on the row so we stop hammering the broker once the
    task is done.
    """

    __tablename__ = "background_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    # Short user-facing summary of the inputs (company / title / location).
    inputs_summary: Mapped[Optional[str]] = mapped_column(Text)
    # Full result blob, only set when state == SUCCESS.
    result_json: Mapped[Optional[dict]] = mapped_column(JSON)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
