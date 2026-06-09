"""Unit tests for Phase 2: ATS scoring, verdict extraction, URL ingest."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class AnalyzeRequirementsFailureTests(unittest.TestCase):
    """analyze_requirements must surface a provider failure (non-JSON) when a
    key is set, rather than returning an empty 'successful' analysis."""

    def setUp(self):
        from utils.cache import cache
        cache.clear()

    def test_raises_on_non_json_when_keyed(self):
        from unittest import mock

        import tools.job_tools as jt
        with mock.patch.object(jt, "get_completion", return_value="not json at all"), \
             mock.patch.object(jt, "is_demo_mode", return_value=False):
            with self.assertRaises(ValueError):
                jt.analyze_requirements("Some posting", model="fast")

    def test_empty_skeleton_only_in_demo(self):
        from unittest import mock

        import tools.job_tools as jt
        with mock.patch.object(jt, "get_completion", return_value="not json"), \
             mock.patch.object(jt, "is_demo_mode", return_value=True):
            out = jt.analyze_requirements("Some posting", model="fast")
        self.assertEqual(out["technical_skills"], [])


class AtsKeywordMatchTests(unittest.TestCase):
    def test_score_and_partition(self):
        from tools.resume_tools import ats_keyword_match

        resume = "Built ML pipelines in Python with TensorFlow on AWS. Used Docker for deploys."
        required = ["Python", "TensorFlow", "AWS", "Kubernetes", "PyTorch"]
        result = ats_keyword_match(resume, required)
        self.assertEqual(result["score"], 60)  # 3 of 5
        self.assertEqual(set(result["matched"]), {"Python", "TensorFlow", "AWS"})
        self.assertEqual(set(result["missing"]), {"Kubernetes", "PyTorch"})

    def test_strips_proficiency_parenthetical(self):
        from tools.resume_tools import ats_keyword_match

        result = ats_keyword_match("Senior Python engineer.", ["Python (Expert)"])
        self.assertEqual(result["score"], 100)

    def test_punctuation_skill_boundaries(self):
        """C++ shouldn't be matched by 'C' and vice versa."""
        from tools.resume_tools import ats_keyword_match

        result = ats_keyword_match("Wrote C# microservices.", ["C", "C#", "C++"])
        self.assertIn("C#", result["matched"])
        self.assertNotIn("C", result["matched"])
        self.assertNotIn("C++", result["matched"])

    def test_empty_requirements(self):
        from tools.resume_tools import ats_keyword_match

        result = ats_keyword_match("anything", [])
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["matched"], [])


class AtsFormatChecksTests(unittest.TestCase):
    def test_flags_tables_and_caps(self):
        from tools.resume_tools import ats_format_checks

        resume = (
            "| Skill | Years |\n| Python | 5 |\n| AWS | 3 |\n"
            "EXTENSIVE EXPERIENCE WITH MACHINE LEARNING SYSTEMS DEPLOYED IN PRODUCTION\n"
            "DESIGNED AND BUILT MANY DATA PIPELINES IN PYTHON AND SQL OVER YEARS\n"
            "LED A TEAM OF FOUR ENGINEERS ON A LARGE SCALE ANALYTICS PLATFORM\n"
        )
        issues = ats_format_checks(resume)
        joined = " ".join(issues)
        self.assertIn("pipe-separated", joined)
        self.assertIn("ALL-CAPS", joined)

    def test_empty_resume(self):
        from tools.resume_tools import ats_format_checks

        self.assertEqual(ats_format_checks(""), ["Resume text is empty — ATS will read nothing."])


class VerdictTests(unittest.TestCase):
    def test_structured_block_parsed(self):
        from utils.verdict import extract_verdict, strip_verdict_block

        report = (
            "# Report\n\nSome content.\n\n"
            '<verdict_json>{"verdict":"Highly Recommended","reasons":["a","b","c"],"confidence":9}</verdict_json>\n'
        )
        v = extract_verdict(report)
        self.assertEqual(v["verdict"], "Highly Recommended")
        self.assertEqual(v["light"], "green")
        self.assertEqual(v["reasons"], ["a", "b", "c"])
        self.assertEqual(v["confidence"], 9)
        self.assertEqual(v["source"], "structured")
        self.assertNotIn("verdict_json", strip_verdict_block(report))

    def test_inferred_from_text(self):
        from utils.verdict import extract_verdict

        report = "## Final Recommendation\n\n**Consider with Caution** — the comp is below market."
        v = extract_verdict(report)
        self.assertEqual(v["verdict"], "Consider with Caution")
        self.assertEqual(v["light"], "yellow")
        self.assertEqual(v["source"], "inferred")

    def test_highly_recommended_not_eaten_by_recommended(self):
        from utils.verdict import extract_verdict

        v = extract_verdict("Final: Highly Recommended")
        self.assertEqual(v["verdict"], "Highly Recommended")

    def test_default_when_no_signal(self):
        from utils.verdict import extract_verdict

        v = extract_verdict("no verdict words anywhere here")
        self.assertEqual(v["verdict"], "Consider with Caution")
        self.assertEqual(v["light"], "yellow")

    def test_malformed_json_falls_back_to_inference(self):
        from utils.verdict import extract_verdict

        report = (
            "## Final Recommendation: Recommended\n\n"
            "<verdict_json>{not valid json}</verdict_json>\n"
        )
        v = extract_verdict(report)
        self.assertEqual(v["verdict"], "Recommended")
        self.assertEqual(v["source"], "inferred")


class UrlIngestTests(unittest.TestCase):
    def test_is_url(self):
        from tools.url_ingest import is_url

        self.assertTrue(is_url("https://example.com/jobs/123"))
        self.assertTrue(is_url("http://x.y"))
        self.assertFalse(is_url(""))
        self.assertFalse(is_url("example.com"))
        self.assertFalse(is_url(None))

    def test_clean_html_extracts_text(self):
        from tools.url_ingest import _clean_html

        html = b"""
        <html><head><style>.x{}</style><script>x()</script></head>
        <body>
          <header>nav</header>
          <main class="job-description">
            <h1>Senior Engineer</h1>
            <p>Build cool things with Python and AWS.</p>
          </main>
          <footer>fine print</footer>
        </body></html>
        """
        text = _clean_html(html)
        self.assertIn("Senior Engineer", text)
        self.assertIn("Python", text)
        self.assertNotIn("nav", text)
        self.assertNotIn("fine print", text)


class ResumeStageOptionalTests(unittest.TestCase):
    def test_resume_stage_skips_when_no_resume(self):
        from agents.resume_analyzer import analyze

        state = {"resume_text": "", "job_details": {}, "model": "fast"}
        out = analyze(state)
        self.assertNotIn("resume_analysis", out)
        self.assertEqual(out.get("error", ""), "")


if __name__ == "__main__":
    unittest.main()
