"""P0.4: schema-drift guard.

The app boots with ``create_all`` by default but production runs Alembic, so a
model change without a matching migration would silently diverge — `create_all`
dev/test would have the column, the migrated prod DB wouldn't. This test
applies all migrations to a fresh DB and asserts Alembic's autogenerate finds
**no difference** between the migrated schema and the SQLAlchemy models.

If this fails, you added/changed a model column without a migration: generate
one (`alembic revision --autogenerate -m "..."`), review it, and commit it.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# We assert on SUBSTANTIVE drift only: a model table/column that has no
# matching migration (or vice-versa). That's the canonical "I added a field
# and forgot the migration" failure the roadmap calls out.
#
# We deliberately do NOT assert on index/constraint/default/type diffs here:
#   * The models enforce uniqueness via column `unique=True` (a unique INDEX),
#     while the hand-written migrations use `UniqueConstraint` — functionally
#     identical (Postgres backs a UNIQUE constraint with a unique index), but
#     compare_metadata reports the cosmetic difference. Reconciling would need
#     an index-rename migration across SQLite+Postgres for no behavioural gain.
#   * SQLite reflection can't round-trip server defaults, BigInteger (-> INTEGER),
#     or String lengths, so type/default diffs are false positives here. The
#     Postgres CI migration job exercises real types/defaults instead.
_SUBSTANTIVE_OPS = {"add_table", "remove_table", "add_column", "remove_column"}


def _op_name(diff):
    """Extract the operation name from an Alembic diff entry.

    Entries are either a tuple ``(op, ...)`` or, for modify_*, a list of such
    tuples. Returns the op string (first tuple's first element).
    """
    if isinstance(diff, list):
        return diff[0][0] if diff and isinstance(diff[0], (list, tuple)) else None
    if isinstance(diff, (list, tuple)) and diff:
        return diff[0]
    return None


class SchemaDriftTests(unittest.TestCase):
    def test_models_match_migrations(self):
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        from sqlalchemy import create_engine

        from db.models import Base

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "drift.db"
            url = f"sqlite:///{db_path.as_posix()}"

            # Apply every migration to a fresh DB via the project's alembic.
            import subprocess
            env = dict(os.environ)
            env["DATABASE_URL"] = url
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=Path(__file__).resolve().parents[1],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(
                result.returncode, 0,
                msg=f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}",
            )

            engine = create_engine(url)
            with engine.connect() as conn:
                ctx = MigrationContext.configure(conn)
                raw_diffs = compare_metadata(ctx, Base.metadata)

            # Keep only substantive table/column add/remove diffs, excluding
            # the alembic bookkeeping table.
            diffs = [
                d for d in raw_diffs
                if _op_name(d) in _SUBSTANTIVE_OPS and "alembic_version" not in str(d)
            ]
            self.assertEqual(
                diffs, [],
                msg=(
                    "Model/migration table-or-column drift detected — these "
                    "are in the models but not the migrations (or vice versa):\n"
                    + "\n".join(f"  - {d}" for d in diffs)
                    + "\n\nGenerate a migration: "
                    "`alembic revision --autogenerate -m '<change>'`, review it, commit it."
                ),
            )


if __name__ == "__main__":
    unittest.main()
