"""Billing: subscription tiers, quota enforcement, Stripe webhook mirroring.

Stripe itself is always mocked (no network). The invariant guarded hardest:
with NO STRIPE_SECRET_KEY, billing is disabled and every user is on the
"unlimited" tier — self-hosted deployments behave exactly as before billing
existed (which is also why the whole pre-existing suite passes unchanged).
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db.session import reset_engine_for_testing  # noqa: E402

_BILLING_ENV = {"STRIPE_SECRET_KEY": "sk_test_x",
                "STRIPE_PRICE_PRO": "price_pro_1",
                "STRIPE_PRICE_POWER": "price_power_1"}


def _fresh():
    reset_engine_for_testing("sqlite:///:memory:")
    from services.rate_limit import reset_backend_for_testing
    reset_backend_for_testing()
    from services.auth import register_user
    return register_user("bill@example.com", "Sup3rSecret!")


def _set_tier(user_id: int, tier: str, status: str = "active", **cols):
    from db.models import Subscription
    from db.session import get_session
    with get_session() as session:
        session.add(Subscription(user_id=user_id, tier=tier, status=status, **cols))
        session.commit()


class TierResolutionTests(unittest.TestCase):
    def setUp(self):
        self.user = _fresh()

    def test_billing_disabled_means_unlimited(self):
        from services import billing
        os.environ.pop("STRIPE_SECRET_KEY", None)
        self.assertFalse(billing.billing_enabled())
        self.assertEqual(billing.get_tier(self.user.id), "unlimited")
        limits = billing.get_limits(self.user.id)
        self.assertEqual(limits.analyses_per_window, -1)
        self.assertTrue(limits.api_access)

    def test_enabled_without_subscription_is_free(self):
        from services import billing
        with mock.patch.dict(os.environ, _BILLING_ENV):
            self.assertEqual(billing.get_tier(self.user.id), "free")
            self.assertFalse(billing.get_limits(self.user.id).detailed_model)

    def test_active_subscription_resolves_tier(self):
        from services import billing
        _set_tier(self.user.id, "pro")
        with mock.patch.dict(os.environ, _BILLING_ENV):
            self.assertEqual(billing.get_tier(self.user.id), "pro")
            self.assertTrue(billing.get_limits(self.user.id).detailed_model)

    def test_canceled_subscription_falls_back_to_free(self):
        from services import billing
        _set_tier(self.user.id, "power", status="canceled")
        with mock.patch.dict(os.environ, _BILLING_ENV):
            self.assertEqual(billing.get_tier(self.user.id), "free")

    def test_tier_limits_json_override_merges(self):
        from services import billing
        with mock.patch.dict(os.environ, {
            **_BILLING_ENV,
            "TIER_LIMITS_JSON": '{"free": {"analyses_per_window": 9}}',
        }):
            limits = billing.get_limits(self.user.id)
        self.assertEqual(limits.analyses_per_window, 9)
        self.assertEqual(limits.artifacts_per_window, 2)  # default preserved


class AnalysisQuotaTests(unittest.TestCase):
    def setUp(self):
        self.user = _fresh()

    def test_free_tier_blocks_after_limit(self):
        from services import billing
        with mock.patch.dict(os.environ, _BILLING_ENV):
            for _ in range(5):
                billing.check_and_record_analysis(self.user.id)
            self.assertEqual(billing.analyses_used(self.user.id), 5)
            with self.assertRaises(billing.TierLimitExceeded) as ctx:
                billing.check_and_record_analysis(self.user.id)
        self.assertIn("Upgrade", str(ctx.exception))
        self.assertEqual(ctx.exception.limit_kind, "analyses")

    def test_unlimited_tier_never_blocks(self):
        from services import billing
        os.environ.pop("STRIPE_SECRET_KEY", None)
        for _ in range(10):
            billing.check_and_record_analysis(self.user.id)
        self.assertEqual(billing.analyses_used(self.user.id), 10)

    def test_tier_budget_blocks(self):
        from services import billing, usage
        # Spend over the free tier's $0.25 budget with an overridden price.
        with mock.patch.dict(os.environ, {"LLM_PRICING_JSON": '{"m": [1.0, 1.0]}'}):
            usage.record_completion("openai", "m",
                                    {"prompt_tokens": 500, "completion_tokens": 0},
                                    user_id=self.user.id)
        with mock.patch.dict(os.environ, _BILLING_ENV):
            with self.assertRaises(billing.TierLimitExceeded) as ctx:
                billing.check_and_record_analysis(self.user.id)
        self.assertEqual(ctx.exception.limit_kind, "budget")

    def test_check_user_quota_integration(self):
        from services.analysis_runner import check_user_quota
        from services.billing import TierLimitExceeded, check_and_record_analysis
        with mock.patch.dict(os.environ, _BILLING_ENV):
            for _ in range(5):
                check_and_record_analysis(self.user.id)
            with self.assertRaises(TierLimitExceeded):
                check_user_quota(self.user.id)


class ArtifactQuotaAndModelTests(unittest.TestCase):
    def setUp(self):
        self.user = _fresh()

    def _add_artifacts(self, n: int):
        from db.models import Application, ApplicationArtifact
        from db.session import get_session
        with get_session() as session:
            app = Application(user_id=self.user.id, company_name="A",
                              job_title="T", status="saved")
            session.add(app)
            session.commit()
            for _ in range(n):
                session.add(ApplicationArtifact(
                    application_id=app.id, user_id=self.user.id,
                    kind="tailored_cv", content="x",
                ))
            session.commit()

    def test_artifact_limit_blocks(self):
        from services import billing
        self._add_artifacts(2)
        with mock.patch.dict(os.environ, _BILLING_ENV):
            with self.assertRaises(billing.TierLimitExceeded) as ctx:
                billing.check_artifact_quota(self.user.id)
        self.assertEqual(ctx.exception.limit_kind, "artifacts")

    def test_artifact_quota_passes_under_limit(self):
        from services import billing
        self._add_artifacts(1)
        with mock.patch.dict(os.environ, _BILLING_ENV):
            billing.check_artifact_quota(self.user.id)  # no raise

    def test_clamp_model_free_downgrades_detailed(self):
        from services import billing
        with mock.patch.dict(os.environ, _BILLING_ENV):
            self.assertEqual(billing.clamp_model(self.user.id, "detailed"), "fast")
            self.assertEqual(billing.clamp_model(self.user.id, "fast"), "fast")
        os.environ.pop("STRIPE_SECRET_KEY", None)
        self.assertEqual(billing.clamp_model(self.user.id, "detailed"), "detailed")

    def test_plan_summary_shape(self):
        from services import billing
        with mock.patch.dict(os.environ, _BILLING_ENV):
            plan = billing.plan_summary(self.user.id)
        self.assertEqual(plan["tier"], "free")
        self.assertEqual(plan["analyses_limit"], 5)
        self.assertTrue(plan["billing_enabled"])


class StripeWebhookTests(unittest.TestCase):
    def setUp(self):
        self.user = _fresh()

    def test_checkout_completed_creates_subscription(self):
        from services import billing
        event = {
            "type": "checkout.session.completed",
            "data": {"object": {
                "metadata": {"user_id": str(self.user.id), "tier": "pro"},
                "customer": "cus_1", "subscription": "sub_1",
            }},
        }
        with mock.patch.dict(os.environ, _BILLING_ENV):
            self.assertTrue(billing.handle_webhook_event(event))
            self.assertEqual(billing.get_tier(self.user.id), "pro")

    def test_subscription_deleted_downgrades(self):
        from services import billing
        _set_tier(self.user.id, "pro", stripe_subscription_id="sub_9")
        event = {"type": "customer.subscription.deleted",
                 "data": {"object": {"id": "sub_9"}}}
        with mock.patch.dict(os.environ, _BILLING_ENV):
            self.assertTrue(billing.handle_webhook_event(event))
            self.assertEqual(billing.get_tier(self.user.id), "free")

    def test_subscription_updated_switches_tier_and_status(self):
        from services import billing
        _set_tier(self.user.id, "pro", stripe_subscription_id="sub_5")
        event = {
            "type": "customer.subscription.updated",
            "data": {"object": {
                "id": "sub_5", "status": "active",
                "current_period_end": 1900000000,
                "items": {"data": [{"price": {"id": "price_power_1"}}]},
            }},
        }
        with mock.patch.dict(os.environ, _BILLING_ENV):
            self.assertTrue(billing.handle_webhook_event(event))
            self.assertEqual(billing.get_tier(self.user.id), "power")

    def test_unknown_event_ignored(self):
        from services import billing
        self.assertFalse(billing.handle_webhook_event({"type": "invoice.paid",
                                                       "data": {"object": {}}}))

    def test_checkout_session_creation(self):
        from services import billing
        fake_stripe = mock.Mock()
        fake_stripe.checkout.Session.create.return_value = mock.Mock(
            url="https://checkout.stripe.test/s/abc")
        with mock.patch.dict(os.environ, _BILLING_ENV), \
             mock.patch.object(billing, "_get_stripe", return_value=fake_stripe):
            url = billing.create_checkout_session(self.user.id, "pro", "bill@example.com")
        self.assertTrue(url.startswith("https://checkout.stripe.test"))
        kwargs = fake_stripe.checkout.Session.create.call_args.kwargs
        self.assertEqual(kwargs["metadata"]["user_id"], str(self.user.id))
        self.assertEqual(kwargs["line_items"][0]["price"], "price_pro_1")

    def test_checkout_unconfigured_raises(self):
        from services import billing
        os.environ.pop("STRIPE_SECRET_KEY", None)
        with self.assertRaises(billing.BillingError):
            billing.create_checkout_session(self.user.id, "pro")


class ApiGateTests(unittest.TestCase):
    def setUp(self):
        self.user = _fresh()
        from services.api_tokens import issue
        self.token = issue(self.user.id, "t", ttl_days=30).raw_token

    def _client(self):
        from fastapi.testclient import TestClient

        from api.main import create_app
        return TestClient(create_app())

    def _auth(self):
        return {"Authorization": f"Bearer {self.token}"}

    def test_free_tier_api_blocked_402(self):
        with mock.patch.dict(os.environ, _BILLING_ENV):
            r = self._client().get("/v1/me", headers=self._auth())
        self.assertEqual(r.status_code, 402)

    def test_power_tier_api_allowed(self):
        _set_tier(self.user.id, "power")
        with mock.patch.dict(os.environ, _BILLING_ENV):
            r = self._client().get("/v1/me", headers=self._auth())
        self.assertEqual(r.status_code, 200)

    def test_billing_disabled_api_open(self):
        os.environ.pop("STRIPE_SECRET_KEY", None)
        r = self._client().get("/v1/me", headers=self._auth())
        self.assertEqual(r.status_code, 200)

    def test_plan_endpoint(self):
        _set_tier(self.user.id, "power")
        with mock.patch.dict(os.environ, _BILLING_ENV):
            r = self._client().get("/v1/billing/plan", headers=self._auth())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["tier"], "power")

    def test_webhook_503_when_unconfigured(self):
        os.environ.pop("STRIPE_SECRET_KEY", None)
        r = self._client().post("/v1/billing/webhook", content=b"{}")
        self.assertEqual(r.status_code, 503)

    def test_webhook_bad_signature_400(self):
        import services.billing as billing
        with mock.patch.dict(os.environ, _BILLING_ENV), \
             mock.patch.object(billing, "verify_webhook",
                               side_effect=billing.BillingError("bad sig")), \
             mock.patch("api.billing.verify_webhook",
                        side_effect=billing.BillingError("bad sig")):
            r = self._client().post(
                "/v1/billing/webhook", content=b"{}",
                headers={"stripe-signature": "t=1,v1=bad"},
            )
        self.assertEqual(r.status_code, 400)


class TailoringQuotaIntegrationTests(unittest.TestCase):
    def test_generation_blocked_at_artifact_limit(self):
        user = _fresh()
        from services.applications import save_analysis
        from services.billing import TierLimitExceeded
        from services.master_cv import save_master_cv
        from services.tailoring import generate_tailored_cv
        save_master_cv(user.id, "Skills: Python.")
        app = save_analysis(user.id, {"company_name": "A", "job_title": "T"}, {})
        with mock.patch.dict(os.environ, {
            **_BILLING_ENV,
            "TIER_LIMITS_JSON": '{"free": {"artifacts_per_window": 0}}',
        }):
            with self.assertRaises(TierLimitExceeded):
                generate_tailored_cv(user.id, app.id, model="fast")


if __name__ == "__main__":
    unittest.main()
