"""add oauth_identities

Revision ID: 268857701205
Revises: 32e5033bc887
Create Date: 2026-06-06 00:13:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "268857701205"
down_revision: Union[str, None] = "32e5033bc887"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oauth_identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_user_id", sa.String(128), nullable=False),
        sa.Column("email", sa.String(255)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_oauth_provider_subject"),
    )
    op.create_index("ix_oauth_identities_user_id", "oauth_identities", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_oauth_identities_user_id", table_name="oauth_identities")
    op.drop_table("oauth_identities")
