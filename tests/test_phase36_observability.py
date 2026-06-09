"""P1 #8: observability shipping — Prometheus exposition + /metrics endpoint.

Covers utils/metrics.render_prometheus (format, name/label sanitisation,
counters + histogram summary), the gated API /metrics endpoint (404 when off,
401 without the token, 200 with it), and the metrics_dump Pushgateway helper.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class PrometheusRenderTests(unittest.TestCase):
    def setUp(self):
        import utils.metrics as metrics
        metrics.reset_for_testing()

    def test_counter_rendered_with_type_and_labels(self):
        import utils.metrics as metrics
        metrics.increment("llm.requests", 3, tags={"provider": "openai"})
        out = metrics.render_prometheus()
        # Name sanitised: dot -> underscore.
        self.assertIn("# TYPE llm_requests counter", out)
        self.assertIn('llm_requests{provider="openai"} 3', out)

    def test_histogram_rendered_as_summary(self):
        import utils.metrics as metrics
        for v in (10.0, 20.0, 30.0):
            metrics.observe("llm.latency", v, tags={"provider": "openai"})
        out = metrics.render_prometheus()
        self.assertIn("# TYPE llm_latency summary", out)
        self.assertIn('quantile="0.5"', out)
        self.assertIn('llm_latency_count{provider="openai"} 3', out)
        self.assertIn('llm_latency_sum{provider="openai"} 60.0', out)
        self.assertIn("# TYPE llm_latency_min gauge", out)
        self.assertIn("# TYPE llm_latency_max gauge", out)

    def test_label_value_escaping(self):
        import utils.metrics as metrics
        metrics.increment("x", 1, tags={"model": 'a"b\\c'})
        out = metrics.render_prometheus()
        self.assertIn(r'model="a\"b\\c"', out)

    def test_type_declared_once_per_name(self):
        import utils.metrics as metrics
        metrics.increment("hits", 1, tags={"a": "1"})
        metrics.increment("hits", 1, tags={"a": "2"})
        out = metrics.render_prometheus()
        self.assertEqual(out.count("# TYPE hits counter"), 1)

    def test_empty_registry_renders_cleanly(self):
        import utils.metrics as metrics
        self.assertEqual(metrics.render_prometheus().strip(), "")


class MetricsEndpointTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        import utils.metrics as metrics
        from api.main import create_app
        metrics.reset_for_testing()
        metrics.increment("llm.requests", 1, tags={"provider": "openai"})
        self.client = TestClient(create_app())

    def test_404_when_disabled(self):
        os.environ.pop("METRICS_ENABLED", None)
        self.assertEqual(self.client.get("/metrics").status_code, 404)

    def test_200_when_enabled_no_token(self):
        with mock.patch.dict(os.environ, {"METRICS_ENABLED": "1"}):
            os.environ.pop("METRICS_TOKEN", None)
            resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("llm_requests", resp.text)
        self.assertIn("text/plain", resp.headers["content-type"])

    def test_401_without_token(self):
        with mock.patch.dict(os.environ, {"METRICS_ENABLED": "1", "METRICS_TOKEN": "sek"}):
            resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 401)

    def test_200_with_token(self):
        with mock.patch.dict(os.environ, {"METRICS_ENABLED": "1", "METRICS_TOKEN": "sek"}):
            resp = self.client.get("/metrics", headers={"Authorization": "Bearer sek"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("llm_requests", resp.text)


class PushgatewayTests(unittest.TestCase):
    def test_push_posts_to_job_endpoint(self):
        from worker import metrics_dump

        captured = {}

        class _Resp:
            def raise_for_status(self):
                pass

        def fake_post(url, data=None, headers=None, timeout=None):
            captured["url"] = url
            captured["data"] = data
            return _Resp()

        with mock.patch("requests.post", side_effect=fake_post):
            ok = metrics_dump.push_to_gateway("metric 1\n", "http://gw:9091/", "myjob")
        self.assertTrue(ok)
        self.assertEqual(captured["url"], "http://gw:9091/metrics/job/myjob")

    def test_push_failure_returns_false(self):
        from worker import metrics_dump

        with mock.patch("requests.post", side_effect=RuntimeError("down")):
            self.assertFalse(metrics_dump.push_to_gateway("x", "http://gw", "j"))

    def test_push_cli_requires_url(self):
        from worker import metrics_dump
        os.environ.pop("METRICS_PUSHGATEWAY_URL", None)
        self.assertEqual(metrics_dump.main(["--push"]), 2)


if __name__ == "__main__":
    unittest.main()
