"""add master_cv_revisions

Revision ID: a3b401ffdd58
Revises: 83518f3daa22
Create Date: 2026-06-06 00:04:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3b401ffdd58"
down_revision: Union[str, None] = "83518f3daa22"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "master_cv_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "master_cv_id", sa.Integer(),
            sa.ForeignKey("master_cvs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("structured", sa.JSON()),
        sa.Column("reason", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_master_cv_revisions_master_cv_id",
        "master_cv_revisions",
        ["master_cv_id"],
    )
    op.create_index(
        "ix_master_cv_revisions_user_id",
        "master_cv_revisions",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_master_cv_revisions_user_id", table_name="master_cv_revisions"
    )
    op.drop_index(
        "ix_master_cv_revisions_master_cv_id", table_name="master_cv_revisions"
    )
    op.drop_table("master_cv_revisions")
