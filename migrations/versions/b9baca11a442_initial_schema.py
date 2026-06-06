"""initial schema: users + applications

Revision ID: b9baca11a442
Revises:
Create Date: 2026-06-06 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9baca11a442"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("job_title", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255)),
        sa.Column("status", sa.String(32), nullable=False, server_default="saved"),
        sa.Column("verdict", sa.String(64)),
        sa.Column("verdict_light", sa.String(16)),
        sa.Column("ats_score", sa.Integer()),
        sa.Column("notes", sa.Text()),
        sa.Column("analysis_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_applications_user_id", "applications", ["user_id"])
    op.create_index(
        "ix_applications_user_created",
        "applications",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_applications_user_created", table_name="applications")
    op.drop_index("ix_applications_user_id", table_name="applications")
    op.drop_table("applications")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
