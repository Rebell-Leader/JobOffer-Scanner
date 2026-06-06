"""Unit tests for Phase 8: pipeline stages + analytics."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_db():
    from db.session import reset_engine_for_testing
    from services.rate_limit import reset_backend_for_testing
    reset_engine_for_testing("sqlite:///:memory:")
    reset_backend_for_testing()


def _register_user(email="u@x.com"):
    from services.auth import register_user
    return register_user(email, "longenough")


def _save_app(user_id, company="Acme", title="Eng", verdict="Recommended", light="green"):
    from services.applications import save_analysis
    return save_analysis(
        user_id,
        {"company_name": company, "job_title": title, "location": "Berlin"},
        {
            "final_report": "# r",
            "verdict": {"verdict": verdict, "light": light, "reasons": []},
            "resume_analysis": {"ats_score": 70},
        },
    )


# ---------------------------------------------------------------------------
# Stage CRUD + auto status sync
# ---------------------------------------------------------------------------

class StageCrudTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register_user()
        self.other = _register_user("other@x.com")
        self.app = _save_app(self.user.id)

    def test_add_and_list_stages(self):
        from services.stages import add_stage, list_stages

        add_stage(self.user.id, self.app.id, "applied", occurred_on=date(2026, 1, 1))
        add_stage(self.user.id, self.app.id, "phone_screen", occurred_on=date(2026, 1, 5))
        rows = list_stages(self.user.id, self.app.id)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].kind, "applied")
        self.assertEqual(rows[1].kind, "phone_screen")

    def test_add_stage_rejects_unknown_kind(self):
        from services.stages import StageError, add_stage

        with self.assertRaises(StageError):
            add_stage(self.user.id, self.app.id, "got_a_pony")

    def test_add_stage_cross_user_blocked(self):
        from services.stages import StageError, add_stage

        with self.assertRaises(StageError):
            add_stage(self.other.id, self.app.id, "applied")

    def test_list_stages_cross_user_blocked(self):
        from services.stages import StageError, add_stage, list_stages

        add_stage(self.user.id, self.app.id, "applied")
        with self.assertRaises(StageError):
            list_stages(self.other.id, self.app.id)

    def test_delete_stage_cross_user_blocked(self):
        from services.stages import StageError, add_stage, delete_stage

        s = add_stage(self.user.id, self.app.id, "applied")
        with self.assertRaises(StageError):
            delete_stage(self.other.id, s.id)

    def test_status_syncs_on_add(self):
        """Adding stages updates the parent Application.status field."""
        from services.applications import get_application
        from services.stages import add_stage

        add_stage(self.user.id, self.app.id, "applied", occurred_on=date(2026, 1, 1))
        self.assertEqual(get_application(self.user.id, self.app.id).status, "applied")

        add_stage(self.user.id, self.app.id, "technical_interview",
                  occurred_on=date(2026, 1, 10))
        self.assertEqual(get_application(self.user.id, self.app.id).status, "interviewing")

        add_stage(self.user.id, self.app.id, "offer_received",
                  occurred_on=date(2026, 1, 20))
        self.assertEqual(get_application(self.user.id, self.app.id).status, "offer")

        add_stage(self.user.id, self.app.id, "rejected", occurred_on=date(2026, 1, 25))
        self.assertEqual(get_application(self.user.id, self.app.id).status, "rejected")

    def test_status_uses_latest_by_date_not_insert_order(self):
        from services.applications import get_application
        from services.stages import add_stage

        # Insert the later-dated stage first.
        add_stage(self.user.id, self.app.id, "offer_received", occurred_on=date(2026, 2, 1))
        add_stage(self.user.id, self.app.id, "applied", occurred_on=date(2026, 1, 1))
        self.assertEqual(get_application(self.user.id, self.app.id).status, "offer")

    def test_status_reverts_when_latest_stage_deleted(self):
        from services.applications import get_application
        from services.stages import add_stage, delete_stage

        applied = add_stage(self.user.id, self.app.id, "applied", occurred_on=date(2026, 1, 1))
        offer = add_stage(self.user.id, self.app.id, "offer_received",
                          occurred_on=date(2026, 2, 1))
        delete_stage(self.user.id, offer.id)
        self.assertEqual(get_application(self.user.id, self.app.id).status, "applied")
        # Removing the last stage reverts to "saved".
        delete_stage(self.user.id, applied.id)
        self.assertEqual(get_application(self.user.id, self.app.id).status, "saved")

    def test_default_occurred_on_is_today(self):
        from services.stages import add_stage

        s = add_stage(self.user.id, self.app.id, "applied")
        self.assertEqual(s.occurred_on, date.today())


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register_user()
        from services.stages import add_stage

        # App A: applied -> screen -> tech -> rejected (at tech)
        a = _save_app(self.user.id, company="A", verdict="Recommended")
        add_stage(self.user.id, a.id, "applied", occurred_on=date(2026, 1, 1))
        add_stage(self.user.id, a.id, "phone_screen", occurred_on=date(2026, 1, 5))
        add_stage(self.user.id, a.id, "technical_interview", occurred_on=date(2026, 1, 10))
        add_stage(self.user.id, a.id, "rejected", occurred_on=date(2026, 1, 12),
                  at_pipeline_stage="technical_interview")

        # App B: applied -> screen -> tech -> onsite -> offer_received -> accepted
        b = _save_app(self.user.id, company="B", verdict="Highly Recommended", light="green")
        add_stage(self.user.id, b.id, "applied", occurred_on=date(2026, 1, 2))
        add_stage(self.user.id, b.id, "phone_screen", occurred_on=date(2026, 1, 8))
        add_stage(self.user.id, b.id, "technical_interview", occurred_on=date(2026, 1, 15))
        add_stage(self.user.id, b.id, "onsite", occurred_on=date(2026, 1, 22))
        add_stage(self.user.id, b.id, "offer_received", occurred_on=date(2026, 1, 25))
        add_stage(self.user.id, b.id, "offer_accepted", occurred_on=date(2026, 1, 28))

        # App C: applied only, still active
        c = _save_app(self.user.id, company="C", verdict="Consider with Caution",
                      light="yellow")
        add_stage(self.user.id, c.id, "applied", occurred_on=date(2026, 1, 3))

        # App D: applied -> ghosted
        d = _save_app(self.user.id, company="D", verdict="Not Recommended", light="red")
        add_stage(self.user.id, d.id, "applied", occurred_on=date(2026, 1, 4))
        add_stage(self.user.id, d.id, "ghosted", occurred_on=date(2026, 2, 4))

    def test_overview_counts(self):
        from services.analytics import compute_dashboard
        o = compute_dashboard(self.user.id).overview
        self.assertEqual(o.total_applications, 4)
        self.assertEqual(o.offers_received, 1)
        self.assertEqual(o.offers_accepted, 1)
        self.assertEqual(o.rejected, 1)
        self.assertEqual(o.ghosted, 1)
        # Active = neither rejected/withdrew/ghosted nor accepted. C is active.
        self.assertEqual(o.active, 1)
        # rejection rate = rejected / (rejected + offers_received) = 1 / 2 = 0.5
        self.assertEqual(o.rejection_rate, 0.5)

    def test_funnel_counts_distinct_applications(self):
        from services.analytics import compute_dashboard
        funnel = {row.stage: row.reached for row in compute_dashboard(self.user.id).funnel}
        self.assertEqual(funnel["applied"], 4)
        self.assertEqual(funnel["phone_screen"], 2)        # A, B
        self.assertEqual(funnel["technical_interview"], 2)  # A, B
        self.assertEqual(funnel["onsite"], 1)               # B
        self.assertEqual(funnel["offer_received"], 1)       # B
        self.assertEqual(funnel["offer_accepted"], 1)       # B

    def test_funnel_conversion_rates(self):
        from services.analytics import compute_dashboard
        rows = {r.stage: r.conversion_from_previous for r in compute_dashboard(self.user.id).funnel}
        self.assertIsNone(rows["applied"])
        # phone_screen / applied = 2/4 = 0.5; tech / phone = 2/2 = 1.0
        self.assertEqual(rows["phone_screen"], 0.5)
        self.assertEqual(rows["technical_interview"], 1.0)
        # onsite / tech = 1/2 = 0.5
        self.assertEqual(rows["onsite"], 0.5)

    def test_time_in_stage(self):
        from services.analytics import compute_dashboard

        tis = {
            (t.from_stage, t.to_stage): t for t in compute_dashboard(self.user.id).time_in_stage
        }
        # applied -> phone_screen: A=4d (1->5), B=6d (2->8) -> avg 5.0
        self.assertAlmostEqual(tis[("applied", "phone_screen")].average_days, 5.0)
        self.assertEqual(tis[("applied", "phone_screen")].samples, 2)
        # phone_screen -> technical: A=5d, B=7d -> avg 6.0
        self.assertAlmostEqual(tis[("phone_screen", "technical_interview")].average_days, 6.0)

    def test_verdict_outcome(self):
        from services.analytics import compute_dashboard

        v = {row.verdict: row for row in compute_dashboard(self.user.id).verdict_outcomes}
        self.assertEqual(v["Highly Recommended"].reached_offer, 1)
        self.assertEqual(v["Highly Recommended"].offer_rate, 1.0)
        self.assertEqual(v["Recommended"].rejected, 1)
        self.assertEqual(v["Recommended"].offer_rate, 0.0)
        self.assertNotIn("Recommended", {x for x in v.keys() if x == "Considered with Caution"})

    def test_rejection_stage_distribution(self):
        from services.analytics import compute_dashboard

        dist = compute_dashboard(self.user.id).rejection_stage_distribution
        # App A was rejected with at_pipeline_stage="technical_interview".
        self.assertEqual(dist.get("technical_interview"), 1)

    def test_empty_dashboard_for_new_user(self):
        from services.analytics import compute_dashboard
        from services.auth import register_user

        u = register_user("new@x.com", "longenough")
        dash = compute_dashboard(u.id)
        self.assertEqual(dash.overview.total_applications, 0)
        # Funnel skips zero-reach stages, so a new user gets an empty funnel.
        self.assertEqual(dash.funnel, [])
        self.assertEqual(dash.time_in_stage, [])
        self.assertEqual(dash.verdict_outcomes, [])
        self.assertEqual(dash.rejection_stage_distribution, {})
        self.assertEqual(dash.volume_by_week, {})

    def test_analytics_isolated_per_user(self):
        """Other users' data must not leak in."""
        from services.analytics import compute_dashboard
        from services.auth import register_user

        other = register_user("other@x.com", "longenough")
        self.assertEqual(compute_dashboard(other.id).overview.total_applications, 0)


# ---------------------------------------------------------------------------
# Alembic head includes the new table
# ---------------------------------------------------------------------------

class StagesMigrationTests(unittest.TestCase):
    def test_upgrade_creates_application_stages(self):
        from sqlalchemy import create_engine, inspect

        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "alembic.db"
            env = dict(os.environ)
            env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=project_root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode, 0,
                msg=f"alembic upgrade failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
            )
            engine = create_engine(f"sqlite:///{db_path.as_posix()}")
            self.assertIn("application_stages", set(inspect(engine).get_table_names()))


if __name__ == "__main__":
    unittest.main()
