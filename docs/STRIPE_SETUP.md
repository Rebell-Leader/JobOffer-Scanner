# Payments setup — Stripe (and easier alternatives)

How to go from zero to charging money, including the exact dry run to verify
the integration in test mode before touching a live key.

---

## 0. First decision: Stripe vs. a Merchant of Record

Two models exist, and for a micro-SaaS this choice matters more than fees:

| | **Stripe** (payment processor) | **Lemon Squeezy / Paddle** (Merchant of Record) |
|---|---|---|
| Fees | 2.9% + $0.30 per transaction | ~5% + $0.50 per transaction |
| Sales tax / VAT | **You** register, collect, file — in every jurisdiction where you cross thresholds | **They** are the legal seller; they collect & remit US sales tax, EU/UK VAT, AU GST for you |
| Identity/KYC | Full KYC on you (below) | Lighter onboarding; they carry the merchant burden |
| Integration | This repo is already built for it (`services/billing`) | Different webhook/product model — adapter needed |
| Best when | US-only customers at first, want lowest fees + most control | Selling globally from day one as a solo founder |

**Practical recommendation for this project:** start with **Stripe** — the
integration is already built and tested, and during validation (first ~50
customers, likely mostly one country) the tax burden is minimal: most US
states have economic-nexus thresholds around $100k/year, and if you're
EU-based you handle your one home-country VAT registration. **Revisit at
~$1–2k MRR with international customers** — at that point an MoR's 2% premium
is cheaper than your time filing VAT-OSS returns. The adapter work to switch
later is contained (one service + one webhook route).

