"""widen user_two_factor.secret for envelope-encrypted ciphertext

Revision ID: 5d2f8a1c4e7b
Revises: 46b9602c99a1
Create Date: 2026-06-07 00:00:00

The TOTP secret is now envelope-encrypted at rest (utils.crypto, "enc:v1:…")
when SECRETS_ENCRYPTION_KEY is set. The Fernet ciphertext is far longer than
the 32-char base32 plaintext, so the column is widened 64 -> 255. Existing
plaintext values still fit and are re-encrypted lazily by the app.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5d2f8a1c4e7b"
down_revision: Union[str, None] = "46b9602c99a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("user_two_factor") as batch:
        batch.alter_column(
            "secret",
            existing_type=sa.String(64),
            type_=sa.String(255),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Note: if any encrypted secret exceeds 64 chars this would truncate; the
    # downgrade is only safe before encryption is enabled (i.e. in a rollback
    # of this migration prior to keying). Kept symmetric for the up→down→up CI.
    with op.batch_alter_table("user_two_factor") as batch:
        batch.alter_column(
            "secret",
            existing_type=sa.String(255),
            type_=sa.String(64),
            existing_nullable=False,
        )
