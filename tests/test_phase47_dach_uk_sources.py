"""DACH / UK job sources: Personio, Teamtailor, Recruitee, Workable, Pinpoint,
Softgarden, the DevJobs RSS network, and board detection."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class _Resp:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
        self.content = body.encode() if isinstance(body, str) else b""

    def json(self):
        return self._body


def _routes(table):
    def get(url, params=None, **_kw):
        for fragment, body in table:
            if fragment in url:
                return body if isinstance(body, _Resp) else _Resp(body)
        return _Resp({}, status=404)
    return get


def setUpModule():
    from utils.cache import cache
    cache.clear()


# Trimmed from real responses (2026-10).
PERSONIO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs><position>
  <id>2821024</id><subcompany>OroraTech GmbH</subcompany><office>Munich</office>
  <additionalOffices><office>Remote</office></additionalOffices>
  <department>Engineering</department><name>Senior Platform Engineer (m/w/d)</name>
  <jobDescriptions><jobDescription><name>The Mission</name>
    <value><![CDATA[Run our <b>Kubernetes</b> platform.<br>Python and Go.]]></value>
  </jobDescription></jobDescriptions>
  <seniority>experienced</seniority><schedule>full-time</schedule>
  <createdAt>2026-09-30T08:00:00+00:00</createdAt>
</position></workzag-jobs>"""

TEAMTAILOR_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="https://teamtailor.com/locations"><channel>
<title>IQM</title><link>https://iqm.teamtailor.com/jobs</link>
<item><title>Quantum Software Engineer</title>
<description>&lt;p&gt;Build the &lt;strong&gt;compiler&lt;/strong&gt;.&lt;/p&gt;</description>
<pubDate>Fri, 24 Jul 2026 09:05:16 +0200</pubDate>
<link>https://iqm.teamtailor.com/jobs/1-quantum-software-engineer</link>
<remoteStatus>hybrid</remoteStatus><guid>abc-1</guid>
<tt:locations><tt:location><tt:city>Munich</tt:city><tt:country>Germany</tt:country></tt:location></tt:locations>
<tt:department>Software</tt:department><tt:role>Engineer</tt:role>
</item></channel></rss>"""

RECRUITEE = {"offers": [{
    "id": 7, "title": "ML Engineer", "careers_url": "https://careers.acme.com/o/ml-engineer",
    "company_name": "Acme", "location": "Amsterdam, Netherlands", "remote": False, "hybrid": True,
    "published_at": "2026-10-15 09:31:36 UTC", "description": "<p>Train models</p>",
    "requirements": "<ul><li>PyTorch</li></ul>",
    "salary": {"min": 80000, "max": 100000, "currency": "EUR", "period": "year"},
    "department": "AI",
}]}

WORKABLE = {"name": "Hugging Face", "jobs": [{
    "title": "Open-Source ML Engineer - EMEA Remote", "shortcode": "81B4",
    "url": "https://apply.workable.com/j/81B4", "telecommuting": True,
    "published_on": "2026-05-29", "locations": [{"city": "Paris", "country": "France"}],
    "description": "<p>Transformers</p>", "department": "Open Source", "function": "Engineering",
}]}

PINPOINT = {"data": [{
    "id": "559663", "title": "Backend Engineer", "url": "https://acme.pinpointhq.com/en/postings/x",
    "description": "<p>APIs in Go</p>", "key_responsibilities_header": "What you'll do",
    "key_responsibilities": "<ul><li>Ship</li></ul>", "workplace_type": "remote",
    "employment_type_text": "Full Time", "location": {"city": "London", "name": "Remote"},
    "job": {"department": {"name": "Engineering"}}, "compensation_visible": True,
    "compensation_minimum": 70000, "compensation_maximum": 90000,
    "compensation_currency": "GBP", "compensation_frequency": "year",
}]}

SOFTGARDEN = {"@type": "DataFeed", "dataFeedElement": [{"item": {
    "@type": "JobPosting", "title": "Embedded Engineer (m/w/d)",
    "url": "https://acme.career.softgarden.de/jobs/1/Embedded-Engineer/",
    "datePosted": "2026-09-23T10:37:06.121+02:00",
    "identifier": {"name": "Acme GmbH", "value": 1},
    "description": "<p>C++ on microcontrollers</p>",
    "hiringOrganization": {"name": "Acme GmbH"},
    "jobLocation": {"address": {"addressLocality": "Stuttgart", "addressCountry": "DE"}},
}}]}

DEVJOBS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<item><title><![CDATA[Full-Stack Entwickler/in @ IWF Web Solutions AG [CHF 85'000 - 115'000]]]></title>
<link>https://swissdevjobs.ch/jobs/IWF-Full-Stack?utm_source=our_rss_feed</link>
<guid isPermaLink="false">g1</guid><pubDate>Mon, 19 Oct 2026 18:12:18 GMT</pubDate>
<content:encoded><![CDATA[<p>Support users remote und vor Ort.</p>]]></content:encoded></item>
<item><title><![CDATA[Senior Python Engineer @ Remote GmbH [90.000 - 110.000 €]]]></title>
<link>https://germantechjobs.de/jobs/x</link><guid>g2</guid><pubDate>Mon, 19 Oct 2026 18:12:18 GMT</pubDate>
<content:encoded><![CDATA[<p>This is a 100% remote position.</p>]]></content:encoded></item>
</channel></rss>"""


