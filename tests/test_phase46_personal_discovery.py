"""Personal mode: search profile, job discovery, web research, personalised report."""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh():
    from db.session import reset_engine_for_testing
    from services.auth import register_user
    from services.rate_limit import reset_backend_for_testing
    from utils.cache import cache

    reset_engine_for_testing("sqlite:///:memory:")
    reset_backend_for_testing()
    cache.clear()
    return register_user("me@example.com", "Sup3rSecret!")


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def _routes(table):
    """requests.get stand-in: first URL substring match wins."""
    def get(url, params=None, **_kw):
        for fragment, payload in table:
            if fragment in url:
                return payload if isinstance(payload, _Resp) else _Resp(payload)
        return _Resp({}, status=404)
    return get


# Shapes trimmed from real API responses (2026-10).
GREENHOUSE = {"jobs": [{
    "id": 1, "title": "Senior Backend Engineer", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
    "location": {"name": "Remote, Germany"}, "company_name": "Acme",
    "first_published": "2026-10-02T11:31:50-04:00",
    "content": "&lt;p&gt;Build &lt;b&gt;Python&lt;/b&gt; services on Kubernetes.&lt;/p&gt;",
    "departments": [{"name": "Engineering"}],
}]}
LEVER = [{
    "id": "abc", "text": "Platform Engineer", "hostedUrl": "https://jobs.lever.co/acme/abc",
    "categories": {"location": "Berlin", "team": "Infra", "commitment": "Permanent"},
    "workplaceType": "hybrid", "createdAt": 1791566108174,
    "descriptionPlain": "Run our platform.", "lists": [{"text": "You have", "content": "<li>Go</li>"}],
    "additionalPlain": "", "salaryRange": {"min": 80000, "max": 100000, "currency": "EUR", "interval": "per-year-salary"},
}]
ASHBY = {"jobs": [{
    "id": "x1", "title": "Staff Backend Engineer", "location": "Europe", "secondaryLocations": [],
    "isRemote": True, "isListed": True, "publishedAt": "2021-04-27T20:13:45.158+00:00",
    "jobUrl": "https://jobs.ashbyhq.com/acme/x1", "descriptionPlain": "Evergreen role.",
    "compensation": {"compensationTierSummary": "€90K – €120K"},
}]}
REMOTEOK = [
    {"legal": "API Terms of Service: link back..."},
    {"id": "9", "position": "Backend Engineer", "company": "Sothebyâ\u0080\u0099s", "location": "",
     "epoch": 1792350002, "url": "https://remoteOK.com/remote-jobs/9", "description": "<p>Python</p>",
     "salary_min": 0, "salary_max": 0, "tags": ["python"]},
]
HN_STORIES = {"hits": [
    {"title": "Ask HN: Who wants to be hired? (October 2026)", "objectID": "2"},
    {"title": "Ask HN: Who is hiring? (October 2026)", "objectID": "1"},
]}
HN_ITEM = {"children": [
    {"id": 11, "created_at": "2026-10-01T15:02:37Z",
     "text": "PAGNOS | Cloud Engineer, AWS Serverless | REMOTE (Germany)<p>We build things."},
    {"id": 12, "created_at": "2026-10-01T15:03:00Z", "text": None},
]}


