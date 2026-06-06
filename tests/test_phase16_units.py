"""Unit tests for Phase 16: timeline data + inline diff."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date

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
# Inline word-level diff
# ---------------------------------------------------------------------------

class InlineDiffTests(unittest.TestCase):
    def test_identical_returns_plain_no_styling(self):
        from utils.diff import inline_diff_html

        text = "Jane Doe is an ML engineer."
        out = inline_diff_html(text, text)
        # No styled spans when nothing changed.
        self.assertNotIn("<span", out)
        # Original words preserved.
        self.assertIn("Jane", out)
        self.assertIn("engineer.", out)

    def test_insertion_wrapped_in_green_span(self):
        from utils.diff import inline_diff_html

        before = "I use Python."
        after = "I use Python and PyTorch."
        out = inline_diff_html(before, after)
        # The inserted words land inside an ins-style span (green-ish bg).
        self.assertIn("<span", out)
        self.assertIn("rgba(40, 180, 99", out)
        self.assertIn("PyTorch", out)

    def test_deletion_wrapped_in_red_strikethrough_span(self):
        from utils.diff import inline_diff_html

        before = "I use Python and Java."
        after = "I use Python."
        out = inline_diff_html(before, after)
        self.assertIn("rgba(231, 76, 60", out)
        self.assertIn("line-through", out)
        # "Java" survives the diff as crossed-out text.
        self.assertIn("Java", out)

    def test_replacement_emits_both_red_and_green(self):
        from utils.diff import inline_diff_html

        before = "Built on TensorFlow."
        after = "Built on PyTorch."
        out = inline_diff_html(before, after)
        self.assertIn("rgba(231, 76, 60", out)   # delete
        self.assertIn("rgba(40, 180, 99", out)   # insert
        self.assertIn("TensorFlow", out)
        self.assertIn("PyTorch", out)

    def test_newlines_become_br_tags(self):
        from utils.diff import inline_diff_html

        before = "Line one\nLine two\n"
        after = "Line one\nLine two changed\n"
        out = inline_diff_html(before, after)
        self.assertIn("<br>", out)
        self.assertIn("changed", out)

    def test_html_metacharacters_escaped(self):
        """The renderer must escape <, >, & so user content can't smuggle HTML
        into the markdown output."""
        from utils.diff import inline_diff_html

        before = "I use C++ & <script>alert(1)</script>."
        after = "I use C++ & <span>safe</span>."
        out = inline_diff_html(before, after)
        # Raw < should never appear from user content.
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("&amp;", out)
        # Our own spans are still fine.
        self.assertIn('<span style="', out)


# ---------------------------------------------------------------------------
# Timeline data builders
# ---------------------------------------------------------------------------

class TimelineTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        from services.applications import save_analysis
        from services.stages import add_stage

        self.user = _register()
        self.other = _register("other@x.com")

        # App A: applied → phone_screen → tech → onsite → offer_received → accepted
        self.app_a = save_analysis(
            self.user.id,
            {"company_name": "Stripe", "job_title": "Staff ML Engineer", "location": "Berlin"},
            {"final_report": "# r", "verdict": {}, "resume_analysis": {}},
        )
        for kind, d in [
            ("applied",             date(2026, 5, 1)),
            ("phone_screen",        date(2026, 5, 6)),
            ("technical_interview", date(2026, 5, 14)),
            ("onsite",              date(2026, 5, 21)),
            ("offer_received",      date(2026, 5, 25)),
            ("offer_accepted",      date(2026, 5, 28)),
        ]:
            add_stage(self.user.id, self.app_a.id, kind, occurred_on=d, notes=f"note for {kind}")

        # App B: applied → ghosted
        self.app_b = save_analysis(
            self.user.id,
            {"company_name": "Acme", "job_title": "ML Engineer", "location": "Berlin"},
            {"final_report": "# r", "verdict": {}, "resume_analysis": {}},
        )
        add_stage(self.user.id, self.app_b.id, "applied", occurred_on=date(2026, 4, 1))
        add_stage(self.user.id, self.app_b.id, "ghosted", occurred_on=date(2026, 5, 1))

    def test_per_application_returns_chronological_points(self):
        from services.timeline import per_application_timeline

        pts = per_application_timeline(self.user.id, self.app_a.id)
        self.assertEqual(len(pts), 6)
        # Sorted by occurred_on (already in chronological order above).
        dates = [p.occurred_on for p in pts]
        self.assertEqual(dates, sorted(dates))

    def test_per_application_label_format(self):
        from services.timeline import per_application_timeline

        pts = per_application_timeline(self.user.id, self.app_a.id)
        self.assertTrue(all(p.application_label == "Staff ML Engineer @ Stripe" for p in pts))

    def test_per_application_colors_match_stage(self):
        from services.timeline import STAGE_COLORS, per_application_timeline

        pts = per_application_timeline(self.user.id, self.app_a.id)
        # offer_accepted should be the green entry.
        offer_point = next(p for p in pts if p.kind == "offer_accepted")
        self.assertEqual(offer_point.color, STAGE_COLORS["offer_accepted"])

    def test_per_application_pipeline_index(self):
        from db.models import PIPELINE_STAGES
        from services.timeline import per_application_timeline

        pts = per_application_timeline(self.user.id, self.app_a.id)
        for p in pts:
            self.assertEqual(p.pipeline_index, PIPELINE_STAGES.index(p.kind))

        # App B has a non-pipeline ghosted stage with index -1.
        b_pts = per_application_timeline(self.user.id, self.app_b.id)
        ghosted = next(p for p in b_pts if p.kind == "ghosted")
        self.assertEqual(ghosted.pipeline_index, -1)

    def test_per_application_cross_user_returns_empty(self):
        from services.timeline import per_application_timeline
        # Ownership-checked silently.
        self.assertEqual(per_application_timeline(self.other.id, self.app_a.id), [])

    def test_cross_application_swimlane_includes_all_apps(self):
        from services.timeline import cross_application_swimlane

        rows = cross_application_swimlane(self.user.id)
        # 6 stages for A + 2 stages for B = 8 total.
        self.assertEqual(len(rows), 8)
        labels = {p.application_label for p in rows}
        self.assertEqual(
            labels,
            {"Staff ML Engineer @ Stripe", "ML Engineer @ Acme"},
        )

    def test_cross_application_swimlane_user_scoped(self):
        from services.timeline import cross_application_swimlane

        self.assertEqual(cross_application_swimlane(self.other.id), [])

    def test_swimlane_empty_for_user_with_no_apps(self):
        from services.timeline import cross_application_swimlane

        new_user = _register("blank@x.com")
        self.assertEqual(cross_application_swimlane(new_user.id), [])

    def test_points_to_records_shape(self):
        from services.timeline import per_application_timeline, points_to_records

        pts = per_application_timeline(self.user.id, self.app_a.id)
        records = points_to_records(pts)
        self.assertEqual(len(records), len(pts))
        required = {"application_id", "application_label", "kind",
                    "occurred_on", "color", "pipeline_index", "notes"}
        self.assertTrue(required.issubset(records[0].keys()))


if __name__ == "__main__":
    unittest.main()
