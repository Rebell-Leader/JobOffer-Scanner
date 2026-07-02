"""add waitlist_entries

Revision ID: b2f7c9d04a16
Revises: 9e4b7d21c803
Create Date: 2026-06-10 00:00:00

Marketing-site email capture for visitors not ready to sign up (public,
unauthenticated insert via POST /waitlist).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2f7c9d04a16"
down_revision: Union[str, None] = "9e4b7d21c803"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "waitlist_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("source", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("email", name="uq_waitlist_email"),
    )
    op.create_index("ix_waitlist_entries_email", "waitlist_entries", ["email"])
    op.create_index("ix_waitlist_entries_created_at", "waitlist_entries", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_waitlist_entries_created_at", table_name="waitlist_entries")
    op.drop_index("ix_waitlist_entries_email", table_name="waitlist_entries")
    op.drop_table("waitlist_entries")
