"""add subscriptions + usage_events (billing tiers)

Revision ID: 9e4b7d21c803
Revises: 7c1a9e3b5f02
Create Date: 2026-06-09 00:00:00

Billing layer: ``subscriptions`` mirrors the user's Stripe plan (absence of a
row = free tier, or unlimited when billing is unconfigured); ``usage_events``
meters product actions (one analysis = one event) for tier quotas — distinct
from llm_usage, which ledgers per-completion token costs.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9e4b7d21c803"
down_revision: Union[str, None] = "7c1a9e3b5f02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("tier", sa.String(16), nullable=False, server_default="free"),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("stripe_customer_id", sa.String(64)),
        sa.Column("stripe_subscription_id", sa.String(64)),
        sa.Column("current_period_end", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_subscriptions_user_id"),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    op.create_index(
        "ix_subscriptions_stripe_customer_id", "subscriptions", ["stripe_customer_id"]
    )
    op.create_index(
        "ix_subscriptions_stripe_subscription_id",
        "subscriptions", ["stripe_subscription_id"],
    )

    op.create_table(
        "usage_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_usage_events_user_id", "usage_events", ["user_id"])
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_events_created_at", table_name="usage_events")
    op.drop_index("ix_usage_events_user_id", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_index(
        "ix_subscriptions_stripe_subscription_id", table_name="subscriptions"
    )
    op.drop_index("ix_subscriptions_stripe_customer_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    op.drop_table("subscriptions")
