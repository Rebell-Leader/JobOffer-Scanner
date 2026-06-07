"""Unit tests for Phase 14: diff utility + master-CV revision diff + master CV PDF."""

from __future__ import annotations

import os
import sys
import unittest

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
# Unified diff utility
# ---------------------------------------------------------------------------

class DiffUtilityTests(unittest.TestCase):
    def test_empty_when_identical(self):
        from utils.diff import unified_diff

        text = "Jane Doe\nSenior ML Engineer\n"
        self.assertEqual(unified_diff(text, text), "")

    def test_has_differences_detects_whitespace_only_changes_as_same(self):
        from utils.diff import has_differences

        # ``has_differences`` strips outer whitespace, so a trailing newline
        # alone doesn't count as a change.
        self.assertFalse(has_differences("hello", "hello\n"))
        self.assertTrue(has_differences("hello", "hello!"))

    def test_diff_shows_addition_and_deletion(self):
        from utils.diff import unified_diff

        before = "Skills: Python, AWS\n"
        after = "Skills: Python, AWS, Kubernetes\n"
        d = unified_diff(before, after, "v1", "v2")
        self.assertIn("---", d)
        self.assertIn("+++", d)
        self.assertIn("v1", d)
        self.assertIn("v2", d)
        # The new content is on a `+` line.
        self.assertTrue(any(line.startswith("+") and "Kubernetes" in line
                            for line in d.splitlines()))

    def test_diff_preserves_newlines(self):
        """``splitlines(keepends=True)`` is important — without it ``difflib``
        complains about missing line endings."""
        from utils.diff import unified_diff

        before = "line a\nline b\nline c\n"
        after = "line a\nline b changed\nline c\n"
        d = unified_diff(before, after)
        self.assertIn("-line b\n", d)
        self.assertIn("+line b changed", d)

    def test_handles_empty_inputs(self):
        from utils.diff import unified_diff

        self.assertEqual(unified_diff("", ""), "")
        self.assertIn("+content", unified_diff("", "content\n"))
        self.assertIn("-content", unified_diff("content\n", ""))


# ---------------------------------------------------------------------------
# Master CV revision diff
# ---------------------------------------------------------------------------

class MasterCVDiffTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        self.other = _register("other@x.com")

    def test_diff_revision_against_current(self):
        from services.master_cv import (
            diff_revision_against_current,
            list_revisions,
            save_master_cv,
        )

        save_master_cv(self.user.id, "Skills: Python\n")
        save_master_cv(self.user.id, "Skills: Python, AWS\n")
        rev = list_revisions(self.user.id)[0]  # snapshot of v1
        diff = diff_revision_against_current(self.user.id, rev.id)
        self.assertIn("+Skills: Python, AWS", diff)
        self.assertIn("-Skills: Python", diff)

    def test_diff_when_current_unchanged_after_restore(self):
        """If a revision matches the current text, the diff is empty."""
        from services.master_cv import (
            diff_revision_against_current,
            list_revisions,
            restore_revision,
            save_master_cv,
        )

        save_master_cv(self.user.id, "v1\n")
        save_master_cv(self.user.id, "v2\n")
        rev_v1_snap = list_revisions(self.user.id)[0]
        restore_revision(self.user.id, rev_v1_snap.id)
        # After restore, current matches the v1 revision content.
        self.assertEqual(diff_revision_against_current(self.user.id, rev_v1_snap.id), "")

    def test_diff_cross_user_blocked(self):
        from services.master_cv import (
            MasterCVError,
            diff_revision_against_current,
            list_revisions,
            save_master_cv,
        )

        save_master_cv(self.user.id, "v1")
        save_master_cv(self.user.id, "v2")
        rev = list_revisions(self.user.id)[0]
        with self.assertRaises(MasterCVError):
            diff_revision_against_current(self.other.id, rev.id)

    def test_diff_without_active_cv_raises(self):
        from services.master_cv import (
            MasterCVError,
            delete_master_cv,
            diff_revision_against_current,
            list_revisions,
            save_master_cv,
        )

        save_master_cv(self.user.id, "v1")
        save_master_cv(self.user.id, "v2")
        rev_id = list_revisions(self.user.id)[0].id
        delete_master_cv(self.user.id)  # also cascades the revisions
        with self.assertRaises(MasterCVError):
            diff_revision_against_current(self.user.id, rev_id)


# ---------------------------------------------------------------------------
# Master CV PDF reuses markdown_to_pdf — verify it survives plain-text input
# (master CVs aren't required to be markdown)
# ---------------------------------------------------------------------------

try:
    import fpdf  # noqa: F401
    HAS_FPDF = True
except ImportError:
    HAS_FPDF = False


@unittest.skipUnless(HAS_FPDF, "fpdf2 not installed; PDF export unavailable")
class MasterCVPdfTests(unittest.TestCase):
    def test_plain_text_master_cv_renders(self):
        from services.pdf_export import markdown_to_pdf

        plain = (
            "Jane Doe\n"
            "Senior ML Engineer — Berlin\n\n"
            "Skills: Python, PyTorch, AWS, Docker\n\n"
            "Experience:\n"
            "Acme (2022-now), Globex (2019-2022)\n"
        )
        out = markdown_to_pdf(plain, title="Master CV")
        self.assertEqual(bytes(out[:5]), b"%PDF-")
        # Spot-check that the content survived to the PDF via pypdf extraction.
        import io

        from pypdf import PdfReader
        text = PdfReader(io.BytesIO(out)).pages[0].extract_text()
        for needle in ("Jane Doe", "Python", "Acme"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
