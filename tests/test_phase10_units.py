"""Unit tests for Phase 10: constraint-check + tone presets + auto-check + recheck."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_db():
    from db.session import reset_engine_for_testing
    from services.rate_limit import reset_backend_for_testing
    reset_engine_for_testing("sqlite:///:memory:")
    reset_backend_for_testing()


# ---------------------------------------------------------------------------
# Token extraction primitives
# ---------------------------------------------------------------------------

class TokenExtractionTests(unittest.TestCase):
    def test_extract_proper_nouns_basic(self):
        from services.constraint_check import extract_proper_nouns

        nouns = extract_proper_nouns("Built features in Python and PyTorch on AWS.")
        # "python", "pytorch", "aws" are skill-shaped and not in COMMON_WORDS.
        self.assertIn("python", nouns)
        self.assertIn("pytorch", nouns)
        self.assertIn("aws", nouns)

    def test_extract_handles_punctuation_skills(self):
        from services.constraint_check import extract_proper_nouns

        nouns = extract_proper_nouns("Wrote C++ and C# microservices; deployed via Node.js.")
        self.assertIn("c++", nouns)
        self.assertIn("c#", nouns)
        self.assertIn("node.js", nouns)

    def test_extract_skips_common_section_words(self):
        from services.constraint_check import extract_proper_nouns

        nouns = extract_proper_nouns("# Summary\n## Skills\n## Experience")
        for w in ("summary", "skills", "experience"):
            self.assertNotIn(w, nouns)

    def test_extract_years(self):
        from services.constraint_check import extract_years

        years = extract_years("Worked at Acme 2019-2022 and Globex 2022-present.")
        self.assertEqual(years, {"2019", "2022"})

    def test_extract_percentages(self):
        from services.constraint_check import extract_percentages

        self.assertEqual(extract_percentages("Cut latency 40%"), {"40%"})
        self.assertEqual(extract_percentages("Cut latency 12.5%"), {"12.5%"})

    def test_extract_quantitative_claims(self):
        from services.constraint_check import extract_quantitative_claims

        text = "Serving 10M users with $50M ARR, achieved 3x growth."
        claims = extract_quantitative_claims(text)
        self.assertTrue(any("10m" in c and "users" in c for c in claims))
        self.assertTrue(any("growth" in c for c in claims))


# ---------------------------------------------------------------------------
# check_tailored_output — the actual circuit breaker
# ---------------------------------------------------------------------------

MASTER_CV = (
    "Jane Doe — Senior ML Engineer.\n"
    "Skills: Python, PyTorch, AWS, Kubernetes.\n"
    "Experience: Senior ML Engineer at Acme (2022-present), ML Engineer at Globex (2019-2022).\n"
    "Education: BS Computer Science, MIT, 2019."
)

PROJECTS = (
    "### Recsys\n- Role: lead\n- Tech: Python\n- Highlights: rolled out to 10M users\n"
)

JOB_CONTEXT = (
    "Company: Stripe\nTitle: Staff ML Engineer\nLocation: Berlin\n"
    "Required technical skills: Python, PyTorch, distributed systems"
)


class ConstraintCheckTests(unittest.TestCase):
    def test_clean_when_tailored_stays_within_sources(self):
        from services.constraint_check import check_tailored_output

        tailored = (
            "# Jane Doe — Staff ML Engineer\n\n"
            "Python and PyTorch on AWS at Acme (2022). MIT 2019."
        )
        check = check_tailored_output(MASTER_CV, PROJECTS, tailored, JOB_CONTEXT)
        self.assertTrue(check.is_clean, msg=check.to_dict())
        self.assertEqual(check.total_flags, 0)

    def test_flags_invented_skill(self):
        from services.constraint_check import check_tailored_output

        # "TensorFlow" is NOT in the master CV (only PyTorch is) and NOT in the job context.
        tailored = "Skills: Python, TensorFlow, AWS."
        check = check_tailored_output(MASTER_CV, PROJECTS, tailored, JOB_CONTEXT)
        self.assertFalse(check.is_clean)
        self.assertIn("tensorflow", check.new_proper_nouns)

    def test_flags_invented_year(self):
        from services.constraint_check import check_tailored_output

        tailored = "Worked at Acme since 2018."
        check = check_tailored_output(MASTER_CV, PROJECTS, tailored, JOB_CONTEXT)
        self.assertFalse(check.is_clean)
        self.assertIn("2018", check.new_years)

    def test_flags_invented_percentage(self):
        from services.constraint_check import check_tailored_output

        tailored = "Reduced latency by 87%."
        check = check_tailored_output(MASTER_CV, PROJECTS, tailored, JOB_CONTEXT)
        self.assertFalse(check.is_clean)
        self.assertIn("87%", check.new_percentages)

    def test_job_context_company_not_flagged(self):
        """Echoing the target company name back must NOT trigger the check."""
        from services.constraint_check import check_tailored_output

        tailored = "I'm excited to join Stripe in Berlin."
        check = check_tailored_output(MASTER_CV, PROJECTS, tailored, JOB_CONTEXT)
        # Stripe and Berlin are both in JOB_CONTEXT and should be allowed.
        self.assertNotIn("stripe", check.new_proper_nouns)
        self.assertNotIn("berlin", check.new_proper_nouns)

    def test_required_skill_from_job_context_not_flagged(self):
        """A required skill the candidate also has shouldn't get flagged."""
        from services.constraint_check import check_tailored_output

        # "distributed systems" is in the job context — even though the master
        # CV doesn't list it verbatim, mentioning it in the tailored output
        # while honestly framing related experience shouldn't trigger a flag.
        tailored = "Built distributed systems with Python at Acme."
        check = check_tailored_output(MASTER_CV, PROJECTS, tailored, JOB_CONTEXT)
        self.assertNotIn("distributed", check.new_proper_nouns)

    def test_serialization_roundtrip(self):
        from services.constraint_check import ConstraintCheck, check_tailored_output

        tailored = "Skills: Python, Tensorflow.\nWorked 2018."
        check = check_tailored_output(MASTER_CV, PROJECTS, tailored, JOB_CONTEXT)
        d = check.to_dict()
        restored = ConstraintCheck.from_dict(d)
        self.assertEqual(restored.severity, check.severity)
        self.assertEqual(restored.new_proper_nouns, check.new_proper_nouns)
        self.assertEqual(restored.new_years, check.new_years)
        self.assertEqual(restored.total_flags, check.total_flags)

    def test_from_dict_handles_none(self):
        """Artifacts predating the post-check have no meta entry."""
        from services.constraint_check import ConstraintCheck

        c = ConstraintCheck.from_dict(None)
        self.assertTrue(c.is_clean)

    def test_summarize_clean(self):
        from services.constraint_check import ConstraintCheck, summarize

        self.assertIn("No new facts", summarize(ConstraintCheck()))

    def test_summarize_with_flags(self):
        from services.constraint_check import ConstraintCheck, summarize

        c = ConstraintCheck(
            new_proper_nouns=["tensorflow", "kafka"],
            new_years=["2018"],
            severity="review_recommended",
        )
        text = summarize(c)
        self.assertIn("Review recommended", text)
        self.assertIn("tensorflow", text)
        self.assertIn("2018", text)


