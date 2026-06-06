"""Unit tests for Phase 13: bulk project + application import (CSV + LLM)."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date
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
# Project CSV parse
# ---------------------------------------------------------------------------

class ProjectCsvTests(unittest.TestCase):
    def test_parse_basic_csv(self):
        from services.bulk_import import parse_projects_csv

        csv = (
            "title,role,tech_stack,summary,highlights,url\n"
            "Recsys,Lead,Python|PyTorch,Recommendation system,1.2k stars|10M users,https://x.com\n"
            "OSS lib,creator,Go,Library,, \n"
        )
        out = parse_projects_csv(csv)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["title"], "Recsys")
        self.assertEqual(out[0]["highlights"], ["1.2k stars", "10M users"])
        self.assertEqual(out[0]["url"], "https://x.com")
        self.assertIsNone(out[1]["url"])

    def test_parse_rejects_missing_title_column(self):
        from services.bulk_import import BulkImportError, parse_projects_csv

        with self.assertRaises(BulkImportError):
            parse_projects_csv("name,tech\nFoo,Python\n")

    def test_skips_empty_title_rows(self):
        from services.bulk_import import parse_projects_csv

        csv = "title,role\nReal,lead\n,\n"
        out = parse_projects_csv(csv)
        self.assertEqual(len(out), 1)

    def test_empty_input(self):
        from services.bulk_import import parse_projects_csv
        self.assertEqual(parse_projects_csv(""), [])


# ---------------------------------------------------------------------------
# Project free-form parse (LLM-mocked)
# ---------------------------------------------------------------------------

class ProjectFreeformTests(unittest.TestCase):
    def test_parses_clean_json(self):
        import services.bulk_import as mod

        payload = """[
          {"title":"Recsys","role":"lead","tech_stack":"Python","summary":"s",
           "highlights":["fast","big"],"url":"https://x.com"}
        ]"""
        with mock.patch.object(mod, "get_completion", return_value=payload):
            out = mod.parse_projects_freeform("any text")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Recsys")
        self.assertEqual(out[0]["highlights"], ["fast", "big"])

    def test_strips_code_fences(self):
        import services.bulk_import as mod

        wrapped = '```json\n[{"title":"X"}]\n```'
        with mock.patch.object(mod, "get_completion", return_value=wrapped):
            out = mod.parse_projects_freeform("any text")
        self.assertEqual(out[0]["title"], "X")

    def test_rejects_non_json(self):
        import services.bulk_import as mod

        with mock.patch.object(mod, "get_completion", return_value="not json"):
            with self.assertRaises(mod.BulkImportError):
                mod.parse_projects_freeform("anything")

    def test_rejects_non_array(self):
        import services.bulk_import as mod

        with mock.patch.object(mod, "get_completion", return_value='{"not":"array"}'):
            with self.assertRaises(mod.BulkImportError):
                mod.parse_projects_freeform("anything")

    def test_prompt_wraps_untrusted_and_forbids_invention(self):
        """The free-form parse prompt must include the no-fabrication phrasing
        and the user-supplied text inside the untrusted wrapper."""
        import services.bulk_import as mod

        captured = {}
        def grab(prompt, model="detailed"):
            captured["prompt"] = prompt
            return "[]"
        with mock.patch.object(mod, "get_completion", side_effect=grab):
            mod.parse_projects_freeform("My portfolio paste")
        self.assertIn("Do NOT invent", captured["prompt"])
        self.assertIn("<<<BEGIN_UNTRUSTED>>>", captured["prompt"])
        self.assertIn("My portfolio paste", captured["prompt"])


# ---------------------------------------------------------------------------
# Project save_projects (persistence)
# ---------------------------------------------------------------------------

class ProjectSaveTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()

    def test_save_persists_all_previews(self):
        from services.bulk_import import save_projects
        from services.projects import list_projects

        previews = [
            {"title": "A", "role": None, "tech_stack": None, "summary": None,
             "highlights": [], "url": None},
            {"title": "B", "role": "lead", "tech_stack": "Python",
             "summary": "B summary", "highlights": ["one", "two"], "url": "https://b"},
        ]
        saved = save_projects(self.user.id, previews)
        self.assertEqual(len(saved), 2)
        rows = list_projects(self.user.id)
        self.assertEqual({r.title for r in rows}, {"A", "B"})


# ---------------------------------------------------------------------------
# Application CSV parse
# ---------------------------------------------------------------------------

class ApplicationCsvTests(unittest.TestCase):
    def test_parse_basic_csv(self):
        from services.bulk_import import parse_applications_csv

        csv = (
            "company_name,job_title,location,applied_on,status,verdict,notes\n"
            "Acme,ML Eng,Berlin,2026-03-12,interviewing,Recommended,Strong screen\n"
            "Globex,Backend Eng,Remote,2026-04-01,applied,,\n"
        )
        out = parse_applications_csv(csv)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["company_name"], "Acme")
        self.assertEqual(out[0]["applied_on"], date(2026, 3, 12))
        self.assertEqual(out[0]["status"], "interviewing")
        self.assertEqual(out[1]["applied_on"], date(2026, 4, 1))

    def test_parse_rejects_missing_required_columns(self):
        from services.bulk_import import BulkImportError, parse_applications_csv

        with self.assertRaises(BulkImportError):
            parse_applications_csv("company,role\nAcme,Eng\n")

    def test_unknown_status_falls_back_to_applied(self):
        from services.bulk_import import parse_applications_csv

        csv = "company_name,job_title,status\nAcme,Eng,ghosted-by-recruiter\n"
        out = parse_applications_csv(csv)
        self.assertEqual(out[0]["status"], "applied")

    def test_accepts_multiple_date_formats(self):
        from services.bulk_import import parse_applications_csv

        csv = (
            "company_name,job_title,applied_on\n"
            "A,Eng,2026-03-12\n"
            "B,Eng,03/12/2026\n"
            "C,Eng,not-a-date\n"
        )
        out = parse_applications_csv(csv)
        self.assertEqual(out[0]["applied_on"], date(2026, 3, 12))
        self.assertEqual(out[1]["applied_on"], date(2026, 3, 12))
        self.assertIsNone(out[2]["applied_on"])


# ---------------------------------------------------------------------------
# Application free-form parse + save_applications
# ---------------------------------------------------------------------------

class ApplicationFreeformAndSaveTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()

    def test_freeform_parse(self):
        import services.bulk_import as mod

        payload = """[
          {"company_name":"Stripe","job_title":"Staff ML Eng","location":"Berlin",
           "applied_on":"2026-03-01","status":"interviewing","verdict":"Recommended","notes":"Phone screen Mar 5"}
        ]"""
        with mock.patch.object(mod, "get_completion", return_value=payload):
            out = mod.parse_applications_freeform("any paste")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["status"], "interviewing")
        self.assertEqual(out[0]["applied_on"], date(2026, 3, 1))

    def test_save_persists_and_creates_stages_matching_status(self):
        """Stage events are materialized so the imported status survives the
        auto-status-sync that runs whenever add_stage is called."""
        from services.applications import list_applications
        from services.bulk_import import save_applications
        from services.stages import list_stages

        previews = [
            {
                "company_name": "Stripe", "job_title": "ML Engineer",
                "location": "Berlin", "applied_on": date(2026, 3, 1),
                "status": "interviewing", "verdict": "Recommended",
                "notes": None,
            },
        ]
        ids = save_applications(self.user.id, previews)
        self.assertEqual(len(ids), 1)

        apps = list_applications(self.user.id)
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0].company_name, "Stripe")
        # Imported status MUST survive auto-sync — this was a real bug.
        self.assertEqual(apps[0].status, "interviewing")
        self.assertTrue(apps[0].analysis_json.get("_imported"))

        stages = list_stages(self.user.id, apps[0].id)
        kinds = [s.kind for s in stages]
        self.assertEqual(kinds, ["applied", "phone_screen"])
        self.assertTrue(all(s.occurred_on == date(2026, 3, 1) for s in stages))

    def test_save_offer_status_creates_offer_stage(self):
        from services.applications import list_applications
        from services.bulk_import import save_applications
        from services.stages import list_stages

        previews = [{
            "company_name": "Acme", "job_title": "Eng", "location": None,
            "applied_on": date(2026, 4, 1), "status": "offer",
            "verdict": None, "notes": None,
        }]
        save_applications(self.user.id, previews)
        apps = list_applications(self.user.id)
        self.assertEqual(apps[0].status, "offer")
        kinds = [s.kind for s in list_stages(self.user.id, apps[0].id)]
        self.assertEqual(kinds, ["applied", "offer_received"])

    def test_save_rejected_status_creates_rejected_stage(self):
        from services.applications import list_applications
        from services.bulk_import import save_applications
        from services.stages import list_stages

        previews = [{
            "company_name": "Acme", "job_title": "Eng", "location": None,
            "applied_on": date(2026, 4, 1), "status": "rejected",
            "verdict": None, "notes": None,
        }]
        save_applications(self.user.id, previews)
        apps = list_applications(self.user.id)
        self.assertEqual(apps[0].status, "rejected")
        kinds = [s.kind for s in list_stages(self.user.id, apps[0].id)]
        self.assertEqual(kinds, ["applied", "rejected"])

    def test_save_without_date_skips_stage(self):
        from services.applications import list_applications
        from services.bulk_import import save_applications
        from services.stages import list_stages

        previews = [
            {
                "company_name": "A", "job_title": "B",
                "location": None, "applied_on": None,
                "status": "saved", "verdict": None, "notes": None,
            },
        ]
        save_applications(self.user.id, previews)
        apps = list_applications(self.user.id)
        self.assertEqual(list_stages(self.user.id, apps[0].id), [])

    def test_application_freeform_prompt_carries_no_invention_constraint(self):
        import services.bulk_import as mod

        captured = {}
        def grab(prompt, model="detailed"):
            captured["prompt"] = prompt
            return "[]"
        with mock.patch.object(mod, "get_completion", side_effect=grab):
            mod.parse_applications_freeform("My past apps")
        self.assertIn("Do NOT invent", captured["prompt"])
        self.assertIn("<<<BEGIN_UNTRUSTED>>>", captured["prompt"])


if __name__ == "__main__":
    unittest.main()
