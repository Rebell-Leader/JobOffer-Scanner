# Job sources: current coverage and plan for DE / CH / UK deeptech

Research date: 2026-10-20.
- **[V]** = the endpoint was fetched live during research.
- **[R]** = reported by search results or third parties only.

Ground rule (unchanged): official or public career-feed APIs only. No
scraping against ToS. LinkedIn / Indeed / Glassdoor / StepStone / XING stay
paste-only or go through the Chrome extension.

## Covered today (`tools/job_sources.py`)

- **Company boards (watchlist):** Greenhouse, Lever (incl. the EU host),
  Ashby, SmartRecruiters.
- **Aggregators:** Arbeitnow, Remotive, RemoteOK, HN "Who is hiring",
  Adzuna (key).
- **Open web:** Claude web search (`services.discovery.web_discover`).

Spot-checking real DE deeptech careers pages [V] shows much of the field is
already reachable:
- **Ashby:** DeepL, Aleph Alpha, Sereact, n8n, Synthesia.
- **Greenhouse:** ARX Robotics, Isar Aerospace, RFA, KONUX, Terra Quantum.

The gaps are the DACH/UK-specific ATSs. IQM, for example, is on Teamtailor;
Agile Robots, OroraTech and UnternehmerTUM / TUM Venture Labs are on
Personio.

## Next sources, in implementation order

| # | Source | Endpoint | Auth | Data | Terms | Effort |
|---|---|---|---|---|---|---|
| 1 | **Personio** | `GET https://{slug}.jobs.personio.de/xml?language=en` [V] | none | full HTML description, offices, department, seniority, employment type; no salary | URL shown in Personio dev docs; robots `Allow: /` [V] | S |
| 2 | **Teamtailor** | `GET https://{careers-host}/jobs.rss?offset=0&per_page=100` [V] | none | full description, remote status, locations, department | officially documented public feed [V] | S |
| 3 | **Recruitee** | `GET https://{slug}.recruitee.com/api/offers/` [V] | none | full description, **salary min/max/currency**, remote/hybrid/onsite | Careers Site API documented as no-auth [R] | S |
| 4 | **Workable** | `GET https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true` [V] | none | full description, locations, telecommuting | backs the official embed widget; robots allows it [V] | S |
| 5 | **Pinpoint** | `GET https://{slug}.pinpointhq.com/postings.json` [V] | none | full description, workplace type, **compensation** | public career-site feed [R] | S |
| 6 | **Softgarden** | `GET https://{slug}.career.softgarden.de/jobs.feed.json` [V] | none | schema.org DataFeed of JobPosting | robots allows all, publishes llms.txt [V] | S |
| 7 | **DevJobs RSS** (GermanTechJobs / SwissDevJobs / DevITjobs.uk) | `GET https://germantechjobs.de/rss`, `https://swissdevjobs.ch/rss`, `https://devitjobs.uk/rss` [V] | none | **salary in every item**; about 1k DE and 365 UK items | links tagged `utm_source=our_rss_feed` [V]; ToS not reviewed | S |
| 8 | **Reed** (UK) | `GET https://www.reed.co.uk/api/1.0/search?…`, detail `/api/1.0/jobs/{id}` [V docs] | free key (Basic auth) | full description in the detail call, salary | official API | S |
| 9 | **Workday** | `POST https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` [V], or `/{site}/siteMap.xml` plus JSON-LD per job [V] | none | full description | JSON endpoint undocumented; prefer sitemap + JSON-LD | M |
| 10 | **JOIN** | `join.com/companies/sitemap-jobs-index.xml` plus JSON-LD per job page [V] | none (official API is employer-only) | full JSON-LD record | robots allows it [V] | M |

**Cheap extras (remote-EU):**
- Jobicy: `/api/v2/remote-jobs?geo=germany`, attribution required [V].
- Himalayas: `/jobs/api`, cursor paging [V].
- Berlin Startup Jobs: `/engineering/feed/?paged=N`, excerpts only [V].
- ML@Scale "The Zürich Feed": Substack RSS, needs LLM extraction [V].

**Watchlist auto-detection:** extend `detect_board` to fetch a company's
careers page and match the ATS domain it embeds (`jobs.personio.de`,
`teamtailor.com`, `recruitee.com`, `apply.workable.com`, `pinpointhq.com`,
`myworkdayjobs.com`, …). Seed the watchlist from VC portfolio pages by
hand, not by crawling them. This is how aggregators like Getro get
coverage: they read each company's ATS.

## Avoid (ToS / technical)

- **Getro boards** (HV Capital, Earlybird, Merantix, EF): 403 for automated
  clients and a paid API [V].
- **Consider boards** (Balderton, Built In Europe): ToS §1.4(l) bans robots
  and scrapers [V].
- **Wellfound, Welcome to the Jungle / Otta:** ToS ban scraping [V].
- **LinkedIn, Indeed, Glassdoor, StepStone, XING:** ToS ban it, and LinkedIn
  is actively litigating (ProAPIs, Proxycurl) [R]. Use the extension.
- **Grey; skip, or use only at low volume when you trigger a search:**
  - Bundesagentur Jobsuche: works with header
    `X-API-Key: jobboerse-jobsuche` [V], but its terms ban robots [V].
  - Job-Room CH: its robots.txt says "Do not crawl Job Adverts" [V].
  - SAP SuccessFactors: its XML feed works, but robots is `Disallow: /` [V].
- **No machine-readable path:**
  - jobs.ch / jobup.ch [R];
  - UK DWP Find a Job (SFTP partners only) [R];
  - Climatebase and Work In Startups (bot walls) [V];
  - ELLIS (HTML only) [V].

## Communities and curated lists

| Source | What it actually is | How to use it |
|---|---|---|
| UnternehmerTUM / TUM Venture Labs | jobs posted on Personio [V] | covered once source #1 lands |
| Clera | a candidate-side AI headhunter, not a job feed [R] | not a source |
| {Tech: Europe} | only a Notion page for its own hiring [R] | not a source |
| ETH Zurich careers | HTML plus "jobmail" alerts [V] | inbox parsing |
| EURAXESS | email alerts [R] | inbox parsing |
| appliedAI, TUM.ai, Hello Tomorrow | no job feed found [V, inconclusive] | — |

## What similar products do

- **JobSpy** (about 4.5k stars) scrapes LinkedIn / Indeed / Glassdoor and
  documents rate limits and proxies [V]. The more durable projects read ATS
  endpoints directly (ats-scrapers/jobhive, Levergreen) [R].
- **AIHawk** (about 29k stars) removed its LinkedIn features after legal
  threats [R]. Auto-applying on consumer boards is a dead end.
- **Teal / Huntr** capture jobs via a browser extension on the page the user
  is viewing, not server-side scraping [R]. Same model as our extension.
- **Aggregators** (Getro, WTTJ/Otta, Jobright) ingest from employer ATSs and
  then gate their own data [V/R].
- **ATS career feeds are increasingly open to AI clients:** Softgarden and
  JOIN publish llms.txt, and Workable sends `Content-Signal: ai-input=yes`
  [V]. Low-rate personal use of these feeds is low-risk.
