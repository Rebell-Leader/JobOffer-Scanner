"""Smoke test: end-to-end pipeline in demo mode (no provider keys).

Verifies that:
  * No provider key => demo mode is reported.
  * The pipeline runs to completion without an error.
  * The final report is populated and visibly labelled as demo data.
"""

from __future__ import annotations

import os
import sys
import unittest

# Ensure repo root is on the path when running this file directly.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Strip any provider keys that may leak in from the host environment so the
# test exercises the demo-mode path deterministically.
for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FEATHERLESS_API_KEY", "LLM_PROVIDER"):
    os.environ.pop(var, None)


class PipelineDemoSmokeTest(unittest.TestCase):
    def test_demo_pipeline_runs_end_to_end(self):
        from agents.orchestrator import run_analysis
        from utils.config import check_environment_setup

        status = check_environment_setup()
        self.assertTrue(status["demo_mode"])
        self.assertIsNone(status["llm_provider"])

        result = run_analysis(
            "Company: Sample Co\nTitle: Backend Engineer\nLocation: Berlin, Germany",
            manual_inputs={
                "company_name": "Sample Co",
                "job_title": "Backend Engineer",
                "location": "Berlin, Germany",
                "compensation": "EUR 70k",
            },
            model="fast",
        )

        self.assertEqual(result.get("error", ""), "", msg=result.get("error"))
        self.assertTrue(result.get("final_report"), "final_report should be populated")
        # Demo output must be visibly labelled as demo data.
        self.assertIn("DEMO", result["final_report"].upper())


if __name__ == "__main__":
    unittest.main()
