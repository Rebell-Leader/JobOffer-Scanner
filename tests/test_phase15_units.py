"""Unit tests for Phase 15: Telegram account linking + stage notifications."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
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
# Binding flow
# ---------------------------------------------------------------------------

class TelegramBindingTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        self.other = _register("other@x.com")

    def test_issue_token_then_bind_creates_link(self):
        from services.telegram_link import (
            complete_binding,
            get_link,
            issue_binding_token,
        )

        token = issue_binding_token(self.user.id)
        self.assertGreater(len(token), 10)

        link = complete_binding(chat_id=12345, raw_token=token, chat_username="janedoe")
        self.assertEqual(link.user_id, self.user.id)
        self.assertEqual(link.chat_id, 12345)
        self.assertTrue(link.notify_on_stage)

        fetched = get_link(self.user.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.chat_id, 12345)
        self.assertEqual(fetched.chat_username, "janedoe")

    def test_bind_rejects_invalid_token(self):
        from services.telegram_link import TelegramLinkError, complete_binding

        with self.assertRaises(TelegramLinkError):
            complete_binding(chat_id=1, raw_token="totally-fake-token")

    def test_bind_rejects_empty_token(self):
        from services.telegram_link import TelegramLinkError, complete_binding

        with self.assertRaises(TelegramLinkError):
            complete_binding(chat_id=1, raw_token="")

    def test_bind_token_is_one_shot(self):
        from services.telegram_link import (
            TelegramLinkError,
            complete_binding,
            issue_binding_token,
        )

        token = issue_binding_token(self.user.id)
        complete_binding(chat_id=1, raw_token=token)
        with self.assertRaises(TelegramLinkError):
            complete_binding(chat_id=2, raw_token=token)  # already used

    def test_expired_token_rejected(self):
        from sqlalchemy import select

        from db.models import TelegramLinkBindingToken
        from db.session import get_session
        from services.telegram_link import (
            TelegramLinkError,
            complete_binding,
            issue_binding_token,
        )

        token = issue_binding_token(self.user.id)
        # Manually expire.
        with get_session() as s:
            row = s.execute(select(TelegramLinkBindingToken)).scalar_one()
            row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            s.commit()
        with self.assertRaises(TelegramLinkError):
            complete_binding(chat_id=1, raw_token=token)

    def test_raw_token_is_not_persisted(self):
        from sqlalchemy import select

        from db.models import TelegramLinkBindingToken
        from db.session import get_session
        from services.telegram_link import issue_binding_token

        token = issue_binding_token(self.user.id)
        with get_session() as s:
            row = s.execute(select(TelegramLinkBindingToken)).scalar_one()
            self.assertNotEqual(row.token_hash, token)
            # bcrypt hashes start with $2.
            self.assertTrue(row.token_hash.startswith("$2"))

    def test_rebind_replaces_existing_link(self):
        from services.telegram_link import (
            complete_binding,
            get_link,
            issue_binding_token,
        )

        # First bind.
        tok1 = issue_binding_token(self.user.id)
        complete_binding(chat_id=111, raw_token=tok1)
        # Second bind: same user, different chat — should overwrite.
        tok2 = issue_binding_token(self.user.id)
        complete_binding(chat_id=222, raw_token=tok2, chat_username="newhandle")

        link = get_link(self.user.id)
        self.assertEqual(link.chat_id, 222)
        self.assertEqual(link.chat_username, "newhandle")

    def test_get_user_id_by_chat(self):
        from services.telegram_link import (
            complete_binding,
            get_user_id_by_chat,
            issue_binding_token,
        )

        tok = issue_binding_token(self.user.id)
        complete_binding(chat_id=999, raw_token=tok)
        self.assertEqual(get_user_id_by_chat(999), self.user.id)
        self.assertIsNone(get_user_id_by_chat(1234567890))

    def test_unlink(self):
        from services.telegram_link import (
            complete_binding,
            get_link,
            issue_binding_token,
            unlink,
        )

        tok = issue_binding_token(self.user.id)
        complete_binding(chat_id=42, raw_token=tok)
        self.assertTrue(unlink(self.user.id))
        self.assertIsNone(get_link(self.user.id))
        # Idempotent — second unlink is a no-op.
        self.assertFalse(unlink(self.user.id))

    def test_set_notify_on_stage(self):
        from services.telegram_link import (
            TelegramLinkError,
            complete_binding,
            get_link,
            issue_binding_token,
            set_notify_on_stage,
        )

        tok = issue_binding_token(self.user.id)
        complete_binding(chat_id=42, raw_token=tok)
        set_notify_on_stage(self.user.id, False)
        self.assertFalse(get_link(self.user.id).notify_on_stage)
        set_notify_on_stage(self.user.id, True)
        self.assertTrue(get_link(self.user.id).notify_on_stage)
        # Error path: user with no link.
        with self.assertRaises(TelegramLinkError):
            set_notify_on_stage(self.other.id, True)


# ---------------------------------------------------------------------------
# HTTP send
# ---------------------------------------------------------------------------

class TelegramSendTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        from services.telegram_link import complete_binding, issue_binding_token
        complete_binding(chat_id=777, raw_token=issue_binding_token(self.user.id))

    def test_send_to_chat_skips_when_token_unset(self):
        import services.telegram_link as mod

        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        # Should NOT make an HTTP request when there's no bot token.
        with mock.patch.object(mod.requests, "post") as post:
            ok = mod.send_to_chat(123, "hello")
        self.assertFalse(ok)
        post.assert_not_called()

    def test_send_to_chat_posts_to_telegram_api(self):
        import services.telegram_link as mod

        class _Resp:
            status_code = 200
            text = "ok"

        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "TESTKEY"}), \
             mock.patch.object(mod.requests, "post", return_value=_Resp()) as post:
            ok = mod.send_to_chat(123, "hi", parse_mode="Markdown")
        self.assertTrue(ok)
        called_url = post.call_args[0][0]
        self.assertIn("api.telegram.org", called_url)
        self.assertIn("TESTKEY", called_url)
        called_payload = post.call_args[1]["json"]
        self.assertEqual(called_payload["chat_id"], 123)
        self.assertEqual(called_payload["text"], "hi")
        self.assertEqual(called_payload["parse_mode"], "Markdown")

    def test_send_to_chat_returns_false_on_http_error(self):
        import services.telegram_link as mod

        class _Resp:
            status_code = 400
            text = "Bad Request"

        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "X"}), \
             mock.patch.object(mod.requests, "post", return_value=_Resp()):
            self.assertFalse(mod.send_to_chat(1, "x"))

    def test_send_to_user_uses_linked_chat(self):
        import services.telegram_link as mod

        captured = {}
        def fake_send(chat_id, text, parse_mode="Markdown"):
            captured["chat_id"] = chat_id
            captured["text"] = text
            return True
        with mock.patch.object(mod, "send_to_chat", side_effect=fake_send):
            ok = mod.send_to_user(self.user.id, "hello!")
        self.assertTrue(ok)
        self.assertEqual(captured["chat_id"], 777)
        self.assertEqual(captured["text"], "hello!")

    def test_send_to_user_returns_false_when_unlinked(self):
        import services.telegram_link as mod
        from services.auth import register_user
        u = register_user("nolink@x.com", "longenough")
        self.assertFalse(mod.send_to_user(u.id, "x"))


# ---------------------------------------------------------------------------
# Stage notification wrapper
# ---------------------------------------------------------------------------

class StageNotificationTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()
        from services.applications import save_analysis
        from services.telegram_link import complete_binding, issue_binding_token
        self.app_rec = save_analysis(
            self.user.id,
            {"company_name": "Stripe", "job_title": "ML Engineer", "location": "Berlin"},
            {"final_report": "# r", "verdict": {}, "resume_analysis": {}},
        )
        complete_binding(chat_id=42, raw_token=issue_binding_token(self.user.id))

    def test_notifies_when_linked_and_enabled(self):
        import services.notifications as notif

        stage = mock.MagicMock(
            kind="phone_screen", occurred_on=date(2026, 5, 1), notes="Great chat",
        )
        with mock.patch.object(notif, "send_to_user", return_value=True) as send:
            ok = notif.notify_stage_added(self.user.id, self.app_rec, stage)
        self.assertTrue(ok)
        text = send.call_args[0][1]
        self.assertIn("Phone screen", text)
        self.assertIn("ML Engineer", text)
        self.assertIn("Stripe", text)
        self.assertIn("Great chat", text)

    def test_skips_when_notify_disabled(self):
        import services.notifications as notif
        from services.telegram_link import set_notify_on_stage

        set_notify_on_stage(self.user.id, False)
        stage = mock.MagicMock(kind="applied", occurred_on=date.today(), notes=None)
        with mock.patch.object(notif, "send_to_user") as send:
            ok = notif.notify_stage_added(self.user.id, self.app_rec, stage)
        self.assertFalse(ok)
        send.assert_not_called()

    def test_skips_when_unlinked(self):
        import services.notifications as notif
        from services.telegram_link import unlink

        unlink(self.user.id)
        stage = mock.MagicMock(kind="applied", occurred_on=date.today(), notes=None)
        with mock.patch.object(notif, "send_to_user") as send:
            ok = notif.notify_stage_added(self.user.id, self.app_rec, stage)
        self.assertFalse(ok)
        send.assert_not_called()

    def test_notification_truncates_long_notes(self):
        import services.notifications as notif

        long_note = "x" * 800
        stage = mock.MagicMock(kind="applied", occurred_on=date.today(), notes=long_note)
        captured_text = {}
        def grab(user_id, text):
            captured_text["text"] = text
            return True
        with mock.patch.object(notif, "send_to_user", side_effect=grab):
            notif.notify_stage_added(self.user.id, self.app_rec, stage)
        # Truncated to ~240 chars + ellipsis; not the full 800.
        self.assertLess(len(captured_text["text"]), 500)
        self.assertIn("…", captured_text["text"])

    def test_notification_never_raises_on_send_failure(self):
        """Failure to deliver a notification must never bubble up to the UI."""
        import services.notifications as notif

        stage = mock.MagicMock(kind="applied", occurred_on=date.today(), notes=None)
        with mock.patch.object(notif, "send_to_user", side_effect=RuntimeError("boom")):
            # Must not raise.
            ok = notif.notify_stage_added(self.user.id, self.app_rec, stage)
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# Bot handlers
# ---------------------------------------------------------------------------

class BotLinkHandlerTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        self.user = _register()

    def _capture(self):
        captured = []
        async def reply(text):
            captured.append(text)
        return captured, reply

    def test_bind_with_valid_token_succeeds(self):
        from bot import handlers
        from services.telegram_link import get_link, issue_binding_token

        token = issue_binding_token(self.user.id)
        captured, reply = self._capture()
        asyncio.run(handlers.handle_bind(reply, args=token, chat_id=555, chat_username="me"))
        self.assertTrue(any("Linked" in m for m in captured))
        self.assertEqual(get_link(self.user.id).chat_id, 555)

    def test_bind_with_no_token_shows_usage(self):
        from bot import handlers

        captured, reply = self._capture()
        asyncio.run(handlers.handle_bind(reply, args="", chat_id=1, chat_username=None))
        self.assertTrue(any("Usage" in m for m in captured))

    def test_bind_with_bad_token_returns_error(self):
        from bot import handlers

        captured, reply = self._capture()
        asyncio.run(handlers.handle_bind(
            reply, args="bogus-token", chat_id=1, chat_username=None,
        ))
        self.assertTrue(any("Invalid" in m or "expired" in m for m in captured))

    def test_unbind_when_unlinked_is_polite(self):
        from bot import handlers

        captured, reply = self._capture()
        asyncio.run(handlers.handle_unbind(reply, args="", chat_id=1))
        self.assertTrue(any("isn't linked" in m for m in captured))

    def test_unbind_removes_existing_link(self):
        from bot import handlers
        from services.telegram_link import (
            complete_binding,
            get_link,
            issue_binding_token,
        )

        complete_binding(chat_id=42, raw_token=issue_binding_token(self.user.id))
        captured, reply = self._capture()
        asyncio.run(handlers.handle_unbind(reply, args="", chat_id=42))
        self.assertIsNone(get_link(self.user.id))
        self.assertTrue(any("Disconnected" in m for m in captured))

    def test_me_reports_unlinked(self):
        from bot import handlers

        captured, reply = self._capture()
        asyncio.run(handlers.handle_me(reply, args="", chat_id=99))
        self.assertTrue(any("isn't linked" in m for m in captured))

    def test_me_reports_linked_account(self):
        from bot import handlers
        from services.telegram_link import complete_binding, issue_binding_token

        complete_binding(chat_id=42, raw_token=issue_binding_token(self.user.id))
        captured, reply = self._capture()
        asyncio.run(handlers.handle_me(reply, args="", chat_id=42))
        self.assertTrue(any(f"#{self.user.id}" in m for m in captured))


# ---------------------------------------------------------------------------
# Migration applies cleanly
# ---------------------------------------------------------------------------

class TelegramMigrationTests(unittest.TestCase):
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
            self.assertIn("telegram_links", tables)
            self.assertIn("telegram_link_tokens", tables)


if __name__ == "__main__":
    unittest.main()
