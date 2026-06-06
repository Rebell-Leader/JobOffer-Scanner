"""add audit_events table

Revision ID: f7cd19cbca6e
Revises: bee6851b0338
Create Date: 2026-06-06 00:07:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7cd19cbca6e"
down_revision: Union[str, None] = "bee6851b0338"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("details", sa.JSON()),
        sa.Column("ip", sa.String(64)),
        sa.Column("request_id", sa.String(32)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_audit_events_user_id", "audit_events", ["user_id"])
    op.create_index("ix_audit_events_kind", "audit_events", ["kind"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_kind", table_name="audit_events")
    op.drop_index("ix_audit_events_user_id", table_name="audit_events")
    op.drop_table("audit_events")
