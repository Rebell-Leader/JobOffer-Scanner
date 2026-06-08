"""P2 #12: backup/restore ops scripts + runbook.

No Postgres in CI, so we don't actually dump/restore here — we guard the
scripts' shape: they parse (`bash -n`), use strict mode, refuse to run without
DATABASE_URL, reject non-Postgres targets, and the restore script is
destructive-by-default but gated by a confirmation. Also assert the runbook
documents the drill.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_ROOT = Path(__file__).resolve().parents[1]
_BACKUP = _ROOT / "scripts" / "backup_db.sh"
_RESTORE = _ROOT / "scripts" / "restore_db.sh"
_RUNBOOK = _ROOT / "deploy" / "RUNBOOK.md"

_BASH = shutil.which("bash")


@unittest.skipUnless(_BASH, "bash not available")
class ScriptSyntaxTests(unittest.TestCase):
    def test_backup_parses(self):
        self.assertEqual(subprocess.run([_BASH, "-n", str(_BACKUP)]).returncode, 0)

    def test_restore_parses(self):
        self.assertEqual(subprocess.run([_BASH, "-n", str(_RESTORE)]).returncode, 0)


class ScriptContentTests(unittest.TestCase):
    def setUp(self):
        self.backup = _BACKUP.read_text()
        self.restore = _RESTORE.read_text()

    def test_strict_mode(self):
        for src in (self.backup, self.restore):
            self.assertIn("set -euo pipefail", src)

    def test_backup_requires_database_url(self):
        self.assertIn("DATABASE_URL", self.backup)
        self.assertIn("custom", self.backup)  # pg_dump custom format

    def test_backup_rejects_non_postgres(self):
        self.assertIn("postgres", self.backup)

    def test_restore_is_gated_by_confirmation(self):
        # Destructive: must require an explicit confirmation unless --force.
        self.assertIn("--force", self.restore)
        self.assertIn("restore", self.restore)
        self.assertIn("--clean", self.restore)

    def test_scripts_are_executable(self):
        for p in (_BACKUP, _RESTORE):
            self.assertTrue(os.access(p, os.X_OK), f"{p} should be executable")


@unittest.skipUnless(_BASH, "bash not available")
class BackupGuardBehaviourTests(unittest.TestCase):
    """Exercise the early-exit guards without needing a database."""

    def _run(self, script, args, env):
        full_env = {**os.environ, **env}
        return subprocess.run(
            [_BASH, str(script), *args],
            env=full_env, capture_output=True, text=True,
        )

    def test_backup_without_url_exits_1(self):
        env = dict(os.environ)
        env.pop("DATABASE_URL", None)
        r = subprocess.run(
            [_BASH, str(_BACKUP)],
            env={k: v for k, v in env.items()}, capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("DATABASE_URL", r.stderr)

    def test_backup_rejects_sqlite_url(self):
        r = self._run(_BACKUP, [], {"DATABASE_URL": "sqlite:///./data/joboffer.db"})
        self.assertEqual(r.returncode, 1)

    def test_restore_requires_dump_arg(self):
        r = self._run(_RESTORE, [], {"DATABASE_URL": "postgresql://u:p@h/db"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("Usage", r.stderr)

    def test_restore_missing_file_exits_1(self):
        r = self._run(_RESTORE, ["/no/such/file.dump"],
                      {"DATABASE_URL": "postgresql://u:p@h/db"})
        self.assertEqual(r.returncode, 1)


class RunbookTests(unittest.TestCase):
    def test_runbook_exists_and_covers_drill(self):
        text = _RUNBOOK.read_text().lower()
        for needle in ("backup", "restore drill", "migration", "escalation"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