class JobSourceParsingTests(unittest.TestCase):
    def setUp(self):
        _fresh()

    def test_greenhouse_unescapes_html_content(self):
        from tools import job_sources as js
        with mock.patch.object(js.requests, "get", side_effect=_routes([("greenhouse.io", GREENHOUSE)])):
            [lead] = js.greenhouse("acme")
        self.assertEqual(lead.company, "Acme")
        self.assertTrue(lead.remote)
        self.assertIn("Build Python services", lead.description)
        self.assertNotIn("&lt;", lead.description)

    def test_lever_falls_back_to_eu_host_and_reads_salary(self):
        from tools import job_sources as js
        routes = [("api.eu.lever.co", LEVER), ("api.lever.co", _Resp({}, status=404))]
        with mock.patch.object(js.requests, "get", side_effect=_routes(routes)):
            [lead] = js.lever("acme", "Acme")
        self.assertEqual(lead.title, "Platform Engineer")
        self.assertIn("EUR 80000-100000", lead.salary)
        self.assertIn("Go", lead.description)

    def test_ashby_compensation_and_remote(self):
        from tools import job_sources as js
        with mock.patch.object(js.requests, "get", side_effect=_routes([("ashbyhq", ASHBY)])):
            [lead] = js.ashby("acme", "Acme")
        self.assertTrue(lead.remote)
        self.assertEqual(lead.salary, "€90K – €120K")

    def test_remoteok_skips_legal_notice_and_fixes_mojibake(self):
        from tools import job_sources as js
        with mock.patch.object(js.requests, "get", side_effect=_routes([("remoteok", REMOTEOK)])):
            leads = js.remoteok()
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].company, "Sotheby’s")

    def test_hn_picks_hiring_thread_and_parses_header(self):
        from tools import job_sources as js
        routes = [("search_by_date", HN_STORIES), ("items/1", HN_ITEM)]
        with mock.patch.object(js.requests, "get", side_effect=_routes(routes)):
            [lead] = js.hn_whos_hiring()
        self.assertEqual(lead.company, "PAGNOS")
        self.assertIn("Cloud Engineer", lead.title)
        self.assertIn("REMOTE (Germany)", lead.location)
        self.assertTrue(lead.remote)

    def test_board_slug_is_validated(self):
        from tools import job_sources as js
        with self.assertRaises(js.SourceError):
            js.greenhouse("../../admin")

    def test_responses_are_cached(self):
        from tools import job_sources as js
        get = mock.Mock(side_effect=_routes([("greenhouse.io", GREENHOUSE)]))
        with mock.patch.object(js.requests, "get", get):
            js.greenhouse("acme")
            js.greenhouse("acme")
        self.assertEqual(get.call_count, 1)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.user = _fresh()

    def test_roundtrip_normalizes(self):
        from services.profile import CandidateProfile, get_profile, save_profile
        save_profile(self.user.id, CandidateProfile(
            target_titles=["Backend Engineer", " backend engineer ", ""],
            work_modes=["remote", "teleport"], salary_currency="eur",
        ))
        p = get_profile(self.user.id)
        self.assertEqual(p.target_titles, ["Backend Engineer"])
        self.assertEqual(p.work_modes, ["remote"])
        self.assertEqual(p.salary_currency, "EUR")

    def test_watchlist_requires_known_ats(self):
        from services.profile import CandidateProfile, ProfileError, save_profile
        with self.assertRaises(ProfileError):
            save_profile(self.user.id, CandidateProfile(
                target_titles=["x"], watchlist=[{"company": "A", "ats": "workday", "slug": "a"}]))

    def test_prompt_text_includes_floor_and_dealbreakers(self):
        from services.profile import CandidateProfile, profile_as_prompt
        text = profile_as_prompt(CandidateProfile(
            target_titles=["SRE"], salary_floor=80000, dealbreakers=["crypto"]))
        self.assertIn("80,000 EUR/year", text)
        self.assertIn("Dealbreakers: crypto", text)
        self.assertEqual(profile_as_prompt(CandidateProfile()), "")


def _profile(**kw):
    from services.profile import CandidateProfile
    base = dict(target_titles=["Senior Backend Engineer"], seniority="Senior",
                locations=["Berlin", "Germany"], work_modes=["remote", "hybrid"],
                remote_regions="EU", salary_floor=80000, salary_currency="EUR",
                search_keywords=["Python", "Kubernetes"])
    base.update(kw)
    return CandidateProfile(**base)


def _lead(**kw):
    from tools.job_sources import JobLead
    base = dict(source="remotive", external_id="1", url="https://x.test/1",
                title="Backend Engineer", company="Acme", location="Remote", remote=True,
                posted_at=datetime.utcnow(), description="Python and Kubernetes")
    base.update(kw)
    return JobLead(**base)


