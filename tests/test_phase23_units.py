"""Unit tests for Phase 23: webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
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


class _Resp:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class WebhookCrudTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        self.other = _register("other@x.com")

    def test_register_returns_secret(self):
        from services.webhooks import register_webhook

        wh = register_webhook(self.user.id, "https://x.com/h", ["stage.added"])
        self.assertGreater(len(wh.secret), 20)
        self.assertEqual(wh.events, ["stage.added"])
        self.assertTrue(wh.active)

    def test_register_rejects_bad_url(self):
        from services.webhooks import WebhookError, register_webhook

        with self.assertRaises(WebhookError):
            register_webhook(self.user.id, "ftp://x.com", ["stage.added"])

    def test_register_rejects_unknown_event(self):
        from services.webhooks import WebhookError, register_webhook

        with self.assertRaises(WebhookError):
            register_webhook(self.user.id, "https://x.com", ["not.an.event"])

    def test_register_requires_at_least_one_event(self):
        from services.webhooks import WebhookError, register_webhook

        with self.assertRaises(WebhookError):
            register_webhook(self.user.id, "https://x.com", [])

    def test_list_user_scoped(self):
        from services.webhooks import list_webhooks, register_webhook

        register_webhook(self.user.id, "https://x.com", ["stage.added"])
        self.assertEqual(len(list_webhooks(self.user.id)), 1)
        self.assertEqual(list_webhooks(self.other.id), [])

    def test_set_active_and_delete_cross_user_blocked(self):
        from services.webhooks import (
            WebhookError,
            delete_webhook,
            register_webhook,
            set_active,
        )

        wh = register_webhook(self.user.id, "https://x.com", ["stage.added"])
        with self.assertRaises(WebhookError):
            set_active(self.other.id, wh.id, False)
        with self.assertRaises(WebhookError):
            delete_webhook(self.other.id, wh.id)

    def test_set_active_toggles(self):
        from services.webhooks import list_webhooks, register_webhook, set_active

        wh = register_webhook(self.user.id, "https://x.com", ["stage.added"])
        set_active(self.user.id, wh.id, False)
        self.assertFalse(list_webhooks(self.user.id)[0].active)

    def test_audit_on_create_and_delete(self):
        from services.audit import list_for_user
        from services.webhooks import delete_webhook, register_webhook

        wh = register_webhook(self.user.id, "https://x.com", ["stage.added"])
        delete_webhook(self.user.id, wh.id)
        kinds = {e.kind for e in list_for_user(self.user.id)}
        self.assertIn("webhook.create", kinds)
        self.assertIn("webhook.delete", kinds)


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------

class SigningTests(unittest.TestCase):
    def test_sign_matches_manual_hmac(self):
        from services.webhooks import sign

        secret = "topsecret"
        body = b'{"event":"x"}'
        expected = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        self.assertEqual(sign(secret, body), expected)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

class DispatchTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()

    def test_dispatch_posts_to_subscribed_webhook(self):
        import services.webhooks as mod

        mod.register_webhook(self.user.id, "https://x.com/h", ["stage.added"])
        with mock.patch.object(mod.requests, "post", return_value=_Resp(200)) as post:
            deliveries = mod.dispatch_event(
                self.user.id, "stage.added", {"application_id": 1},
            )
        self.assertEqual(len(deliveries), 1)
        self.assertTrue(deliveries[0].success)
        self.assertEqual(deliveries[0].status_code, 200)
        post.assert_called_once()
        # Signature header present + correct.
        call = post.call_args
        headers = call.kwargs["headers"]
        self.assertIn("X-JobOffer-Signature", headers)
        self.assertEqual(headers["X-JobOffer-Event"], "stage.added")
        body = call.kwargs["data"]
        self.assertEqual(headers["X-JobOffer-Signature"], mod.sign(
            mod.list_webhooks(self.user.id)[0].secret, body,
        ))

    def test_dispatch_skips_unsubscribed_event(self):
        import services.webhooks as mod

        mod.register_webhook(self.user.id, "https://x.com/h", ["application.saved"])
        with mock.patch.object(mod.requests, "post") as post:
            deliveries = mod.dispatch_event(
                self.user.id, "stage.added", {"x": 1},
            )
        self.assertEqual(deliveries, [])
        post.assert_not_called()

    def test_dispatch_skips_inactive_webhook(self):
        import services.webhooks as mod

        wh = mod.register_webhook(self.user.id, "https://x.com/h", ["stage.added"])
        mod.set_active(self.user.id, wh.id, False)
        with mock.patch.object(mod.requests, "post") as post:
            deliveries = mod.dispatch_event(self.user.id, "stage.added", {"x": 1})
        self.assertEqual(deliveries, [])
        post.assert_not_called()

    def test_dispatch_records_failure_without_raising(self):
        import services.webhooks as mod

        mod.register_webhook(self.user.id, "https://x.com/h", ["stage.added"])
        with mock.patch.object(mod.requests, "post", return_value=_Resp(500, "boom")):
            deliveries = mod.dispatch_event(self.user.id, "stage.added", {"x": 1})
        self.assertEqual(len(deliveries), 1)
        self.assertFalse(deliveries[0].success)
        self.assertEqual(deliveries[0].status_code, 500)
        self.assertIn("500", deliveries[0].error)

    def test_dispatch_records_network_error(self):
        import services.webhooks as mod

        mod.register_webhook(self.user.id, "https://x.com/h", ["stage.added"])
        with mock.patch.object(mod.requests, "post",
                               side_effect=mod.requests.ConnectionError("no route")):
            deliveries = mod.dispatch_event(self.user.id, "stage.added", {"x": 1})
        self.assertFalse(deliveries[0].success)
        self.assertIsNone(deliveries[0].status_code)
        self.assertIn("no route", deliveries[0].error)

    def test_redeliver_creates_new_delivery_row(self):
        import services.webhooks as mod

        mod.register_webhook(self.user.id, "https://x.com/h", ["stage.added"])
        with mock.patch.object(mod.requests, "post", return_value=_Resp(500)):
            first = mod.dispatch_event(self.user.id, "stage.added", {"x": 1})[0]
        with mock.patch.object(mod.requests, "post", return_value=_Resp(200)):
            retry = mod.redeliver(self.user.id, first.id)
        self.assertNotEqual(retry.id, first.id)
        self.assertTrue(retry.success)
        # Both rows exist in the log.
        self.assertEqual(len(mod.list_deliveries(self.user.id)), 2)

    def test_redeliver_cross_user_blocked(self):
        import services.webhooks as mod

        mod.register_webhook(self.user.id, "https://x.com/h", ["stage.added"])
        with mock.patch.object(mod.requests, "post", return_value=_Resp(200)):
            d = mod.dispatch_event(self.user.id, "stage.added", {"x": 1})[0]
        other = _register("other@x.com")
        with self.assertRaises(mod.WebhookError):
            mod.redeliver(other.id, d.id)

    def test_payload_body_includes_event_and_data(self):
        import services.webhooks as mod

        mod.register_webhook(self.user.id, "https://x.com/h", ["stage.added"])
        captured = {}
        def fake_post(url, data=None, headers=None, timeout=None):
            captured["body"] = json.loads(data)
            return _Resp(200)
        with mock.patch.object(mod.requests, "post", side_effect=fake_post):
            mod.dispatch_event(self.user.id, "stage.added", {"application_id": 7})
        self.assertEqual(captured["body"]["event"], "stage.added")
        self.assertEqual(captured["body"]["data"]["application_id"], 7)
        self.assertIn("sent_at", captured["body"])


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

class Phase23MigrationTests(unittest.TestCase):
    def test_tables_created(self):
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
            self.assertIn("webhooks", tables)
            self.assertIn("webhook_deliveries", tables)


if __name__ == "__main__":
    unittest.main()