class ConnectorParsingTests(unittest.TestCase):
    def setUp(self):
        from utils.cache import cache
        cache.clear()

    def _get(self, routes):
        from tools import job_sources as js
        return mock.patch.object(js.requests, "get", side_effect=_routes(routes))

    def test_personio_sections_offices_and_url(self):
        from tools import job_sources as js
        with self._get([("jobs.personio.de/xml", _Resp(PERSONIO_XML))]):
            [lead] = js.personio("ororatech")
        self.assertEqual(lead.company, "OroraTech GmbH")
        self.assertEqual(lead.location, "Munich, Remote")
        self.assertTrue(lead.remote)
        self.assertIn("The Mission:\nRun our Kubernetes platform.", lead.description)
        self.assertEqual(lead.url, "https://ororatech.jobs.personio.de/job/2821024?language=en")

    def test_teamtailor_namespace_fields(self):
        from tools import job_sources as js
        with self._get([("iqm.teamtailor.com/jobs.rss", _Resp(TEAMTAILOR_RSS))]):
            [lead] = js.teamtailor("iqm")
        self.assertEqual((lead.company, lead.location), ("IQM", "Munich, Germany"))
        self.assertIn("Build the compiler.", lead.description)
        self.assertEqual(lead.posted_at.isoformat(), "2026-07-24T07:05:16")
        self.assertIn("hybrid", lead.tags)

    def test_teamtailor_custom_host_is_ssrf_checked(self):
        from tools import job_sources as js
        with self.assertRaises(js.SourceError):
            js.teamtailor("127.0.0.1")
        with self.assertRaises(js.SourceError):
            js.teamtailor("localhost.localdomain/../admin")

    def test_recruitee_salary_and_requirements(self):
        from tools import job_sources as js
        with self._get([("recruitee.com/api/offers", RECRUITEE)]):
            [lead] = js.recruitee("acme")
        self.assertEqual(lead.salary, "EUR 80000-100000 per year")
        self.assertIn("PyTorch", lead.description)
        self.assertEqual(lead.posted_at.isoformat(), "2026-10-15T09:31:36")

    def test_workable_remote_and_company(self):
        from tools import job_sources as js
        with self._get([("apply.workable.com/api/v1/widget", WORKABLE)]):
            [lead] = js.workable("huggingface")
        self.assertEqual((lead.company, lead.location, lead.remote), ("Hugging Face", "Paris, France", True))

    def test_pinpoint_compensation_only_when_visible(self):
        from tools import job_sources as js
        with self._get([("pinpointhq.com/postings.json", PINPOINT)]):
            [lead] = js.pinpoint("acme", "Acme")
        self.assertEqual(lead.salary, "GBP 70000-90000 per year")
        self.assertIn("What you'll do", lead.description)
        hidden = {"data": [dict(PINPOINT["data"][0], compensation_visible=False)]}
        from utils.cache import cache
        cache.clear()
        with self._get([("pinpointhq.com/postings.json", hidden)]):
            [lead] = js.pinpoint("acme", "Acme")
        self.assertEqual(lead.salary, "")

    def test_softgarden_schema_feed(self):
        from tools import job_sources as js
        with self._get([("career.softgarden.de/jobs.feed.json", SOFTGARDEN)]):
            [lead] = js.softgarden("acme")
        self.assertEqual((lead.company, lead.location), ("Acme GmbH", "Stuttgart, DE"))
        self.assertIn("microcontrollers", lead.description)

    def test_devjobs_title_salary_and_remote(self):
        from tools import job_sources as js
        with self._get([("swissdevjobs.ch/rss", _Resp(DEVJOBS_RSS))]):
            leads = js.devjobs("ch")
        first, second = leads
        self.assertEqual((first.title, first.company), ("Full-Stack Entwickler/in", "IWF Web Solutions AG"))
        self.assertEqual(first.salary, "CHF 85'000 - 115'000")
        self.assertIsNone(first.remote)  # "remote und vor Ort" is not a remote job
        self.assertTrue(second.remote)
        self.assertEqual(first.location, "Switzerland")

    def test_xml_entity_expansion_is_rejected(self):
        from tools import job_sources as js
        bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
                '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
                '<workzag-jobs><position><name>&lol2;</name></position></workzag-jobs>')
        with self._get([("jobs.personio.de/xml", _Resp(bomb))]):
            with self.assertRaises(js.SourceError):
                js.personio("evil")


