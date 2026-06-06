"""add snooze_reminders_until + inactive_reminder_days

Revision ID: bee6851b0338
Revises: 3ba1c52f7b93
Create Date: 2026-06-06 00:06:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "bee6851b0338"
down_revision: Union[str, None] = "3ba1c52f7b93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("applications") as batch:
        batch.add_column(sa.Column("snooze_reminders_until", sa.Date(), nullable=True))
    with op.batch_alter_table("telegram_links") as batch:
        batch.add_column(
            sa.Column(
                "inactive_reminder_days", sa.Integer(),
                nullable=False, server_default="7",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("telegram_links") as batch:
        batch.drop_column("inactive_reminder_days")
    with op.batch_alter_table("applications") as batch:
        batch.drop_column("snooze_reminders_until")
