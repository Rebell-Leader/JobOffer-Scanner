"""Unit tests for Phase 5: COL data, salary cache key, export, Alembic."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_db():
    from db.session import reset_engine_for_testing
    from services.rate_limit import reset_backend_for_testing
    reset_engine_for_testing("sqlite:///:memory:")
    reset_backend_for_testing()


# ---------------------------------------------------------------------------
# Cost-of-living dataset
# ---------------------------------------------------------------------------

class CostOfLivingTests(unittest.TestCase):
    def test_format_col_known_fields(self):
        from tools.data_sources import _format_col

        record = {
            "city": "Berlin",
            "cost_of_living_index": 65,
            "rent_index": 50,
            "local_purchasing_power": 110,
            "monthly_rent_1bedroom_city_center": 1200,
            "unused_field": "ignored",
        }
        out = _format_col(record, "Berlin")
        self.assertIn("REAL DATA", out)
        self.assertIn("Berlin", out)
        self.assertIn("65", out)
        self.assertIn("1BR rent", out)
        self.assertNotIn("ignored", out)

    def test_fetch_returns_none_without_url(self):
        from tools import data_sources

        os.environ.pop("COL_DATASET_URL", None)
        self.assertIsNone(data_sources.fetch_cost_of_living("Berlin"))


# ---------------------------------------------------------------------------
# Salary cache-key bug fix
# ---------------------------------------------------------------------------

class SalaryCacheKeyTests(unittest.TestCase):
    """Switching from heuristic-only to real-data MUST invalidate the cache.

    Without this, a user who first runs without ADZUNA keys, gets a cached
    heuristic response, and then configures ADZUNA, would still see the
    stale heuristic-only output.
    """

    def setUp(self):
        from utils.cache import cache
        cache.clear()

    def test_cache_key_changes_when_data_sources_change(self):
        import importlib
        from unittest import mock

        mod = importlib.import_module("tools.salary_tools")

        # First call: no real benchmark.
        with mock.patch.object(mod, "fetch_salary_benchmark", return_value=None), \
             mock.patch.object(mod, "fetch_cost_of_living", return_value=None), \
             mock.patch.object(mod, "get_completion", return_value="HEURISTIC RESPONSE"):
            first = mod.estimate_salary_range("Engineer", "Berlin", "5 years")
        self.assertEqual(first, "HEURISTIC RESPONSE")

        # Second call with the SAME inputs but Adzuna now returning data:
        # the cache must NOT serve the previous heuristic response.
        with mock.patch.object(mod, "fetch_salary_benchmark", return_value="ADZUNA DATA"), \
             mock.patch.object(mod, "fetch_cost_of_living", return_value=None), \
             mock.patch.object(mod, "get_completion", return_value="REAL RESPONSE"):
            second = mod.estimate_salary_range("Engineer", "Berlin", "5 years")
        self.assertEqual(second, "REAL RESPONSE")

    def test_cache_hit_when_inputs_and_sources_unchanged(self):
        import importlib
        from unittest import mock

        mod = importlib.import_module("tools.salary_tools")
        with mock.patch.object(mod, "fetch_salary_benchmark", return_value=None), \
             mock.patch.object(mod, "fetch_cost_of_living", return_value=None), \
             mock.patch.object(mod, "get_completion", return_value="FIRST") as mock_llm:
            mod.estimate_salary_range("Engineer", "Paris", "3 years")
            mod.estimate_salary_range("Engineer", "Paris", "3 years")  # cached
            self.assertEqual(mock_llm.call_count, 1)


# ---------------------------------------------------------------------------
# Application export
# ---------------------------------------------------------------------------

class ApplicationExportTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        from services.applications import save_analysis
        from services.auth import register_user

        self.user = register_user("e@x.com", "longenough")
        self.other = register_user("other@x.com", "longenough")

        for company in ("Acme", "Globex"):
            save_analysis(
                self.user.id,
                {"company_name": company, "job_title": "Eng", "location": "Berlin"},
                {
                    "final_report": f"# {company}",
                    "verdict": {"verdict": "Recommended", "light": "green", "reasons": []},
                    "resume_analysis": {"ats_score": 70},
                },
            )

    def test_csv_export_has_header_and_rows(self):
        from services.applications import export_applications_csv

        csv_text = export_applications_csv(self.user.id)
        lines = csv_text.strip().splitlines()
        self.assertEqual(lines[0].split(",")[0], "id")
        self.assertIn("company_name", lines[0])
        self.assertEqual(len(lines), 3)  # header + 2 rows
        body = "\n".join(lines[1:])
        self.assertIn("Acme", body)
        self.assertIn("Globex", body)

    def test_csv_export_is_user_scoped(self):
        from services.applications import export_applications_csv

        csv_text = export_applications_csv(self.other.id)
        self.assertEqual(csv_text.strip().splitlines(), [
            ",".join((
                "id", "created_at", "updated_at", "company_name", "job_title",
                "location", "status", "verdict", "verdict_light", "ats_score", "notes",
            )),
        ])

    def test_json_export_includes_full_analysis(self):
        from services.applications import export_applications_json

        payload = json.loads(export_applications_json(self.user.id))
        self.assertEqual(len(payload), 2)
        first = payload[0]
        self.assertIn("analysis_json", first)
        self.assertIn("final_report", first["analysis_json"])

    def test_json_export_serializes_datetimes(self):
        from services.applications import export_applications_json

        payload = json.loads(export_applications_json(self.user.id))
        # ISO-8601 datetime string survives a round-trip parse without error.
        from datetime import datetime
        datetime.fromisoformat(payload[0]["created_at"])


# ---------------------------------------------------------------------------
# Alembic migrations
# ---------------------------------------------------------------------------

class AlembicMigrationTests(unittest.TestCase):
    """`alembic upgrade head` should produce the same schema as create_all."""

    def test_upgrade_creates_expected_tables(self):
        import tempfile
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
            tables = set(inspect(engine).get_table_names())
            self.assertIn("users", tables)
            self.assertIn("applications", tables)
            self.assertIn("password_reset_tokens", tables)


if __name__ == "__main__":
    unittest.main()
