"""add users.email_verified + email_verification_tokens

Revision ID: 46b9602c99a1
Revises: 268857701205
Create Date: 2026-06-07 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "46b9602c99a1"
down_revision: Union[str, None] = "268857701205"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add the column with server_default=true so EXISTING rows (which predate
    # verification) are treated as verified and nobody is locked out. New rows
    # are inserted by the ORM with email_verified=False explicitly.
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "email_verified", sa.Boolean(),
                nullable=False, server_default=sa.true(),
            )
        )

    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_email_verification_tokens_user_id",
        "email_verification_tokens", ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_verification_tokens_user_id",
        table_name="email_verification_tokens",
    )
    op.drop_table("email_verification_tokens")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("email_verified")
