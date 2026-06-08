"""P2 #11: durable webhook delivery via the Celery queue with retry/backoff.

The synchronous dispatch_event path is covered in test_phase23_units; here we
test the durable layer: attempt_delivery against an existing row, the pending
row creation, the backoff schedule, and that dispatch_event_durable enqueues a
retrying task per matching webhook (and skips unsubscribed/inactive ones).
Celery itself is mocked — no broker in the sandbox.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db.session import reset_engine_for_testing  # noqa: E402


class _OkResp:
    status_code = 200
    text = "ok"


class _ErrResp:
    status_code = 500
    text = "boom"


class BackoffTests(unittest.TestCase):
    def test_exponential_backoff(self):
        from services import webhooks
        with mock.patch.dict(os.environ, {"WEBHOOK_RETRY_BACKOFF": "10"}):
            self.assertEqual(webhooks.retry_delay_for(0), 10)
            self.assertEqual(webhooks.retry_delay_for(1), 20)
            self.assertEqual(webhooks.retry_delay_for(2), 40)

    def test_backoff_capped_at_one_hour(self):
        from services import webhooks
        with mock.patch.dict(os.environ, {"WEBHOOK_RETRY_BACKOFF": "10"}):
            self.assertEqual(webhooks.retry_delay_for(20), 3600)

    def test_max_attempts_default(self):
        from services import webhooks
        os.environ.pop("WEBHOOK_MAX_ATTEMPTS", None)
        self.assertEqual(webhooks._max_attempts(), 5)


class AttemptDeliveryTests(unittest.TestCase):
    def setUp(self):
        reset_engine_for_testing("sqlite:///:memory:")
        from services import auth, webhooks
        self.user = auth.register_user("wh@example.com", "Sup3rSecret!")
        self.hook = webhooks.register_webhook(
            self.user.id, "https://example.test/hook", ["stage.added"]
        )

    def test_attempt_records_success(self):
        from services import webhooks
        delivery_id = webhooks._create_delivery(self.hook, "stage.added", {"x": 1})
        with mock.patch.object(webhooks.requests, "post", return_value=_OkResp()):
            ok = webhooks.attempt_delivery(delivery_id)
        self.assertTrue(ok)
        rec = webhooks.list_deliveries(self.user.id)[0]
        self.assertTrue(rec.success)
        self.assertEqual(rec.attempts, 1)
        self.assertEqual(rec.status_code, 200)

    def test_attempt_records_failure_without_raising(self):
        from services import webhooks
        delivery_id = webhooks._create_delivery(self.hook, "stage.added", {"x": 1})
        with mock.patch.object(webhooks.requests, "post", return_value=_ErrResp()):
            ok = webhooks.attempt_delivery(delivery_id)
        self.assertFalse(ok)
        rec = webhooks.list_deliveries(self.user.id)[0]
        self.assertFalse(rec.success)
        self.assertEqual(rec.attempts, 1)
        self.assertIn("HTTP 500", rec.error)

    def test_repeated_attempts_increment_count(self):
        from services import webhooks
        delivery_id = webhooks._create_delivery(self.hook, "stage.added", {"x": 1})
        with mock.patch.object(webhooks.requests, "post", return_value=_ErrResp()):
            webhooks.attempt_delivery(delivery_id)
            webhooks.attempt_delivery(delivery_id)
        rec = webhooks.list_deliveries(self.user.id)[0]
        self.assertEqual(rec.attempts, 2)

    def test_attempt_missing_row_is_false(self):
        from services import webhooks
        self.assertFalse(webhooks.attempt_delivery(999999))

    def test_create_delivery_is_pending(self):
        from services import webhooks
        delivery_id = webhooks._create_delivery(self.hook, "stage.added", {"x": 1})
        rec = webhooks.list_deliveries(self.user.id)[0]
        self.assertEqual(rec.id, delivery_id)
        self.assertFalse(rec.success)
        self.assertEqual(rec.attempts, 0)


class DurableDispatchTests(unittest.TestCase):
    def setUp(self):
        reset_engine_for_testing("sqlite:///:memory:")
        from services import auth, webhooks
        self.user = auth.register_user("wh2@example.com", "Sup3rSecret!")
        self.active = webhooks.register_webhook(
            self.user.id, "https://example.test/a", ["stage.added"]
        )
        self.other_event = webhooks.register_webhook(
            self.user.id, "https://example.test/b", ["application.saved"]
        )

    def test_enqueues_task_per_matching_hook(self):
        from services import webhooks
        fake_task = mock.Mock()
        with mock.patch.object(webhooks, "_celery_delivery_enabled", return_value=True), \
             mock.patch("worker.tasks.deliver_webhook_task", fake_task):
            webhooks.dispatch_event_durable(self.user.id, "stage.added", {"x": 1})
        # Only the hook subscribed to stage.added is enqueued.
        self.assertEqual(fake_task.delay.call_count, 1)
        # A pending row was created for it.
        recs = webhooks.list_deliveries(self.user.id)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].attempts, 0)

    def test_falls_back_to_thread_without_celery(self):
        from services import webhooks
        with mock.patch.object(webhooks, "_celery_delivery_enabled", return_value=False), \
             mock.patch.object(webhooks, "dispatch_event_background") as bg:
            webhooks.dispatch_event_durable(self.user.id, "stage.added", {"x": 1})
        bg.assert_called_once_with(self.user.id, "stage.added", {"x": 1})

    def test_enqueue_failure_falls_back(self):
        from services import webhooks
        with mock.patch.object(webhooks, "_celery_delivery_enabled", return_value=True), \
             mock.patch("worker.tasks.deliver_webhook_task") as task, \
             mock.patch.object(webhooks, "dispatch_event_background") as bg:
            task.delay.side_effect = RuntimeError("broker down")
            webhooks.dispatch_event_durable(self.user.id, "stage.added", {"x": 1})
        bg.assert_called_once()


class TaskBodyTests(unittest.TestCase):
    def setUp(self):
        reset_engine_for_testing("sqlite:///:memory:")
        from services import auth, webhooks
        self.user = auth.register_user("wh3@example.com", "Sup3rSecret!")
        self.hook = webhooks.register_webhook(
            self.user.id, "https://example.test/hook", ["stage.added"]
        )

    def test_deliver_webhook_wrapper_calls_attempt(self):
        from services import webhooks
        from worker.tasks import _deliver_webhook
        delivery_id = webhooks._create_delivery(self.hook, "stage.added", {"x": 1})
        with mock.patch.object(webhooks.requests, "post", return_value=_OkResp()):
            self.assertTrue(_deliver_webhook(delivery_id))


if __name__ == "__main__":
    unittest.main()
