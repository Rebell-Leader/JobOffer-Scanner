"""Unit tests for Phase 18: structured logging, metrics, timing, audit log."""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import unittest
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
# Structured logging — JSON formatter + request_id contextvar
# ---------------------------------------------------------------------------

class JsonFormatterTests(unittest.TestCase):
    def test_basic_fields_serialised(self):
        from utils.logging_setup import JsonFormatter

        rec = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname=__file__, lineno=1,
            msg="hello %s", args=("world",),
            exc_info=None,
        )
        rec.request_id = None
        out = JsonFormatter().format(rec)
        payload = json.loads(out)
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["logger"], "test")
        self.assertEqual(payload["msg"], "hello world")
        self.assertIn("ts", payload)
        self.assertNotIn("request_id", payload)

    def test_request_id_included_when_set(self):
        from utils.logging_setup import JsonFormatter

        rec = logging.LogRecord(
            name="x", level=logging.INFO, pathname=__file__, lineno=1,
            msg="m", args=(), exc_info=None,
        )
        rec.request_id = "abc123"
        payload = json.loads(JsonFormatter().format(rec))
        self.assertEqual(payload["request_id"], "abc123")

    def test_extra_fields_serialised(self):
        from utils.logging_setup import JsonFormatter

        rec = logging.LogRecord(
            name="x", level=logging.INFO, pathname=__file__, lineno=1,
            msg="m", args=(), exc_info=None,
        )
        rec.request_id = None
        rec.duration_ms = 42.5
        rec.provider = "openai"
        payload = json.loads(JsonFormatter().format(rec))
        self.assertEqual(payload["duration_ms"], 42.5)
        self.assertEqual(payload["provider"], "openai")

    def test_unserializable_fields_repr_fallback(self):
        from utils.logging_setup import JsonFormatter

        rec = logging.LogRecord(
            name="x", level=logging.INFO, pathname=__file__, lineno=1,
            msg="m", args=(), exc_info=None,
        )
        rec.request_id = None
        rec.weird = object()
        payload = json.loads(JsonFormatter().format(rec))
        self.assertIn("weird", payload)
        self.assertIsInstance(payload["weird"], str)


class RequestContextTests(unittest.TestCase):
    def test_request_context_sets_and_clears(self):
        from utils.logging_setup import get_request_id, request_context

        self.assertIsNone(get_request_id())
        with request_context("custom-id"):
            self.assertEqual(get_request_id(), "custom-id")
        self.assertIsNone(get_request_id())

    def test_request_context_autogenerates_id(self):
        from utils.logging_setup import get_request_id, request_context

        with request_context() as rid:
            self.assertEqual(get_request_id(), rid)
            self.assertGreater(len(rid), 6)

    def test_log_emits_request_id_through_filter(self):
        from utils.logging_setup import configure, request_context

        configure(force=True)
        # Replace the handler with a stream we can inspect.
        stream = io.StringIO()
        os.environ["LOG_FORMAT"] = "json"
        configure(force=True)
        # Re-attach a capturing handler whose formatter is the same JSON one
        # the configure helper installed.
        root = logging.getLogger()
        capture = logging.StreamHandler(stream)
        capture.addFilter(root.handlers[0].filters[0])
        capture.setFormatter(root.handlers[0].formatter)
        root.addHandler(capture)

        log = logging.getLogger("test")
        with request_context("traced-id"):
            log.info("from inside")
        log.info("from outside")

        os.environ.pop("LOG_FORMAT", None)
        configure(force=True)  # restore default

        captured = stream.getvalue().strip().splitlines()
        # Two log lines emitted; first carries the request_id, second doesn't.
        payloads = [json.loads(line) for line in captured]
        self.assertEqual(payloads[0]["request_id"], "traced-id")
        self.assertNotIn("request_id", payloads[1])


# ---------------------------------------------------------------------------
# Metrics registry
# ---------------------------------------------------------------------------

