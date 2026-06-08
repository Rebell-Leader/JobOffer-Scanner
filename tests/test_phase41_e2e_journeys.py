"""Use-case (user-journey) e2e tests.

The middle layer that was missing: full journeys with mocked LLM/network but
REAL services + DB + orchestrator, driven through the real entry points
(orchestrator, REST API, services). These exercise the seams the unit suite
structurally can't reach — the real multi-agent graph, the API analyze body,
threaded usage attribution, and the cost-control gates — with no network.

See ``tests/e2e_helpers.py`` for ``mock_llm`` (stubs the provider call with
stage-appropriate output while taking the real, non-demo code path).
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))  # for e2e_helpers (tests/ not a package)

import e2e_helpers as e2e  # noqa: E402

_VALID_VERDICTS = {
    "Highly Recommended", "Recommended", "Consider with Caution", "Not Recommended",
}

_POSTING = (
    "Company: Acme\nTitle: Senior Backend Engineer\nLocation: Berlin, Germany\n\n"
    "We need a backend engineer with Python, PostgreSQL and AWS. 5+ years."
)
_MANUAL = {"company_name": "Acme", "job_title": "Senior Backend Engineer",
           "location": "Berlin, Germany", "compensation": "EUR 90k"}


class FullPipelineJourney(unittest.TestCase):
    """Journey 1: the real orchestrator graph runs every stage with a stubbed
    LLM and produces a verdict + report; usage is attributed to the user."""

    def setUp(self):
        e2e.fresh_db()
        self.user = e2e.make_user()

    def test_real_graph_runs_and_attributes_usage(self):
        from services.analysis_runner import run_analysis_sync
        from services.usage import spend_usd

        with e2e.mock_llm():
            result = run_analysis_sync(
                _POSTING, manual_inputs=_MANUAL, model="fast", user_id=self.user.id,
            )

        self.assertEqual(result.get("error", ""), "", msg=result.get("error"))
        # All four stages populated.
        self.assertTrue(result.get("job_details"))
        self.assertTrue(result.get("company_analysis"))
        self.assertTrue(result.get("salary_analysis"))
        self.assertTrue(result.get("final_report"))
        self.assertIn((result.get("verdict") or {}).get("verdict"), _VALID_VERDICTS)
        # Threaded company‖salary attribution: spend was ledgered to THIS user
        # (the contextvars re-application across the stage thread pool works).
        self.assertGreater(spend_usd(self.user.id), 0.0)


class ApiAnalyzeSaveFetchJourney(unittest.TestCase):
    """Journey 2: API token → POST /v1/analyze (real graph) with save → GET the
    persisted application back."""

    def setUp(self):
        e2e.fresh_db()
        self.user = e2e.make_user()
        self.token = e2e.issue_api_token(self.user.id)

    def _client(self):
        from fastapi.testclient import TestClient

        from api.main import create_app
        return TestClient(create_app())

    def test_analyze_then_fetch(self):
        client = self._client()
        headers = {"Authorization": f"Bearer {self.token}"}
        with e2e.mock_llm():
            r = client.post("/v1/analyze", headers=headers, json={
                "job_posting": _POSTING, "company_name": "Acme",
                "job_title": "Senior Backend Engineer", "location": "Berlin",
                "save": True, "save_status": "applied",
            })
        self.assertEqual(r.status_code, 200, msg=r.text)
        saved_id = r.json()["saved_application_id"]
        self.assertIsNotNone(saved_id)

        got = client.get(f"/v1/applications/{saved_id}", headers=headers)
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["status"], "applied")


class BudgetBlockJourney(unittest.TestCase):
    """Journey 4: a user already over their LLM budget is refused at the API
    BEFORE any pipeline work (402), with spend attribution intact."""

    def setUp(self):
        e2e.fresh_db()
        self.user = e2e.make_user()
        self.token = e2e.issue_api_token(self.user.id)

    def test_over_budget_blocks_before_pipeline(self):
        from unittest import mock

        from fastapi.testclient import TestClient

        import api.routes as routes
        from api.main import create_app
        from services import usage

        # Pre-load spend over a $1 budget using an overridden price.
        with mock.patch.dict(os.environ, {"LLM_PRICING_JSON": '{"m": [1.0, 2.0]}'}):
            usage.record_completion("openai", "m",
                                    {"prompt_tokens": 1000, "completion_tokens": 1000},
                                    user_id=self.user.id)

        client = TestClient(create_app())
        headers = {"Authorization": f"Bearer {self.token}"}
        with mock.patch.dict(os.environ, {"LLM_BUDGET_USD": "1.00"}), \
             mock.patch.object(routes, "run_analysis_sync") as run:
            r = client.post("/v1/analyze", headers=headers,
                            json={"job_posting": _POSTING})
        self.assertEqual(r.status_code, 402)
        run.assert_not_called()


class TailoredCvConstraintJourney(unittest.TestCase):
    """Journey 6: master CV + project → tailored CV → constraint check is clean;
    and the negative case where a fabricated skill is flagged."""

    def setUp(self):
        e2e.fresh_db()
        self.user = e2e.make_user()
        from services.applications import save_analysis
        from services.master_cv import save_master_cv
        from services.projects import create_project
        save_master_cv(self.user.id,
                       "Skills: Python, PostgreSQL, AWS.\nExperience: Backend at Globex.")
        create_project(self.user.id, title="Payments API", role="lead",
                       tech_stack="Python, PostgreSQL", summary="Built a payments API",
                       highlights=["Scaled to 1M requests"])
        self.app = save_analysis(self.user.id, _MANUAL,
                                 {"verdict": {"verdict": "Recommended", "light": "green"}})

    def test_clean_tailored_cv(self):
        from services.tailoring import generate_tailored_cv

        clean_cv = "Senior Backend Engineer. Skills: Python, PostgreSQL, AWS at Globex."
        with e2e.mock_llm(handler=lambda prompt: clean_cv):
            art = generate_tailored_cv(self.user.id, self.app.id, model="fast")
        self.assertEqual(art.meta["constraint_check"]["severity"], "clean")

    def test_fabricated_skill_is_flagged(self):
        from services.tailoring import generate_tailored_cv

        # Injects "Kubernetes" + "Rust" — neither is in the CV/projects/job.
        fabricated = "Skills: Python, Kubernetes, Rust. Reduced latency by 80%."
        with e2e.mock_llm(handler=lambda prompt: fabricated):
            art = generate_tailored_cv(self.user.id, self.app.id, model="fast")
        check = art.meta["constraint_check"]
        self.assertEqual(check["severity"], "review_recommended")
        self.assertIn("kubernetes", check["new_proper_nouns"])
        self.assertIn("rust", check["new_proper_nouns"])
        self.assertIn("80%", check["new_percentages"])


class TwoFactorLoginJourney(unittest.TestCase):
    """Journey 7: enable TOTP → authenticate signals 2FA required → OTP gate
    accepts a valid code and rejects a wrong one; a backup code is one-shot."""

    def setUp(self):
        e2e.fresh_db()
        self.user = e2e.make_user(email="2fa@example.com")

    def test_login_gate(self):
        import pyotp

        from services import totp
        from services.auth import authenticate_user

        setup = totp.start_setup(self.user.id, "2fa@example.com")
        confirm = totp.confirm_setup(self.user.id, pyotp.TOTP(setup.secret).now())

        # Password auth now signals the second factor is required.
        authed = authenticate_user("2fa@example.com", "Sup3rSecret!")
        self.assertTrue(authed.two_factor_required)

        # Wrong OTP rejected; correct OTP accepted.
        self.assertFalse(totp.verify_login(self.user.id, "000000"))
        self.assertTrue(totp.verify_login(self.user.id, pyotp.TOTP(setup.secret).now()))

        # A backup code works once, then is consumed.
        code = confirm.backup_codes[0]
        self.assertTrue(totp.verify_login(self.user.id, code))
        self.assertFalse(totp.verify_login(self.user.id, code))


class AgenticFallbackJourney(unittest.TestCase):
    """Journey 8: no news/COL keys, but a provider key + mocked DuckDuckGo →
    the agentic fallback synthesises a briefing that reaches company analysis."""

    def setUp(self):
        e2e.fresh_db()
        self.user = e2e.make_user()

    def test_fallback_briefing_used(self):
        from unittest import mock

        from tools.data_sources import fetch_company_news

        ddg_results = [
            {"title": "Acme raises $60M Series B", "snippet": "Led by Foo Ventures.",
             "url": "https://news.example/acme"},
        ]
        # Provider key set (via mock_llm) but re-enable the fallback and mock DDG.
        with e2e.mock_llm(), \
             mock.patch.dict(os.environ, {"COMPANY_RESEARCH_FALLBACK": "1"}), \
             mock.patch("tools.company_research.ddg_search", return_value=ddg_results), \
             mock.patch("tools.company_research.deep_fetch", return_value=None):
            out = fetch_company_news("Acme")
        self.assertIsNotNone(out)
        # Synthesised by the (stubbed) LLM from the search snippets.
        self.assertGreater(len(out), 50)


if __name__ == "__main__":
    unittest.main()
