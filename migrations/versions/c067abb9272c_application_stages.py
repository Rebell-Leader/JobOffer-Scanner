"""add application_stages table

Revision ID: c067abb9272c
Revises: e6cc454184fc
Create Date: 2026-06-06 00:02:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c067abb9272c"
down_revision: Union[str, None] = "e6cc454184fc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "application_stages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("at_pipeline_stage", sa.String(32)),
        sa.Column("extra", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_application_stages_application_id",
        "application_stages",
        ["application_id"],
    )
    op.create_index(
        "ix_application_stages_app_occurred",
        "application_stages",
        ["application_id", "occurred_on"],
    )


def downgrade() -> None:
    op.drop_index("ix_application_stages_app_occurred", table_name="application_stages")
    op.drop_index("ix_application_stages_application_id", table_name="application_stages")
    op.drop_table("application_stages")
