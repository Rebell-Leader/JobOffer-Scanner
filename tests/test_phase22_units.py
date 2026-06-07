"""Unit tests for Phase 22: API tokens + FastAPI endpoints."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
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


# ---------------------------------------------------------------------------
# API token service
# ---------------------------------------------------------------------------

class ApiTokenIssueTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()

    def test_issue_returns_raw_token_with_prefix(self):
        from services.api_tokens import issue

        out = issue(self.user.id, "cli", ttl_days=30)
        self.assertTrue(out.raw_token.startswith("jos_"))
        self.assertGreater(len(out.raw_token), 30)
        self.assertEqual(out.record.name, "cli")
        self.assertEqual(out.record.prefix, out.raw_token[:8])

    def test_raw_token_not_persisted(self):
        from db.models import ApiToken
        from db.session import get_session
        from services.api_tokens import issue

        out = issue(self.user.id, "cli")
        with get_session() as s:
            row = s.query(ApiToken).filter_by(id=out.record.id).one()
        self.assertNotEqual(row.token_hash, out.raw_token)
        self.assertTrue(row.token_hash.startswith("$2"))

    def test_issue_requires_name(self):
        from services.api_tokens import ApiTokenError, issue

        with self.assertRaises(ApiTokenError):
            issue(self.user.id, "   ")

    def test_no_ttl_means_no_expiry(self):
        from services.api_tokens import issue

        out = issue(self.user.id, "forever", ttl_days=None)
        self.assertIsNone(out.record.expires_at)

    def test_list_newest_first(self):
        import time

        from services.api_tokens import issue, list_for_user

        issue(self.user.id, "a")
        time.sleep(0.01)
        issue(self.user.id, "b")
        rows = list_for_user(self.user.id)
        self.assertEqual([r.name for r in rows], ["b", "a"])

    def test_revoke_marks_inactive(self):
        from services.api_tokens import issue, list_for_user, revoke

        out = issue(self.user.id, "tmp")
        revoke(self.user.id, out.record.id)
        rec = list_for_user(self.user.id)[0]
        self.assertIsNotNone(rec.revoked_at)
        self.assertFalse(rec.is_active)

    def test_revoke_cross_user_blocked(self):
        from services.api_tokens import ApiTokenError, issue, revoke

        other = _register("other@x.com")
        out = issue(self.user.id, "mine")
        with self.assertRaises(ApiTokenError):
            revoke(other.id, out.record.id)


class ApiTokenVerifyTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        from services.api_tokens import issue
        self.token = issue(self.user.id, "cli", ttl_days=30).raw_token

    def test_verify_resolves_user(self):
        from services.api_tokens import verify

        self.assertEqual(verify(self.token), self.user.id)

    def test_verify_rejects_unknown_prefix(self):
        from services.api_tokens import verify

        self.assertIsNone(verify("jos_totallyfake_token"))

    def test_verify_rejects_missing_prefix(self):
        """Token without the jos_ prefix is rejected without DB lookup."""
        from services.api_tokens import verify

        self.assertIsNone(verify("not_a_jos_token"))

    def test_verify_rejects_empty(self):
        from services.api_tokens import verify

        self.assertIsNone(verify(""))
        self.assertIsNone(verify("   "))

    def test_verify_rejects_revoked(self):
        from services.api_tokens import list_for_user, revoke, verify

        token_id = list_for_user(self.user.id)[0].id
        revoke(self.user.id, token_id)
        self.assertIsNone(verify(self.token))

    def test_verify_rejects_expired(self):
        from db.models import ApiToken
        from db.session import get_session
        from services.api_tokens import verify

        with get_session() as s:
            row = s.query(ApiToken).first()
            row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            s.commit()
        self.assertIsNone(verify(self.token))

    def test_verify_updates_last_used_at(self):
        from services.api_tokens import list_for_user, verify

        before = list_for_user(self.user.id)[0]
        self.assertIsNone(before.last_used_at)
        verify(self.token)
        after = list_for_user(self.user.id)[0]
        self.assertIsNotNone(after.last_used_at)

    def test_verify_records_audit(self):
        from services.api_tokens import verify
        from services.audit import list_for_user

        verify(self.token)
        kinds = {e.kind for e in list_for_user(self.user.id)}
        self.assertIn("api_token.used", kinds)


# ---------------------------------------------------------------------------
# FastAPI integration
# ---------------------------------------------------------------------------

class ApiEndpointTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        from services.api_tokens import issue
        self.token = issue(self.user.id, "test", ttl_days=30).raw_token

    def _client(self):
        from fastapi.testclient import TestClient

        from api.main import create_app

        return TestClient(create_app())

    def _auth(self):
        return {"Authorization": f"Bearer {self.token}"}

    def test_unauthenticated_request_returns_401(self):
        client = self._client()
        self.assertEqual(client.get("/v1/me").status_code, 401)

    def test_invalid_token_returns_401(self):
        client = self._client()
        r = client.get(
            "/v1/me", headers={"Authorization": "Bearer jos_obviouslyfake"},
        )
        self.assertEqual(r.status_code, 401)

    def test_me_returns_user(self):
        client = self._client()
        r = client.get("/v1/me", headers=self._auth())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"user_id": self.user.id, "email": "u@x.com"})

    def test_healthz_does_not_require_auth(self):
        client = self._client()
        r = client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})

    def test_get_applications_returns_user_data(self):
        from services.applications import save_analysis

        save_analysis(
            self.user.id,
            {"company_name": "Acme", "job_title": "Eng", "location": "Berlin"},
            {"final_report": "# r",
             "verdict": {"verdict": "Recommended", "light": "green"},
             "resume_analysis": {"ats_score": 80}},
        )
        client = self._client()
        r = client.get("/v1/applications", headers=self._auth())
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["company_name"], "Acme")
        self.assertEqual(data[0]["ats_score"], 80)

    def test_get_applications_is_user_scoped(self):
        """Another user's tokens don't see this user's data."""
        from services.api_tokens import issue
        from services.applications import save_analysis

        save_analysis(
            self.user.id,
            {"company_name": "Acme", "job_title": "Eng", "location": "Berlin"},
            {"final_report": "# r", "verdict": {}, "resume_analysis": {}},
        )
        other = _register("other@x.com")
        other_token = issue(other.id, "other-cli").raw_token

        client = self._client()
        r = client.get(
            "/v1/applications",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    def test_delete_application(self):
        from services.applications import list_applications, save_analysis

        rec = save_analysis(
            self.user.id,
            {"company_name": "Acme", "job_title": "Eng", "location": "Berlin"},
            {"final_report": "# r", "verdict": {}, "resume_analysis": {}},
        )
        client = self._client()
        r = client.delete(f"/v1/applications/{rec.id}", headers=self._auth())
        self.assertEqual(r.status_code, 204)
        self.assertEqual(list_applications(self.user.id), [])

    def test_delete_nonexistent_application_returns_404(self):
        client = self._client()
        r = client.delete("/v1/applications/9999", headers=self._auth())
        self.assertEqual(r.status_code, 404)

    def test_get_one_application_cross_user_returns_404(self):
        """Cross-user reads must look like "not found", not 403, so the API
        doesn't leak which IDs exist."""
        from services.api_tokens import issue
        from services.applications import save_analysis

        rec = save_analysis(
            self.user.id,
            {"company_name": "Acme", "job_title": "Eng", "location": "Berlin"},
            {"final_report": "# r", "verdict": {}, "resume_analysis": {}},
        )
        other = _register("other@x.com")
        other_token = issue(other.id, "cli").raw_token

        client = self._client()
        r = client.get(
            f"/v1/applications/{rec.id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        self.assertEqual(r.status_code, 404)

    def test_master_cv_round_trip(self):
        client = self._client()

        # Initially empty.
        r = client.get("/v1/cv", headers=self._auth())
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["raw_text"])

        # PUT body.
        body = "Jane Doe\nSkills: Python, AWS"
        r = client.put("/v1/cv", json={"raw_text": body}, headers=self._auth())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["raw_text"], body)

        # GET reflects it.
        r = client.get("/v1/cv", headers=self._auth())
        self.assertEqual(r.json()["raw_text"], body)

    def test_put_cv_empty_rejected(self):
        client = self._client()
        r = client.put("/v1/cv", json={"raw_text": ""}, headers=self._auth())
        # Pydantic min_length=1 -> 422.
        self.assertEqual(r.status_code, 422)

    def test_post_analyze_returns_result(self):
        import api.routes as routes_mod

        fake_result = {
            "final_report": "# done",
            "verdict": {"verdict": "Recommended", "light": "green"},
            "job_details": {"extracted_details": {"company_name": "Acme"}},
            "company_analysis": {"x": 1},
            "salary_analysis": {"y": 2},
            "resume_analysis": {},
        }
        with mock.patch.object(routes_mod, "run_analysis", return_value=fake_result):
            client = self._client()
            r = client.post(
                "/v1/analyze",
                headers=self._auth(),
                json={"job_posting": "hello", "company_name": "Acme",
                      "job_title": "ML", "location": "Berlin"},
            )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["final_report"], "# done")
        self.assertEqual(data["verdict"]["verdict"], "Recommended")
        self.assertIsNone(data["saved_application_id"])

    def test_post_analyze_with_save_persists(self):
        import api.routes as routes_mod
        from services.applications import list_applications

        fake_result = {
            "final_report": "# done",
            "verdict": {"verdict": "Recommended", "light": "green"},
            "job_details": {
                "extracted_details": {
                    "company_name": "Acme", "job_title": "ML",
                    "location": "Berlin",
                },
                "requirements_analysis": {"technical_skills": []},
            },
            "company_analysis": {}, "salary_analysis": {}, "resume_analysis": {},
        }
        with mock.patch.object(routes_mod, "run_analysis", return_value=fake_result):
            client = self._client()
            r = client.post(
                "/v1/analyze",
                headers=self._auth(),
                json={
                    "job_posting": "hello", "company_name": "Acme",
                    "job_title": "ML", "location": "Berlin",
                    "save": True, "save_status": "applied",
                },
            )
        self.assertEqual(r.status_code, 200)
        saved_id = r.json()["saved_application_id"]
        self.assertIsNotNone(saved_id)
        apps = list_applications(self.user.id)
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0].status, "applied")

    def test_post_analyze_propagates_pipeline_error(self):
        import api.routes as routes_mod

        with mock.patch.object(routes_mod, "run_analysis",
                               return_value={"error": "LLM hung up"}):
            client = self._client()
            r = client.post(
                "/v1/analyze", headers=self._auth(),
                json={"job_posting": "hello"},
            )
        self.assertEqual(r.status_code, 502)
        self.assertIn("LLM hung up", r.json()["detail"])

    def test_analytics_endpoint_returns_full_shape(self):
        client = self._client()
        r = client.get("/v1/analytics", headers=self._auth())
        self.assertEqual(r.status_code, 200)
        data = r.json()
        for key in (
            "overview", "funnel", "time_in_stage", "verdict_outcomes",
            "rejection_stage_distribution", "volume_by_week",
        ):
            self.assertIn(key, data)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

class Phase22MigrationTests(unittest.TestCase):
    def test_table_created(self):
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
            tables = set(inspect(create_engine(f"sqlite:///{db_path.as_posix()}")).get_table_names())
            self.assertIn("api_tokens", tables)


if __name__ == "__main__":
    unittest.main()