class RankingTests(unittest.TestCase):
    def test_title_match_ignores_seniority_and_synonyms(self):
        from services.discovery import title_match
        self.assertEqual(title_match("Sr. Back-end Developer", ["Senior Backend Engineer"]), 1.0)
        self.assertEqual(title_match("Engineering Manager", ["Backend Engineer"]), 0.0)
        self.assertEqual(title_match("ML Engineer", ["Machine Learning Engineer"]), 1.0)

    def test_flags_us_only_remote_for_eu_candidate(self):
        from services.discovery import score_lead
        score_us, flags = score_lead(_lead(location="Remote (US only)"), _profile())
        score_eu, _ = score_lead(_lead(location="Remote, Europe"), _profile())
        self.assertIn("Remote, but restricted to the Americas", flags)
        self.assertLess(score_us, score_eu)

    def test_salary_below_floor_and_dealbreaker_flags(self):
        from services.discovery import score_lead
        _, flags = score_lead(_lead(salary="EUR 50000-60000"), _profile())
        self.assertTrue(any("below your floor" in f for f in flags))
        _, flags = score_lead(_lead(company="CryptoCorp", tags=["crypto"]), _profile(dealbreakers=["crypto"]))
        self.assertTrue(any("dealbreaker" in f for f in flags))

    def test_salary_parsing(self):
        from services.discovery import parse_salary_max
        self.assertEqual(parse_salary_max("€90K – €120K"), (120000.0, "EUR"))
        self.assertEqual(parse_salary_max("$1000 per task"), (None, None))
        self.assertEqual(parse_salary_max("EUR 85,000 - 105,000"), (105000.0, "EUR"))

    def test_prefilter_age_applies_to_aggregators_not_watchlist(self):
        from services.discovery import prefilter
        old = _lead(posted_at=datetime.utcnow() - timedelta(days=400))
        self.assertFalse(prefilter("remotive:backend", old, _profile()))
        self.assertTrue(prefilter("ashby:Acme", old, _profile()))
        self.assertFalse(prefilter("remotive:x", _lead(title="Sales Manager"), _profile()))


class DiscoveryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.user = _fresh()
        from services.profile import save_profile
        save_profile(self.user.id, _profile())

    def _refresh(self, leads, **kw):
        from services import discovery
        report = discovery.RefreshReport(fetched=len(leads))
        with mock.patch.object(discovery, "gather", return_value=([("remotive:x", l) for l in leads], report)):
            return discovery.refresh_feed(self.user.id, triage_limit=0, **kw)

    def test_dedupe_and_dismissed_stays_dismissed(self):
        from services import discovery
        a = _lead(url="https://x.test/1?utm=1")
        b = _lead(url="https://x.test/1", source="remoteok")
        report = self._refresh([a, b])
        self.assertEqual(report.new, 1)
        [job] = discovery.list_feed(self.user.id)
        discovery.set_status(self.user.id, job.id, "dismissed")
        report = self._refresh([a])
        self.assertEqual((report.new, report.seen_again), (0, 1))
        self.assertEqual(discovery.list_feed(self.user.id), [])
        self.assertEqual(discovery.feed_counts(self.user.id)["dismissed"], 1)

    def test_triage_scores_and_orders_feed(self):
        from services import discovery
        self._refresh([_lead(url="https://x.test/1"), _lead(url="https://x.test/2", title="Backend Engineer II")])
        jobs = discovery.list_feed(self.user.id)
        payload = json.dumps([
            {"id": jobs[0].id, "fit": 40, "summary": "meh", "flags": ["German C1 required"]},
            {"id": jobs[1].id, "fit": 90, "summary": "strong", "flags": []},
            {"id": 99999, "fit": 100, "summary": "not in batch"},
        ])
        with mock.patch("utils.llm.is_demo_mode", return_value=False), \
             mock.patch.object(discovery, "get_completion", return_value=payload):
            self.assertEqual(discovery.triage(self.user.id), 2)
        ordered = discovery.list_feed(self.user.id)
        self.assertEqual([j.fit_score for j in ordered], [90, 40])
        self.assertIn("German C1 required", ordered[1].fit_flags)

    def test_track_creates_application(self):
        from services import discovery
        from services.applications import get_application
        self._refresh([_lead(description="x" * 500)])
        [job] = discovery.list_feed(self.user.id)
        app_id = discovery.track(self.user.id, job.id)
        app = get_application(self.user.id, app_id)
        self.assertEqual((app.company_name, app.job_title), ("Acme", "Backend Engineer"))
        self.assertEqual(discovery.get_job(self.user.id, job.id).status, "saved")

    def test_refresh_requires_profile(self):
        from services import discovery
        from services.auth import register_user
        other = register_user("other@example.com", "Sup3rSecret!")
        with self.assertRaises(discovery.DiscoveryError):
            discovery.refresh_feed(other.id)