Sources: [fee comparison 2026](https://www.globalsolo.global/blog/stripe-vs-paddle-vs-lemon-squeezy-2026),
[micro-SaaS platform comparison](https://f3fundit.com/stripe-vs-paddle-vs-lemon-squeezy-micro-saas-2026/),
[MoR comparison](https://www.buildmvpfast.com/blog/lemon-squeezy-vs-polar-paddle-merchant-of-record-2026).

---

## 1. Registering a Stripe account

1. **Sign up** at dashboard.stripe.com/register (email + password). You get a
   **test-mode** account immediately — you can build and dry-run everything
   below before any verification.
2. **Activate payments** ("Activate your account" in the dashboard) — this is
   where KYC happens. You'll be asked for:
   - **Business type.** As a solo founder choose *Individual / Sole
     proprietor* — you do NOT need an LLC or company to start. (Forming an
     LLC later is a supported account update.)
   - **Legal name, DOB, home address, phone.**
   - **Tax ID.** US: your **SSN** (or ITIN) — an EIN is optional for sole
     proprietors; if you have one you can use it instead. Other countries:
     the local equivalent (Stripe's form adapts per country).
   - **Bank account** for payouts (a personal account is fine for a sole
     proprietor).
   - **Business website + description.** Use the landing page URL; describe
     it plainly ("subscription software that analyzes job postings").
     A live, real-looking landing page noticeably smooths verification.
3. **Identity verification.** Stripe verifies the details against official
   records; sometimes it asks for a photo ID document upload. This usually
   clears in minutes-to-days. Until verified you can accept a limited volume;
   payouts are held until verification completes.
4. **Enable Stripe Tax** (Settings → Tax) even before you owe anything — it
   monitors your sales against each jurisdiction's registration thresholds
   and warns you when you approach one.

Sources: [US account requirements](https://support.stripe.com/questions/requirements-for-having-a-us-stripe-account),
[sole proprietor without EIN](https://support.stripe.com/questions/signing-up-for-stripe-as-a-sole-proprietor-without-employer-id-number),
[required verification information](https://docs.stripe.com/connect/required-verification-information).

---

## 2. Dashboard configuration (test mode first)

Everything below is done twice: once with the **test-mode** toggle on, then
repeated in live mode after the dry run passes.

### 2.1 Products & prices
1. **Products → Add product**: "JobOffer Scanner Pro", recurring, **$12 /
   month**. Copy the price id (`price_…`).
2. Repeat: "JobOffer Scanner Power", recurring, **$24 / month**.
3. Optional but recommended: add **yearly** prices ($120 / $240 — 2 months
   free) on the same products; they fight hire-and-churn.

### 2.2 Webhook endpoint
1. **Developers → Webhooks → Add endpoint.**
2. URL: `https://<your-api-host>/v1/billing/webhook`
3. Events — exactly the three the app mirrors:
   - `checkout.session.completed`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
4. Copy the **signing secret** (`whsec_…`).

### 2.3 Customer portal
**Settings → Billing → Customer portal**: enable it, allow plan
switching between Pro and Power and cancellation. The app's "Manage billing"
button opens this portal — no UI to build.

### 2.4 Environment
```bash
STRIPE_SECRET_KEY=sk_test_...        # Developers -> API keys (test mode!)
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_PRO=price_...           # the $12 recurring price
STRIPE_PRICE_POWER=price_...         # the $24 recurring price
APP_BASE_URL=https://yourdomain.com  # checkout success/cancel redirects
```
Install the SDK: `pip install '.[billing]'`. The moment `STRIPE_SECRET_KEY`
is set, tier enforcement turns ON (users without a subscription drop to the
Free tier) — so set it on staging first, not prod.

---

## 3. Test-mode dry run (do all of these before going live)

Use Stripe's test card `4242 4242 4242 4242` (any future expiry, any CVC).
For local webhook testing, the Stripe CLI forwards events:
`stripe listen --forward-to localhost:8000/v1/billing/webhook` (it prints a
temporary `whsec_…` — use that as `STRIPE_WEBHOOK_SECRET` during the run).

| # | Step | Expected |
|---|------|----------|
| 1 | Register a fresh user in the app | Sidebar "Plan & usage" shows **Free plan**, 0/5 analyses |
| 2 | Run 5 analyses | All succeed on the fast model; meter shows 5/5 |
| 3 | Run a 6th | Blocked with the upgrade message (no tokens spent) |
| 4 | Select "Detailed" model on any run | Notice: "Detailed analysis is a paid-plan feature — running the fast model" |
| 5 | Sidebar → **Upgrade to Pro** → complete checkout with `4242…` | Redirect back with `?billing=success`; webhook fires |
| 6 | Reload sidebar | **Pro plan**, 5/50 analyses; detailed model now allowed |
| 7 | Issue an API token, call `GET /v1/me` | **402** (API is Power-only) |
| 8 | In the Stripe dashboard, switch the subscription to the Power price | Webhook `customer.subscription.updated` → sidebar shows **Power**; the API call now returns 200 |
| 9 | `GET /v1/billing/plan` with the token | JSON with tier `power` + usage numbers |
| 10 | Customer portal → **cancel** the subscription | Webhook `…deleted` → sidebar back to **Free** |
| 11 | Send the webhook a garbage body / wrong signature | **400**; nothing changes locally |
| 12 | Check `audit_events` | `billing.checkout.started`, `billing.subscription.created/updated/canceled` rows present |

When all 12 pass: repeat §2 in live mode (live keys, live prices, live
webhook endpoint), set the live env vars, and run step 5 once with a real
card for $12, then refund yourself from the dashboard.

---

## 4. Go-live checklist

- [ ] Account activated + identity verified (payouts enabled)
- [ ] Live products/prices created; env vars switched to `sk_live_…`
- [ ] Live webhook endpoint added (the test one stays for staging)
- [ ] Statement descriptor set (Settings → Public details) — what appears on
      card statements; unclear descriptors cause disputes
- [ ] Customer emails enabled (Settings → Emails): receipts + failed payments
- [ ] **Smart Retries / dunning** on (Settings → Billing → Revenue recovery)
- [ ] Terms + Privacy published and linked from checkout (landing `/terms`,
      `/privacy`)
- [ ] Stripe Tax monitoring enabled
- [ ] Test a real $12 charge + refund end-to-end
