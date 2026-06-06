"""Unit tests for Phase 17: resumable pipeline + inactivity reminders."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_db():
    from db.session import reset_engine_for_testing
    from services.checkpoint import reset_store_for_testing
    from services.rate_limit import reset_backend_for_testing
    reset_engine_for_testing("sqlite:///:memory:")
    reset_backend_for_testing()
    reset_store_for_testing()


def _register(email="u@x.com"):
    from services.auth import register_user
    return register_user(email, "longenough")


# ---------------------------------------------------------------------------
# Checkpoint primitives
# ---------------------------------------------------------------------------

class CheckpointKeyTests(unittest.TestCase):
    def test_same_inputs_yield_same_key(self):
        from services.checkpoint import compute_key

        k1 = compute_key("posting", {"company_name": "Acme"}, "fast", "resume text", user_id=1)
        k2 = compute_key("posting", {"company_name": "Acme"}, "fast", "resume text", user_id=1)
        self.assertEqual(k1, k2)

    def test_different_user_different_key(self):
        from services.checkpoint import compute_key

        k1 = compute_key("posting", {}, "fast", None, user_id=1)
        k2 = compute_key("posting", {}, "fast", None, user_id=2)
        self.assertNotEqual(k1, k2)

    def test_different_posting_different_key(self):
        from services.checkpoint import compute_key

        k1 = compute_key("posting A", {}, "fast", None)
        k2 = compute_key("posting B", {}, "fast", None)
        self.assertNotEqual(k1, k2)

    def test_cosmetic_whitespace_does_not_change_key(self):
        from services.checkpoint import compute_key

        k1 = compute_key("hello", {"company_name": "Acme"}, "fast", None)
        k2 = compute_key("  hello  ", {"company_name": "  Acme  "}, "fast", None)
        self.assertEqual(k1, k2)

    def test_empty_manual_fields_dropped(self):
        from services.checkpoint import compute_key

        k1 = compute_key("p", {"company_name": "Acme"}, "fast", None)
        k2 = compute_key("p", {"company_name": "Acme", "location": ""}, "fast", None)
        self.assertEqual(k1, k2)


class CheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        from services.checkpoint import reset_store_for_testing
        reset_store_for_testing()

    def test_set_get_clear_round_trip(self):
        from services.checkpoint import get_store

        store = get_store()
        store.set("k1", "job_details", {"company_name": "Acme"})
        store.set("k1", "company_analysis", {"stability": "ok"})
        payload = store.get("k1")
        self.assertTrue(payload.has("job_details"))
        self.assertTrue(payload.has("company_analysis"))
        self.assertEqual(payload.get("job_details")["company_name"], "Acme")

        store.clear("k1")
        self.assertFalse(store.has("k1"))

    def test_completed_stages_in_canonical_order(self):
        from services.checkpoint import CHECKPOINT_STAGES, get_store

        store = get_store()
        # Write in reverse order…
        store.set("k", "salary_analysis", {"x": 1})
        store.set("k", "job_details", {"y": 1})
        store.set("k", "company_analysis", {"z": 1})
        # …completed_stages still returns them in canonical (pipeline) order.
        done = store.completed_stages("k")
        self.assertEqual(
            done,
            tuple(s for s in CHECKPOINT_STAGES if s in (
                "job_details", "company_analysis", "salary_analysis",
            )),
        )

    def test_separate_keys_isolated(self):
        from services.checkpoint import get_store

        store = get_store()
        store.set("alice", "job_details", {"co": "a"})
        store.set("bob", "job_details", {"co": "b"})
        self.assertEqual(store.get("alice").get("job_details")["co"], "a")
        self.assertEqual(store.get("bob").get("job_details")["co"], "b")


# ---------------------------------------------------------------------------
# Orchestrator: checkpoint-aware resume
# ---------------------------------------------------------------------------

class OrchestratorResumeTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_completed_stages_are_skipped_on_resume(self):
        """If a previous run wrote ``job_details`` to the checkpoint, the
        wrapper around analyze_job must NOT re-call the analyzer."""
        from agents import orchestrator
        from services.checkpoint import compute_key, get_store

        key = compute_key("posting text", {"company_name": "Acme"}, "fast", None, user_id=1)
        get_store().set(key, "job_details", {
            "extracted_details": {"company_name": "Acme", "job_title": "ML"},
            "requirements_analysis": {"technical_skills": []},
        })

        with mock.patch("agents.job_analyzer.analyze") as analyzer:
            state = {
                "checkpoint_key": key,
                "job_posting": "posting text",
                "manual_inputs": {"company_name": "Acme"},
                "model": "fast",
                "progress_callback": None,
            }
            out = orchestrator._analyze_job_with_checkpoint(state)
        analyzer.assert_not_called()
        self.assertEqual(out["job_details"]["extracted_details"]["company_name"], "Acme")

    def test_first_run_writes_checkpoint(self):
        from agents import orchestrator
        from services.checkpoint import compute_key, get_store

        key = compute_key("p", {}, "fast", None, user_id=1)
        fake_result = {
            "job_details": {
                "extracted_details": {"company_name": "X"},
                "requirements_analysis": {"technical_skills": []},
            },
        }
        def side_effect(state):
            state["job_details"] = fake_result["job_details"]
            return state
        with mock.patch("agents.job_analyzer.analyze", side_effect=side_effect):
            state = {
                "checkpoint_key": key, "job_posting": "p",
                "manual_inputs": {}, "model": "fast", "progress_callback": None,
            }
            orchestrator._analyze_job_with_checkpoint(state)

        self.assertTrue(get_store().get(key).has("job_details"))

    def test_run_analysis_partial_failure_preserves_completed_stages(self):
        """End-to-end: first run fails at salary; second run with the same
        key skips the already-done job + company stages and re-runs salary."""
        from agents import orchestrator
        from services.checkpoint import compute_key, get_store

        key = compute_key("p", {"company_name": "Acme"}, "fast", None, user_id=1)

        def fake_job(state):
            state["job_details"] = {
                "extracted_details": {"company_name": "Acme", "job_title": "ML"},
                "requirements_analysis": {"technical_skills": []},
            }
            return state

        def fake_company(state):
            state["company_analysis"] = {"stability_analysis": "ok"}
            return state

        salary_calls = {"n": 0}
        def flaky_salary(state):
            salary_calls["n"] += 1
            if salary_calls["n"] == 1:
                state["error"] = "salary blew up"
            else:
                state["salary_analysis"] = {"estimated_range": "$100k-$200k"}
            return state

        def fake_resume(state):
            return state

        def fake_report(state):
            state["final_report"] = "# done"
            state["verdict"] = {"verdict": "Recommended"}
            return state

        with mock.patch("agents.job_analyzer.analyze", side_effect=fake_job), \
             mock.patch("agents.company_analyzer.analyze", side_effect=fake_company), \
             mock.patch("agents.salary_analyzer.analyze", side_effect=flaky_salary), \
             mock.patch("agents.resume_analyzer.analyze", side_effect=fake_resume), \
             mock.patch("agents.report_generator.generate", side_effect=fake_report):
            r1 = orchestrator.run_analysis(
                "p", manual_inputs={"company_name": "Acme"}, model="fast",
                checkpoint_key=key,
            )
            self.assertTrue(r1.get("error"))
            self.assertIn("job_details", get_store().get(key).stages)
            self.assertIn("company_analysis", get_store().get(key).stages)
            self.assertNotIn("salary_analysis", get_store().get(key).stages)

            # Resume — same key.
            r2 = orchestrator.run_analysis(
                "p", manual_inputs={"company_name": "Acme"}, model="fast",
                checkpoint_key=key,
            )
        self.assertEqual(r2.get("error", ""), "")
        # Salary ran exactly twice across both calls (once failing, once ok),
        # NOT three times — which means resume didn't re-run job/company.
        self.assertEqual(salary_calls["n"], 2)
        self.assertEqual(r2["verdict"]["verdict"], "Recommended")


# ---------------------------------------------------------------------------
# Inactivity reminders
# ---------------------------------------------------------------------------

class InactivityReminderTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        from services.applications import save_analysis
        from services.auth import register_user
        from services.stages import add_stage
        from services.telegram_link import complete_binding, issue_binding_token

        self.user = register_user("u@x.com", "longenough")
        complete_binding(chat_id=42, raw_token=issue_binding_token(self.user.id))
        self.today = date(2026, 6, 1)

        # App A: applied 30 days ago, no further activity -> stale at threshold 7.
        self.app_stale = save_analysis(
            self.user.id,
            {"company_name": "StaleCo", "job_title": "Eng", "location": "Berlin"},
            {"final_report": "# r", "verdict": {}, "resume_analysis": {}},
        )
        add_stage(self.user.id, self.app_stale.id, "applied",
                  occurred_on=self.today - timedelta(days=30))

        # App B: applied yesterday -> fresh.
        self.app_fresh = save_analysis(
            self.user.id,
            {"company_name": "FreshCo", "job_title": "Eng", "location": "Berlin"},
            {"final_report": "# r", "verdict": {}, "resume_analysis": {}},
        )
        add_stage(self.user.id, self.app_fresh.id, "applied",
                  occurred_on=self.today - timedelta(days=1))

        # App C: applied 30 days ago BUT was rejected -> closed, no reminder.
        self.app_closed = save_analysis(
            self.user.id,
            {"company_name": "ClosedCo", "job_title": "Eng", "location": "Berlin"},
            {"final_report": "# r", "verdict": {}, "resume_analysis": {}},
        )
        add_stage(self.user.id, self.app_closed.id, "applied",
                  occurred_on=self.today - timedelta(days=30))
        add_stage(self.user.id, self.app_closed.id, "rejected",
                  occurred_on=self.today - timedelta(days=20))

    def test_find_stale_returns_only_open_idle_apps(self):
        from services.reminders import find_stale_applications

        stale = find_stale_applications(
            self.user.id, threshold_days=7, today=self.today,
        )
        ids = {s.application_id for s in stale}
        self.assertIn(self.app_stale.id, ids)
        self.assertNotIn(self.app_fresh.id, ids)  # recent
        self.assertNotIn(self.app_closed.id, ids)  # rejected
        # days_idle reflects the gap correctly.
        stale_record = next(s for s in stale if s.application_id == self.app_stale.id)
        self.assertEqual(stale_record.days_idle, 30)

    def test_threshold_zero_disables(self):
        from services.reminders import find_stale_applications

        self.assertEqual(
            find_stale_applications(self.user.id, threshold_days=0, today=self.today),
            [],
        )

    def test_snoozed_app_skipped(self):
        from services.reminders import find_stale_applications, snooze_application

        snooze_application(
            self.user.id, self.app_stale.id, self.today + timedelta(days=14),
        )
        stale = find_stale_applications(
            self.user.id, threshold_days=7, today=self.today,
        )
        ids = {s.application_id for s in stale}
        self.assertNotIn(self.app_stale.id, ids)

    def test_unsnooze_brings_app_back_into_scope(self):
        from services.reminders import (
            find_stale_applications,
            snooze_application,
            unsnooze_application,
        )

        snooze_application(
            self.user.id, self.app_stale.id, self.today + timedelta(days=14),
        )
        unsnooze_application(self.user.id, self.app_stale.id)
        stale = find_stale_applications(
            self.user.id, threshold_days=7, today=self.today,
        )
        self.assertIn(
            self.app_stale.id,
            {s.application_id for s in stale},
        )

    def test_format_summary_includes_stale_apps(self):
        from services.reminders import find_stale_applications, format_summary

        stale = find_stale_applications(
            self.user.id, threshold_days=7, today=self.today,
        )
        text = format_summary(stale)
        self.assertIn("StaleCo", text)
        self.assertIn("Stale applications", text)
        self.assertIn("Snooze", text)

    def test_send_inactivity_reminders_calls_telegram(self):
        import services.reminders as mod

        with mock.patch.object(mod, "send_to_chat", return_value=True) as send:
            sent = mod.send_inactivity_reminders(today=self.today)
        self.assertEqual(sent, 1)
        send.assert_called_once()
        chat_id_called, text_called = send.call_args[0]
        self.assertEqual(chat_id_called, 42)
        self.assertIn("StaleCo", text_called)

    def test_send_inactivity_reminders_targeted_user(self):
        import services.reminders as mod
        from services.auth import register_user

        # Another linked user with no stale apps → not notified.
        register_user("other@x.com", "longenough")
        with mock.patch.object(mod, "send_to_chat", return_value=True) as send:
            sent = mod.send_inactivity_reminders(user_id=self.user.id, today=self.today)
        self.assertEqual(sent, 1)
        send.assert_called_once()

    def test_set_inactive_threshold_persists(self):
        from services.reminders import set_inactive_threshold
        from services.telegram_link import get_link

        set_inactive_threshold(self.user.id, 14)
        self.assertEqual(get_link(self.user.id).inactive_reminder_days, 14)

    def test_set_threshold_requires_telegram_link(self):
        from services.auth import register_user
        from services.reminders import set_inactive_threshold

        new_user = register_user("nolink@x.com", "longenough")
        with self.assertRaises(PermissionError):
            set_inactive_threshold(new_user.id, 7)

    def test_snooze_cross_user_blocked(self):
        from services.auth import register_user
        from services.reminders import snooze_application

        other = register_user("other2@x.com", "longenough")
        with self.assertRaises(PermissionError):
            snooze_application(
                other.id, self.app_stale.id,
                self.today + timedelta(days=7),
            )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

class ReminderCliTests(unittest.TestCase):
    def test_cli_runs_send_inactivity_reminders(self):
        from worker import reminders as cli

        with mock.patch.object(cli, "send_inactivity_reminders", return_value=3) as fn:
            rc = cli.main([])
        self.assertEqual(rc, 0)
        fn.assert_called_once_with(user_id=None)

    def test_cli_passes_user_arg(self):
        from worker import reminders as cli

        with mock.patch.object(cli, "send_inactivity_reminders", return_value=1) as fn:
            cli.main(["--user", "42"])
        fn.assert_called_once_with(user_id=42)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

class Phase17MigrationTests(unittest.TestCase):
    def test_columns_added(self):
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
            engine = create_engine(f"sqlite:///{db_path.as_posix()}")
            insp = inspect(engine)
            app_cols = {c["name"] for c in insp.get_columns("applications")}
            self.assertIn("snooze_reminders_until", app_cols)
            link_cols = {c["name"] for c in insp.get_columns("telegram_links")}
            self.assertIn("inactive_reminder_days", link_cols)


if __name__ == "__main__":
    unittest.main()