def _block(kind, **kw):
    return SimpleNamespace(type=kind, **kw)


class WebResearchTests(unittest.TestCase):
    def setUp(self):
        _fresh()
        self.env = mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _client(self, responses):
        client = mock.Mock()
        client.beta.messages.create.side_effect = responses
        return client

    def test_pause_turn_continues_and_collects_sources(self):
        from utils import llm
        usage = SimpleNamespace(input_tokens=100, output_tokens=50,
                                server_tool_use=SimpleNamespace(web_search_requests=2))
        paused = SimpleNamespace(stop_reason="pause_turn", usage=usage, content=[
            _block("text", text="Part one.", citations=None),
            _block("web_search_tool_result", content=[SimpleNamespace(url="https://a.test", title="A")]),
        ])
        done = SimpleNamespace(stop_reason="end_turn", usage=usage, content=[
            _block("text", text="Part two.", citations=[SimpleNamespace(url="https://a.test", title="A"),
                                                      SimpleNamespace(url="https://b.test", title="B")]),
        ])
        client = self._client([paused, done])
        recorded = []
        with mock.patch("anthropic.Anthropic", return_value=client), \
             mock.patch.object(llm, "_record_usage", side_effect=lambda *a: recorded.append(a)):
            text, sources = llm.web_research("Research Acme")
        self.assertEqual(text, "Part one.\nPart two.")
        self.assertEqual([s["url"] for s in sources], ["https://a.test", "https://b.test"])
        second_call = client.beta.messages.create.call_args_list[1].kwargs
        self.assertEqual(second_call["messages"][1]["role"], "assistant")
        self.assertEqual(len(recorded), 2)
        self.assertAlmostEqual(recorded[0][2]["extra_cost_usd"], 0.02)
        first = client.beta.messages.create.call_args_list[0].kwargs
        self.assertEqual(first["extra_body"], {"fallbacks": "default"})
        self.assertEqual(first["tools"][0]["type"], "web_search_20260209")

    def test_rejected_fallback_beta_degrades_to_plain_request(self):
        from utils import llm

        class _BadRequest(Exception):
            status_code = 400

        client = mock.Mock()
        client.beta.messages.create.side_effect = _BadRequest("fallbacks: unknown beta")
        client.messages.create.return_value = SimpleNamespace(
            stop_reason="end_turn", usage=None, content=[_block("text", text="ok", citations=None)])
        with mock.patch.object(llm, "_fallbacks_rejected", False), \
             mock.patch("anthropic.Anthropic", return_value=client):
            text, _ = llm.web_research("x")
            self.assertTrue(llm._fallbacks_rejected)
        self.assertEqual(text, "ok")

    def test_unavailable_without_anthropic_key(self):
        from utils import llm
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaises(llm.WebResearchUnavailable):
                llm.web_research("x")


class PersonalisedReportTests(unittest.TestCase):
    def test_report_prompt_carries_profile_and_fit_section(self):
        from agents import report_generator
        captured = {}

        def grab(prompt, model=None):
            captured["prompt"] = prompt
            return "# Report\n\nok"

        state = {"job_details": {"extracted_details": {"company_name": "Acme"}},
                 "profile_text": "- Minimum acceptable base salary: 80,000 EUR/year"}
        with mock.patch.object(report_generator, "get_completion", side_effect=grab):
            report_generator.generate(state)
        self.assertIn("80,000 EUR/year", captured["prompt"])
        self.assertIn("Fit for You", captured["prompt"])
        self.assertIn("caps it at `Consider with Caution`", captured["prompt"])

        with mock.patch.object(report_generator, "get_completion", side_effect=grab):
            report_generator.generate({"job_details": {}})
        self.assertNotIn("Fit for You", captured["prompt"])