class MetricsTests(unittest.TestCase):
    def setUp(self):
        from utils.metrics import reset_for_testing
        reset_for_testing()

    def test_counter_accumulates(self):
        from utils.metrics import get_registry

        r = get_registry()
        r.increment("requests.total")
        r.increment("requests.total")
        r.increment("requests.total", amount=5)
        self.assertEqual(r.counter("requests.total"), 7)

    def test_counter_tags_are_separate_series(self):
        from utils.metrics import get_registry

        r = get_registry()
        r.increment("requests", tags={"endpoint": "a"})
        r.increment("requests", tags={"endpoint": "b"})
        r.increment("requests", tags={"endpoint": "a"})
        self.assertEqual(r.counter("requests", tags={"endpoint": "a"}), 2)
        self.assertEqual(r.counter("requests", tags={"endpoint": "b"}), 1)
        # The untagged series is independent.
        self.assertEqual(r.counter("requests"), 0)

    def test_histogram_records_quantiles(self):
        from utils.metrics import get_registry

        r = get_registry()
        for v in range(1, 101):
            r.observe("latency_ms", v)
        snap = r.snapshot()
        hist = next(h for h in snap.histograms if h.name == "latency_ms")
        self.assertEqual(hist.count, 100)
        self.assertEqual(hist.min, 1.0)
        self.assertEqual(hist.max, 100.0)
        # p50 ≈ 50, p95 ≈ 95.
        self.assertGreaterEqual(hist.p50, 49)
        self.assertLessEqual(hist.p50, 52)
        self.assertGreaterEqual(hist.p95, 94)
        self.assertLessEqual(hist.p95, 96)

    def test_render_snapshot_text_handles_empty(self):
        from utils.metrics import render_snapshot_text, snapshot

        self.assertIn("no metrics", render_snapshot_text(snapshot()))

    def test_render_snapshot_text_includes_counter_and_histogram(self):
        from utils.metrics import (
            get_registry,
            render_snapshot_text,
            snapshot,
        )

        r = get_registry()
        r.increment("orders.placed", tags={"region": "eu"})
        for v in (5, 15, 25):
            r.observe("orders.duration_ms", v, tags={"region": "eu"})
        text = render_snapshot_text(snapshot())
        self.assertIn("orders.placed", text)
        self.assertIn("region=eu", text)
        self.assertIn("orders.duration_ms", text)


