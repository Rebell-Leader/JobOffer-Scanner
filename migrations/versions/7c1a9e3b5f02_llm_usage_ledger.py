"""add llm_usage ledger for token accounting + budgets

Revision ID: 7c1a9e3b5f02
Revises: 5d2f8a1c4e7b
Create Date: 2026-06-07 00:00:00

Per-call LLM token usage + estimated cost (integer micro-USD), attributed to a
user when available. services/usage sums cost over a rolling window to enforce
LLM_BUDGET_USD.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7c1a9e3b5f02"
down_revision: Union[str, None] = "5d2f8a1c4e7b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_micro_usd", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_llm_usage_user_id", "llm_usage", ["user_id"])
    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_usage_created_at", table_name="llm_usage")
    op.drop_index("ix_llm_usage_user_id", table_name="llm_usage")
    op.drop_table("llm_usage")
