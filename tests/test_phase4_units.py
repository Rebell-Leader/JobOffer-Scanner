"""Unit tests for Phase 4: password reset, Adzuna summary, telegram handlers."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_db():
    from db.session import reset_engine_for_testing
    reset_engine_for_testing("sqlite:///:memory:")


# ---------------------------------------------------------------------------
# Password reset & change-password
# ---------------------------------------------------------------------------

class PasswordResetTests(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        from services.auth import register_user
        self.user = register_user("p@x.com", "originalpw")

    def test_change_password_happy_path(self):
        from services.auth import authenticate_user, change_password

        change_password(self.user.id, "originalpw", "newpassword")
        # Old password no longer works.
        from services.auth import AuthError
        with self.assertRaises(AuthError):
            authenticate_user("p@x.com", "originalpw")
        # New one does.
        authenticate_user("p@x.com", "newpassword")

    def test_change_password_rejects_wrong_current(self):
        from services.auth import AuthError, change_password
        with self.assertRaises(AuthError):
            change_password(self.user.id, "wrongguess", "newpassword")

    def test_change_password_enforces_min_length(self):
        from services.auth import AuthError, change_password
        with self.assertRaises(AuthError):
            change_password(self.user.id, "originalpw", "short")

    def test_request_reset_returns_token_for_known_user(self):
        from services.auth import request_password_reset

        token = request_password_reset("p@x.com")
        self.assertIsNotNone(token)
        self.assertGreater(len(token), 20)

    def test_request_reset_returns_none_for_unknown(self):
        from services.auth import request_password_reset
        self.assertIsNone(request_password_reset("nobody@x.com"))

    def test_complete_reset_happy_path(self):
        from services.auth import (
            authenticate_user,
            complete_password_reset,
            request_password_reset,
        )

        token = request_password_reset("p@x.com")
        complete_password_reset("p@x.com", token, "brandnewpw")
        authenticate_user("p@x.com", "brandnewpw")

    def test_reset_token_is_one_shot(self):
        from services.auth import (
            AuthError,
            complete_password_reset,
            request_password_reset,
        )

        token = request_password_reset("p@x.com")
        complete_password_reset("p@x.com", token, "brandnewpw")
        with self.assertRaises(AuthError):
            complete_password_reset("p@x.com", token, "anotherpw")

    def test_expired_token_rejected(self):
        from db.models import PasswordResetToken
        from db.session import get_session
        from services.auth import (
            AuthError,
            complete_password_reset,
            request_password_reset,
        )
        from sqlalchemy import select

        token = request_password_reset("p@x.com")
        # Hand-expire the token.
        with get_session() as s:
            row = s.execute(select(PasswordResetToken)).scalar_one()
            row.expires_at = datetime.utcnow() - timedelta(minutes=1)
            s.commit()
        with self.assertRaises(AuthError):
            complete_password_reset("p@x.com", token, "brandnewpw")

    def test_wrong_token_rejected(self):
        from services.auth import (
            AuthError,
            complete_password_reset,
            request_password_reset,
        )

        request_password_reset("p@x.com")
        with self.assertRaises(AuthError):
            complete_password_reset("p@x.com", "totally-wrong-token", "brandnewpw")

    def test_token_hash_not_raw_in_db(self):
        from db.models import PasswordResetToken
        from db.session import get_session
        from services.auth import request_password_reset
        from sqlalchemy import select

        token = request_password_reset("p@x.com")
        with get_session() as s:
            row = s.execute(select(PasswordResetToken)).scalar_one()
            self.assertNotEqual(row.token_hash, token)
            self.assertTrue(row.token_hash.startswith("$2"))


# ---------------------------------------------------------------------------
# Adzuna salary summary
# ---------------------------------------------------------------------------

class AdzunaSummaryTests(unittest.TestCase):
    def test_summary_with_data(self):
        from tools.data_sources import _summarize_adzuna

        payload = {
            "results": [
                {"salary_min": 80000, "salary_max": 100000, "salary_currency": "USD",
                 "salary_is_predicted": "0"},
                {"salary_min": 90000, "salary_max": 110000, "salary_currency": "USD",
                 "salary_is_predicted": "1"},
            ]
        }
        out = _summarize_adzuna(payload, "ML Engineer", "Berlin")
        self.assertIn("REAL DATA", out)
        self.assertIn("ML Engineer", out)
        self.assertIn("USD", out)
        # Median of the two midpoints: (90000 + 100000) / 2 = 95,000.
        self.assertIn("95,000", out)
        self.assertIn("Predicted salaries", out)

    def test_summary_returns_none_when_no_salaries(self):
        from tools.data_sources import _summarize_adzuna
        payload = {"results": [{"title": "x"}]}  # no salary fields
        self.assertIsNone(_summarize_adzuna(payload, "x", "y"))

    def test_fetch_returns_none_without_keys(self):
        from tools import data_sources

        os.environ.pop("ADZUNA_APP_ID", None)
        os.environ.pop("ADZUNA_APP_KEY", None)
        self.assertIsNone(data_sources.fetch_salary_benchmark("Engineer", "Berlin"))

    def test_country_routing(self):
        from tools.data_sources import _country_for_location

        self.assertEqual(_country_for_location("Berlin, Germany"), "de")
        self.assertEqual(_country_for_location("London, UK"), "gb")
        self.assertEqual(_country_for_location("Toronto, Canada"), "ca")
        # Unknown defaults to US so Adzuna gets a valid country code.
        self.assertEqual(_country_for_location("Atlantis"), "us")
        self.assertEqual(_country_for_location(None), "us")


# ---------------------------------------------------------------------------
# Telegram handler logic (no Telegram lib needed)
# ---------------------------------------------------------------------------

class _ReplyCapture:
    def __init__(self):
        self.messages = []

    async def __call__(self, text: str) -> None:
        self.messages.append(text)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


class TelegramHandlerTests(unittest.TestCase):
    def test_parse_url_only(self):
        from bot.handlers import parse_analyze_args
        out = parse_analyze_args("https://example.com/jobs/1")
        self.assertEqual(out.url, "https://example.com/jobs/1")
        self.assertEqual(out.text, "")

    def test_parse_url_with_followup_text(self):
        from bot.handlers import parse_analyze_args
        out = parse_analyze_args("https://example.com/jobs/1\nExtra context")
        self.assertEqual(out.url, "https://example.com/jobs/1")
        self.assertEqual(out.text, "Extra context")

    def test_parse_text_only(self):
        from bot.handlers import parse_analyze_args
        out = parse_analyze_args("Company: Acme\nTitle: Engineer")
        self.assertIsNone(out.url)
        self.assertIn("Acme", out.text)

    def test_chunk_short(self):
        from bot.handlers import chunk_for_telegram
        self.assertEqual(chunk_for_telegram("hi"), ["hi"])

    def test_chunk_long_splits_on_paragraph(self):
        from bot.handlers import chunk_for_telegram
        text = ("para one " * 600) + "\n\n" + ("para two " * 600)
        chunks = chunk_for_telegram(text, limit=4000)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 4000)

    def test_format_summary_renders_verdict(self):
        from bot.handlers import format_summary
        result = {
            "verdict": {"verdict": "Recommended", "light": "green",
                        "reasons": ["a", "b"], "confidence": 8},
            "job_details": {"extracted_details": {
                "company_name": "Acme", "job_title": "Eng", "location": "Berlin"
            }},
        }
        out = format_summary(result)
        self.assertIn("🟢", out)
        self.assertIn("Recommended", out)
        self.assertIn("confidence 8/10", out)
        self.assertIn("Acme", out)

    def test_format_summary_handles_error(self):
        from bot.handlers import format_summary
        self.assertIn("Analysis failed", format_summary({"error": "boom"}))

    def test_analyze_handler_empty_input_helps(self):
        from bot.handlers import handle_analyze
        cap = _ReplyCapture()
        _run(handle_analyze(cap, ""))
        self.assertTrue(any("URL" in m or "posting" in m for m in cap.messages))

    def test_analyze_handler_with_text(self):
        from bot import handlers
        cap = _ReplyCapture()
        fake_result = {
            "verdict": {"verdict": "Recommended", "light": "green", "reasons": []},
            "job_details": {"extracted_details": {"company_name": "Acme"}},
            "final_report": "# Report\n\nShort.",
        }
        with mock.patch.object(handlers, "run_analysis", return_value=fake_result):
            _run(handlers.handle_analyze(cap, "Company: Acme\nTitle: Eng"))
        joined = "\n".join(cap.messages)
        self.assertIn("Analyzing", joined)
        self.assertIn("Recommended", joined)
        self.assertIn("Short.", joined)


if __name__ == "__main__":
    unittest.main()