class TimingTests(unittest.TestCase):
    def setUp(self):
        from utils.metrics import reset_for_testing
        reset_for_testing()

    def test_timed_block_records_duration_and_count(self):
        import time

        from utils.metrics import get_registry
        from utils.timing import timed_block

        r = get_registry()
        with timed_block("test.op", tags={"k": "v"}):
            time.sleep(0.001)
        self.assertEqual(r.counter("test.op.count", tags={"k": "v"}), 1)
        snap = r.snapshot()
        self.assertTrue(any(h.name == "test.op.duration_ms" for h in snap.histograms))

    def test_timed_block_records_errors(self):
        from utils.metrics import get_registry
        from utils.timing import timed_block

        r = get_registry()
        with self.assertRaises(RuntimeError):
            with timed_block("test.op", tags={"k": "v"}):
                raise RuntimeError("boom")
        self.assertEqual(r.counter("test.op.errors", tags={"k": "v"}), 1)

    def test_timed_block_reraises_underlying_exception(self):
        from utils.timing import timed_block

        with self.assertRaises(ValueError) as ctx:
            with timed_block("test.op"):
                raise ValueError("nope")
        self.assertEqual(str(ctx.exception), "nope")


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class AuditTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()

    def test_register_creates_audit_event(self):
        from services.audit import list_for_user

        events = list_for_user(self.user.id)
        kinds = {e.kind for e in events}
        self.assertIn("user.register", kinds)

    def test_login_success_and_failure_recorded(self):
        from services.auth import AuthError, authenticate_user
        from services.audit import list_for_user

        with self.assertRaises(AuthError):
            authenticate_user("u@x.com", "wrongwrongwrong")
        authenticate_user("u@x.com", "longenough")
        events = list_for_user(self.user.id)
        kinds = [e.kind for e in events]
        self.assertIn("user.login.success", kinds)
        self.assertIn("user.login.failure", kinds)

    def test_login_failure_records_event_even_for_unknown_email(self):
        from services.auth import AuthError, authenticate_user
        from services.audit import list_recent

        with self.assertRaises(AuthError):
            authenticate_user("nobody@nowhere.com", "longenough")
        recent = list_recent(limit=10)
        # Failure for an unknown user has user_id=None and email in details.
        match = next(
            (e for e in recent
             if e.kind == "user.login.failure"
             and e.details.get("email") == "nobody@nowhere.com"),
            None,
        )
        self.assertIsNotNone(match)
        self.assertIsNone(match.user_id)

    def test_password_change_recorded(self):
        from services.audit import list_for_user
        from services.auth import change_password

        change_password(self.user.id, "longenough", "betterpassword")
        events = list_for_user(self.user.id)
        self.assertTrue(any(e.kind == "user.password.change" for e in events))

    def test_password_reset_request_and_complete_recorded(self):
        from services.audit import list_for_user
        from services.auth import complete_password_reset, request_password_reset

        token = request_password_reset("u@x.com")
        complete_password_reset("u@x.com", token, "anothergood1")
        events = list_for_user(self.user.id)
        kinds = {e.kind for e in events}
        self.assertIn("user.password.reset.request", kinds)
        self.assertIn("user.password.reset.complete", kinds)

    def test_application_delete_recorded(self):
        from services.applications import delete_application, save_analysis
        from services.audit import list_for_user

        rec = save_analysis(
            self.user.id,
            {"company_name": "Acme", "job_title": "Eng", "location": "Berlin"},
            {"final_report": "# r", "verdict": {}, "resume_analysis": {}},
        )
        delete_application(self.user.id, rec.id)
        events = list_for_user(self.user.id)
        match = next(
            (e for e in events if e.kind == "application.delete"),
            None,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.details.get("application_id"), rec.id)

    def test_telegram_bind_and_unbind_recorded(self):
        from services.audit import list_for_user
        from services.telegram_link import (
            complete_binding,
            issue_binding_token,
            unlink,
        )

        complete_binding(chat_id=42, raw_token=issue_binding_token(self.user.id))
        unlink(self.user.id)
        events = list_for_user(self.user.id)
        kinds = {e.kind for e in events}
        self.assertIn("telegram.bind", kinds)
        self.assertIn("telegram.unbind", kinds)

    def test_record_does_not_raise_on_db_error(self):
        import services.audit as mod

        # Even if the session blows up, audit.record swallows the error so the
        # caller's flow is not interrupted.
        with mock.patch.object(mod, "get_session", side_effect=RuntimeError("db down")):
            mod.record("user.login.success", user_id=1)  # must not raise

    def test_record_with_unknown_kind_still_writes(self):
        from services.audit import list_for_user, record

        record("freshly.invented.kind", user_id=self.user.id)
        events = list_for_user(self.user.id)
        self.assertTrue(any(e.kind == "freshly.invented.kind" for e in events))

    def test_request_id_is_attached(self):
        from services.audit import list_recent, record
        from utils.logging_setup import request_context

        with request_context("rid-foo"):
            record("user.login.success", user_id=self.user.id)
        recent = list_recent(limit=5)
        match = next(
            (e for e in recent
             if e.kind == "user.login.success" and e.user_id == self.user.id),
            None,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.request_id, "rid-foo")


# ---------------------------------------------------------------------------
# Metrics CLI
# ---------------------------------------------------------------------------

class MetricsCliTests(unittest.TestCase):
    def setUp(self):
        from utils.metrics import increment, observe, reset_for_testing
        reset_for_testing()
        increment("hits", tags={"r": "eu"})
        observe("dur", 12.5, tags={"r": "eu"})

    def test_text_mode(self):
        from worker import metrics_dump

        with mock.patch("builtins.print") as fake_print:
            metrics_dump.main([])
        printed = "\n".join(str(c.args[0]) for c in fake_print.call_args_list)
        self.assertIn("hits", printed)
        self.assertIn("dur", printed)

    def test_json_mode(self):
        from worker import metrics_dump

        with mock.patch("builtins.print") as fake_print:
            metrics_dump.main(["--json"])
        printed = "\n".join(str(c.args[0]) for c in fake_print.call_args_list)
        payload = json.loads(printed)
        self.assertIn("counters", payload)
        self.assertIn("histograms", payload)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

class Phase18MigrationTests(unittest.TestCase):
    def test_audit_events_table_created(self):
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
            self.assertIn("audit_events", tables)


if __name__ == "__main__":
    unittest.main()
