"""Unit tests for Phase 9: master CV + projects + tailored CV/cover letter."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
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


def _save_app(user_id, company="Acme", title="ML Engineer", with_analysis=True):
    from services.applications import save_analysis
    return save_analysis(
        user_id,
        {"company_name": company, "job_title": title, "location": "Berlin"},
        {
            "final_report": "# r",
            "verdict": {"verdict": "Recommended", "light": "green", "reasons": []},
            "resume_analysis": {"ats_score": 70},
            "job_details": {
                "extracted_details": {
                    "company_name": company,
                    "job_title": title,
                    "location": "Berlin",
                },
                "requirements_analysis": {
                    "technical_skills": ["Python", "AWS", "Docker"],
                    "soft_skills": ["communication"],
                },
            },
        } if with_analysis else {},
    )


SAMPLE_CV = (
    "Jane Doe\n"
    "Senior ML Engineer\n\n"
    "Summary: 7 years of ML engineering experience at scale.\n\n"
    "Skills: Python, PyTorch, AWS, Kubernetes, Docker\n\n"
    "Experience:\n"
    "- Senior ML Engineer at Acme (2022-present) — built recsys serving 10M users\n"
    "- ML Engineer at Globex (2019-2022) — production NLP pipelines\n\n"
    "Education: BS Computer Science, MIT, 2019"
)


# ---------------------------------------------------------------------------
# Master CV CRUD
# ---------------------------------------------------------------------------

class MasterCVTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()

    def test_save_then_get(self):
        from services.master_cv import get_master_cv, save_master_cv

        rec = save_master_cv(self.user.id, SAMPLE_CV)
        self.assertIn("Jane Doe", rec.raw_text)
        fetched = get_master_cv(self.user.id)
        self.assertEqual(fetched.raw_text, rec.raw_text)

    def test_save_empty_rejected(self):
        from services.master_cv import MasterCVError, save_master_cv

        with self.assertRaises(MasterCVError):
            save_master_cv(self.user.id, "  \n  ")

    def test_update_does_not_wipe_structured_unless_explicit(self):
        from services.master_cv import get_master_cv, save_master_cv

        save_master_cv(self.user.id, SAMPLE_CV, structured={"skills": ["Python"]})
        # Text-only update keeps the previous structured projection.
        save_master_cv(self.user.id, SAMPLE_CV + "\nNew line")
        rec = get_master_cv(self.user.id)
        self.assertEqual(rec.structured, {"skills": ["Python"]})
        # Explicit empty dict clears it.
        save_master_cv(self.user.id, SAMPLE_CV, structured={})
        self.assertEqual(get_master_cv(self.user.id).structured, {})

    def test_one_master_cv_per_user(self):
        from sqlalchemy import select

        from db.models import MasterCV
        from db.session import get_session
        from services.master_cv import save_master_cv

        save_master_cv(self.user.id, SAMPLE_CV)
        save_master_cv(self.user.id, SAMPLE_CV + "\nMore")  # overwrites
        with get_session() as s:
            rows = s.execute(select(MasterCV).where(MasterCV.user_id == self.user.id)).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertIn("More", rows[0].raw_text)

    def test_delete(self):
        from services.master_cv import delete_master_cv, get_master_cv, save_master_cv

        save_master_cv(self.user.id, SAMPLE_CV)
        delete_master_cv(self.user.id)
        self.assertIsNone(get_master_cv(self.user.id))

    def test_parse_uses_llm_and_persists(self):
        import services.master_cv as mod

        mod.save_master_cv(self.user.id, SAMPLE_CV)
        fake_json = '{"name":"Jane Doe","skills":["Python","AWS"],"experience":[],"education":[]}'
        with mock.patch.object(mod, "get_completion", return_value=fake_json):
            parsed = mod.parse_master_cv(self.user.id)
        self.assertEqual(parsed["name"], "Jane Doe")
        # Was persisted alongside the raw text.
        rec = mod.get_master_cv(self.user.id)
        self.assertEqual(rec.structured["name"], "Jane Doe")
        # Wrapping happened — verify by re-running with a prompt assertion.
        captured = {}
        def grab(prompt, model="detailed"):
            captured["prompt"] = prompt
            return fake_json
        with mock.patch.object(mod, "get_completion", side_effect=grab):
            mod.parse_master_cv(self.user.id)
        self.assertIn("<<<BEGIN_UNTRUSTED>>>", captured["prompt"])
        self.assertIn("master_cv", captured["prompt"])

    def test_parse_invalid_json_raises(self):
        import services.master_cv as mod

        mod.save_master_cv(self.user.id, SAMPLE_CV)
        with mock.patch.object(mod, "get_completion", return_value="not json"):
            with self.assertRaises(mod.MasterCVError):
                mod.parse_master_cv(self.user.id)


# ---------------------------------------------------------------------------
# Project gallery CRUD
# ---------------------------------------------------------------------------

class ProjectTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        self.other = _register("other@x.com")

    def test_create_and_list(self):
        from services.projects import create_project, list_projects

        p = create_project(
            self.user.id,
            title="recsys",
            role="lead",
            tech_stack="Python, PyTorch",
            highlights="reduced latency by 40%\nrolled out to 10M users",
        )
        self.assertEqual(p.title, "recsys")
        rows = list_projects(self.user.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].highlights, ["reduced latency by 40%", "rolled out to 10M users"])

    def test_empty_title_rejected(self):
        from services.projects import ProjectError, create_project

        with self.assertRaises(ProjectError):
            create_project(self.user.id, title="   ")

    def test_update_and_delete(self):
        from services.projects import create_project, delete_project, list_projects, update_project

        p = create_project(self.user.id, title="recsys")
        update_project(self.user.id, p.id, title="recsys v2", summary="new")
        rows = list_projects(self.user.id)
        self.assertEqual(rows[0].title, "recsys v2")
        self.assertEqual(rows[0].summary, "new")
        delete_project(self.user.id, p.id)
        self.assertEqual(list_projects(self.user.id), [])

    def test_cross_user_isolation(self):
        from services.projects import (
            ProjectError,
            create_project,
            delete_project,
            list_projects,
            update_project,
        )

        p = create_project(self.user.id, title="mine")
        self.assertEqual(list_projects(self.other.id), [])
        with self.assertRaises(ProjectError):
            update_project(self.other.id, p.id, title="theirs now")
        with self.assertRaises(ProjectError):
            delete_project(self.other.id, p.id)

    def test_projects_as_text_renders_all_fields(self):
        from services.projects import create_project, projects_as_text

        create_project(
            self.user.id,
            title="recsys", role="lead", tech_stack="Python",
            summary="recommendation system",
            highlights=["scaled to 10M"], url="https://example.com",
        )
        text = projects_as_text(self.user.id)
        for needle in ("recsys", "lead", "Python", "recommendation", "10M", "example.com"):
            self.assertIn(needle, text)

    def test_projects_as_text_empty(self):
        from services.projects import projects_as_text
        self.assertIn("no projects", projects_as_text(self.user.id))


# ---------------------------------------------------------------------------
# Tailoring — the actual constraint enforcement
# ---------------------------------------------------------------------------

class TailoringTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        self.other = _register("other@x.com")
        self.app = _save_app(self.user.id)

    def test_generate_tailored_cv_requires_master_cv(self):
        from services.tailoring import TailoringError, generate_tailored_cv

        with self.assertRaises(TailoringError):
            generate_tailored_cv(self.user.id, self.app.id)

    def test_tailored_cv_prompt_contains_constraints(self):
        """The no-fabrication rules MUST appear verbatim in the generated prompt."""
        from services.tailoring import (
            build_tailored_cv_prompt,
        )

        prompt = build_tailored_cv_prompt(
            job_context="Title: ML Engineer\nCompany: Acme\nRequired: Python, AWS",
            master_cv_raw=SAMPLE_CV,
            projects_text="(no projects)",
        )
        # Critical phrases that must persist.
        for phrase in [
            "Use ONLY facts present",
            "You MUST NOT invent",
            "skills, technologies, or tools the candidate has not listed",
            "employers, job titles, dates",
            "quantitative claims",
            "projects, products, or open-source contributions",
            "If the job posting asks for something the candidate doesn't have, do NOT",
        ]:
            self.assertIn(phrase, prompt, msg=f"missing constraint phrase: {phrase!r}")
        # Untrusted-wrapping on user-supplied data.
        self.assertIn("<<<BEGIN_UNTRUSTED>>>", prompt)
        # Sanity: the CV and job context are actually inside.
        self.assertIn("Jane Doe", prompt)
        self.assertIn("ML Engineer", prompt)
        self.assertIn("Python, AWS", prompt)

    def test_cover_letter_prompt_contains_constraints(self):
        from services.tailoring import build_cover_letter_prompt

        prompt = build_cover_letter_prompt(
            job_context="Title: ML Engineer\nCompany: Acme",
            master_cv_raw=SAMPLE_CV,
            projects_text="(no projects)",
            tone="warm",
        )
        for phrase in [
            "Use ONLY facts present",
            "You MUST NOT invent",
            "No clichés",
        ]:
            self.assertIn(phrase, prompt)
        self.assertIn("Tone: warm", prompt)
        self.assertIn("<<<BEGIN_UNTRUSTED>>>", prompt)

    def test_generate_tailored_cv_persists_artifact(self):
        import services.tailoring as mod
        from services.master_cv import save_master_cv

        save_master_cv(self.user.id, SAMPLE_CV)
        with mock.patch.object(mod, "get_completion", return_value="# Tailored CV\n\nGreat fit."):
            art = mod.generate_tailored_cv(self.user.id, self.app.id)
        self.assertEqual(art.kind, "tailored_cv")
        self.assertIn("Great fit", art.content)
        # Persisted and listable.
        from services.tailoring import list_artifacts
        rows = list_artifacts(self.user.id, self.app.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, art.id)

    def test_multiple_generations_keep_history(self):
        import services.tailoring as mod
        from services.master_cv import save_master_cv

        save_master_cv(self.user.id, SAMPLE_CV)
        with mock.patch.object(mod, "get_completion", side_effect=["v1", "v2", "v3"]):
            mod.generate_tailored_cv(self.user.id, self.app.id)
            mod.generate_tailored_cv(self.user.id, self.app.id)
            mod.generate_tailored_cv(self.user.id, self.app.id)
        rows = mod.list_artifacts(self.user.id, self.app.id)
        self.assertEqual(len(rows), 3)
        # Newest-first ordering.
        self.assertEqual(rows[0].content, "v3")

    def test_generate_uses_real_job_context(self):
        """The prompt must include extracted job details from the saved analysis."""
        import services.tailoring as mod
        from services.master_cv import save_master_cv

        save_master_cv(self.user.id, SAMPLE_CV)
        captured = {}
        def grab(prompt, model="detailed"):
            captured["prompt"] = prompt
            return "tailored output"
        with mock.patch.object(mod, "get_completion", side_effect=grab):
            mod.generate_tailored_cv(self.user.id, self.app.id)
        # Required skills from the analysis flow into the prompt.
        self.assertIn("Python", captured["prompt"])
        self.assertIn("AWS", captured["prompt"])
        self.assertIn("Docker", captured["prompt"])
        # Job title and company.
        self.assertIn("ML Engineer", captured["prompt"])
        self.assertIn("Acme", captured["prompt"])

    def test_artifact_ownership_isolation(self):
        import services.tailoring as mod
        from services.master_cv import save_master_cv

        save_master_cv(self.user.id, SAMPLE_CV)
        with mock.patch.object(mod, "get_completion", return_value="content"):
            art = mod.generate_tailored_cv(self.user.id, self.app.id)
        # Other user can't list / read / delete this artifact.
        with self.assertRaises(mod.TailoringError):
            mod.list_artifacts(self.other.id, self.app.id)
        with self.assertRaises(mod.TailoringError):
            mod.delete_artifact(self.other.id, art.id)
        # Other user can't generate against this application either.
        from services.master_cv import save_master_cv as save_other
        save_other(self.other.id, "their cv text here")
        with self.assertRaises(mod.TailoringError):
            mod.generate_tailored_cv(self.other.id, self.app.id)

    def test_preview_mode_does_not_persist(self):
        import services.tailoring as mod
        from services.master_cv import save_master_cv

        save_master_cv(self.user.id, SAMPLE_CV)
        with mock.patch.object(mod, "get_completion", return_value="preview only"):
            art = mod.generate_tailored_cv(
                self.user.id, self.app.id, persist=False
            )
        self.assertEqual(art.id, -1)
        rows = mod.list_artifacts(self.user.id, self.app.id)
        self.assertEqual(len(rows), 0)


# ---------------------------------------------------------------------------
# Alembic migration covers the new tables
# ---------------------------------------------------------------------------

class Phase9MigrationTests(unittest.TestCase):
    def test_upgrade_creates_master_cv_projects_artifacts(self):
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
            tables = set(inspect(create_engine(f"sqlite:///{db_path.as_posix()}")).get_table_names())
            self.assertIn("master_cvs", tables)
            self.assertIn("projects", tables)
            self.assertIn("application_artifacts", tables)


if __name__ == "__main__":
    unittest.main()
