"""add master_cvs, projects, application_artifacts

Revision ID: 83518f3daa22
Revises: c067abb9272c
Create Date: 2026-06-06 00:03:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "83518f3daa22"
down_revision: Union[str, None] = "c067abb9272c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "master_cvs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("structured", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_master_cvs_user_id"),
    )
    op.create_index("ix_master_cvs_user_id", "master_cvs", ["user_id"])

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("role", sa.String(255)),
        sa.Column("tech_stack", sa.String(500)),
        sa.Column("summary", sa.Text()),
        sa.Column("highlights", sa.JSON()),
        sa.Column("url", sa.String(500)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_projects_user_id", "projects", ["user_id"])

    op.create_table(
        "application_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "application_id", sa.Integer(),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("meta", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_artifacts_application_id", "application_artifacts", ["application_id"]
    )
    op.create_index(
        "ix_artifacts_user_id", "application_artifacts", ["user_id"]
    )
    op.create_index(
        "ix_artifacts_app_kind_created",
        "application_artifacts",
        ["application_id", "kind", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_artifacts_app_kind_created", table_name="application_artifacts")
    op.drop_index("ix_artifacts_user_id", table_name="application_artifacts")
    op.drop_index("ix_artifacts_application_id", table_name="application_artifacts")
    op.drop_table("application_artifacts")
    op.drop_index("ix_projects_user_id", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_master_cvs_user_id", table_name="master_cvs")
    op.drop_table("master_cvs")
