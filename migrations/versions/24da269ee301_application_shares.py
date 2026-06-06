"""add application_shares table

Revision ID: 24da269ee301
Revises: fa017c8b7014
Create Date: 2026-06-06 00:10:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "24da269ee301"
down_revision: Union[str, None] = "fa017c8b7014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "application_shares",
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
        sa.Column("token", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime()),
        sa.Column("revoked_at", sa.DateTime()),
        sa.Column(
            "include_artifacts", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_viewed_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("token", name="uq_application_shares_token"),
    )
    op.create_index("ix_application_shares_application_id", "application_shares", ["application_id"])
    op.create_index("ix_application_shares_user_id", "application_shares", ["user_id"])
    op.create_index("ix_application_shares_token", "application_shares", ["token"])


def downgrade() -> None:
    op.drop_index("ix_application_shares_token", table_name="application_shares")
    op.drop_index("ix_application_shares_user_id", table_name="application_shares")
    op.drop_index("ix_application_shares_application_id", table_name="application_shares")
    op.drop_table("application_shares")
