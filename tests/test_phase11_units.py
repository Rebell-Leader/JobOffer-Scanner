"""Unit tests for Phase 11: soft suggestions + master CV revision history."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
# Soft suggestions
# ---------------------------------------------------------------------------

class SuggestionBuildTests(unittest.TestCase):
    def test_skill_flag_produces_auto_appliable_suggestion(self):
        from services.constraint_check import ConstraintCheck
        from services.suggestions import build_suggestions

        check = ConstraintCheck(
            new_proper_nouns=["tensorflow", "kafka"],
            severity="review_recommended",
        )
        sugs = build_suggestions(check)
        self.assertEqual(len(sugs), 2)
        self.assertTrue(all(s.auto_appliable for s in sugs))
        self.assertTrue(all(s.kind == "skill" for s in sugs))
        self.assertIn("tensorflow", sugs[0].term)

    def test_year_flag_is_not_auto_appliable(self):
        from services.constraint_check import ConstraintCheck
        from services.suggestions import build_suggestions

        check = ConstraintCheck(
            new_years=["2018"],
            severity="review_recommended",
        )
        sugs = build_suggestions(check)
        self.assertEqual(len(sugs), 1)
        self.assertFalse(sugs[0].auto_appliable)
        self.assertEqual(sugs[0].kind, "year")

    def test_percentage_flag_is_not_auto_appliable(self):
        from services.constraint_check import ConstraintCheck
        from services.suggestions import build_suggestions

        check = ConstraintCheck(
            new_percentages=["87%"],
            severity="review_recommended",
        )
        sugs = build_suggestions(check)
        self.assertFalse(sugs[0].auto_appliable)

    def test_explanation_warns_user(self):
        from services.constraint_check import ConstraintCheck
        from services.suggestions import build_suggestions

        check = ConstraintCheck(new_proper_nouns=["x"], severity="review_recommended")
        sug = build_suggestions(check)[0]
        # Don't silently encourage adding skills the user doesn't have.
        text = sug.explanation.lower()
        self.assertTrue(
            "genuinely" in text or "actually" in text,
            msg=f"explanation should warn against lying: {sug.explanation!r}",
        )

    def test_empty_check_yields_no_suggestions(self):
        from services.constraint_check import ConstraintCheck
        from services.suggestions import build_suggestions

        self.assertEqual(build_suggestions(ConstraintCheck()), [])


class ApplySkillAdditionTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        from services.master_cv import save_master_cv
        save_master_cv(self.user.id, "Jane Doe\nSkills: Python, AWS")

    def test_appends_to_dedicated_section(self):
        from services.master_cv import get_master_cv
        from services.suggestions import apply_skill_addition

        apply_skill_addition(self.user.id, "TensorFlow")
        raw = get_master_cv(self.user.id).raw_text
        self.assertIn("Additional skills (added in-app):", raw)
        self.assertIn("- TensorFlow", raw)
        # Original Skills section untouched.
        self.assertIn("Skills: Python, AWS", raw)

    def test_idempotent_on_same_skill(self):
        from services.master_cv import get_master_cv
        from services.suggestions import apply_skill_addition

        apply_skill_addition(self.user.id, "TensorFlow")
        before = get_master_cv(self.user.id).raw_text
        apply_skill_addition(self.user.id, "tensorflow")  # different case
        after = get_master_cv(self.user.id).raw_text
        self.assertEqual(before, after)
        # Only one bullet for TensorFlow.
        self.assertEqual(after.count("TensorFlow"), 1)

    def test_multiple_skills_accumulate(self):
        from services.master_cv import get_master_cv
        from services.suggestions import apply_skill_addition

        apply_skill_addition(self.user.id, "TensorFlow")
        apply_skill_addition(self.user.id, "Kafka")
        apply_skill_addition(self.user.id, "Apache Spark")
        raw = get_master_cv(self.user.id).raw_text
        self.assertIn("- TensorFlow", raw)
        self.assertIn("- Kafka", raw)
        self.assertIn("- Apache Spark", raw)
        # One header, not three.
        self.assertEqual(raw.count("Additional skills (added in-app):"), 1)

    def test_rejects_empty_skill(self):
        from services.master_cv import MasterCVError
        from services.suggestions import apply_skill_addition

        with self.assertRaises(MasterCVError):
            apply_skill_addition(self.user.id, "   ")

    def test_no_master_cv_raises(self):
        from services.master_cv import MasterCVError, delete_master_cv
        from services.suggestions import apply_skill_addition

        delete_master_cv(self.user.id)
        with self.assertRaises(MasterCVError):
            apply_skill_addition(self.user.id, "Python")

    def test_apply_clears_constraint_flag_end_to_end(self):
        """The actual point of the feature: add a flagged skill, re-check, clean."""
        from services.constraint_check import check_tailored_output
        from services.master_cv import get_master_cv
        from services.suggestions import apply_skill_addition

        master = get_master_cv(self.user.id).raw_text
        tailored = "Used TensorFlow on the project."

        before = check_tailored_output(master, "", tailored, "")
        self.assertIn("tensorflow", before.new_proper_nouns)

        apply_skill_addition(self.user.id, "TensorFlow")
        master_after = get_master_cv(self.user.id).raw_text
        after = check_tailored_output(master_after, "", tailored, "")
        self.assertNotIn("tensorflow", after.new_proper_nouns)


# ---------------------------------------------------------------------------
# Master CV revision history
# ---------------------------------------------------------------------------

class MasterCVRevisionTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        self.other = _register("other@x.com")

    def test_first_save_creates_no_revision(self):
        from services.master_cv import list_revisions, save_master_cv

        save_master_cv(self.user.id, "v1 content here")
        self.assertEqual(list_revisions(self.user.id), [])

    def test_overwrite_snapshots_previous(self):
        from services.master_cv import list_revisions, save_master_cv

        save_master_cv(self.user.id, "v1 content here")
        save_master_cv(self.user.id, "v2 content here different")
        revs = list_revisions(self.user.id)
        self.assertEqual(len(revs), 1)
        self.assertIn("v1", revs[0].raw_text)
        self.assertEqual(revs[0].reason, "manual edit")

    def test_no_op_save_does_not_snapshot(self):
        """Saving the SAME content twice should not balloon the revision list."""
        from services.master_cv import list_revisions, save_master_cv

        save_master_cv(self.user.id, "same content")
        save_master_cv(self.user.id, "same content")
        save_master_cv(self.user.id, "same content")
        self.assertEqual(list_revisions(self.user.id), [])

    def test_reason_tag_is_recorded(self):
        from services.master_cv import list_revisions, save_master_cv

        save_master_cv(self.user.id, "v1")
        save_master_cv(self.user.id, "v2", reason="added skill: TensorFlow")
        revs = list_revisions(self.user.id)
        self.assertEqual(revs[0].reason, "added skill: TensorFlow")

    def test_revisions_newest_first(self):
        import time

        from services.master_cv import list_revisions, save_master_cv

        save_master_cv(self.user.id, "v1")
        time.sleep(0.01)
        save_master_cv(self.user.id, "v2")
        time.sleep(0.01)
        save_master_cv(self.user.id, "v3")
        revs = list_revisions(self.user.id)
        self.assertEqual(len(revs), 2)
        # Newest revision (snapshot of v2) first, then snapshot of v1.
        self.assertIn("v2", revs[0].raw_text)
        self.assertIn("v1", revs[1].raw_text)

    def test_restore_revision_promotes_content_and_snapshots_current(self):
        from services.master_cv import (
            get_master_cv,
            list_revisions,
            restore_revision,
            save_master_cv,
        )

        save_master_cv(self.user.id, "original draft")
        save_master_cv(self.user.id, "active version")
        revs = list_revisions(self.user.id)
        original_rev = revs[0]
        self.assertIn("original draft", original_rev.raw_text)

        restore_revision(self.user.id, original_rev.id)
        # Active now matches the original.
        self.assertEqual(
            get_master_cv(self.user.id).raw_text.strip(),
            "original draft",
        )
        # Restore itself snapshotted the prior active version.
        revs_after = list_revisions(self.user.id)
        self.assertTrue(
            any("before restore" in (r.reason or "") for r in revs_after),
        )
        self.assertTrue(
            any("active version" in r.raw_text for r in revs_after),
        )

    def test_restore_cross_user_blocked(self):
        from services.master_cv import (
            MasterCVError,
            list_revisions,
            restore_revision,
            save_master_cv,
        )

        save_master_cv(self.user.id, "v1")
        save_master_cv(self.user.id, "v2")
        rev = list_revisions(self.user.id)[0]
        with self.assertRaises(MasterCVError):
            restore_revision(self.other.id, rev.id)

    def test_delete_revision_cross_user_blocked(self):
        from services.master_cv import (
            MasterCVError,
            delete_revision,
            list_revisions,
            save_master_cv,
        )

        save_master_cv(self.user.id, "v1")
        save_master_cv(self.user.id, "v2")
        rev = list_revisions(self.user.id)[0]
        with self.assertRaises(MasterCVError):
            delete_revision(self.other.id, rev.id)

    def test_apply_skill_addition_creates_revision(self):
        """Sanity: the auto-apply path goes through save_master_cv, so the
        previous version gets snapshotted automatically."""
        from services.master_cv import list_revisions, save_master_cv
        from services.suggestions import apply_skill_addition

        save_master_cv(self.user.id, "Jane Doe\nSkills: Python")
        apply_skill_addition(self.user.id, "TensorFlow")
        revs = list_revisions(self.user.id)
        self.assertEqual(len(revs), 1)
        self.assertEqual(revs[0].reason, "added skill: TensorFlow")
        self.assertNotIn("TensorFlow", revs[0].raw_text)  # the SNAPSHOT is pre-add


# ---------------------------------------------------------------------------
# Migration applies cleanly
# ---------------------------------------------------------------------------

class MigrationTests(unittest.TestCase):
    def test_master_cv_revisions_table_created(self):
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
            self.assertIn("master_cv_revisions", tables)


if __name__ == "__main__":
    unittest.main()
