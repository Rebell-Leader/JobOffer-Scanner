"""P1 #7: LLM cost controls — token accounting, budgets, completion cache.

Covers services/usage (pricing, ledger record, spend sum, budget gate +
attribution scope), the utils/llm wiring (usage recorded from a provider's
usage object; opt-in completion cache returns free hits), and the budget
enforcement in analysis_runner.check_user_quota.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db.session import reset_engine_for_testing  # noqa: E402


class PricingTests(unittest.TestCase):
    def test_known_model_priced(self):
        from services import usage
        # gpt-4o-mini: (0.00015, 0.0006) per 1k.
        cost = usage.estimate_cost_usd("gpt-4o-mini", 1000, 1000)
        self.assertAlmostEqual(cost, 0.00015 + 0.0006, places=6)

    def test_unknown_model_uses_default_rate(self):
        from services import usage
        cost = usage.estimate_cost_usd("some-unlisted-model", 1000, 0)
        self.assertAlmostEqual(cost, usage._DEFAULT_RATE[0], places=6)

    def test_pricing_override_via_env(self):
        from services import usage
        with mock.patch.dict(os.environ, {"LLM_PRICING_JSON": '{"m": [1.0, 2.0]}'}):
            cost = usage.estimate_cost_usd("m", 1000, 1000)
        self.assertAlmostEqual(cost, 3.0, places=6)

    def test_bad_pricing_override_falls_back(self):
        from services import usage
        with mock.patch.dict(os.environ, {"LLM_PRICING_JSON": "not json"}):
            # Falls back to defaults; known model still priced.
            cost = usage.estimate_cost_usd("gpt-4o-mini", 1000, 0)
        self.assertAlmostEqual(cost, 0.00015, places=6)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        reset_engine_for_testing("sqlite:///:memory:")
        from services import auth
        self.user = auth.register_user("usage@example.com", "Sup3rSecret!")

    def test_record_and_spend(self):
        from services import usage
        usage.record_completion(
            "openai", "gpt-4o-mini",
            {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000},
            user_id=self.user.id,
        )
        spent = usage.spend_usd(self.user.id)
        self.assertAlmostEqual(spent, 0.00075, places=6)

    def test_record_none_usage_is_noop_row(self):
        from services import usage
        usage.record_completion("openai", "gpt-4o-mini", None, user_id=self.user.id)
        self.assertEqual(usage.spend_usd(self.user.id), 0.0)

    def test_attribution_via_scope(self):
        from services import usage
        with usage.account(self.user.id):
            self.assertEqual(usage.current_user(), self.user.id)
            usage.record_completion(
                "openai", "gpt-4o",
                {"prompt_tokens": 1000, "completion_tokens": 0},
            )
        self.assertGreater(usage.spend_usd(self.user.id), 0.0)
        # Scope restored on exit.
        self.assertIsNone(usage.current_user())

    def test_unattributed_records_with_null_owner(self):
        from sqlalchemy import func, select

        from db.models import LlmUsage
        from db.session import get_session
        from services import usage
        usage.record_completion(
            "openai", "gpt-4o-mini",
            {"prompt_tokens": 10, "completion_tokens": 10},
        )  # no user_id, no scope
        with get_session() as session:
            n = session.execute(
                select(func.count()).select_from(LlmUsage).where(LlmUsage.user_id.is_(None))
            ).scalar_one()
        self.assertEqual(n, 1)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        reset_engine_for_testing("sqlite:///:memory:")
        from services import auth
        self.user = auth.register_user("budget@example.com", "Sup3rSecret!")

    def test_no_budget_configured_is_noop(self):
        from services import usage
        os.environ.pop("LLM_BUDGET_USD", None)
        usage.check_budget(self.user.id)  # should not raise

    def test_under_budget_passes(self):
        from services import usage
        usage.record_completion(
            "openai", "gpt-4o-mini",
            {"prompt_tokens": 1000, "completion_tokens": 0}, user_id=self.user.id,
        )
        with mock.patch.dict(os.environ, {"LLM_BUDGET_USD": "1.00"}):
            usage.check_budget(self.user.id)  # 0.00015 << 1.00

    def test_over_budget_raises(self):
        from services import usage
        # Spend ~$3 with the override price.
        with mock.patch.dict(os.environ, {"LLM_PRICING_JSON": '{"m": [1.0, 2.0]}'}):
            usage.record_completion(
                "openai", "m",
                {"prompt_tokens": 1000, "completion_tokens": 1000}, user_id=self.user.id,
            )
        with mock.patch.dict(os.environ, {"LLM_BUDGET_USD": "1.00"}):
            with self.assertRaises(usage.BudgetExceeded):
                usage.check_budget(self.user.id)

    def test_none_user_skips_budget(self):
        from services import usage
        with mock.patch.dict(os.environ, {"LLM_BUDGET_USD": "0.01"}):
            usage.check_budget(None)  # anon/bot — never raises

    def test_check_user_quota_enforces_budget(self):
        from services import analysis_runner, usage
        from services.rate_limit import reset_backend_for_testing
        reset_backend_for_testing()
        with mock.patch.dict(os.environ, {"LLM_PRICING_JSON": '{"m": [1.0, 2.0]}'}):
            usage.record_completion(
                "openai", "m",
                {"prompt_tokens": 1000, "completion_tokens": 1000}, user_id=self.user.id,
            )
        with mock.patch.dict(os.environ, {"LLM_BUDGET_USD": "1.00"}):
            with self.assertRaises(usage.BudgetExceeded):
                analysis_runner.check_user_quota(self.user.id)


class LlmWiringTests(unittest.TestCase):
    """utils/llm records usage from a provider's usage object, and the opt-in
    completion cache returns free hits without a second provider call."""

    def setUp(self):
        reset_engine_for_testing("sqlite:///:memory:")

    def test_usage_recorded_from_openai_response(self):
        import utils.llm as llm
        from services import usage

        class _Usage:
            prompt_tokens = 100
            completion_tokens = 50
            total_tokens = 150

        class _Msg:
            content = "hello"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]
            usage = _Usage()

        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k", "LLM_PROVIDER": "openai"}), \
             mock.patch("openai.OpenAI") as Client:
            Client.return_value.chat.completions.create.return_value = _Resp()
            with usage.account(None):
                out = llm.get_completion("hi", model="fast")
        self.assertEqual(out, "hello")
        # Recorded one row with the reported tokens.
        from sqlalchemy import select

        from db.models import LlmUsage
        from db.session import get_session
        with get_session() as session:
            rows = session.execute(select(LlmUsage)).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].prompt_tokens, 100)
        self.assertEqual(rows[0].completion_tokens, 50)

    def test_completion_cache_hit_skips_second_call(self):
        import utils.llm as llm

        class _Msg:
            content = "cached-answer"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]
            usage = None

        from utils.cache import cache
        cache.clear()
        with mock.patch.dict(os.environ, {
            "OPENAI_API_KEY": "k", "LLM_PROVIDER": "openai",
            "LLM_CACHE_COMPLETIONS": "1",
        }), mock.patch("openai.OpenAI") as Client:
            create = Client.return_value.chat.completions.create
            create.return_value = _Resp()
            first = llm.get_completion("same prompt", model="fast")
            second = llm.get_completion("same prompt", model="fast")
        self.assertEqual(first, "cached-answer")
        self.assertEqual(second, "cached-answer")
        # Second call served from cache -> provider invoked exactly once.
        self.assertEqual(create.call_count, 1)


if __name__ == "__main__":
    unittest.main()
