"""Unit tests for Phase 19: background analyses + Celery integration."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_db():
    from db.session import reset_engine_for_testing
    from services.rate_limit import reset_backend_for_testing
    reset_engine_for_testing("sqlite:///:memory:")
    reset_backend_for_testing()


def _register(email="u@x.com"):
    from services.auth import register_user
    return register_user(email, "longenough")


# ---------------------------------------------------------------------------
# Submit / list / get / delete
# ---------------------------------------------------------------------------

class BackgroundSubmitTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        self.other = _register("other@x.com")

    def test_submit_returns_none_when_async_disabled(self):
        import services.background_analysis as mod

        with mock.patch.object(mod, "async_enabled", return_value=False):
            self.assertIsNone(
                mod.submit_background_analysis(
                    self.user.id, "posting text",
                    manual_inputs={"company_name": "Acme"},
                )
            )

    def test_submit_persists_row_with_task_id(self):
        import services.background_analysis as mod

        with mock.patch.object(mod, "async_enabled", return_value=True), \
             mock.patch.object(mod, "enqueue_analysis", return_value="celery-task-xyz"):
            rec = mod.submit_background_analysis(
                self.user.id, "posting text",
                manual_inputs={
                    "company_name": "Stripe", "job_title": "ML Engineer",
                    "location": "Berlin",
                },
            )
        self.assertIsNotNone(rec)
        self.assertEqual(rec.task_id, "celery-task-xyz")
        self.assertEqual(rec.title, "ML Engineer @ Stripe")
        self.assertIn("Stripe", rec.inputs_summary)
        self.assertIn("Berlin", rec.inputs_summary)
        self.assertEqual(rec.state, "PENDING")

    def test_submit_returns_none_when_enqueue_returns_none(self):
        """If the queue accepts the call but enqueue_analysis can't actually
        dispatch (no broker URL despite async_enabled returning True), we
        must not persist a phantom row."""
        import services.background_analysis as mod

        with mock.patch.object(mod, "async_enabled", return_value=True), \
             mock.patch.object(mod, "enqueue_analysis", return_value=None):
            self.assertIsNone(
                mod.submit_background_analysis(
                    self.user.id, "posting", manual_inputs={"company_name": "X"},
                )
            )

    def test_list_for_user_returns_newest_first(self):
        import time

        import services.background_analysis as mod

        with mock.patch.object(mod, "async_enabled", return_value=True), \
             mock.patch.object(mod, "enqueue_analysis", side_effect=["t1", "t2", "t3"]):
            mod.submit_background_analysis(self.user.id, "p1", {"company_name": "A"})
            time.sleep(0.01)
            mod.submit_background_analysis(self.user.id, "p2", {"company_name": "B"})
            time.sleep(0.01)
            mod.submit_background_analysis(self.user.id, "p3", {"company_name": "C"})

        rows = mod.list_for_user(self.user.id)
        self.assertEqual([r.task_id for r in rows], ["t3", "t2", "t1"])

    def test_list_is_user_scoped(self):
        import services.background_analysis as mod

        with mock.patch.object(mod, "async_enabled", return_value=True), \
             mock.patch.object(mod, "enqueue_analysis", return_value="t1"):
            mod.submit_background_analysis(self.user.id, "p", {"company_name": "A"})
        # other user sees nothing
        self.assertEqual(mod.list_for_user(self.other.id), [])

    def test_get_cross_user_blocked(self):
        import services.background_analysis as mod

        with mock.patch.object(mod, "async_enabled", return_value=True), \
             mock.patch.object(mod, "enqueue_analysis", return_value="t1"):
            rec = mod.submit_background_analysis(
                self.user.id, "p", {"company_name": "A"},
            )
        with self.assertRaises(mod.BackgroundAnalysisError):
            mod.get(self.other.id, rec.id)

    def test_delete_cross_user_blocked(self):
        import services.background_analysis as mod

        with mock.patch.object(mod, "async_enabled", return_value=True), \
             mock.patch.object(mod, "enqueue_analysis", return_value="t1"):
            rec = mod.submit_background_analysis(
                self.user.id, "p", {"company_name": "A"},
            )
        with self.assertRaises(mod.BackgroundAnalysisError):
            mod.delete(self.other.id, rec.id)

    def test_delete_removes_row(self):
        import services.background_analysis as mod

        with mock.patch.object(mod, "async_enabled", return_value=True), \
             mock.patch.object(mod, "enqueue_analysis", return_value="t1"):
            rec = mod.submit_background_analysis(
                self.user.id, "p", {"company_name": "A"},
            )
        mod.delete(self.user.id, rec.id)
        self.assertEqual(mod.list_for_user(self.user.id), [])


# ---------------------------------------------------------------------------
# refresh_state: short-circuit + state cache + cross-user
# ---------------------------------------------------------------------------

class BackgroundRefreshTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        self.other = _register("other@x.com")
        import services.background_analysis as mod
        with mock.patch.object(mod, "async_enabled", return_value=True), \
             mock.patch.object(mod, "enqueue_analysis", return_value="task-xyz"):
            self.rec = mod.submit_background_analysis(
                self.user.id, "posting text",
                manual_inputs={"company_name": "Stripe", "job_title": "ML Engineer"},
            )

    def test_pending_polls_broker(self):
        import services.background_analysis as mod

        with mock.patch.object(mod, "get_async_result",
                               return_value=("STARTED", None)) as poll:
            r = mod.refresh_state(self.user.id, self.rec.id)
        poll.assert_called_once_with("task-xyz")
        self.assertEqual(r.state, "STARTED")
        self.assertIsNone(r.result_json)
        self.assertIsNone(r.completed_at)

    def test_success_caches_result_and_completion_time(self):
        import services.background_analysis as mod

        fake_result = {
            "final_report": "# done",
            "verdict": {"verdict": "Recommended", "light": "green"},
            "job_details": {"extracted_details": {"company_name": "Stripe"}},
        }
        with mock.patch.object(mod, "get_async_result",
                               return_value=("SUCCESS", fake_result)):
            r = mod.refresh_state(self.user.id, self.rec.id)
        self.assertEqual(r.state, "SUCCESS")
        self.assertIsNotNone(r.completed_at)
        self.assertEqual(r.result_json["final_report"], "# done")

    def test_terminal_state_short_circuits(self):
        """Already-terminal rows must NOT poll Celery again."""
        import services.background_analysis as mod

        with mock.patch.object(mod, "get_async_result",
                               return_value=("SUCCESS", {"final_report": "ok"})):
            mod.refresh_state(self.user.id, self.rec.id)

        with mock.patch.object(mod, "get_async_result") as poll:
            mod.refresh_state(self.user.id, self.rec.id)
        poll.assert_not_called()

    def test_failure_caches_error_message(self):
        import services.background_analysis as mod

        with mock.patch.object(mod, "get_async_result",
                               return_value=("FAILURE", "Adzuna 503")):
            r = mod.refresh_state(self.user.id, self.rec.id)
        self.assertEqual(r.state, "FAILURE")
        self.assertEqual(r.error_message, "Adzuna 503")

    def test_unavailable_broker_leaves_row_untouched(self):
        import services.background_analysis as mod

        with mock.patch.object(mod, "get_async_result",
                               return_value=("UNAVAILABLE", None)):
            r = mod.refresh_state(self.user.id, self.rec.id)
        self.assertEqual(r.state, "PENDING")
        self.assertIsNone(r.completed_at)

    def test_refresh_state_cross_user_blocked(self):
        import services.background_analysis as mod

        with self.assertRaises(mod.BackgroundAnalysisError):
            mod.refresh_state(self.other.id, self.rec.id)

    def test_refresh_all_pending_skips_terminal_rows(self):
        import services.background_analysis as mod

        # Submit a second row.
        with mock.patch.object(mod, "async_enabled", return_value=True), \
             mock.patch.object(mod, "enqueue_analysis", return_value="task-2"):
            mod.submit_background_analysis(
                self.user.id, "p2", {"company_name": "Acme"},
            )

        # Mark the first as terminal.
        with mock.patch.object(mod, "get_async_result",
                               return_value=("SUCCESS", {"final_report": "done"})):
            mod.refresh_state(self.user.id, self.rec.id)

        # refresh_all_pending should poll only the still-pending one.
        with mock.patch.object(mod, "get_async_result",
                               return_value=("STARTED", None)) as poll:
            rows = mod.refresh_all_pending(self.user.id)
        poll.assert_called_once_with("task-2")
        states = {r.task_id: r.state for r in rows}
        self.assertEqual(states["task-xyz"], "SUCCESS")
        self.assertEqual(states["task-2"], "STARTED")


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

class BackgroundCleanupTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()

    def test_cleanup_removes_old_terminal_rows(self):
        import services.background_analysis as mod
        from db.models import BackgroundAnalysis
        from db.session import get_session

        # Two terminal rows: one old, one fresh.
        with mock.patch.object(mod, "async_enabled", return_value=True), \
             mock.patch.object(mod, "enqueue_analysis",
                               side_effect=["old", "fresh"]):
            mod.submit_background_analysis(
                self.user.id, "p", {"company_name": "A"})
            mod.submit_background_analysis(
                self.user.id, "p", {"company_name": "B"})

        with get_session() as s:
            for r in s.query(BackgroundAnalysis).all():
                r.state = "SUCCESS"
                if r.task_id == "old":
                    r.completed_at = datetime.utcnow() - timedelta(days=60)
                else:
                    r.completed_at = datetime.utcnow()
            s.commit()

        removed = mod.cleanup_terminal_older_than(days=30)
        self.assertEqual(removed, 1)
        remaining_ids = {r.task_id for r in mod.list_for_user(self.user.id)}
        self.assertEqual(remaining_ids, {"fresh"})


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

class Phase19MigrationTests(unittest.TestCase):
    def test_background_analyses_created(self):
        from sqlalchemy import create_engine, inspect

        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "alembic.db"
            env = dict(os.environ)
            env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=project_root, env=env,
                capture_output=True, text=True,
            )
            self.assertEqual(
                result.returncode, 0,
                msg=f"alembic upgrade failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
            )
            tables = set(inspect(create_engine(f"sqlite:///{db_path.as_posix()}")).get_table_names())
            self.assertIn("background_analyses", tables)


if __name__ == "__main__":
    unittest.main()