class DetectionTests(unittest.TestCase):
    def setUp(self):
        from utils.cache import cache
        cache.clear()

    def test_careers_page_detects_embedded_board(self):
        from tools import job_sources as js
        page = '<a href="https://iqm.teamtailor.com/jobs">Jobs</a><script src="https://cdn.x/y.js"></script>'
        with mock.patch("tools.url_ingest.fetch_html", return_value=page), \
             mock.patch.object(js.requests, "get",
                               side_effect=_routes([("iqm.teamtailor.com/jobs.rss", _Resp(TEAMTAILOR_RSS))])):
            self.assertEqual(js.detect_board_from_page("https://meetiqm.com/careers/"), ("teamtailor", "iqm", 1))

    def test_custom_domain_teamtailor_by_marker(self):
        from tools import job_sources as js
        page = '<meta name="generator" content="Teamtailor"><link href="https://assets.teamtailor-cdn.com/x.css">'
        with mock.patch("tools.url_ingest.fetch_html", return_value=page), \
             mock.patch("utils.security.socket.getaddrinfo",
                        return_value=[(None, None, None, None, ("93.184.216.34", 0))]), \
             mock.patch.object(js.requests, "get",
                               side_effect=_routes([("careers.acme.com/jobs.rss", _Resp(TEAMTAILOR_RSS))])):
            hit = js.detect_board_from_page("https://careers.acme.com/")
        self.assertEqual(hit[:2], ("teamtailor", "careers.acme.com"))

    def test_find_boards_returns_every_hit_for_the_user_to_pick(self):
        from tools import job_sources as js
        routes = [("iqm.teamtailor.com/jobs.rss", _Resp(TEAMTAILOR_RSS)),
                  ("api.lever.co/v0/postings/iqm", [{"id": "1", "text": "Marketing Manager",
                                                     "hostedUrl": "https://jobs.lever.co/iqm/1",
                                                     "categories": {"location": "NY"}}])]
        with mock.patch.object(js.requests, "get", side_effect=_routes(routes)):
            hits = js.find_boards("IQM")
        self.assertEqual({h[0] for h in hits}, {"teamtailor", "lever"})


class DiscoveryWiringTests(unittest.TestCase):
    def test_devjobs_feeds_follow_profile_countries(self):
        from services.discovery import DEFAULT_SOURCES, _devjobs_countries
        from services.profile import CandidateProfile

        self.assertIn("devjobs", DEFAULT_SOURCES)
        self.assertEqual(_devjobs_countries(CandidateProfile(locations=["Zurich", "Munich"])), ["de", "ch"])
        self.assertEqual(_devjobs_countries(CandidateProfile(locations=["London"])), ["gb"])
        self.assertEqual(sorted(_devjobs_countries(CandidateProfile(locations=["Lisbon"]))), ["ch", "de", "gb"])

    def test_watchlist_boards_admit_adjacent_roles(self):
        from datetime import datetime

        from services.discovery import prefilter
        from services.profile import CandidateProfile
        from tools.job_sources import JobLead

        profile = CandidateProfile(target_titles=["Software Engineer"])
        quantum = JobLead(source="teamtailor", external_id="1", url="https://x/1",
                          title="Quantum Engineer, Gates", posted_at=datetime.utcnow())
        legal = JobLead(source="teamtailor", external_id="2", url="https://x/2", title="Legal Counsel")
        self.assertTrue(prefilter("teamtailor:IQM", quantum, profile))
        self.assertFalse(prefilter("remotive:x", quantum, profile))
        self.assertFalse(prefilter("teamtailor:IQM", legal, profile))

    def test_watchlist_accepts_new_boards(self):
        from services.profile import WATCHLIST_ATS
        for ats in ("personio", "teamtailor", "recruitee", "workable", "pinpoint", "softgarden"):
            self.assertIn(ats, WATCHLIST_ATS)


if __name__ == "__main__":
    unittest.main()
