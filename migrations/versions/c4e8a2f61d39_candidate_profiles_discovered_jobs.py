"""add candidate_profiles + discovered_jobs

Revision ID: c4e8a2f61d39
Revises: b2f7c9d04a16
Create Date: 2026-10-19 00:00:00

Personal job search: the candidate's search profile (target roles, locations,
salary floor, dealbreakers; JSON document) and the job-discovery feed
(deduplicated per user, with deterministic match score + LLM fit triage).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a2f61d39"
down_revision: Union[str, None] = "b2f7c9d04a16"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "candidate_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_candidate_profiles_user_id", "candidate_profiles", ["user_id"], unique=True
    )

    op.create_table(
        "discovered_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("url", sa.Text()),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("company", sa.String(255)),
        sa.Column("location", sa.String(255)),
        sa.Column("remote", sa.Boolean()),
        sa.Column("posted_at", sa.DateTime()),
        sa.Column("salary_text", sa.String(255)),
        sa.Column("description", sa.Text()),
        sa.Column("match_score", sa.Integer()),
        sa.Column("fit_score", sa.Integer()),
        sa.Column("fit_summary", sa.Text()),
        sa.Column("fit_flags", sa.JSON()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "application_id", sa.Integer(),
            sa.ForeignKey("applications.id", ondelete="SET NULL"),
        ),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "dedupe_key", name="uq_discovered_jobs_user_dedupe"),
    )
    op.create_index("ix_discovered_jobs_user_id", "discovered_jobs", ["user_id"])
    op.create_index(
        "ix_discovered_jobs_user_status", "discovered_jobs", ["user_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_discovered_jobs_user_status", table_name="discovered_jobs")
    op.drop_index("ix_discovered_jobs_user_id", table_name="discovered_jobs")
    op.drop_table("discovered_jobs")
    op.drop_index("ix_candidate_profiles_user_id", table_name="candidate_profiles")
    op.drop_table("candidate_profiles")
