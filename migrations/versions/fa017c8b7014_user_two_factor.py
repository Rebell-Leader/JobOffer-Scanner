"""add user_two_factor table

Revision ID: fa017c8b7014
Revises: cc044d01b2b9
Create Date: 2026-06-06 00:09:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "fa017c8b7014"
down_revision: Union[str, None] = "cc044d01b2b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_two_factor",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("secret", sa.String(64), nullable=False),
        sa.Column(
            "verified", sa.Boolean(), nullable=False, server_default=sa.false(),
        ),
        sa.Column("backup_codes", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_user_two_factor_user_id"),
    )
    op.create_index("ix_user_two_factor_user_id", "user_two_factor", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_two_factor_user_id", table_name="user_two_factor")
    op.drop_table("user_two_factor")
