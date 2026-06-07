"""Phase 28: Streamlit render smoke test.

The whole 462-test suite exercises *services* but never rendered ``app.py``,
which is exactly how a nested-``st.expander`` bug (Streamlit raises
"Expanders may not be nested") shipped through CI and only surfaced on the
live Replit deployment.

This test closes that gap. It seeds a fully-populated account (an application
with stages, multiple tailored artifacts, a master CV with a revision, and a
share link — i.e. the densest nested-UI path) and renders the real ``app.py``
through Streamlit's ``AppTest`` harness, asserting the script raises no
exception. If anyone reintroduces a nested expander (or any render-time
error in the authenticated UI), this fails in CI instead of in production.

``AppTest`` executes the script in a ScriptRunner thread, so the DB must be a
StaticPool in-memory SQLite (one shared connection across threads) — which
``reset_engine_for_testing`` already provides.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from streamlit.testing.v1 import AppTest
    HAS_APPTEST = True
except Exception:  # noqa: BLE001 - older/newer streamlit without the harness
    HAS_APPTEST = False

_APP_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "app.py")
)


def _fmt_exc(at) -> str:
    """Render AppTest exception elements into a readable message."""
    try:
        return " | ".join(str(getattr(e, "value", e)) for e in at.exception) or "(none)"
    except Exception:  # noqa: BLE001
        return str(at.exception)

def _textarea_by_label(at, label):
    for ta in at.text_area:
        if (ta.label or "") == label:
            return ta
    return None


def _button_by_label(at, label):
    for b in at.button:
        if (b.label or "") == label:
            return b
    return None


def _button_by_key(at, key):
    for b in at.button:
        if getattr(b, "key", None) == key:
            return b
    return None


def _fresh_db():
    from db.session import reset_engine_for_testing
    from services.rate_limit import reset_backend_for_testing
    reset_engine_for_testing("sqlite:///:memory:")
    reset_backend_for_testing()


def _seed_dense_account():
    """Create a user whose My Applications row exercises the full nested UI:
    stages (timeline + list), 2 artifacts (A/B compare + per-artifact toggles),
    a master CV with a revision (history + diff), and a share link.
    Returns the user record.
    """
    from datetime import date

    from services.applications import save_analysis
    from services.auth import register_user
    from services.master_cv import save_master_cv
    from services.sharing import create_share
    from services.stages import add_stage
    from services.tailoring import save_artifact

    user = register_user("smoke@x.com", "longenough")

    # Master CV + a second save so a revision exists.
    save_master_cv(user.id, "Jane Doe\nSkills: Python, AWS, PyTorch")
    save_master_cv(user.id, "Jane Doe\nSkills: Python, AWS, PyTorch, Docker")

    app_rec = save_analysis(
        user.id,
        {"company_name": "Acme", "job_title": "ML Engineer", "location": "Berlin"},
        {
            "final_report": "# Report\n\nStrong fit.",
            "verdict": {"verdict": "Recommended", "light": "green",
                        "reasons": ["good match"], "confidence": 8,
                        "source": "structured"},
            "resume_analysis": {"ats_score": 78, "matched_skills": ["Python"],
                                "missing_skills": ["Kubernetes"],
                                "format_issues": [], "commentary": "Looks good."},
            "job_details": {
                "extracted_details": {"company_name": "Acme",
                                      "job_title": "ML Engineer",
                                      "location": "Berlin"},
                "requirements_analysis": {"technical_skills": ["Python", "AWS"]},
            },
        },
    )

    add_stage(user.id, app_rec.id, "applied", occurred_on=date(2026, 5, 1))
    add_stage(user.id, app_rec.id, "phone_screen", occurred_on=date(2026, 5, 6))

    # Two artifacts of the same kind so the A/B compare UI renders.
    check = {"severity": "review_recommended", "new_proper_nouns": ["kafka"],
             "new_years": [], "new_percentages": [], "new_quantitative_claims": [],
             "total_flags": 1}
    save_artifact(user.id, app_rec.id, "tailored_cv", "# CV v1\n\n- Python",
                  meta={"model": "fast", "constraint_check": check})
    save_artifact(user.id, app_rec.id, "tailored_cv", "# CV v2\n\n- Python, AWS",
                  meta={"model": "fast", "constraint_check":
                        {"severity": "clean", "new_proper_nouns": [],
                         "new_years": [], "new_percentages": [],
                         "new_quantitative_claims": [], "total_flags": 0}})

    create_share(user.id, app_rec.id, ttl_days=7, include_artifacts=True)
    return user


@unittest.skipUnless(HAS_APPTEST, "streamlit AppTest harness unavailable")
class AppRenderSmokeTests(unittest.TestCase):
    def setUp(self):
        # No provider key -> demo mode, so no network during render.
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FEATHERLESS_API_KEY",
                  "LLM_PROVIDER"):
            os.environ.pop(k, None)
        _fresh_db()

    def _run_app(self):
        at = AppTest.from_file(_APP_PATH, default_timeout=60)
        return at

    def test_auth_gate_renders_without_error(self):
        """Unauthenticated: the login gate must render cleanly (no user_id)."""
        at = self._run_app()
        at.run()
        self.assertEqual(len(at.exception), 0, msg=_fmt_exc(at))

    def test_full_authenticated_ui_renders_without_error(self):
        """The dense nested-UI path must render without a render-time exception
        (this is the path the nested-expander bug lived in)."""
        user = _seed_dense_account()
        at = self._run_app()
        # First run hits the auth gate + st.stop(); then we sign in and re-run
        # so session_state exists exactly as the live app would have it.
        at.run()
        at.session_state["user_id"] = user.id
        at.session_state["user_email"] = user.email
        at.run()
        self.assertEqual(
            len(at.exception), 0,
            msg="app.py raised during authenticated render: " + _fmt_exc(at),
        )
        # Sanity: the app rendered tabs (>=5 top-level; more counting the
        # nested st.tabs in the CV + analyze sections).
        self.assertGreaterEqual(len(at.tabs), 5)

    def test_share_view_renders_without_error(self):
        """The public ?share= view (renders before the auth gate) must work."""
        user = _seed_dense_account()
        from services.sharing import list_shares_for_application
        from services.applications import list_applications

        app_id = list_applications(user.id)[0].id
        token = list_shares_for_application(user.id, app_id)[0].token

        at = self._run_app()
        at.query_params["share"] = token
        at.run()
        self.assertEqual(len(at.exception), 0, msg=_fmt_exc(at))

    # -- interaction paths (click, don't just render) ----------------------

    def test_analyze_submit_in_demo_mode_renders_result(self):
        """Submitting the analyze form in demo mode must run the pipeline and
        render a result without error — covers the analyze -> render_result
        callback path, not just initial render."""
        user = _seed_dense_account()
        at = self._run_app()
        at.run()
        at.session_state["user_id"] = user.id
        at.session_state["user_email"] = user.email
        at.run()
        self.assertEqual(len(at.exception), 0, msg=_fmt_exc(at))

        jd = _textarea_by_label(at, "Job description")
        self.assertIsNotNone(jd, "Job description text area not found")
        jd.set_value(
            "Company: Acme\nTitle: ML Engineer\nLocation: Berlin\n"
            "We need Python, AWS and PyTorch experience for a senior role."
        )
        submit = _button_by_label(at, "🔍 Analyze posting")
        self.assertIsNotNone(submit, "Analyze submit button not found")
        submit.click().run()

        self.assertEqual(len(at.exception), 0, msg=_fmt_exc(at))
        # The pipeline result is stashed in session_state and rendered.
        try:
            last = at.session_state["last_result"]
        except (KeyError, AttributeError):
            last = None
        self.assertIsNotNone(last, "analyze submit did not produce a result")
        self.assertTrue(last.get("final_report"))

    def test_stage_quick_action_click_adds_stage(self):
        """Clicking a stage quick-action button must add a stage + re-render
        the dense application UI without error."""
        from services.applications import list_applications
        from services.stages import list_stages

        user = _seed_dense_account()
        app_id = list_applications(user.id)[0].id
        before = len(list_stages(user.id, app_id))

        at = self._run_app()
        at.run()
        at.session_state["user_id"] = user.id
        at.session_state["user_email"] = user.email
        at.run()

        btn = _button_by_key(at, f"qa_{app_id}_technical_interview")
        self.assertIsNotNone(btn, "technical_interview quick-action not found")
        btn.click().run()

        self.assertEqual(len(at.exception), 0, msg=_fmt_exc(at))
        after = list_stages(user.id, app_id)
        self.assertEqual(len(after), before + 1)
        self.assertIn("technical_interview", [s.kind for s in after])

    def test_no_nested_expanders_in_source(self):
        """Belt-and-suspenders static guard: app.py must not nest expanders.

        AppTest catches it dynamically only on a code path that actually
        renders; this static check catches a nested `with st.expander` inside
        another `with st.expander` block regardless of runtime path.
        """
        src = open(_APP_PATH, encoding="utf-8").read()
        depth = 0
        max_depth = 0
        # Track indentation-based nesting of `with st.expander(` blocks. This is
        # a heuristic — it flags an expander opened while another is still in
        # scope at greater indentation.
        expander_stack = []  # list of indentation levels of open expanders
        for line in src.splitlines():
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            # Pop expanders we've dedented out of.
            while expander_stack and indent <= expander_stack[-1]:
                expander_stack.pop()
            if stripped.startswith("with st.expander("):
                if expander_stack:
                    max_depth = max(max_depth, len(expander_stack) + 1)
                expander_stack.append(indent)
                depth = max(depth, len(expander_stack))
        self.assertLessEqual(
            max_depth, 0,
            msg="Found a nested `with st.expander(...)` in app.py — Streamlit "
                "forbids nested expanders. Use st.checkbox toggles for the "
                "inner level (see the Phase-27 Replit fix).",
        )


if __name__ == "__main__":
    unittest.main()
