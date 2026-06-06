"""add background_analyses table

Revision ID: cc044d01b2b9
Revises: f7cd19cbca6e
Create Date: 2026-06-06 00:08:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "cc044d01b2b9"
down_revision: Union[str, None] = "f7cd19cbca6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "background_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("inputs_summary", sa.Text()),
        sa.Column("result_json", sa.JSON()),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
        sa.UniqueConstraint("task_id", name="uq_background_analyses_task_id"),
    )
    op.create_index("ix_background_analyses_user_id", "background_analyses", ["user_id"])
    op.create_index("ix_background_analyses_task_id", "background_analyses", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_background_analyses_task_id", table_name="background_analyses")
    op.drop_index("ix_background_analyses_user_id", table_name="background_analyses")
    op.drop_table("background_analyses")
