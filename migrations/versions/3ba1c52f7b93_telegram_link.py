"""add telegram_links, telegram_link_tokens

Revision ID: 3ba1c52f7b93
Revises: a3b401ffdd58
Create Date: 2026-06-06 00:05:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3ba1c52f7b93"
down_revision: Union[str, None] = "a3b401ffdd58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_link_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_telegram_link_tokens_user_id",
        "telegram_link_tokens",
        ["user_id"],
    )

    op.create_table(
        "telegram_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_username", sa.String(64)),
        sa.Column(
            "notify_on_stage", sa.Boolean(),
            nullable=False, server_default=sa.true(),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_telegram_links_user_id"),
    )
    op.create_index("ix_telegram_links_chat_id", "telegram_links", ["chat_id"])


def downgrade() -> None:
    op.drop_index("ix_telegram_links_chat_id", table_name="telegram_links")
    op.drop_table("telegram_links")
    op.drop_index("ix_telegram_link_tokens_user_id", table_name="telegram_link_tokens")
    op.drop_table("telegram_link_tokens")