# ---------------------------------------------------------------------------
# Tailoring auto-check + recheck
# ---------------------------------------------------------------------------

class TailoringIntegrationTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        from services.applications import save_analysis
        from services.auth import register_user
        from services.master_cv import save_master_cv

        self.user = register_user("u@x.com", "longenough")
        self.other = register_user("other@x.com", "longenough")
        save_master_cv(self.user.id, MASTER_CV)
        self.app = save_analysis(
            self.user.id,
            {"company_name": "Stripe", "job_title": "Staff ML Engineer", "location": "Berlin"},
            {
                "final_report": "# r",
                "verdict": {"verdict": "Recommended", "light": "green", "reasons": []},
                "resume_analysis": {"ats_score": 70},
                "job_details": {
                    "extracted_details": {
                        "company_name": "Stripe", "job_title": "Staff ML Engineer",
                        "location": "Berlin",
                    },
                    "requirements_analysis": {
                        "technical_skills": ["Python", "PyTorch", "distributed systems"],
                    },
                },
            },
        )

    def test_clean_generation_has_no_flags_in_meta(self):
        import services.tailoring as tailoring

        clean_output = (
            "# Jane Doe\n## Summary\nML engineer with Python and PyTorch experience at Acme."
        )
        with mock.patch.object(tailoring, "get_completion", return_value=clean_output):
            art = tailoring.generate_tailored_cv(self.user.id, self.app.id)
        check = art.meta["constraint_check"]
        self.assertEqual(check["severity"], "clean")
        self.assertEqual(check["total_flags"], 0)

    def test_invented_skill_flagged_in_meta(self):
        import services.tailoring as tailoring

        bad_output = "# Jane Doe\nSkills: Python, TensorFlow, Apache Spark.\nAt Acme."
        with mock.patch.object(tailoring, "get_completion", return_value=bad_output):
            art = tailoring.generate_tailored_cv(self.user.id, self.app.id)
        check = art.meta["constraint_check"]
        self.assertEqual(check["severity"], "review_recommended")
        # TensorFlow and Spark are not in master CV / projects / job context.
        flagged = set(check["new_proper_nouns"])
        self.assertIn("tensorflow", flagged)
        # "Apache Spark" extraction may land as "spark" or "apache spark"; one of them.
        self.assertTrue("spark" in flagged or "apache spark" in flagged)

    def test_cover_letter_uses_tone_and_checks(self):
        import services.tailoring as tailoring

        captured = {}
        def grab(prompt, model="detailed"):
            captured["prompt"] = prompt
            return "Dear hiring manager, I have Python and PyTorch experience at Acme."

        with mock.patch.object(tailoring, "get_completion", side_effect=grab):
            art = tailoring.generate_cover_letter(
                self.user.id, self.app.id, tone="warm"
            )
        self.assertIn("Tone: warm", captured["prompt"])
        self.assertEqual(art.meta["tone"], "warm")
        self.assertIn("constraint_check", art.meta)

    def test_recheck_runs_against_current_master_cv(self):
        """If the user later edits the master CV to include a flagged skill,
        a re-check should clear the flag without regenerating the artifact."""
        import services.tailoring as tailoring
        from services.master_cv import save_master_cv

        bad_output = "# Jane Doe\nSkills: Python, TensorFlow.\nAt Acme."
        with mock.patch.object(tailoring, "get_completion", return_value=bad_output):
            art = tailoring.generate_tailored_cv(self.user.id, self.app.id)
        self.assertEqual(art.meta["constraint_check"]["severity"], "review_recommended")
        self.assertIn("tensorflow", art.meta["constraint_check"]["new_proper_nouns"])

        # User updates the master CV to include TensorFlow honestly.
        save_master_cv(self.user.id, MASTER_CV + "\nAlso experienced with TensorFlow.")

        new_check = tailoring.recheck_artifact(self.user.id, art.id)
        self.assertTrue(new_check.is_clean)
        # Persisted update too — list_artifacts reflects the new meta.
        rows = tailoring.list_artifacts(self.user.id, self.app.id)
        self.assertEqual(rows[0].meta["constraint_check"]["severity"], "clean")

    def test_recheck_ownership_isolation(self):
        import services.tailoring as tailoring

        with mock.patch.object(tailoring, "get_completion", return_value="content"):
            art = tailoring.generate_tailored_cv(self.user.id, self.app.id)
        with self.assertRaises(tailoring.TailoringError):
            tailoring.recheck_artifact(self.other.id, art.id)

    def test_cover_letter_tones_constant_exposed(self):
        from services.tailoring import COVER_LETTER_TONES

        self.assertIn("professional", COVER_LETTER_TONES)
        self.assertIn("warm", COVER_LETTER_TONES)
        self.assertGreaterEqual(len(COVER_LETTER_TONES), 4)


if __name__ == "__main__":
    unittest.main()