try:
    from streamlit.testing.v1 import AppTest
    HAS_APPTEST = True
except Exception:  # noqa: BLE001
    HAS_APPTEST = False


@unittest.skipUnless(HAS_APPTEST, "streamlit AppTest harness unavailable")
class PersonalModeAppTests(unittest.TestCase):
    def setUp(self):
        _fresh()
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FEATHERLESS_API_KEY", "LLM_PROVIDER"):
            os.environ.pop(k, None)

    def test_renders_without_login_and_discover_first(self):
        app_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app.py"))
        with mock.patch.dict(os.environ, {"PERSONAL_MODE": "1"}):
            at = AppTest.from_file(app_path, default_timeout=60)
            at.run()
        self.assertFalse(at.exception, msg=[str(e.value) for e in at.exception])
        self.assertEqual(at.tabs[0].label, "🔭 Discover")
        labels = {b.label for b in at.button}
        self.assertNotIn("Sign out", labels)
        self.assertIn("✨ Draft from my master CV", labels)
        self.assertFalse(any("Sign in" in (b.label or "") for b in at.button))


if __name__ == "__main__":
    unittest.main()


class UnmigratedDatabaseTests(unittest.TestCase):
    """The live deploy has lagged migrations before: a database without the
    new tables must not break analyses or halt the page."""

    def setUp(self):
        _fresh()
        from sqlalchemy import text

        from db.session import get_engine
        with get_engine().begin() as conn:
            conn.execute(text("DROP TABLE candidate_profiles"))
            conn.execute(text("DROP TABLE discovered_jobs"))

    def test_profile_prompt_is_best_effort(self):
        from services.profile import profile_prompt_for
        self.assertEqual(profile_prompt_for(1), "")

    @unittest.skipUnless(HAS_APPTEST, "streamlit AppTest harness unavailable")
    def test_app_still_renders_with_migration_hint(self):
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FEATHERLESS_API_KEY", "LLM_PROVIDER"):
            os.environ.pop(k, None)
        app_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app.py"))
        with mock.patch.dict(os.environ, {"PERSONAL_MODE": "1"}):
            at = AppTest.from_file(app_path, default_timeout=60)
            at.run()
        self.assertFalse(at.exception, msg=[str(e.value) for e in at.exception])
        self.assertTrue(any("needs a database migration" in e.value for e in at.error))
        # The Analyze tab renders after Discover: its submit button proves the
        # script wasn't halted by the missing tables.
        self.assertIn("🔍 Analyze posting", [b.label for b in at.button])


_ISOLATION_PROBE = r"""
import os, sys
for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FEATHERLESS_API_KEY"):
    os.environ.pop(k, None)
os.environ["PERSONAL_MODE"] = "1"
from db.session import reset_engine_for_testing
reset_engine_for_testing("sqlite:///:memory:")
from services.personal import ensure_local_owner
ensure_local_owner()
sys.modules.pop("services.auth", None)  # owner creation happens once, on first start
from streamlit.testing.v1 import AppTest
at = AppTest.from_file(sys.argv[1], default_timeout=60)
at.run()
assert not at.exception, [e.value for e in at.exception]
side = [m for m in sys.modules if m.split(".")[0] in ("celery", "stripe", "telegram", "pyotp", "qrcode")
        or m in ("services.auth", "services.webhooks", "services.telegram_link", "services.totp",
                 "services.oauth", "services.background_analysis", "services.sharing",
                 "services.notifications", "services.api_tokens", "services.email_verify",
                 "worker.tasks", "worker.celery_app")]
print("SIDE=" + ",".join(sorted(side)))
"""


@unittest.skipUnless(HAS_APPTEST, "streamlit AppTest harness unavailable")
class PersonalModeIsolationTests(unittest.TestCase):
    def test_side_features_are_not_imported(self):
        """Fresh process: personal mode must not import accounts, 2FA, OAuth,
        webhooks, Telegram, sharing, notifications, background jobs, billing SDKs."""
        import subprocess

        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        out = subprocess.run(
            [sys.executable, "-c", _ISOLATION_PROBE, os.path.join(root, "app.py")],
            cwd=root, capture_output=True, text=True, timeout=180,
        )
        line = next((l for l in out.stdout.splitlines() if l.startswith("SIDE=")), None)
        self.assertIsNotNone(line, msg=out.stderr[-2000:])
        self.assertEqual(line, "SIDE=", msg="side-feature modules loaded in personal mode")


@unittest.skipUnless(HAS_APPTEST, "streamlit AppTest harness unavailable")
class PersonalJourneyTests(unittest.TestCase):
    """Personal mode, end to end: feed → track → stages → tailored CV → PDF."""

    def setUp(self):
        _fresh()
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FEATHERLESS_API_KEY", "LLM_PROVIDER"):
            os.environ.pop(k, None)
        self.env = mock.patch.dict(os.environ, {"PERSONAL_MODE": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        from services.master_cv import save_master_cv
        from services.personal import ensure_local_owner
        from services.profile import save_profile
        self.uid, _ = ensure_local_owner()
        save_master_cv(self.uid, "Alex Kim\nSoftware Engineer at Initech (2019-2024)\n"
                                 "Skills: Python, Django, PostgreSQL\n- Built billing APIs")
        save_profile(self.uid, _profile())

    def _app(self):
        app_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app.py"))
        at = AppTest.from_file(app_path, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, msg=[str(e.value) for e in at.exception])
        return at

    def _button(self, at, key):
        return next((b for b in at.button if getattr(b, "key", None) == key), None)

    def test_track_stage_and_tailor_flow(self):
        from services import discovery
        from services.applications import get_application
        from services.stages import list_stages
        from services.tailoring import list_artifacts

        report = discovery.RefreshReport()
        lead = _lead(company="Globex", title="Backend Engineer", description="Python and Django. " * 30)
        with mock.patch.object(discovery, "gather", return_value=([("remotive:x", lead)], report)):
            discovery.refresh_feed(self.uid, triage_limit=0)

        at = self._app()
        [job] = discovery.list_feed(self.uid)
        self._button(at, f"disc_tr_{job.id}").click().run()       # 📌 Track
        self.assertFalse(at.exception, msg=[str(e.value) for e in at.exception])
        app_id = discovery.get_job(self.uid, job.id).application_id
        self.assertIsNotNone(app_id)

        self._button(at, f"qa_{app_id}_applied").click().run()      # stage quick-actions
        self._button(at, f"qa_{app_id}_technical_interview").click().run()
        self.assertFalse(at.exception, msg=[str(e.value) for e in at.exception])
        self.assertEqual([s.kind for s in list_stages(self.uid, app_id)], ["applied", "technical_interview"])
        self.assertEqual(get_application(self.uid, app_id).status, "interviewing")

        # Tailored CV: the generated text claims a skill the CV lacks; the check must flag it.
        with mock.patch("services.tailoring.get_completion",
                        return_value="# Alex Kim\n## Skills\nPython, Django, Kubernetes"):
            self._button(at, f"gen_cv_{app_id}").click().run()
        self.assertFalse(at.exception, msg=[str(e.value) for e in at.exception])
        [art] = list_artifacts(self.uid, app_id)
        self.assertIn("kubernetes", art.meta["constraint_check"]["new_proper_nouns"])
        at.checkbox(key=f"art_expand_{art.id}").check().run()       # open the artifact
        self.assertFalse(at.exception, msg=[str(e.value) for e in at.exception])
        self.assertTrue(any("Review recommended" in w.value and "kubernetes" in w.value
                            for w in at.warning))
        # Markdown + PDF downloads rendered for this artifact (PDF built OK).
        ids = " ".join(str(d.proto.id) for d in at.get("download_button"))
        self.assertIn(f"art_dl_pdf_{art.id}", ids)
        self.assertIn(f"art_dl_md_{art.id}", ids)

        with mock.patch("services.tailoring.get_completion",
                        return_value="Dear Hiring Manager,\n\nI built billing APIs in Python at Initech."):
            self._button(at, f"gen_cl_{app_id}").click().run()
        self.assertFalse(at.exception, msg=[str(e.value) for e in at.exception])
        kinds = sorted(a.kind for a in list_artifacts(self.uid, app_id))
        self.assertEqual(kinds, ["cover_letter", "tailored_cv"])
