# Civitas Research — Political Representation Tracker

**Live at [civitas-research.org](https://civitas-research.org)** · Bluesky [@civitas-research.org](https://bsky.app/profile/civitas-research.org)

[![CI](https://github.com/kamoras/civitas/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kamoras/civitas/actions/workflows/ci.yml)
[![Lighthouse — Accessibility & SEO](https://github.com/kamoras/civitas/actions/workflows/lighthouse.yml/badge.svg?branch=main)](https://github.com/kamoras/civitas/actions/workflows/lighthouse.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

Civitas Research ("Civitas" for short) is an open-data AI/ML platform that aggregates data from official
U.S. government sources into unified transparency scorecards for senators,
House representatives, presidents, and Supreme Court justices (whose records
are shown, not scored). It also
features an Action Center that surfaces trending civic issues from news
analysis, auto-detects ongoing national concerns as trackable monitors,
and builds a year-in-review timeline. Voting records, campaign finance,
floor speeches, judicial opinions, and party platforms are analyzed using
embedding-based classification, roll-call party alignment (content-based
where no roll call exists), and deterministic scoring — all running locally on a Raspberry Pi 5 with zero
external API calls to cloud AI services.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           DATA SOURCES (read-only)                           │
│                                                                              │
│  Congress.gov   FEC API    GovInfo API  Senate.gov  Oyez    BLS / FRED       │
│  (bills, votes, (campaign  (bill text,  (speeches,  (SCOTUS (jobs,           │
│   members)       finance)   histories)   remarks)    cases)  GDP)            │
│                                                                              │
│  AP / NPR / PBS / BBC / The Hill / Politico / Roll Call (RSS)                │
│  + 41 per-state newsrooms (election coverage)                                │
│  Google Trends     Bluesky            State election offices (statewide      │
│                    (trending)          ballot measures, read directly)       │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │  rate-limited HTTP
                           │  (Congress 1.2 RPS, FEC 0.25 RPS, GovInfo 1.0 RPS)
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                NIGHTLY PIPELINE  (APScheduler, default 3 AM UTC)             │
│                                                                              │
│  ┌─────────┐   ┌───────────┐   ┌───────────────────────────────────────┐     │
│  │ 1.FETCH │──▶│2.TRANSFORM│──▶│            3. ANALYZE                 │     │
│  └─────────┘   └───────────┘   │                                       │     │
│                                │  ┌──────────────────────────────────┐ │     │
│                                │  │ per member, in order:            │ │     │
│                                │  │ embed (batches) → score → persist│ │     │
│                                │  │                                  │ │     │
│                                │  │ bill embeddings   donor kNN      │ │     │
│                                │  │ lobbying match    promise align  │ │     │
│                                │  │ (deterministic — no LLM call)    │ │     │
│                                │  │                                  │ │     │
│                                │  └──────────────────────────────────┘ │     │
│                                └───────────────────────────────────────┘     │
│                                                │                             │
│  ┌──────────┐   ┌──────────┐   ┌────────────┐  │   ┌──────────────────────┐  │
│  │4. EXPLORE│   │5.JUSTICES│   │6.PRESIDENTS│  │   │     7. FINALIZE      │  │
│  │ (sqlite- │   │ (Oyez)   │   │(BLS/FRED/  │◄─┘   │ (persist scores,     │  │
│  │  vec)    │   │          │   │ UCSB)      │      │  PipelineRun record) │  │
│  └──────────┘   └──────────┘   └────────────┘      └──────────────────────┘  │
│                                                                              │
│  ──────────────────── HOUSE PIPELINE (runs after Senate) ──────────────────  │
│  Own ~6-phase pipeline for all 435 representatives (FETCH MEMBERS, NORMALIZE,│
│  FETCH BILLS & VOTES, CLASSIFY BILLS, SPONSORSHIP ANALYSIS, FEC + SCORING) — │
│  reuses the unified EXPLORE pipeline for House floor speeches                │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │ writes
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              PERSISTENCE LAYER                               │
│                                                                              │
│  SQLite  (civitas.db)                    sqlite-vec  (vectors.db)            │
│  ├── senators / representatives          ├── vec_explore  (documents)        │
│  ├── KeyVote / SponsoredBill             └── vec_bills    (classification)   │
│  ├── Donor / IndustryDonation / LobbyingMatch                                │
│  ├── CampaignPromise                                                         │
│  ├── ActionIssue / NationalMonitor / MonitorUpdate                           │
│  ├── TimelineEntry / WeekSummary / MonthSummary / YearSummary                │
│  ├── ScoreSnapshot  (historical score tracking per senator/rep)              │
│  ├── ApiCache  (raw API responses, TTL=72h, never cleared)                   │
│  ├── AnalysisCache  (LLM outputs, hash-keyed, cleared on code changes)       │
│  └── LearnedClassification  (cross-run entity learning store)                │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │ reads
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  FastAPI backend  (port 8000)                                                │
│  /api/senators      /api/representatives     /api/presidents                 │
│  /api/justices      /api/action              /api/explore                    │
│  /api/admin         /api/public/v1  (open, rate-limited)    /health          │
│                                              ┌────────────────────────┐      │
│  APScheduler ──▶ nightly pipeline            │  llama.cpp server      │      │
│  APScheduler ──▶ hourly action refresh  ────▶│  LFM2.5-1.2B-Instruct  │      │
│                                              │  port 8070 (Docker)    │      │
│                                              └────────────────────────┘      │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │ JSON
                           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  Next.js 16 frontend  (port 3000)                                            │
│  /politicians  /congress  /leaderboard  /action  /explore  /compare          │
│  /elections  /elections/states/<ST>  /issue  /about  /changelog  /admin       │
│  /accessibility  /feedback  /compare/presidents                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

> **Rendered diagrams:** [`docs/diagrams/`](docs/diagrams/) has Mermaid versions of
> this and eight other views — pipeline internals, classification tiers, scoring
> composition, the schema, and the deploy sequence — with more detail than ASCII
> fits. They render as pictures on GitHub. The diagrams here stay ASCII so the
> README reads the same in a terminal.

**A missing record says where to go next.** A member, bill, issue, document or state address with nothing behind it is a real 404 (marked noindex), and its page offers the next step for that kind of record (`not-found.tsx` in each route, `components/notFound/RecordNotFound.tsx`): a member's address lists the politicians whose addresses are closest to it, one tap each (`GET /api/politicians/suggest`, difflib's similarity of the address to each id and to each word of it, from 0.75), then the directory; a bill's the bills list and the latest day in Congress; an issue's today's issues and the archive; a document's Explore search; a state ballot's or a state map's the ballot of the state the address still names (a full name like `/elections/states/ohio`, or a state that didn't redraw its House map), then the redrawn states and the elections map. A mistyped member address took five taps on a phone to reach the member (menu, directory, search box, type, result); it now takes one.

**Server rendering:** `/action`, `/explore`, `/politicians` and the other
search-param pages keep their view state in the address bar, and `useSearchParams()` has
no value during SSR — so React suspends and the server renders the nearest
`<Suspense>` fallback. Each of those pages supplies a real one
(`components/layout/PageFallback.tsx`: navbar, masthead, content skeleton), so
the frame arrives in the first paint. They previously wrapped the *entire* page
in `<Suspense fallback={null}>`, which made the server send a ~12KB body with no
chrome and nothing visible until ~653KB of JavaScript had parsed and hydrated.
The fallback carries the page's own masthead line at the page's own width
(`lede`, `width`), so the largest text in the first screen is server-sent:
/action, /explore and /politicians painted it at 1.9 s on a throttled phone
before (2026-10-14), 0.8 s after. Pages whose first screen depends on data
or on the query string render per request instead (`await connection()`, or
reading `searchParams`), with the data the first view needs read on the
server and handed to the client as its first state: `/elections` (the lean
map and phase, `elections/layout.tsx`), `/leaderboard` (the branch the URL
asks for and its field strip, `leaderboard/page.tsx`), `/congress/bills`
(the pipeline overview, `congress/bills/layout.tsx`) and `/compare` (a
linked pair's loading view keeps the masthead). Mobile LCP on those went
from 1.7–2.1 s to 0.8–0.86 s and leaderboard CLS from 0.09 to 0. Anything
the server could not read, the page still reads itself.

**Hardware:** Raspberry Pi 5 (16 GB RAM), NVMe SSD. All models, databases, and services run on-device. No cloud GPU, no third-party AI APIs, no data leaves the device. The backend image sets `HF_HUB_DISABLE_TELEMETRY=1`, so the Hugging Face libraries that fetch the embedding models send no usage reports.

**Pages request nothing from other hosts.** Every photo (members' official photos, justices' Oyez photos, an Action Center issue's licensed news photo) is served from the site at `/photo/<kind>/<id>`: the API gives that path (`backend/app/photos.py`), and the frontend route `app/photo/[kind]/[id]/route.ts` fetches the picture server-side by kind and id (never a URL; the source comes from `GET /api/photo-sources/{kind}/{id}`). A member's photo is one of two official photos: the headshot on the member's Congress.gov record (its `depiction`, used only on Congress.gov's `/img/member/` path and only with the attribution Congress.gov gives it) or bioguide's portrait. Neither is always the head-and-shoulders shot (bioguide's is for some members a full-length painting; a few members' own depictions are full-figure or wide shots), so the pipeline measures the face in each with the YuNet face detector and keeps the depiction unless its face is small and bioguide's clearly larger (`backend/app/pipeline/fetch/member_photos.py`, nightly in the stock-trades run; thresholds fitted by `backend/scripts/calibrate_member_photos.py` into `app/data/member_photo_calibration.json`). The model is YuNet's `face_detection_yunet_2023mar.onnx` from the [OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) (MIT License, Copyright (c) 2020 Shiqi Yu), vendored in `backend/app/data/` and run with `opencv-python-headless` in a child process of its own (`pipeline/fetch/face_detection.py`), so OpenCV is never loaded by the API or the pipeline process. The endpoint reads the stored choice (`bioguide_photo_source` in `backend/app/photos.py`). Each kind is fetched only from its own hosts, over https: www.congress.gov and bioguide.congress.gov for members, api.oyez.org, and rollcall.com for issue photos (`PHOTO_SOURCE_HOSTS` in `backend/app/photos.py`), enforced by the pipeline, the endpoint, and the route on every redirect hop, since an issue photo's URL comes from an RSS item. nginx keeps each one a week (`photo_cache`), serves the last copy when a source is down, and rate-limits only cache misses, through the same loopback hop as the API: 10/s per client with a burst of 600, the first 60 immediate, so a cold directory page (543 portraits) is paced, never refused. A photo the source doesn't have is a 404, shown as initials (`components/Photo.tsx`). Pages show each photo scaled to its slot: `/photo/<kind>/<id>/<width>` is the same photo resized with sharp to one of five widths (96, 192, 400, 800, 1200; `PHOTO_WIDTHS` in `lib/photos.ts`), never up, as WebP; any other width is a 404, so nginx, which keys this cache on the path, holds at most those five copies of a photo besides the original. `Photo` gives every image a `srcset` of those widths and the slot's `sizes` (required, so a new photo can't ship without one), and the Action Center's lead issue photo and an issue page's photo are fetched first (`fetchpriority="high"`). Until 2026-10 an issue's 1620×1080, 364 KB photo was sent whole into a 341px slot on a phone, the slowest Largest Contentful Paint on the site (4.1 s on `/action`, 3.7 s on an issue page, at 375px with slow 4G and a 4× slower CPU). Fonts, scripts, styles and map data are self-hosted; `tests/test_same_origin_photos.py` and `frontend/src/lib/sameOriginImages.test.ts` fail on an absolute image URL on another host.

---

## Nightly Pipeline: Phase by Phase

The nightly pipeline processes every senator and House representative through FETCH → TRANSFORM → ANALYZE → FINALIZE before persisting scores and snapshots; the Supplementary pipeline carries the other three phases below (EXPLORE, JUSTICES, PRESIDENTS). They are links in a five-pipeline **chain**, run in this order by `scheduler.py`:

```
Senate ──▶ Supplementary ──▶ House ──▶ Stock trades ──▶ Election
           (explore docs,              (stock_pipeline)  (election_pipeline)
            SCOTUS, PVI,
            presidents)
```

**The links run one at a time, but none waits on the one before it succeeding** (`app/pipeline_chain.py`). Each reads whatever the database holds, so a link that is skipped, fails or crashes gets its own alert and the next link runs anyway. Until September 2026 any of those ended the chain: it once stopped inside Supplementary, and House, Stock trades and Election did not execute for 19 nights, because a pipeline that never starts leaves no row behind to look wrong. One chain runs at a time: a trigger of any of its pipelines is refused while one is going, and a nightly run that finds a triggered chain going leaves the night to it. A link skipped because another run holds the machine (another run of that same pipeline, or a member pipeline holding off Stock trades) is not moved past: the chain waits until no pipeline is running, so it does not start the next heavy pipeline beside that run, then tries the link once more and reports what that attempt did. The other run may have covered one senator, or failed, so it never counts as the link's run. A run past the pipelines' staleness limit stops counting as running, so the wait always ends. The one skip that ends a chain is a data reset holding the database. Both member pipelines check the analysis-code hash at start, since House can now run after a failed Senate. A full manual trigger runs the same five links the same way, so it can recover any of them. A killed process (a crash, a health-check kill, the Docker daemon restarting) takes its running link with it, so the chain records the link it is on and the restarted pipeline process resumes the chain from that link, once, within a day of its start (`scheduler.resume_interrupted_chain`); a resumed chain cut short again raises an alert instead. `ops_alerts.check_pipeline_staleness` still catches a pipeline with no successful completion in `PIPELINE_STALE_ALERT_DAYS`, and `check_pipeline_overrun` the opposite case of a run that started and is taking too long. Its alert says which step the run is in, that step's progress, and how long ago the run last wrote any, so a long but working step (the House sponsorship step reading every sponsored bill after a cache change, hours of rate-limited Congress.gov requests that it now counts in its detail and logs every 500 bills) reads differently from a hang.

**The band at the top of every page says the same, link by link.** `GET /api/pipeline/status` returns each link's latest run (`links`, in chain order, each `completed`, `partial`, `running` or `failed` as the API decides: a RUNNING row whose run is dead reads as failed), how many completed (`linksCompleted`) and the newest completion (`filedAt`). The band (`components/layout/RecordsBand.tsx`) prints COMPLETE with that time only when every link's latest run completed; otherwise it names the link and when ("SENATE DATA RUN FAILED 2026-10-15 05:26 UTC · 4 OF 5 FILED 2026-10-15 10:08 UTC"; on a phone "SENATE DATA FAILED 05:26 UTC · 4/5 FILED", the time from 360 px), linked to `/about/data#nightly-runs`. "DATA" says the run is Civitas's: "SENATE FAILED 05:26 UTC", the first words a phone visitor read, said to a student that the Senate had failed. Two or more failed links name the first and count the rest ("SENATE + 2 MORE DATA RUNS FAILED"; on a phone "3 DATA RUNS FAILED"), every name in the link's title and accessible name, so the band stays one line at every width the header's clearance allows (checked for every link and state, one to five failed, at 320 to 1280 px). Until 2026-10 it read only the Senate's run, and on 2026-10-15 printed LAST RUN FAILED over every page while the other four links had completed.

### Phase 1 — FETCH

Pulls raw data from each government API and stores the complete response verbatim in `ApiCache` before any transformation:

| Source | What is fetched | Rate limit |
|--------|-----------------|------------|
| Congress.gov | Members, bills sponsored/cosponsored and their actions (current congress), bill summaries and titles | 1.2 RPS |
| Senate.gov / House Clerk | Roll-call vote XML (every member's position on each vote) | 1.2 RPS (shared with Congress.gov) |
| unitedstates/congress-legislators (CC0) | Committee seats and chamber leadership titles, weekly (`fetch/committee_leadership.py`). A sitting member it doesn't list yet (it updates every month or two; on 2026-10-08 a senator seated in July and a representative seated in September had no entry) takes their seats from the chamber's own list instead: the House Clerk's member data, senate.gov's committee rosters | weekly, a few files |
| FEC API + bulk committee master (`cm{yy}.zip`) | Campaign finance transactions, committee receipts; committee type, designation and connected organization | 0.25 RPS (bulk files: one download per cycle, weekly) |
| GovInfo API | Bill text; Congressional Record floor remarks (Explore) | 1.0 RPS |
| Lobbying Disclosure Act registry (lda.gov) | Registered lobbying spend by client, and the bills named in filings, for organizations in donor–vote matches. Each registrant's report for a quarter counts once, the latest posted: an amendment restates its quarter, and summing it with the original had overstated 20 of 274 cached searches, one by $3.74M (`lda._latest_per_quarter`) | 0.2 RPS anonymous / 1.0 with `LDA_API_KEY` |
| Oyez / supremecourt.gov | Justice voting records, case metadata, docket pages | ~2 RPS (fixed pauses) |
| Supreme Court Database / FJC / Martin-Quinn | Justice votes in cases the federal government argued (newest release), each justice's nomination dates, ideal points per term | 1.0 RPS, cached |
| BLS | Payroll job growth by administration | batch |
| FRED | Unemployment and consumer prices (BLS series, annual means) | batch, cached 7 days |
| Cooperative Election Study (Harvard Dataverse, CC0) | Approval of each member among their own constituents, by the respondent's party (informational; `scripts/fetch_ces_approval.py`, per survey release) | one-off download |
| MeasuringWorth / Maddison Project / World Bank | US real GDP back to 1790; real GDP per person for 13 peer economies | batch, cached 30 days |
| Federal Register | Executive orders signed per administration | 1.0 RPS |
| House Clerk / Senate eFD | STOCK Act periodic transaction reports, and annual and new-filer financial disclosures (PDF/HTML, parsed) | 1.0 / 0.5 RPS |
| OGE | Sitting president's disclosures: the annual report (OGE Form 278e), whose Part 7 lists every transaction of its year as text, and the OGE Form 278-T periodic transaction reports since, which are scans read by OCR (PDF, parsed) | 0.5 RPS |
| SEC | Ticker and company-name -> issuer, and the SIC industry code the SEC assigned it (`data.sec.gov` submissions), for a traded security's industry | 5 RPS, cached 90 days per issuer |
| Voteview | Congress-specific (Nokken-Poole) member ideal points (per-congress CSV exports), feeding Constituent Alignment's position-congruence component | batch |

**A trade's industry** is the SEC's own code for its issuer, never a guess from the asset's name (`sec_tickers.py`, `stock_pipeline._industries`). A ticker (the Senate's eFD table prints it in its own column; the House puts it in the asset text) or a company name that matches an SEC-registered title exactly, or writes one title in a brokerage statement's short form (each word the title's word, a prefix of it or its letters in order — "UNION PAC CORP", "HONEYWELL INTL INC" — followed only by words describing the security such as "CL A" or "COM", and fitting no second title; `sec_tickers.abbreviated_match`: on a president's annual report it labelled 683 names that matched nothing exactly, against 192 exact, and 120 sampled were all the right company), leads to the issuer's record on `data.sec.gov`, whose Standard Industrial Classification code is decoded by the SIC manual's hierarchy (`SIC_INDUSTRY`) — "3674 Semiconductors" is Technology, "6798 Real estate investment trusts" is Real Estate. A cryptocurrency is Crypto only when the filer declared it one (the House's `[CT]` code, the Senate's Asset Type column). Everything else — Treasury and municipal bonds, funds, private companies, OTC ADRs, and codes with no category of ours such as waste management — stays unclassified. A trade two of a filer's reports both list (same owner, asset, date, type and amount bracket — an amended or re-submitted report) counts once, at the earlier report's date (`stock_pipeline.collapse_refiled_trades`; 31 Senate and 2 House groups on 2026-10-08). A Senate amendment (eFD titles it "Periodic Transaction Report for <the report's date> (Amendment N)") is the whole report again: only its newest version is kept, and each trade takes the date it was first disclosed, the original report's for a trade the original listed and the amendment's for one it added (`stock_pipeline.settle_amended_reports`). One senator's nine amendments filed one day had read as up to 867 days late. An original older than the night's search is fetched once by a wider search. This replaced running names through the donor-industry embedding classifier, which put Broadcom and ConocoPhillips under Lobbyists and GitLab under Private Prisons: measured against the SEC's codes on the 1,299 production trade names that have one, the embedding agreed 30% of the time, and 65% at its most confident margins. Embedding the SIC descriptions themselves fared no better ("Semiconductors & Related Devices" read as Pharmaceuticals, "National Commercial Banks" as Real Estate). Every stored trade is relabelled each night, so a change here reaches trades already stored; with the SEC unreachable, labels are left as they are.

**Congress's filing dates.** A trade's disclosure date is the date its report was filed, which the STOCK Act's 45 days run to: the Senate search's filed date, and for the House the FilingDate of the Clerk's yearly index. Until parser version 6 a House trade was stored as disclosed the day it was made (all 4,681 of them), so none could show as late: the form's notification-date header wraps ("Notification / Date") and went unmatched, and even that date is when the filer learned of the trade, not when it was reported. It is now only the fallback when the filing date is unknown. The Senate's eFD table carries its ticker in a column of its own, now read (971 of 1,316 Senate trades had none). A scanned House or Senate filing's dates are read only up to the filing date (parser version 5): without that window one House scan stored a bond's maturity as a trade made in 2033. A scan the current reader finds no row in loses what an older reader stored from it on re-read (a fetch that fails keeps it); a date a text filing prints is kept as printed (one 2025 filing lists two trades dated 05/08/2015 beside a notification date of 05/15/2025).

**The president's trades.** The White House files its 278-T periodic reports as scans. tesseract's plain text reads their table's columns as separate blocks, so each scanned page is read by where its words sit (`ptr_common._ocr_table_page`): rows are found by their transaction-type word or row number, an amount is accepted only as one of the form's own ranges (`AMOUNT_BRACKETS`; one misread bound is recovered from the other), and a date only inside the filing's window (the term's start to the filing date). A row that fails either is counted, not guessed. The date cell starts at the type column's edge, since one filing right-aligns its dates in a wide column, and is read again enlarged when the first reading isn't a date. On the seven 2026 filings (to September 22) this reads 5,212 transactions, against 1,770 before; 1,714 of the rows left unread were in one filing (May 8, 2026), scanned at 150 dpi and printed at half size: its dates are about 6 pixels tall. No reading of them was reliable (tesseract, preprocessed or not, got a quarter right; matching each date against every candidate rendered in the form's font got 38% with no usable confidence), while each row's asset, type and amount read. The president's rows whose date alone is illegible are therefore kept undated (`ocr_extract_rows(keep_undated=True)`, `PresidentTrade.transaction_date` NULL, migration 0019) and listed as "date not legible in the scan" with the filing date, after the dated rows. An annual report supersedes an undated row whose filing was filed by the end of the year it covers. Members' scanned PTRs are read the same way (`PARSER_VERSION` 5, migration 0022): a House or Senate paper filing's row whose date alone is illegible is kept undated with the filing date, and a House scan's dates must fall on or before its filing date. The line parser it replaced took a line's first date, on a bond its maturity, and stored trades dated as late as 2078; it also read only a fraction of each filing's rows (39 of the 189 on one page-dense filing). At 150-200 dpi tesseract still misreads a digit of some dates, so a date read by OCR supports no timeliness figure (`StockTradeSchema`: `daysToDisclose` and `late` are null) and the row is labelled as read from a scan. For every year an annual report covers, its Part 7 replaces the year's 278-T rows (`president_fd`, `stock_pipeline._ingest_president_annual`): 21,285 transactions for 2025 as text, against 2,362 rows the scans had yielded. Part 7 states no date a transaction was first reported, so its rows carry no timeliness figure either.

**Members' holdings.** Each member's newest annual financial disclosure backs the holdings pie on their scorecard (`holdings_pipeline`; House Schedule A read from word positions in `house_fd`, Senate Part 3 from the eFD page in `senate_fd`). A category is only the asset type the filer declared. A member seated in a special election has filed only a new-filer report until their first annual one, so the House's new-filer reports are read too, labelled by filing date (they state no valuation date) and outranked by any annual report: on 2026-10-08 four sitting members had nothing else. A House filer may state an exact value instead of a bracket ("$1,251.00"; 15 holdings on two 2025 reports); it is charted and shown as that figure, not counted as "no value stated". Filers are matched to members by name (`filer_matching`): eFD writes a suffix after the surname ("Doe, Jr."; four senators had gone unmatched), and the House index keeps a member's district from before a redistricting (two members' annual reports, and one member's seven trade reports, had gone unmatched), so a filer listed under a district where no member has that surname is matched within the state when the first names agree. A member who files under a new surname, their own moved into the given names ("Roe, Jane Doe" for Jane Doe), is matched in their own district when their first name leads the filer's and their surname is among the rest (one member's two annual reports and two extensions had gone unmatched; over the 2024-26 indexes the rule changes no other match). An audit of 14 representatives' and 12 senators' stored holdings against the filed reports (2026-10-08) found every sampled report read row for row; the defects above turned up in population-wide checks. A second sample (2026-10-09: 12 representatives and 6 senators of both parties) matched too: 368 of 368 House assets against the asset-type codes in each Schedule A, and 300 of 300 Senate assets against the eFD table's itemized rows, each stored row identical to a fresh parse, every category the one the filer's declared type maps to, and every stored report the member's newest filing (422 of 422 House members).

**The president's holdings.** The same annual report's Parts 2 (Schedule 1, the business entities), 5 (the spouse's) and 6 (the investment accounts) back the holdings pie on the president's scorecard (`president_fd.annual_holdings`, read from pdfplumber's tables; `holdings_pipeline.ingest_president_holdings`, the stock-trades run's third holdings phase; `GET /api/presidents/{id}/holdings`, the members' shape). Every line that states a value is an asset; a managing-member line with none of its own is not. The 278e has no asset-type column, so a category comes only from what the form states: a business entity's own "Underlying Assets" (real estate or a golf club, a bank account, cryptocurrency, a pension; otherwise a business interest), and in Part 6 the excepted-investment-fund column. Every other security is `UNSTATED`, "Type not stated", never typed from its name. A value in euros is kept as printed, not read as dollars. The 2026 report (for 2025): 6,458 assets; by bracket midpoints, 44% type not stated, 26% real estate, 16% business interests, 6% crypto, 5% funds, 4% cash. Only the sitting president's report is read.

Nothing from the API cache is ever cleared — it represents immutable source data. A fresh run can replay against the cached responses without re-hitting the external APIs (controlled by `PIPELINE_CACHE_TTL_HOURS`).

### Phase 2 — TRANSFORM

Normalizes raw API payloads into typed domain objects. Key operations:

- **FEC deduplication**: FEC records contain duplicate committee entries from amended filings. The transform layer resolves these by matching on committee ID and keeping the most recent amendment.
- **Bill title normalization**: Congress.gov returns bill titles in several formats (official, short, display, popular). Transform picks the most human-readable, falling back through the hierarchy.
- **Employer name normalization**: FEC contributor employer fields are free text. A batch embedding pass maps variant spellings (e.g. "Goldman Sachs & Co", "GOLDMAN SACHS") to a canonical form before classification.
- **Memo text parsing**: FEC memo text fields encode earmarks and transfers in free-form text. A batch skip-entity classifier separates genuine contributions from administrative memo entries.
- **Member ids**: a member's id, and their URL `/politicians/<id>`, is `first-last` from Congress.gov's "Last, First Middle" name (`app/member_ids.py`): the name the member goes by where the source marks it — the quoted nickname when the name has one (`hank-doe` for `Doe, Henry C. "Hank"`), else the first given name (passing over initials and suffixes) — and the whole surname (`jane-doe-roe` for "Doe Roe, Jane Q.", or the member detail's `lastName` when that is a longer form ending with it), accents folded to ASCII (`ana-nunez`). One person keeps one id in both chambers; two people with the same name are told apart by state code (`jane-doe-oh`), whoever held the id first keeping it. Ids are derived each run and matched to stored members by bioguide id, so a changed id (a new derivation, a legal name change) renames the member in place — the row, every child table, score history, Explore links, action issues, the spotlight rotation — and records the old id in `member_id_aliases`. The API answers an old id under the new one and `/politicians/<old id>` redirects permanently (308) to the new URL; the sitemap lists current ids only.

### Phase 3 — ANALYZE

The heaviest phase, run per member. Fully deterministic — no LLM call. (Two
LLM-based steps used to live here: PAC-donor classification and narrative
summary generation. Both were removed in 2026-07 after live audits found
their output unreliable — generic boilerplate, occasionally fabricated
per-vote reasoning — regardless of prompting approach. See
`cross_reference.py`'s module docstring.)

1. Embed bill titles → classify policy area, stance direction, procedural and commemorative bills (prototype similarity, kNN for the residual)
2. Classify donors by type and industry (tiered: FEC metadata → learning store → embedding → kNN)
3. Party alignment per bill: how the parties actually split on its roll call; a vote with no roll call carries no party label for loyalty (content similarity to party platforms stands in only where no roll call recorded each party's vote; see "Party Alignment" below)
4. Sponsorship network over the chamber: PageRank legislative leadership and SVD ideology from the cosponsorship matrix
5. Donor–vote connections: substantial industry funding matched against policy-anchored vote similarity, with registered lobbying spend (LDA) by client attached, and bills the member voted on that the organization's own filings name. The scorecard lists the member's own vote on each related bill (Yea or Nay, the roll call's question, date and page), read from the stored vote rows when the profile is served (`_scorecard_common.related_votes`); until 2026-10 the panel showed only bill ids and a cut sentence
6. Select key votes: against the party line, related to a top donor's industry, substantive
7. Compute the representation sub-scores against population references measured from the chamber this run, and persist the member's scorecard

### Phase 4 — EXPLORE

Builds the search indexes over government activity documents — floor
speeches (Senate and House), presidential actions (executive orders,
proclamations, memoranda), Supreme Court opinions, and Federal Register
rulemaking documents (including ones still open for public comment).
A Supreme Court case links to the Court's own slip opinion where one is
posted (`supreme_court.parse_slip_opinions`, the term's slip-opinion table),
leads with the holding the Court states there, names the author, decoded
from the table's code against the sitting justices, and takes its date from
the table (Oyez's copy of the date read a month late for one case); until
then it links to the docket page. A Federal Register correction or
republication of another document (its `correction_of` field) is skipped,
since it repeats that document's title. Each run re-reads the stored Federal
Register documents of the last 90 days, and any still open for comment, by
document number (`_refresh_federal_register`): comment periods the Register
moved or set after the first night, placeholder titles it replaced, and a
provisional number it no longer has (removed: the document is stored under
its own number). A presidential document is credited to the presidency its
date falls in. A speech GovInfo's overlapping granules hold twice (a
section's heading granule and the statement's own) is kept once, from the
statement's own granule:
- Floor speeches are read a day of the Congressional Record at a time and split into each member's
  speeches by the Record's own layout; floor business is not indexed (see "Hybrid Search" below)
- Embeds each document whole, in windows of the encoder's context length (`chunk_text`, `explore_chunks`), the title leading each window and the windows cut to fit beside it
- Encodes with the **index** sentence-transformer (384-dim, all-MiniLM-L6-v2)
- Upserts into the `vec_explore` sqlite-vec table with metadata: doc type, source, date, politician name/ID, chamber
- Rebuilds the `explore_fts` FTS5 keyword index (BM25F over title/summary/body)
- Recomputes citation-graph PageRank into `explore_documents.authority`
- At query time: both channels run, filters pushed into each, results fused by weighted reciprocal rank fusion with recency and authority priors — see "Hybrid Search (Explore)" below

This is a separate index from the tier-3 bill-classification embeddings used
in Phase 3 — it's for browsing/searching primary-source government documents,
not for scoring.

### Phase 5 — JUSTICES

Fetches Supreme Court justices' records (not scored, justice v3), weekly on Sunday UTC (or whenever the table is empty — the uncached Oyez per-case crawl, now only of the terms the Supreme Court Database doesn't cover yet, is slow):
- Who sits on the Court comes from Oyez's justice list, never a typed roster. Every night the run compares that list with the stored sitting justices (one request) and refreshes off the weekly day when they differ, so a confirmation or a departure shows the night Oyez lists it. A justice no longer on the list is marked inactive with the day their service ended, and their record stays readable. A list shorter than the Court's quorum of six (28 U.S.C. 1) retires nobody and sends an ops alert.
- Pulls each justice's votes in the Court's decided cases. Oyez sometimes lists one justice twice in a decision (one justice in two 2025-term cases, with two others missing): identical rows count once, conflicting ones leave that vote out, and a missing justice's vote is never filled in. The duplicate had broken the one-vote-per-case key, so every Sunday refresh rolled back and the scorecards went stale with no alert. Now any pipeline step that fails and is carried past (`ProgressTracker.fail`) sends an ops alert, deduplicated per pipeline, step and day.
- The voting record (`justice_analyzer.py`): majority, dissent and unanimous shares, opinions written, agreement with each sitting justice, over the current term and the three before it (`RECENT_TERMS`). Pages name the terms that actually have decided cases (`recordTerms`), never "the last four terms": in October the newest term has begun and has no decision yet. Shown, not scored. Read from the Supreme Court Database for every term its newest release covers (orally argued cases, `justice_records.scdb_case_votes`) and from Oyez only after that: Oyez enters votes case by case, and on 2026-10-08 had them for 13 of the 2024 term's 59 decided cases, so the record read a fraction of that term (117 cases where the Database has 176).
- No justice is scored (justice v3). Each justice's own estimate of the appointing president's effect (`justice_loyalty.py`, Epstein & Posner 2016) is stored and shown with its 95% confidence interval, unranked: whether a justice sides with the federal government more often while that president is in office than under others, fit per justice with the government's side of the case held fixed. Votes through 2014 are Epstein & Posner's, bundled; later terms come from the newest Supreme Court Database release, with each appointing president taken from the Federal Judicial Center's nomination dates (`fetch/justice_records.py`). Justice v2 scored it 0-100; a placebo study retired that: windows of the same length placed later in each justice's career reproduce most of the differences between justices, so no method yet separates loyalty to the appointing president from career timing for an individual justice (`docs/research/justice-scores.md`). The API serves every justice score as null, never 0. A source that can't be read leaves the stored estimates standing, and the run's justice step says "appointer estimate not measured" and which source couldn't be read, in its progress detail and in an ops alert, rather than only a count. The backend image is Debian trixie because the Database's host refuses httpx's TLS handshake under bookworm's OpenSSL 3.0 (a 403 that left every justice unscored on 2026-09-29; see `backend/Dockerfile`).
- Martin-Quinn positions per term with their 95% credible interval (`post_025`, `post_975`), shown, not scored. A run that can't read any Martin-Quinn file keeps the stored positions and says so (`positions_unread`, an ops alert from the Supplementary run). No LLM step.
- **The Court page** (`/court`, `GET /api/court`, `services/court_service.py`): the sitting justices on one left-right line by Martin-Quinn position, the median marked; how often each pair voted the same way, with the counts behind each share (a keyboard-navigable grid with a value in every cell, and every pair as a plain table); how the Court split (9–0 … 5–4) term by term; and each decision's majority, dissent and author, linked to Oyez, the slip opinion and its Explore page (`GET /api/court/decisions`, paginated). It leads with the share of decisions that were unanimous, and states when the term in session has no decision yet. Each justice's page lists every case on the record, twenty at a time (`GET /api/justices/{id}/cases`): the side, the opinion written and the Court's split. Every figure is counted by the backend from the stored votes; nothing is a score or a rank. Credits: the Supreme Court Database (CC BY-NC 3.0 US), Oyez (CC BY-NC 4.0), Martin-Quinn scores (no licence stated; cited as Martin and Quinn 2002).

### Phase 6 — PRESIDENTS

Scores sitting and historical presidents from a mix of live and archival sources:
- **Live**: BLS payroll jobs, MeasuringWorth real GDP and Maddison/World Bank peer-economy GDP, UCSB American Presidency Project approval polling (with Gallup's by-party breakdown, compared within its era against Voteview's House polarization), FRED/BLS unemployment and consumer prices (Effectiveness since 1947, each judged against where the term started)
- **Historical**: C-SPAN Presidential Historians Survey (historical legacy), UCSB election-margin data, MeasuringWorth real-GDP series (1790–present), Maddison Project GDP per person for the US and 13 peer economies (1946–2022, extended by the World Bank's live series): postwar growth is scored relative to those peers, with their catching up with US incomes set aside
- All three score dimensions (Public Mandate, Effectiveness, Historical Legacy) are computed from source data with no LLM involvement. Dimensions a president has no real data source for are left N/A rather than filled with a placeholder.

**Who is sitting** comes from UCSB's roster each night; nothing names a president in code. A president the tables below were written before is matched by name (`derived_president_id`; the election-mandates table by the roster's names, `presidential_elections`), and every night `ops_alerts.check_sitting_president` compares the roster's sitting president with the Federal Register's newest presidential document: one signed after the stored term began, by someone else, means the roster is behind an inauguration and sends an ops alert.

### Phase 6b — VOTES WITH THE PRESIDENT

Reads new OMB Statements of Administration Policy and rebuilds each member's votes with the president's stated positions and the expected rate for their seat. Informational, not scored; see [Votes with the President](#votes-with-the-president-nightly-informational).

### Phase 7 — FINALIZE

Persists everything computed in phases 3–6:
- Writes senator/representative scores, key votes, donor–vote matches and sponsored bills to SQLite
- Appends a `ScoreSnapshot` record (all 5 sub-scores + overall) for each member — enables historical score trend charts. A member whose run failed keeps the last passing run's scores and gets no snapshot that day, since the snapshot is stamped with this run's `ALGORITHM_VERSION`
- Records a `PipelineRun` with phase timings, counts, and any per-member errors
- Runs a SHA-256 fingerprint over all analysis source files (docstring-stripped ASTs, so comment-only edits don't count); if changed since last run, clears `AnalysisCache` and `LearnedClassification` so stale results from the old code are not served

---

## Caching Architecture

Three independent caching systems serve different purposes:

```
┌────────────────────────────────────────────────────────────────────┐
│  ApiCache  (SQLite: api_cache)                                     │
│  Key: (tier, endpoint+params_hash)   TTL: 72h (configurable)*      │
│  Stores: raw API JSON, verbatim                                    │
│  Cleared: never (source data is immutable; re-fetched after TTL)   │
│  Purpose: replay pipeline without re-hitting external APIs         │
└────────────────────────────────────────────────────────────────────┘
* Per-entity fetches expire at a per-key point in the last third of
  their TTL, so one run's entries don't all come due on one night; a
  bill's own records are kept until the hourly bill refresh sees the
  bill change (30-day backstop). See AGENTS.md, "Data Pipeline".

┌────────────────────────────────────────────────────────────────────┐
│  AnalysisCache  (SQLite: analysis_cache)                           │
│  Key: (prompt_version, SHA-256(input))                             │
│  Stores: LLM JSON output, verbatim                                 │
│  Cleared: when source file fingerprint changes (code update)       │
│  Purpose: skip LLM calls for unchanged inputs on warm reruns       │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│  LearnedClassification  (SQLite: learned_classifications)          │
│  Key: (entity_text, entity_type)                                   │
│  Stores: classification + confidence + source                      │
│  Cleared: when source file fingerprint changes                     │
│  Purpose: cross-run entity memory (kNN bootstrapping, audit trail) │
└────────────────────────────────────────────────────────────────────┘
```

The fingerprint check at pipeline start compares a SHA-256 of every analysis module's docstring-stripped AST to the hash stored in the last `PipelineRun`. If they differ, `AnalysisCache` and `LearnedClassification` are cleared so updated logic produces fresh results. `ApiCache` is never cleared by fingerprint — source data doesn't change when analysis code does.

In front of all three, public read endpoints answer with an ETag hashed from the uncompressed response body, and a matching `If-None-Match` gets a 304 (`app/api/cache_headers.py`). The ETag middleware runs inside gzip, whose header carries a timestamp; hashed outside it, the same body got a new ETag every second.

---

## Data Pipeline: Design Rationale

The pipeline is structured around a specific set of constraints that shape every decision.

### Why a Nightly Batch Pipeline?

A 100-senator + 435-representative full refresh requires 4–6 hours cold (warm: 45–90 minutes). Online/streaming processing is not viable at these volumes on the target hardware: two sentence-transformer models occupy ~90 MB each and the LLM ~900 MB (a separate service, used only by the Action Center — the member and justice pipelines make no LLM call). Batching allows us to control memory precisely, while a separate hourly pipeline handles the Action Center's lower-latency requirements.

Each pipeline holds a database-level lock rather than a process-level one: a run row with `status = "running"`, which a partial UNIQUE index lets only one process insert at a time (`run_tracker.acquire_pipeline_lock_why`), so two backend processes overlapping during a Swarm rollout can't both start the same pipeline. Pipelines run as threads of the pipeline process (of the single backend under plain `docker compose up`), so a restart kills them without letting them record it; on startup that process marks every pipeline's leftover `running` row `stale` (`main._invalidate_orphaned_pipelines`), sparing only a Senate run whose lease still holds (it may be live in the other task), and a row that has written no progress for 6 hours, or is older than 24, is cleared at the next acquisition (`run_tracker.row_is_stale`: a row's last progress is its start plus the `elapsed_seconds` every step update advances, so a long run that is still working, like a House run on a cache warm-up night, keeps its lock; the 6 hours clear the longest step measured to write nothing until it ends, 102 minutes, and a cold Oyez crawl's 5 hours). `check-and-deploy.sh` does not deploy while any pipeline is running. A run that fails keeps its exception on its row (`error_detail`: the step, type, message and the last 4 KB of the traceback, credentials redacted), shown on the admin Pipelines tab and summarised in the failure alert, since a deploy replaces the container's log.

### Generated data refreshes itself

The constants the code reads from `backend/app/data/` that a script computes from public data are regenerated automatically, by that same script, on their own cadence (`app/data_refresh.py`, a daily job outside the nightly chain): poll closing times for each election year, the voter calendar re-dated for each coming election (each rule moved by whole weeks from one Tuesday election to the next, every cited page re-read for the new dates, a deadline it can't confirm left out and the state's page saying so), the FEC's party codes, each Congress's committee jurisdictions from the rules the chambers adopt (the House's found on the Rules Committee's page, not typed) and the committee-routing fit refit whenever they change, state population from each new census, the bill-title word table for the current and previous Congress, the reference-coverage floors, and donor-name casing. Each candidate is gated (shape, plausibility, drift against the copy in use); only one that passes replaces it, on the data volume, with its provenance (`_refreshed`); a failure keeps the last good copy and raises an ops alert for that file. Files a script can't regenerate unattended raise a staleness alert instead, saying what a person has to do: a new CES survey (its question ids change), a new Maddison release (found by search), the Bluesky posting windows, the position-confidence and commemorative calibrations once their next Congress's data is complete (a refit there is a method decision), and the district lean once the next presidential election's returns are out.

### Why an Adversarial Data Architecture?

Government data sources are not designed for machine consumption. Congress.gov returns inconsistent bill title formats; FEC records contain duplicate committee entries; senate.gov lacks a public API. The **FETCH → TRANSFORM** separation isolates source-specific parsing from domain logic. Every raw API response is stored verbatim in `ApiCache` (never cleared, TTL=72h), so the pipeline can be replayed deterministically against historical snapshots — essential for debugging and auditing.

Rate limits are enforced at the source level (Congress.gov: 1.2 RPS, FEC: 0.25 RPS, GovInfo: 1.0 RPS) with per-API backoff, not global throttling. This prevents a slow FEC response from stalling the Congress.gov queue.

### Why Embedding-First, LLM-Sparingly?

The most important architectural decision is what *not* to use the LLM for.

Per-senator/rep analysis (Phase 3) is fully deterministic — no LLM call at
all. Two LLM-based steps used to live there (PAC-donor classification,
narrative summary generation, plus a separate campaign-promise-tracking
feature); all were removed in 2026-07 after live audits found their output
unreliable regardless of prompting approach. See `cross_reference.py`'s
module docstring for the measured cost/quality numbers that motivated the
removal.

The LLM is still used where the output genuinely requires natural-language
synthesis from unstructured input: Action Center claim location (it
points at an attributable sentence in a clustered article; the text shown is
the source's own, checked verbatim), national-monitor titles and descriptions
(checked against the issues that opened the monitor), timeline period summaries, locating who did
what in a race's coverage for its post (verbatim spans), and on-request
Explore document summaries.

Everything else uses geometric methods in sentence-embedding space:

| Task | Method | Rationale |
|------|---------|-----------|
| Bill policy area | Prototype similarity, kNN over the learning store for the residual (18 areas incl. procedural) | Deterministic, explainable, ~100ms vs. ~30s |
| Donor industry | Tiered: FEC metadata → learning store → prototype similarity → kNN | Generalizes from precedent; full audit trail |
| Party alignment | The roll call's actual party split; content similarity to party platform positions only where no roll call exists | Loyalty is defined by how the parties voted (see "Party Alignment" below) |
| Donor–vote connections | Industry funding share × policy-anchored vote similarity | Transparent, reproducible threshold |
| Key vote selection | Composite score: party deviation + donor overlap | Fully deterministic |
| Monitor deduplication | Title cosine on the similarity model ≥ 0.75 | No two different-topic update titles reached it (measured 2026-10-08); merging deletes a monitor |
| Issue-to-monitor matching | Issue title against the monitor's title and description on the classification model ≥ 0.71, nothing else | The LLM verdict that used to decide 0.71–0.80 let 17 of 18 off-topic updates through when replayed (2026-10-09) |
| Issue deduplication | Post-LLM title embedding similarity | Catches LLM-generated near-duplicates missed by pre-filtering |
| Issue topic continuity | The same content, a near-identical title, or a shared source article with titles agreeing (`SHARED_SOURCE_TITLE_SIM`; a live blog's several-story headline compared story by story) or whose headline titled the existing issue, across a 2-day lookback | Shared names and numbers were 25% precise on 65 judged pairs; a shared source alone merged 45 of 94 different-story pairs (both 2026-10-08) |

LLM calls per full nightly run: 0 (Phase 3 is fully deterministic, see above). The LLM runs on its own schedule for Action Center claim location (hourly, a handful of calls per run). Embedding operations per full nightly run: ~50,000. The pipeline is a **semantic classification and retrieval system** that uses a language model only where natural-language synthesis is unavoidable.

### Why Local Inference?

1. **No data exfiltration.** Senator donor records and issue analyses never leave the local network.
2. **Cost at scale.** Cloud API pricing for even a modest daily call volume runs to hundreds of dollars annually. At 0 marginal cost on the Pi.
3. **Reproducibility.** Model weights are pinned. An analysis run today produces identical output to one run six months ago on the same input. Cloud-hosted models update without notice.
4. **Latency independence.** No rate limits, no network jitter, no API quota.

The choice of LFM2.5-1.2B-Instruct over larger alternatives (7B+) is deliberate. The inference tasks here are mostly structured extraction — pointing at an attributable sentence or span in an article, which is then checked verbatim, or a yes/no judgment — not open-ended generation; recommended actions are built from bill and source data, never by the model. Empirically, a ~1B-class model produces acceptable quality on these tasks in a few seconds per call on ARM, vs. 25–45s for a 7B model. The quality ceiling is determined by the structure of the prompt, not model size.

---

## Classification Strategy

Classification decisions — what industry a donor belongs to, which direction a bill leans, whether an entity should be skipped from donor attribution — are made by embedding similarity against natural-language category prototypes, kNN over an accumulated reference corpus, or exact structured-data lookups, never by an arbitrary keyword-to-category judgment call. The pipeline uses a tiered strategy following computational parsimony (Jurafsky & Martin 2023):

| Tier | Technique | Speed | Used For |
|------|-----------|-------|----------|
| 1 | FEC metadata / learning store exact match | Instant | Donor types, previously classified bills and donors |
| 2 | Sentence-transformer embeddings (cosine similarity) | Fast | Bill policy areas, industry, party alignment, donor types, stance direction, procedural detection, commemorative detection, skip entity detection, employer filtering, memo transfer detection, whether two Action Center claims restate one fact (`claims.distinct_claims`) |
| 2b | SVD / PageRank on cosponsorship matrix | Fast | Ideology scoring (Tauberer 2012), legislative leadership (Brin & Page 1998) |
| 3 | k-Nearest Neighbor in embedding space | Fast | Remaining unclassified donors, bill classification from reference corpus, Action Center relevance (against feed articles their publishers filed, refit daily), whether a claim's actor span the capitalised-word rule refuses is a party that can act (`post_composer.is_acting_party`). A donor's industry is taken from the neighbours only when all seven agree (2026-10-09: on 177 record-labelled PAC sponsors the prototype tier abstains on, a plurality vote was right 29% of the time at every similarity floor; unanimous neighbours 8 of 8); otherwise the donor stays unclassified |
| 4 | LLM (LFM2.5-1.2B-Instruct via llama.cpp) | Slow | Natural-language text only: Action Center claim location, monitor decisions, timeline summaries, race-post spans, Explore summaries — never a classification feeding a score |

Key embedding-based classification features:
- **Semantic prototypes** define each category via natural-language descriptions, not keyword lists. The embedding model matches entities to the nearest prototype by cosine similarity.
- **PAC decontextualization** detects "[Industry] PAC" naming patterns via a PAC-context prototype and margin-based runner-up selection, replacing regex suffix stripping.
- **Donor name casing** comes from usage: the FEC prints names in capitals, and a word that the Federal Register's prose writes in capitals or with an interior capital ("UCLA", "AFL-CIO", "McDonnell") keeps that form (`app/data/name_casing.json`, `scripts/build_name_casing.py`); any other word gets a first capital. On 348 capitalized organization names judged by hand it fixed 58 word occurrences that first-letter capitals got wrong and broke 6 ("(Aka", "Co" for Colorado).
- **One employer typed two ways** ("ASSOCIATES" / "ASSCOIATES", or the same name but for punctuation) is one donor (`normalize_finance.merge_misspelled_employers`): a single differing word is a misspelling when its two spellings have a Ratcliff/Obershelp ratio of at least 0.80, are five letters or longer and carry no digit. Calibrated on 168 hand-judged pairs from the FEC employer totals cached for 654 committees: right on an estimated 94.1% of the pairs it joins, finding 78.7% of the one-word misspellings; a guard on how often each spelling appears was measured and not used (nearly every spelling appears in one committee's list).
- **Self-funded detection** uses SequenceMatcher ratio (Ratcliff & Obershelp 1988) for fuzzy name similarity instead of exact string matching.
- **Batch skip detection** classifies employer names and memo texts against skip prototypes in vectorized batches for performance.
- **Semantic category normalization** maps stale/unknown category labels to valid industries via embedding similarity, replacing a hardcoded alias table.
- **Stance direction** is derived primarily from embedding similarity against pro/anti/neutral action prototypes — see the disclosed exception below.
- **Procedural bill detection** uses embedding similarity against a procedural prototype instead of substring matching.
- **Claim actors** (`post_composer.is_acting_party`): a claim the Action Center or an election post composes must name a party that can act — a person, institution, official body or group of officials. A span with a capitalised word passes (see the disclosed exception below); one the rule refuses, unless a pronoun leads it, is compared with natural-language descriptions of an acting party and of what is not one (`ACTOR_PROTOTYPES`, `NON_ACTOR_PROTOTYPES`; a possessive is judged by its head), and passes when its margin reaches the lowest value no data-supplied non-actor reaches: the claim calls' own predicates and the sums and percentages in their sources. The threshold is refit daily from the recorded calls (`refit_actor_threshold`; `app/data/claim_actor_threshold.json` until the first refit). No span is labelled by hand. Replayed on the 328 recorded claim calls it composes every claim the kNN vote over 970 hand-labelled spans did, and one more.
- **Action Center relevance** (`app/policy_relevance.py`) labels its own reference: every feed article it judges is stored with the label its publisher's filing gives it (a politics-only feed or a politics desk's URL section = relevant; a world feed or a foreign or arts desk = not) or that naming a sitting member, justice, the president or a federal agency gives it (relevant). The vote is the similarity-weighted share of relevant articles among the k nearest, with k and the threshold refit once a day by leave-one-out F0.5. No hand labels.
- **Commemorative bill detection** (`analyze/commemorative.py`) gives Legislative Effectiveness Volden & Wiseman's 1x tier for bills like post-office namings and Gold Medals: the margin between commemorative and substantive prototypes on the similarity model, over a threshold calibrated against V&W's own per-member commemorative counts (`scripts/calibrate_commemorative.py`, written to `app/data/commemorative_calibration.json`).

### Disclosed exceptions

A small number of narrow, code-commented precision pre-filters run ahead of the embedding classifier for specific, measured embedding-model weaknesses. Each is a fast-path for a case the embedding model demonstrably mis-scores, not a replacement for it — everything not caught by the pre-filter still goes through the real classifier below it:
- **Bill stance direction** (`bill_analyzer.py::derive_stance`): a tier-0 check against the same word set used to build the pro/anti embedding prototypes, used only to break ties or lower the acceptance margin when the embedding result is already ambiguous. Verified empirically (2026-07, n=2979 real bill titles): removing it changes 1.5% of outcomes, always by recovering a genuinely directional bill ("STOP CCP Act") the embedding alone scored as neutral.
- **Hotel/lodging industry classification** (`industry_classifier.py::classify_industry_with_provenance`): hotel brand names measurably score as MEDIA rather than REAL_ESTATE in the embedding space (a specific, verified anomaly); a small brand/suffix check corrects this before the embedding path runs.
- **Claim actors with a capitalised word** (`post_composer._looks_like_an_actor`): an actor span with a capitalised word, not led by a determiner or pronoun before a lowercase word, passes without the embedding margin above. Unlike the others this admits rather than filters, and it stays because an embedding measurably cannot do its job: a kNN vote alone, at the same precision bar, refused 233 of 401 labelled actors the rule admits (the feed's most frequent actor among them; a surname, an acronym or an organisation's name says little to a sentence embedding), and on 264 recorded claim calls composed 82 claims to the rule's 128 (2026-10-15). A span led by a pronoun ("its spokesman", "your representatives") is refused outright: the pronoun is a function word the embedding can't weigh, and the rendered claim couldn't say whose.
- **PAC and payment-processor detection** (`donor_classifier_ai.py`): an org name containing "PAC" (an FEC filing convention) or matching a handful of named payment-processor brands (ActBlue, WinRed, Anedot, etc. — a closed, real-world set, not a category judgment) is caught by a keyword check ahead of the embedding path, since ALL-CAPS FEC-formatted names score inconsistently against mixed-case prototypes.

These four are documented at the point of definition in code, alongside purely structural lookups that decode already-known facts rather than classify anything: the SIC manual's hierarchy for a traded security's industry (`sec_tickers.SIC_INDUSTRY` — decoding the code the SEC assigned the issuer); an FEC entity-type-code map (`CCM`→`CandidateAffiliated`, etc. — decoding an enum FEC itself assigned); the FEC committee type and designation codes that mark party, candidate, joint-fundraising and leadership committees as political money (`fec.py::is_political_committee` — the FEC's own registration, not a reading of the committee's name); the industry of any other PAC where the records state it (`fec.py::structured_industry`): a labor organization's PAC (the FEC's organization type `L`) is LABOR_UNIONS, a PAC registered with no organization type and no connected organization (a nonconnected committee) is POLITICAL (v6.31; on 80 such PACs judged by hand, 69% correct against the name classifier's 35%, McNemar p < 0.001 — the misses are partnership and physician-group PACs, whose money then counts toward no industry), and a corporation's PAC whose sponsor is an SEC-registered issuer takes that issuer's SIC industry, its name matched exactly once legal-form words are dropped (`sec_tickers.issuer_key`; measured 2026-10-08, the name classifier had put 21% of labor organizations' PAC rows in another industry and agreed with the SEC's code on 59.5% of matched corporate rows, and `scripts/evaluate_industry_classifier.py` measures it against these record-derived labels); the industry a donor's stated occupation works in (`transform/occupation_industry.py`, read against `app/data/occupation_industry.json.gz` from `scripts/fetch_occupation_industry.py`: an occupation counts toward an industry only when a majority of the people in it work there, by the Census Bureau's ACS microdata, and an occupation string is recognized only by an exact match with one of O*NET's titles for it — the industry side is a crosswalk from NAICS codes, the federal taxonomy the Census uses, to Civitas's industries, since the embedding classifier filed "Computer Systems Design" under HEALTHCARE, and matching an unrecognized string to its nearest occupation by embedding was right about the industry 44-71% of the time, so it is left unattributed); a Congress-number→majority-party table for past congresses (the current congress's majority is read from the live roster each run) used for effectiveness baselines; and a name filter on lobbying-registry results (`lda.py::is_same_client` — the registry's client search is loose, so a filing counts only when its client's name begins with the donor's, or names it after "on behalf of"; measured on 48 clients' 2025 filings, and a similarity ratio was rejected because it accepted the American Veterinary Medical Association for the American Medical Association). A committee's name read as a union is set aside when the FEC records its sponsor as another kind of organization (`fec.committee_industry`; of 75 such learning-store labels, a hand-judged 50 held no labor organization). The filter does not decide that a client *is* the donor — a subsidiary and a separate company can share a name — so every amount and linked bill is shown with the client name it was filed under.

### Retrieval-Augmented Classification (RAC)

A persistent learning store (SQLite `LearnedClassification`) accumulates labeled classifications across pipeline runs. A vector reference corpus (the `vec_bills` sqlite-vec table) grows with each run. Together they implement a retrieval-augmented classification pattern: past decisions inform future ones, reducing both latency and error rate over time (Lewis et al. 2020).

Confidence levels distinguish source quality (`donor_classifier_ai._CONFIDENCE_MAP`):
- `1.0` — FEC structured metadata
- `0.92` — an embedding cross-check that corrected a previously learned industry
- `0.9` — embedding prototype similarity
- `0.75` — kNN vote. Stored for lookup, but never used as a kNN reference example: only labels from an upstream tier vote, so one run's guess can't become the next run's evidence.

This enables selective re-verification: low-confidence classifications from previous runs can be re-evaluated when related code changes.

**Version-aware artifact management** ensures updated analysis algorithms always produce fresh results. At pipeline start, a SHA-256 fingerprint of all analysis source files (their docstring-stripped syntax trees, so comment edits don't count) is compared to the stored hash from the last run. If the code has changed, stale artifacts (analysis cache, learned classifications, kNN reference corpus) are cleared so updated algorithms start clean. The API cache (raw Congress.gov / FEC / GovInfo responses) is never cleared — it reflects source data, not processing logic.

### Party Alignment (the Roll Call's Split)

Whether a member voted with or against their party is decided only by **how the parties actually voted on that roll call** (`stamp_roll_call_outcome` records `partySplit`): a roll call is party-labeled when at least 65% of one party voted Yea and at most 35% of the other did, and a member breaks when they vote with the other side. A bill whose content reads partisan but passed with both party majorities is not a party-line vote, and since v6.18 a vote with no recorded roll call carries no party label at all (the bill's content lean used to stand in, so voting for a bill that read as the other party's counted as a break whatever the parties did). Each stored vote records its roll call (`key_votes.roll_call`), and the vote API returns the Congress record's question, date and per-party tally with it, so every break on a profile shows why it is one. Since v6.19, housekeeping questions never count either way (`normalize_votes.is_housekeeping`): quorum calls, adjourning, approving the Journal, the House's previous question, motions to table or to recommit, and (since v6.28) the House's motion to commit, the motion to recommit's twin for a bill no committee reported. They split on party lines as a matter of course, so voting with the other side on one says little about the member; in the 119th Congress they were 31% of the House's party-line roll calls and 3% of the Senate's. Rule votes, cloture and nominations still count: they decide whether a bill reaches the floor, whether it gets a vote, and who serves. Since v6.20, Constituent Alignment's break rate is each member's party-line record over every roll call the chamber recorded this Congress (`party_line_record.party_line_records`, stored per member as `party_line_record`), not the sample of recent votes stored for the profile. A break counts only toward the other party: on that roll call, the party's members who broke sit on average nearer the other party (first-dimension position from the chamber's Voteview data) than the party does. Since v6.27 each position is weighted by its reliability and read from its party's mean, and until a member's new record is full (`prior_until_votes`, 200 votes, a convention) a full record from the last Congress, where they have one, decides their side (before the new Congress's positions pass the gates, everyone's last positions do), and a member with no usable position this Congress is read on the last Congress's (a stated choice); once the new positions are in, neither applies to a member who switched parties during this Congress or between the two, whose last positions were cast in their old party; a position recorded under the other major party is never read for the member. A vote against the party from its own flank is listed on the scorecard but not counted, since how far toward the flank a member sits is measured by position congruence (scored once the new Congress's positions pass the gates). Each measure counts once: a nominee's cloture and confirmation votes, or a bill's motion to proceed, cloture and passage, are one decision (`measure_key`); amendments and motions to commit or waive are each their own question. In the 119th Senate 37% of roll calls repeat a measure already voted on. Both rules were tested against election results (`docs/research/constituent-alignment.md`, sections 11 and 12).

For partisan depth (the lean bar and its per-area breakdown), which is not a count of breaks, each Yea counts toward the party the bill leans to and each Nay toward the other, in every policy area the bill touches, weighted by the area's confidence (a housekeeping roll call is procedural, not a vote on its bill, since v6.28). The lean is the same `partyLeaning` loyalty reads: the roll call's party split wherever one was recorded (`refine_with_vote_data`), so a bill both parties passed counts toward neither. Only where no roll call recorded each party's vote does the bill's content decide its lean, with a nearest-centroid classifier (Rocchio 1971) in sentence-embedding space:
1. Each party's platform positions per policy area are embedded as seed centroids
2. Each run blends the seeds with the bills earlier runs labelled for each party, in both chambers, by the parties' actual split where there was a roll call (`_build_data_centroids`; both chamber pipelines rebuild them at start since v6.28)
3. Bill text is embedded and compared to both party centroids
4. Stance direction (pro/anti) disambiguates policy-area overlap

One procedural exception, read from the chamber's own result field: a **majority leader** who votes with the prevailing side against their own party — a Nay on a motion the chamber recorded as rejected, or a Yea on one carried over their party's opposition — is doing so to be able to move to reconsider (Senate Rule XIII; House Rule XIX cl. 2), so the vote carries no party signal. It applies only within the member's tenure as majority leader (`leadership_tenures.json`); the Speaker and minority leader are never exempted. In the 119th Congress every off-party vote by the two chambers' majority leaders (17 in all) was such a switch.

An independent is read with the party they caucus with. For a representative that is the caucus the House Clerk records (`<caucus>` in MemberData.xml, `house_clerk.parse_caucuses`): the chamber's own record, which can differ from the member's party (in 2026-10 one independent representative's read R; the member chairs a subcommittee, a seat the majority conference assigns). The Senate publishes no such field, so an independent senator's caucus is inferred from voting and cosponsorship patterns; inference also decides for a representative when the Clerk's list can't be read.

---

## Action Center Pipeline (Hourly)

The Action Center is intentionally separate from the nightly pipeline because it operates on different timescales and data characteristics:

```
Every hour at :15
       │
       ▼
  1. FETCH ───── RSS (NPR, PBS, BBC, The Hill, Politico, Roll Call —
       │         7 feeds across 6 newsrooms; NPR's two desks count as one).
       │         AP's mirror went stale on 2026-09-09 and apnews.com walls
       │         off automated readers, so AP was dropped (2026-10).
       │         A feed or trending source that fails, or has nothing new
       │         for a day longer than its longest gap in 60 days (and the
       │         48h window), raises an ops alert (check_source_freshness).
       │         Under a week of history, the longest gap among feeds that
       │         have a week stands in, so a feed first seen midweek isn't
       │         stale on its first weekend.
       │         + Google Trends + Bluesky trending
       │         48-hour article window; direct URLs only (no redirect wrappers)
       │         Reddit was a third trending source until 2026-09: it now
       │         requires OAuth for datacenter traffic and 403s everything
       │         else, so it was retired rather than left silently empty.
       │         A source that FAILS is reported as failed, never merged in
       │         as a source that simply had nothing to say.
       ▼
  2. FILTER ──── Drop multi-story digests (fetch/news_digest.py, shared
       │         with race coverage: a newsletter section, a briefing's
       │         title, a body listing unrelated stories, or a headline and body telling
       │         the same unrelated stories one item each — "<A>. And, <B>":
       │         5 of 1,473 stored articles, all briefings), then a kNN
       │         vote among the k nearest feed articles already judged
       │         (feed_articles), each labelled by its publisher's filing
       │         (politics feed or desk = yes, world feed or desk = no)
       │         or by naming a sitting official or federal agency (yes);
       │         k and the vote threshold refit daily, nothing labelled by
       │         hand (app/policy_relevance.py). Precision 0.88-0.96
       │         against the hand-label vote it replaced (0.89-0.98), and
       │         one more issue than it over 2026-10-02..15
       │         (docs/research/action-center-relevance.md)
       ▼
  3. CLUSTER ─── Complete linkage on headline + first 200 characters of
       │         the description (retrieval model, day-centred), cut at
       │         0.71: EVERY pair in a cluster must clear it, so a chain
       │         of look-alikes (floods ~ storm ~ epidemic) can't form one
       │         story. Single linkage plus a centroid merge from 0.20 built
       │         2026-09-27's chimeras: a Bangkok-floods title over a Hawaii
       │         hurricane lede and facts about a nor'easter and Fiji's HIV
       │         epidemic. Two articles whose own text dates the event
       │         two or more days apart are never joined, and the cut is
       │         refit daily: the lowest no stored pair about one actor on
       │         such different days reaches, plus a stated margin of 0.10
       │         for same-day different acts no fact labels
       │         (action_center.refit_cuts; 0.60 + 0.10 on 2026-09-28..
       │         10-15, where 530 hand-judged pairs had put it at 0.71). The same-run
       │         duplicate check drops a look-alike cluster rather than
       │         appending it (appending folded Hurricane Nolo into the
       │         nor'easter on that feed).
       │         Titles are compared with the day's mean headline vector
       │         removed. The coherence filter used to remove the CLUSTER's
       │         own mean instead, which erases
       │         the topic the articles share: live runs kept 1 of 5
       │         same-story articles, and on 2026-09-27's feed the filter
       │         kept 28 articles where the day's mean keeps 48.
       ▼
  4. RANK ────── score = 0.40 × (civic actionability)
       │                 + 0.35 × (source breadth)
       │                 + 0.25 × (trending score)
       │         Actionability leads: officials mentioned + similarity to the
       │         ingested civic-document corpus, not hand-authored keywords
       │         Try ranked clusters in order (up to CANDIDATE_POOL = 6)
       │         until MAX_ISSUES = 2 publish — a cluster that fails a gate
       │         falls through to the next instead of ending the run. A
       │         cluster is skipped as a duplicate only of one that
       │         PUBLISHED this run: on 2026-10-08 a two-story live blog
       │         ranked first, both stories were dropped as its duplicates,
       │         and it then published nothing, two runs running
       │         Each published issue records its principal actor (the
       │         official named first in its headline, else a kNN class:
       │         story_actor.py); the admin charts their weekly Herfindahl
       │         index. Measured only: a one-per-person daily cap and
       │         demoting stories with no government actor were replayed
       │         over every run since 2026-09-24, held out by date, and
       │         found no replacement story to publish instead
       │         Each issue also records the states and races its own words
       │         name (action_center.ballot_links): a confirmed nominee by
       │         full name (a name two races' nominees share only beside its
       │         state), a state's only nominee of a surname beside the
       │         state's name, or a seat phrase ("Michigan Senate race"). The issue shows "On the ballot: Georgia · Senate
       │         race (Roe) · <next deadline> · Register · Check your
       │         registration" from the voter calendar and the state's own
       │         lookup, read when served (app/ballot_links.py). Measured on
       │         548 stored issues: 64 race links, every named person that
       │         race's nominee; a state alone is never linked (16 of 22
       │         such links were on topic, under the bar)
       │         (docs/research/action-center-ballot-links.md)
       ▼
  5. EXTRACT ─── The model LOCATES an assertion in one article; it never
       │         writes the sentence. post_composer.py checks both spans
       │         appear verbatim, that the source asserts one OF the other
       │         (adjacency — two true fragments can otherwise be assembled
       │         into one false sentence), that the span runs to the end of
       │         its clause, and only then renders the source's own words
       │         from actor through predicate — any words between them
       │         included, so "OpenAI agent made" never becomes "OpenAI
       │         made". Each article is read as headline + summary with
       │         the headline closed as its own sentence: joined by a bare
       │         newline, a claim ending at the headline's end read as cut
       │         off, and 2026-09-27's measured yield was 5 claims from 40
       │         articles (14 with the break marked), so most clusters
       │         missed the two-claim bar and nothing published for days.
       │         A predicate the model cut short ("sues", "grants review
       │         of") is read on in the source to the clause's punctuation
       │         — still verbatim, refused where it can't tell where the
       │         clause ends ("Sens." might be an abbreviation), and a
       │         period inside an open bracket ("(R-Ark.)") is not that
       │         end. A sentence with an unmatched bracket is refused (an
       │         actor named inside one composed "FDA) won't publish");
       │         one opening mid-sentence starts with a capital; a quote
       │         the source closes on a comma ends with a period. Each
       │         article's summary contributes its claim too, after the
       │         headlines: 2026-10-01, 78 of 300 clusters in three days
       │         were dropped as too few facts, 7 of 10 sampled failures
       │         were cut-short predicates. An -ing predicate is a clause
       │         when the source's own words between the spans end in a
       │         form of "be" ("The administration is seeking ..."):
       │         replayed on 356 recorded answers, 162 to 168 verified, all
       │         true. Since 2026-09-24, 37 of 41 issues carry one fact:
       │         each article gives at most two claims and most clusters are
       │         one or two articles (docs/research/action-center-ballot-links.md).
       │         The actor must name or describe a party that can act: a
       │         capitalised name passes, anything else ("a federal judge",
       │         "federal agents") its margin over actor/non-actor
       │         descriptions, threshold refit daily (see Classification
       │         Strategy). An actor inside a quotation, or
       │         one that only attributes a quotation ("…," X said in a
       │         statement), is refused: the first is the speaker's account
       │         published as fact. Two claims that restate one fact count
       │         once (claims.distinct_claims: similarity-model cosine >=
       │         a cut refit daily as the lowest no pair of lines from two
       │         issues published together reaches, plus 0.10: 0.79 on
       │         2026-10-16), so a headline and its own lede can't pass
       │         the two-claim gate as two facts.
       │         Title = the top article's real headline, never a
       │         livestream or video page's: an article whose headline
       │         kicker (the label before its first ": ") scores past the
       │         day's cut ("WATCH LIVE", "WATCH"; embedding margin over
       │         news-report and live-blog prototypes) gives no title,
       │         fact or source, and a cluster of nothing else waits for a
       │         report. The cut is fitted daily on publishers' own
       │         filing, no hand labels: stored feed articles under a
       │         video section (The Hill's /video-clips/, BBC's
       │         /news/videos/) against the same feeds' articles, max F1
       │         (0.120 on 2026-10-16: 18 of 18 filed-video kickers, 2 of
       │         126 articles, both Hill livestreams filed as news).
       │         Replayed on 1,441 stored headlines: 20 flagged, 10
       │         livestreams, 9 recorded clips, 1 Civitas vote-draft title
       │         (never filtered); 2 confirmed titles change (one to PBS's
       │         report, one withheld). Summary = the
       │         single best claim. Facts = the supporting claims, each
       │         carrying the outlet it came from, shown on the issue as
       │         "Media coverage".
       │         A cluster with no attributable assertion produces NO issue:
       │         MAX_ISSUES is a ceiling, not a quota. Publishing anyway is
       │         what produced "This coverage tracks the race and related
       │         developments." Trying only the top two made the ceiling a
       │         cap on ATTEMPTS: when both failed a run published nothing,
       │         and the next hour ranked the same two first again (five
       │         issues on 2026-09-23, none on 2026-09-26).
       │         Post-composition title deduplication (cosine_sim > 0.92)
       ▼
  6. PERSIST ─── Topic-keyed matching: each unique story maps to one permanent
       │         DB row regardless of rank changes or brief displacement.
       │         Same story → update content; repost only when a newer article
       │         also brings new information (a name, figure, or development).
       │         Otherwise the rank updates silently and nothing is posted.
       │         Brand new story → create new row.
       │         Full story (issue page): built here, while the cluster's
       │         articles exist. Each article is asked once more about its
       │         summary alone (with the headline in view the model always
       │         picks the headline); every verified claim, headline and
       │         body, is listed under the outlet that made it. Nothing is
       │         written. None when it would only repeat summary + facts.
       │         It replaced ~800 chars of model prose that published a
       │         relationship no source stated (issue 748), a wrong office
       │         (750) and filler (751); stored prose was cleared (0004).
       ▼
  7. ENRICH ──── sqlite-vec semantic search → link related bills/senators
       │         Related documents need title similarity ≥ 0.49 (min-error
       │         on 299 hand-read stored links; at the old 0.33, 189 were
       │         unrelated, e.g. Miami boating safety zones on a story about
       │         a Florida golf club)
       │         A bill number counts only when the words around it name
       │         that bill (lobbying_records.names_bill); a member's bare
       │         surname only when no other member shares it
       │         Actions (template text around stored data, no model):
       │         - the committee with jurisdiction, per chamber or none
       │           (app/committee_routing.py): House Rule X / Senate Rule
       │           XXV subjects (hubness-corrected similarity) blended with
       │           the votes of the nearest of 460 hand-labelled issues,
       │           named only when both signals pick the same committee;
       │           held out, 82% precise (bootstrap 0.67-0.94), naming one
       │           for 22% of issues; the rules alone, 38%. The bar of an
       │           exact lower 95% bound >= 0.80 is met at no threshold, so
       │           the row says "likely". The chair and ranking member are
       │           read when the issue is served
       │           (app/data/committee_routing_*.json; the calibration file
       │           holds the whole held-out curve)
       │         - "Comment by <date>": a proposed or final rule still open
       │           whose title clears the same 0.49; served only while open
       │         Replayed on the last 30 days of issues (2026-10-11), the
       │         share offering only "find your members" fell 80% -> 64%
       │         The Today tab's open comment periods (/api/action/
       │         open-comments) lead with any a recent issue links, then
       │         rules (the Register's own type) by readers (its page_views,
       │         read once a day into fr_page_views), then notices, then
       │         paperwork notices (app/comment_periods: the Register's
       │         table-of-contents subject for PRA collections, read with
       │         the page views into fr_toc_subject; 303 of 615 open on
       │         2026-10-17, precision 303/303, recall 303/304); by
       │         deadline, 6 of the top 10 were PRA renewals and a waiver
       │         petition (2026-10-11), by salience none were
       │         Every open comment period, in the same order:
       │         /explore?comment=open (GET /api/explore?commentable=true
       │         with no q), the target of the homepage's and the Today
       │         tab's "Every open comment period" links; paperwork
       │         notices come apart under `paperwork` and are folded
       │         ("Paperwork notices (N)"), as on an agency page. Each
       │         card shows the abstract's first sentence verbatim and a
       │         days-left bar with the same fact in words
       │         Every issue but a count issue also offers the reader's
       │         own members on the page: a state picked from a list
       │         loads each office's phone (tel:) and contact form, how
       │         each voted on the issue's roll calls, and a fixed call
       │         script (GET /api/action/issues/{id}/members?state=; the
       │         state is kept nowhere). Before 2026-10-14 this was a
       │         link to the directory: 5 taps over 2 page loads to a
       │         phone number, now 2 on the page
       │         Related votes in Congress (app/roll_call_links.py): each
       │         issue and monitor update is matched to the roll calls
       │         taken 3 days before to 2 after, headline against the
       │         vote's title (an amendment's stated purpose), mean of the
       │         MiniLM and Arctic cosines >= the calibrated 0.64, where the
       │         exact lower 95% bound peaks (0.87 over 8,162 hand-labelled
       │         pairs, names masked; held out 53/57 right, bound 0.83,
       │         recall 0.22). Drift-watched; structural links are kept as
       │         labels for recalibration.
       │         Stored as refs at write time, the last week rechecked and
       │         200 unmatched rows backfilled per run; the members panel
       │         shows each member's vote on them and the call script
       │         cites it; a monitor's "On the record" charts them
       │         Who decides next: a linked bill's stored stage
       │         (bill_decider) or an open comment period's agency
       │         Members named by full name in the articles themselves
       │         are linked too ("named in the reporting")
       ▼
  8. MONITORS ── Detect cross-day recurring topics (title similarity ≥ 0.85)
       │         Create NationalMonitor records (matching issues on 5
       │         distinct days in 14, ≥3 unique sources); the LLM only
       │         names one, checked against its articles
       │         Add today's issues to monitors they match (title against
       │         the monitor's title and description ≥ 0.71, no LLM) and
       │         that share a distinctive entity with it (IDF > 2.0 over
       │         stored issues: "trump", in a third of them, never counts
       │         alone); stored updates re-checked each refresh
       │         Re-merge duplicate monitors (similarity-model title ≥ 0.75)
       │         No update for 7 days: watching; for 30 days: closed
       ▼
  9. TIMELINE ── Record daily TimelineEntry
       │         At week/month/year boundaries: LLM generates period summary
       ▼
 10. BLUESKY ─── Post the day's top two new/updated issues (at most two
       │         issue posts a day): the verified lede, verbatim (or the
       │         real headline if it doesn't fit), opening "Yesterday:" / "On
       │         <date>:" if the event predates today. Nothing model-written.
       │         Daily member spotlight: the member's most distinctive
       │         scorecard finding, then score and rank.  (Also runs on the early-abort paths above — it
       │         doesn't depend on the news)
       │         Repost + like outlet posts that match active issues.
```

**Why cluster before ranking?** Articles about the same event arrive from multiple outlets within minutes. Without clustering, every "top issue" would be the same story from NPR, BBC, PBS and The Hill. Clustering first, then ranking by source breadth, surfaces the most distinct newsworthy topics.

**Why filter at 0.20 cosine similarity?** The policy prototype filter is deliberately permissive. False negatives (dropping a real policy story) are worse than false positives. Borderline cases are handled downstream by extraction rather than by asking a model to be neutral: a cluster that yields no verbatim, adjacently-asserted claim simply produces no issue.

**When does a story become a monitor?** When today's issue matches issues on four other days of the last fourteen (headline similarity ≥ 0.85 on the classification model) and those issues come from at least three outlets. An issue keeps one row while its story continues, so this counts separate stories on separate days, not days on the board; counting every day a matching issue was current let one story carried for a week open a monitor. The floor was measured on 2026-10-09 by replaying creation day by day over every stored issue since 2026-06-26, each assigned by hand to a story, against the seven that ran for weeks (Iran, the midterms, AI policy, federal funding, immigration enforcement, tariffs and trade, Russia-Ukraine). On the issues to August 31 the old 0.83 opened 5 monitors, 4 of them one of those stories, and back-filled 11 updates about other stories into them; 0.85 opened 3, all real, back-filling 4. On the issues after, 2 of 3 were real at both floors. With the existing Iran monitor in place, the replay over the whole period opens two: AI policy (July 25) and federal funding (July 30). The midterms, immigration, trade and Ukraine stories are missed: their issues are different events whose headlines rarely reach the floor, and nothing tried (other floors, the similarity model, headline and summary, more or fewer days) found them without also opening mixed monitors.

**Why was there only one monitor?** Creation worked, but each new monitor vanished the same hour. The model that names a monitor was given "U.S.-Iran Conflict" as an example, and replayed on the production model it gave that name to 18 of 23 topics, AI policy and beef prices among them. Nothing checked the title against the articles, so the new monitor was created under the existing monitor's title and the duplicate check merged it into that monitor, back-filled updates and all. That, and before 2026-10-08 a merge floor that joined any two topics, is how the one monitor came to hold the Canada, Korea, Ukraine and Gaza stories. The prompt no longer has an example, a title word the articles never use is refused (on the replay that turned back 22 of the 23 old titles and kept the one real Iran title), and the model's "is it significant" verdict, which said yes to all 23 including the 13 that were not a long-running story, and its category, FOREIGN_POLICY for 16, are gone: the category is the issues' own policy area. A refused name falls back to the headline nearest the centroid of the issues that opened the monitor, and a monitor still named after one of its own headlines is asked about again on later runs. Until 2026-10-14 the fallback was whichever issue crossed the day floor, so a monitor on a bipartisan senators' letter opened under one interview's headline, and pages printed its slug back out as its name.

**How does a story reach the votes it is about? (2026-10)** Bills were matched only by a number or short title printed in the articles, and news prints neither: 0 of 543 confirmed issues had ever linked a bill, so the members panel never showed a vote, and the Iran monitor (303 updates) showed none of Congress's war-powers votes, the House's 212-212 tie among them. Now `app/roll_call_links.py` links a story to every roll call taken from three days before its date to two after whose title is close to its headline: the mean of the all-MiniLM-L6-v2 and Arctic-XS cosines at or above the calibrated threshold (`app/data/roll_call_link_calibration.json`, fitted by `scripts/calibrate_roll_call_links.py` on `app/data/roll_call_link_examples.json`). Structural signals were looked for first and did not exist: the articles named a bill one of the candidate votes was on in 0 stories, and a vote's record was an issue's primary source in 2. So the 9,868 candidate pairs of 642 stored stories (issues, the Iran monitor's updates, timeline entries; 2026-04-27 to 10-02) were read and marked by hand, under a criterion written into the examples file. The threshold is where the precision's exact lower 95% bound peaks, which must be at least 0.80: 0.64, where 52 of 54 links made on the 8,162 linkable pairs are right (bound 0.87), recall 0.21. Chosen the same way on three blocks of dates and scored on the fourth, 53 of 57 links were right (bound 0.83). Choosing instead the lowest threshold whose bound reached 0.80 linked more, but held out under the bar. On issues alone, 14 of 16 (bound 0.62, too few to clear the bar alone). Hand labels were used only because no data-supplied label exists; every structural link found from now on (a bill the articles name, a vote record as an issue's primary source) is kept as one (`RollCallLinkLabel`) for the next recalibration. Person names are masked as `[name]` in the examples and, by `NameMask` (every name the site holds a record of), in live text, and the mask is left out of what is compared: kept as a token it made every story naming someone close to every nomination vote (best bound 0.79). On the same stories masked as live text is, 51 of 51 links are right (bound 0.93). The gate's pass rate is watched by the calibration-drift check. What is compared was measured: the headline alone against the vote's title alone; the verified lede, an issue's facts and the vote's question each lowered the best bound. An amendment vote's title names only its sponsor, so it is compared by its stated purpose (`RollCall.amendment_purpose`, #1072) and left out without one; the replay's Senate amendment votes carry theirs (read from senate.gov), the House's were not stored yet, so House amendment purposes are linked by the same threshold without having been in the replay. Links are computed in the hourly refresh (the last week every run, 200 unmatched rows per run) and only read by the API. An issue lists them as "Related votes in Congress", the members panel shows each member's vote and the call script cites the newest; a monitor's "On the record" charts every linked vote's margin over the monitor's life, one row per chamber, with a table. On the labelled stories, masked as live text is, 8 issues link 14 votes, all of them right, and the Iran monitor's updates link 13 votes (the 212-212 tie included). The monitor's opened page is 6.6 phone screens long instead of 77.8: its updates show ten at a time, newest first.

**When does a monitor stop?** After 7 days without a matching issue it is marked watching and stays on the Ongoing tab; the next match makes it active again. After 30 it closes: it moves to the Ongoing tab's "closed in the last 60 days" list, and stays in the Archive and at its link (one that never gathered three updates is deleted). On the same history the long-running stories went up to 28 days between issues and came back, and none went longer.

**Why topic-keyed matching instead of rank-slot matching?** The original design keyed issues by `(date, rank)`. When the same story briefly fell off the top slots and returned, a new row was created with `bsky_posted_at=None`, triggering a duplicate Bluesky post. Topic-keyed matching (2-day lookback by cosine similarity) ensures the same story always maps to the same row. New articles advance `primary_article_date`; more outlets covering the same event do not.

**What makes a repost?** A newer article date is necessary but not sufficient — recap coverage rewords the same story under a fresher timestamp, which used to repost with nothing new to say. The new facts must also introduce either a named entity/figure or a story development (a veto, a court blocking an order, a failed override) that the facts *as of the last post* lacked. The baseline is `bsky_posted_facts`, not the live `facts` column: `facts` is rewritten on every hourly refresh whether or not anything was posted, so baselining on it let a development that surfaced between posts be absorbed and never read as new again. Rows that predate the column are backfilled from `facts` at startup — the poster is the only writer of `bsky_posted_facts` and it only ever sees issues the repost gate has already released, so a NULL baseline on an already-posted row could never resolve itself. The backfill is unconditional rather than fired once on the migration: the poster writes `bsky_posted_at` and `bsky_posted_facts` in the same commit, so a row with the first set and the second NULL can only predate the column, and a seeded row stops matching.

**Developing issues.** Before the news fetch, each refresh reads the latest final-passage roll calls and significant Federal Register rules (`early_signal.py`) and drafts a **Developing** issue, not posted anywhere, for one the news hasn't covered yet. A draft is a template filled from its record, with no model: a vote's measure, question, result, tally and date; a rule's agency, title, abstract (quoted whole in the facts, by whole sentences in the summary), publication date and document number. A rule's title leads with what the rule is, the Federal Register's own title ("Final rule: Transparency in Coverage"; for a rule with no title, its abstract's opening sentence), and the agencies open its summary: until 2026-10 the title led with them, which ran to five departments and put the rule's subject past a phone card's second line. A vote names its bill as the Record prints it ("H.R. 9497"), not the Clerk's file spelling ("H R 9497"). A current draft is re-worded from its record by the template each time the poll reads that record again, so a template change reaches live drafts (promoted and retired rows keep what was published). Only a vote from inside a draft's 48-hour confirmation window is drafted (cast on the run's day or the two before; an undated one never), and a current draft whose vote falls outside it is retired (`early_signal_gate_stale`, `early_signal_retired_stale`): the poll takes each chamber's latest votes whenever they were cast, and on 2026-10-15 the House's rolls of 2026-09-16 went up as that day's developing news, the House having voted on nothing since. The model's drafts had characterized the record ("a narrow 77-22" for S. 4668, 2026-09-28). A vote on a bill that a current reported issue already covers, by recording the bill or naming it by number or short title, isn't drafted, and a draft whose bill later reporting covers as its own issue is retired, at the start of a run and again once the run's issues are written. `mark_recent_duplicates` also marks such a draft a duplicate of the covering issue, decided afresh each run from the record's own rows, so it holds after the covering issue retires as well (the S. 4668 draft stayed listed beside the reporting on 2026-09-29 until this). Each pass records why it retired a draft (`ActionIssue.retired_reason`, migration 0052): `unconfirmed` (its vote aged past the window, its confirmation deadline passed) or `covered` (a reported issue covers its bill; the draft's page links it). A retired draft is not part of the record: the homepage's list of recent issues, which keeps retired confirmed stories, leaves it out (`NOT_RETIRED_DRAFT`; two month-old vote drafts led it on 2026-10-15), and so do the Congress week page's links from a rule to its issue and the open comment periods' "in the news" ordering; a covered draft's place there is the reporting's. That list also shows one issue per national monitor, the newest (`action._one_per_monitor`: the monitors an issue links, or whose updates quote its title), beside the monitor's own entry: two developments of one story are separate events, not duplicates (a statement by six senators against the Russian diesel deal, then their dozen's letter three days on, 0.82 apart by title), and on 18 of 46 days from 2026-09-01 one story held two or more of its first six entries; none does now. Its own page stays up until the 14-day cleanup and says why it left: an unconfirmed draft is flagged **Not confirmed**, and its summary's closing "news coverage … has not appeared yet" becomes "did not appear while it was on the Action Center" (`reword_retired_drafts`, each refresh); a covered draft is flagged **Superseded**, says a reported issue covered the same vote, and keeps its summary, since coverage did appear. A draft retired before reasons were kept is given one by the same pass, from whether a reported issue, current or not, covers its bill. News issues resolve their bills by short title as well as by number (`bill_service.short_title_index`: the current Congress's "... Act" titles of three words or more, a title two companion bills share naming neither), since outlets name a bill that way far more than by number. Every issue carries the refresh's own date: a Senate draft once carried the vote's raw date string, which sorted after every ISO date, so the Action Center (which lists the latest date's issues) showed that one draft alone (migration 0021 rewrote the stored dates). That date is an Eastern day, and so is every date the site shows from a stored timestamp (`time_utils.eastern_date`: an issue's "first surfaced", a monitor's opened, updated and closed dates): until 2026-10-15 those took the timestamp's UTC date, so an issue filed after 8 PM ET read as first surfaced the day after it was last updated (46 of 551 issues).

**How do you tell a quiet news day from an over-suppressing gate?** Both look the same from outside: nothing on the Action Center, nothing on Bluesky. Roughly ten independent checks in this pipeline fail closed — the right default when the platform publishes under its own name, but it means silence is the shared failure mode of all of them. Every refresh records what came in (`articles_fetched`, `articles_policy_relevant`, `clusters_considered`), what published (`issues_new_topic`, `issues_matched_existing`, `bsky_reposts_allowed`), and what each gate dropped, including on the two abort paths that publish nothing at all. `GET /api/admin/action-metrics` reads the window back with those three groups totalled: healthy intake against near-zero output is a suppression problem, near-zero intake is a quiet cycle or a broken feed. Runs are hourly, so a gap in the series is itself a signal — a refresh that crashed or was still holding the lock leaves no row. The Today tab's masthead reads the same rows: "Checked" is the newest run that fetched any articles (`checkedAt` on `GET /api/action/issues`), and "stories updated" the newest that published or re-matched an issue (`generatedAt`), so an hour that found nothing new doesn't look like a refresh that stopped, and a run with the feeds down doesn't count as a check.

**The clustering cut is refit from what the articles state, not from a label of shared names.** Since 2026-10-16 it is fitted once a day on the stored feed articles (`action_center.refit_cuts`): the lowest cut at which no pair sharing a distinctive entity, whose own text dates the event two or more days apart, would merge, plus a stated margin (`CUT_MARGIN`, 0.10) for the same-day different acts no stated fact labels; and clustering never joins two articles whose stated days differ that much. On the 2026-09-28..10-15 articles the fit is 0.60, the cut 0.70. What follows is how the hand-judged cut that preceded it came about. Until 2026-10-15 each run logged its article pairs labelled by whether their names and numbers overlap, and once a day the cut moved to where that label erred least. That label was wrong three times in four when read against issue titles (49 of 65 pairs, 2026-10-08), so the fit had no stable answer: total errors were flat from 0.50 to 0.75 and the stored cut moved 0.63, 0.78, 0.57 in two weeks. Now 553 pairs of feed articles (530 once the briefings the ingest filter drops are left out) published within 48 hours of each other (2026-09-28 to 10-15) are judged by hand as one event or two (`app/data/cluster_pair_judgments.json`, rubric inside), and `scripts/calibrate_cluster_pairs.py` picks, precision first, the text and cut that merge the most same-event pairs while merging no different-event pair: headline plus the first 200 characters of the description, at 0.71 (17 of 114 same-event pairs; headlines alone manage 6 at 0.84, and the 0.57 the old fit settled on merged 38 same-event pairs and 22 from different events). Requiring a shared rare name as well changes nothing at that cut. Replayed over 99 hourly runs rebuilt from the articles production stored (2026-10-11 to 10-15), the clusters that reach two claims cover 11 events instead of 12, and the 10 multi-event clusters the old cut let through to the gate (mostly a statement and a later letter about one deal merged into one story) are gone. Three other thresholds stopped being fitted on 2026-10-08 for the same reason. The same-story title shortcut was labelled by that entity overlap, which, read against the issues' titles, was right for 16 of 65 issue pairs; it keeps its hand-measured 0.92. The issue-to-monitor and monitor-merge floors were labelled by the LLM gate, which approved 81 of 88 matches, off-topic ones included, and whose 0.55 merge floor on the retrieval model merged every pair of different topics into one monitor. They are now constants measured on the monitor's 453 updates (`_MONITOR_ISSUE_SIM`, `_MONITOR_MERGE_TITLE_SIM`). For the same reason, deciding that two issues are one story now takes the same content, a near-identical title, or a shared source article with titles that agree: names and numbers in common no longer do, and a shared source alone no longer does either (one roundup article is cited by unrelated stories; on 94 judged pairs, a 0.62 title floor kept every different-story pair apart). A vote draft's source is its record, so a story citing it still matches the draft. The shortcut's 0.92 is bundled in `app/data/action_thresholds.json` with its provenance. The remaining Action Center tunables are still typed, and are marked as such where they are defined.

**Why does no model decide whether a story joins a monitor?** A story whose title is at least 0.71 similar to a monitor's title and description becomes one of its updates. Until 2026-10-09, a story between 0.71 and 0.80 also needed a yes from the LLM. That verdict was replayed offline on the production model, quantization, prompt and decoding against all 330 updates of the one monitor, each labelled on- or off-topic by hand. It said yes to 323. Of the 18 off-topic updates in that band it let 17 through, and of the 9 dated from 2026-07-15 on it rejected 1. Its own reasons often said the story was about something else, after a verdict field it had already filled with yes. Nothing tried in its place, tuned on the updates before 2026-07-15 and checked on the rest, rejected an off-topic update without rejecting on-topic ones too. The variants were: asking for the article's main subject, adding the monitor's description and recent updates, a bare constrained true/false, reason before verdict, a two-step subject check, multiple choice, a majority or unanimity of five samples, the true/false log-probability margin, and the model's extracted subject scored by embedding against the description and its nearest earlier updates. So the gate is the similarity floor, which is what it measurably was, with one model call fewer per borderline match.

**Why can't a deploy silence the refresh?** Two containers overlap during a deploy, so the refresh takes a cross-container lock: a row in `api_cache` whose primary key makes the second insert fail. The lock is a lease. Its holder rewrites the row's timestamp every minute from a heartbeat thread, a row ten minutes without a beat is taken over, and heartbeat and release touch only the row carrying the holder's own token. It used to be honored for a flat four hours from acquisition. A refresh runs in a thread, so a deploy's SIGTERM kills it without its `finally`, and the row it left made every run for the next four hours skip with "held by another container". On 2026-09-26 twenty-two deploys between 15:08 and 22:50 UTC kept that going all day, and no issue was published. A deploy still kills the refresh it lands on, and a refresh takes about twenty minutes of every hour, so on such a day most runs would die anyway: `check-and-deploy.sh` now defers a deploy while a refresh is running, but only for one that started within the last 40 minutes (twice the longest measured run), so an `is_running` flag wedged true cannot hold deploys the way it did on 2026-07-27. The first runs after the fix showed the heartbeat failing on every beat from minute three to the end of the run: the refresh flushed an LLM training-sample row into its own session and did not commit until every remaining cluster's model work was done, holding SQLite's one write transaction for about fifteen minutes. The heartbeat, the election coverage refresh and the LLM cache all waited out their 30-second busy timeout and failed, and the lease went stale under a live holder. Samples are now written on their own short session, and the monitor stage commits each write before its next model call. That rule covers every stage now that the refresh runs beside the nightly chain. On 2026-10-15, the first night it did, the monitor stage's orphan sweep opened a transaction even with nothing to delete and held it through the next model call. The nightly Senate run and the House sponsorship step each failed on "database is locked" inside that window. Each stage now commits before its next slow call, and a write held past ten seconds is logged with the line that opened it. The pipeline's learning-store and cache writes also retry a locked statement. On 2026-10-16 that log caught the Senate run itself holding the lock for about 20 seconds at a time: each learning-store upsert stayed open while the next bills and donors were classified. Those writes now commit at once.

---

## Congress Record (Half-hourly)

What each chamber did each day, for the Congress reports. Every half hour (`congress_activity.py`):

```
  ROLL CALLS ── senate.gov vote XML, clerk.house.gov/evs: from the highest
       │        stored number up, every member's position kept
  FLOOR LOGS ── House Clerk FloorSummary/YYYYMMDD.xml, Senate floor activity
       │        XML: today's and yesterday's, the live view of a session day
  DAILY DIGEST ─ the Congressional Record's own summary of each day (GovInfo,
                published the next day): replaces the live row as final;
                the last week until final, older days back-filled from the
                119th Congress's first day, 20 per run
```

**Newest first.** A run reads today's floor logs and the last week's Digests before any back-fill, and the current session's roll calls before the previous session's, so a fresh database (a first deploy, a data reset) shows this week within one run rather than after the Congress's first months.

**Nothing is rewritten.** Each passed, failed or reported measure, confirmation and committee meeting keeps the Digest's own sentence; the parser only decides which heading it sits under and reads the bill number. **Committee meetings and reports are findable without the date.** `/congress/committees` (a tab beside Reports and Bills) searches every Digest committee entry and report this Congress by committee, by words (all of them, anywhere in the entry or the committee's name) or by a bill number ("S. 465" or "S.465" is read as that bill, matched anywhere in the entry, since a markup names every measure it orders reported), newest first, as a plain GET form the server renders (`GET /api/congress/committees`, `/api/congress/committee-record`, `services/committee_record.py`, `daily_digest.bill_ids`). A bill's page lists the meetings and reports that name it ("In committee"), and an Explore search offers the matching committee entries beside its results, since they are not documents in its index. A bill's page also lists its floor debate ("Floor debate"): the Explore floor speeches whose Congressional Record heading prints the bill's number, grouped by day, chamber and heading with each speaker linked to their speech, and a speech's Explore page links the bill its heading names (`bill_record.floor_debate`, `speech_bills`, `daily_digest.heading_bill_ids`, the same grammar read in either case, since the Record's headings print "S.J. RES. 188"). The edge is the stated number only, never the speech's words or an embedding, and the bill's Congress is the speech day's; on the 2026-10-15 production copy 730 of 7,998 speeches name one of 160 bills. The rows are read with plain SQL filters per request (about four thousand a Congress), so no nightly step or index is added and nothing in `pipeline/` that the analysis fingerprint hashes changes. The Digest names a meeting's committee but not a report's, so reports are found by words and bill, not by committee. It is tested against real issues from both chambers (`backend/tests/fixtures/daily_digest`).

**Served as reports.** `GET /api/congress/latest`, `/day/{YYYY-MM-DD}`, `/week/{any day of it}` and `/month/{YYYY-MM}` return each chamber's day or period: every count, the "passed both chambers" list, the closest votes (measured from each vote's own threshold, so cloture failing 59-41 is one vote short, not an 18-vote margin) and the one-line summary are computed in `congress_service.py` from the stored rows by rule, so a summary reads "The Senate passed 3 bills, agreed to 4 resolutions and took 3 record votes" and never characterises what passed. `GET /api/congress/votes/{chamber}/{congress}/{session}/{number}` is one roll call with every member's position, linked to their page. `GET /api/bills/{id}/record` is any bill's record (not only one a current member sponsored): Congress.gov's CRS summary, sponsors, cosponsors, actions and text versions, plus every stored roll call on it with each party's split. A Congress.gov part that could not be fetched is named in `unavailable` and never cached, so an outage does not read as a bill with no actions; the parts go out together, after the bill itself (all five at once for a bill a sitting member sponsored, which can't be a wrong id), on a limiter of their own rather than the pipeline's 1.2 requests a second, which held a cold page to five requests in a row and 3.6–5.8 seconds before its first byte (measured 2026-10-14; the hourly budget below is what bounds a reader's calls); the route waits at most 10 seconds, and a part still unfetched then is served the same way and fetched on a later visit. So is a part the hourly upstream budget can't pay for: the record is served with what is stored (a tracked bill's title and latest action from its own row when the bill part itself is missing), never a 503 that takes the page down, and a self-declared crawler (a `NameBot/1.0` token, `crawler`, `spider` or a `+https://` link in its User-Agent) spends a 50-call share of the hour rather than the readers' 150. Measured on 2026-10-11: crawlers naming themselves made 2,378 of 2,667 bill-page requests in a day (SemrushBot 1,858), and 2,479 of 2,740 bill records were the budget's 503.

**Nominations.** Every presidential nomination of the current Congress, from Congress.gov's nomination API (`pipeline/fetch/nominations.py`): one row per citation (`PN999-1`; Congress.gov splits a message naming several people into numbered parts) with the description, organization and position title, every nominee's name and state as published, every action, the referral committee's system code and the hearings Congress.gov lists, all in its own words. The status (pending, confirmed, returned, withdrawn) is its action vocabulary read into four words (`STATUS_CODES`: S05310, S05330, S05335; any other code is pending), a translation, not a judgement. A daily job at 14:50 UTC, outside the nightly chain and skipped while any chain pipeline runs, lists what changed since its cursor and reads each new or changed nomination oldest update first, at most 300 a run at half a request a second on top of the shared Congress.gov limiter (the pipeline process's 1.2 a second plus the public routes' 200 an hour keep the key under its 5,000), so the first backfill of a Congress (2,285 citations in the 119th) takes about eight days. Each nomination is committed as it is read and never across a request; the cursor moves only past what was applied in order, so an outage or one unreadable record is read again, never skipped. A roll call links its nominations by the citations its question states ("On the Nomination PN999-1", or seventy at once for an en bloc vote), read as tokens on request (`services/nomination_links.py`): on the 2026-10-15 production copy, 458 of the 119th Congress's 1,591 roll calls name a nomination, and all 1,339 citations they state match a listed nomination (before this, none linked anywhere). A confirmed federal judgeship links the Federal Judicial Center's biography when its directory (`judges.csv`, the file the justice pipeline reads) has exactly one judge with the same court, nomination date and surname; FJC states no PN, so those three exact fields are the whole key (measured on the 119th: every one of the 56 confirmed judgeships in courts the directory covers; the other 13 are D.C. local and territorial courts it does not). `/congress/nominations` shows the counts by status, the days from receipt to confirmation as a histogram per kind (civilian, judges, military; `GET /api/nominations/stats`, every figure computed by the backend) and the searchable list (`GET /api/nominations?status=&kind=&agency=&q=`); `/congress/nominations/{congress}/{PN}` (`GET /api/nominations/{congress}/{PN}`) shows one nomination with its actions, hearings, every roll call that names it and each senator's vote on the one to confirm, filterable by state. A roll call's page and a member's vote list name the nomination each vote decided and link it, labelled by the vote's own question ("Vote to confirm", "Vote to end debate (cloture)", "Vote to take up the nomination"; `nomination_links.VOTE_LABELS`), with the nomination's status shown only on the vote to confirm, and an en bloc vote counted by the citations its question names, however many are stored yet. Until the first backfill has read everything Congress.gov listed (`backfillComplete` in the sync's marker, sent as `backfill` by `/api/nominations/stats`), the page says how many of about how many are read and how many more days the rest should take, and holds the confirmation-time medians, since the nominations read first are the oldest updates. An organization links its agency page (`/agencies/{slug}`) when it is exactly one of the names that agency goes by in the registry: the Federal Register's name or short name, or the USAspending name the registry's crosswalk already matched to it, compared as `agencies.normalized_name` (`nomination_service.agency_index`; a name two agencies share links neither). On the 119th Congress's listing, 63 of 83 civilian organizations link, covering 776 of 938 civilian nominations; the rest (the Judiciary, the Foreign Service, international banks, some boards) and the military branches are shown as Congress.gov's text.

**A bill goes by its short title.** A member's sponsored-legislation listing gives each bill the title it was introduced under, so a bill given its short title in committee kept the official one: H.R. 1 of the 119th Congress read "An act to provide for reconciliation pursuant to title II of H. Con. Res. 14." in the bill list, on its page and in search, and "big beautiful bill" found nothing. Congress.gov's titles list for each bill names its short and popular titles, and the site shows one when the listing title isn't already among them (`fetch/bill_display_titles.display_title`): the short title as enacted, else the newest whole-bill short title, else the popular title, word for word, never one naming a portion of the bill ("FEHB Protection Act of 2025" inside H.R. 1), and never the expansion of an acronym the listing already shows ("TAKE IT DOWN Act" stays). The bill page leads with it, says its title type as Congress.gov names it ("Short Title(s) as Passed House · Congress.gov") and puts the official title from the same list under it; the bills list shows the official title under it too, a member's vote list and party breaks name the vote's bill by it (the chamber's own title stays on hover and on the roll call's page), the roll call page names it beside the bill number, and both searches match it. Day, week and month reports keep each chamber's own wording, as everything in them does. A short title is usually given at introduction, when the listing already shows it, so the titles lists fetched are those of bills reported out of committee or further whose cached copy is missing or went stale as the bill moved (the hourly bill pass, after the statuses, at most 150 a pass; `scheduler._bill_refresh_pass`). It is read on the read path from the cache only, and lives under `fetch/` and in `scheduler.py`, so the analysis-code fingerprint does not move (checked: the same hash before and after).

**What an amendment vote was about (2026-10).** A vote on an amendment is titled by its sponsor and number ("Smith Amdt. No. 6805") and asks about the bill by number ("... to S. 4668 (No short title on file)"), so 202 of the 119th Congress's 1,591 roll calls said nothing a reader could use. The record says what each amendment would do, and `fetch/amendment_purpose.py` stores it word for word (`roll_calls.amendment`, `amendment_purpose`, migration 0058): for the Senate the roll call's own XML (`<amendment_number>`, `<amendment_purpose>`), for the House Congress.gov's house-vote record, which numbers the amendment the Clerk names only by its author, and that amendment's record (`purpose`). It runs after each half-hourly sync, 60 roll calls at a time, newest first; a read that fails (a timeout, a 5xx) leaves the row unread (an outage is never "no purpose stated"), and three in a row stop that chamber's reads for the run while the other chamber's go on; a record that isn't there (Congress.gov publishes a House vote's house-vote record a day or so late; a file that 404s) is skipped and asked again after a day, then twice as long each time up to a month (`amendment_misses`, `amendment_checked_at`), so neither a busy House day nor a dead file holds back older votes; and a record that states none is shown as "No statement of purpose on file". The roll call page quotes it as the official purpose with its source, and every vote list, the bill page's votes and the homepage's closest vote carry it. The roll call page draws the chamber as a seat chart, one dot per member grouped by party and marked by how they voted (shape as well as colour), with the party table under it as its text equivalent, inside the vote's share image. It lives under `fetch/` and in `scheduler.py`, outside the analysis-code fingerprint.

**How a bill got where it is (2026-10).** Half of the 119th Congress's laws (60 of 123) passed with no roll call, by unanimous consent or a voice vote, and their pages said "No recorded votes", which a reader takes for nobody voting. The bill page now reads its path from the official actions (`services/bill_journey.py`): Congress.gov lists many actions twice, once from the chamber and once from the Library of Congress ("Signed by President." under two codes; "Passed/agreed to in House: ..." restating the House's sentence), and same-day twins are folded into the chamber's wording with every code and roll call kept (S. 240's 27 actions are 20). Each folded action carries a one-line plain meaning from a checked-in glossary keyed by its action code (`app/data/bill_action_glossary.json`; a code is listed only where every action carrying it in the cached record shares one meaning). How each chamber passed the bill is read from its passage codes and the chamber's fixed formula ("by Unanimous Consent", "by voice vote", "by the Yeas and Nays"; a roll call wherever the action records one) and said plainly: "Passed the Senate by unanimous consent on December 11, 2025; no roll call is taken for that." A journey map above the stage ladder dates each stop the bill's type passes (introduced, committee, each chamber's floor, resolving differences, the President, law) with the time between them; the list is its own text equivalent. The votes section says how the bill passed instead of "No recorded votes" when no roll call was taken.

**Checked against the sources (2026-10-11).** All 915 Senate roll calls of the 119th Congress match senate.gov's vote menus (number, date, tally), 45 sampled House roll calls match the Clerk's XML, every one of the 122 public laws is listed under the week Congress.gov dates it, and a sampled Senate day's 36 passed measures match its Digest entry for entry. Three things that did not hold are fixed. A House vote on an amendment is titled by the amendment the Clerk names ("Doe of Ohio Part B Amendment No. 1"); the Clerk gives it no description, and 90 read only as their bill's number (`congress_activity._vote_title`; stored votes are re-read once by `repair_house_requirements`). A Senate pro forma session timed to the second reads "for under a minute", not "for 0 minutes" (59 day reports). A bill the second chamber has passed is "Passed Both Chambers" (`BillStage.PASSED_BOTH`), not "In Other Chamber" until it reaches the President (52 bills and resolutions); it counts as Legislative Effectiveness's "passed the chamber" stage, as before. The sitemap lists every current-Congress bill a report links to, not only those a sitting member sponsors (36 were missing).

**An empty day says something (2026-10).** A default view that is all zeros tells a reader nothing, so each of these says what the site does know instead, from stored rows only. `/congress` on a day neither chamber did business (a pro forma sitting, a two-minute House session) led with "met for 2 minutes" above eight zeros; `GET /api/congress/latest` now carries `recess` (`congress_service.recess`) when the latest day had no floor action — no record vote, nothing passed, failed or confirmed; a committee report filed in a pro forma sitting is not floor action, and the House files them through a recess — and neither chamber is still on its live floor log — and the page leads with "Congress is out", the last day of business, each chamber's next sitting as the record gives it (marked when the program says pro forma), and opens the week of that last business with a link to the latest sitting. The bills page's "Active now" list is bills named by a live Action Center issue, which is most days none; when it is, `GET /api/bills?sort=hot` serves bills that moved this week (a recorded action in the last seven days, past referral) with `shown: "moved"` and `movedSince`, and the page says which list it is showing. The "bills in motion" headline is the backend's `inMotion`, no longer summed in the browser. A tracked bill's page states the base rate for its stage (`stageBaseRate`, `bill_service.stage_base_rate`): of this Congress's bills from sitting members, the share at that stage and the share that became law, and of those that got at least that far, the share that became law so far — a description of bills at the stage, worded so it never forecasts the bill (GovTrack's prognosis is the same idea); none for an earlier Congress's bill or one already law or vetoed. Above it the page leads with what the bill does, in the Congressional Research Service's own words (its summary's first paragraph, attributed and dated), and how far it has got: one row per milestone (introduced, reported by a committee, passed one chamber, passed both, became law) with the share of this Congress's bills that reached it and this bill's row marked (`stageMilestones`, `bill_service.stage_milestones`, over the same bills as the base rate; `StageLadder.tsx`). Its sources, the roll calls as JSON from the public API, and an APA-style reference close the page (`BillSources.tsx`, linked as "Data & citation" in the page's own menu). `/congress` leads with two pictures of the Congress so far, both shareable and each with its table (`GET /api/congress/glance`, `services/congress_glance.py`, `CongressGlance.tsx`): a calendar with a row a month and a cell a day, each cell split into the Senate and the House, lit when the chamber sat (its Record day in session, or a roll call that day) and brighter the more record votes it took; and the bill pipeline, how many of this Congress's bills from sitting members were introduced, reported by a committee, passed one chamber, passed both and became law, drawn on a log scale (19,195 introduced against 122 laws on the 2026-10-16 copy leaves every bar after the first invisible on a linear one). With a state picked in the header, "Your members this Congress" follows: each sitting member's roll calls voted and missed, latest three votes (with or against party, `member_positions`) and latest three bills, linking to the profile, from `GET /api/congress/members?state=` (the state in that one URL and nowhere else; `roll_call_positions.member_id` is indexed for it, 4.5 s to 1.6 s for California's 54 members). In a recess the week's lists fold behind their headings and counts, and the section cards' week and month tiles show the last day of business instead of "None". At 375 px the recess page went from 11.8 screens with no visual to 5.0 screens with the calendar at 1.0 screens, in the server-rendered HTML. The Action Center's Ongoing tab also lists monitors closed in the last 60 days, with when each was tracked and closed (`recentlyClosed` on `GET /api/action/monitors`; a closed monitor's row is last written when it closes, so `updated_at` is its closing day).

**The pages.** `/congress` is the latest day either chamber met; `/congress/2026-09-24`, `/congress/week/2026-09-21` and `/congress/month/2026-09` are a day, a week and a month, each with the Senate and the House side by side. `/congress/bills` is the in-motion list that used to be `/bills` (old links redirect), and `/congress/bills/{id}` is any bill's page — of the current Congress, or of Congress N with `?congress=N` (a bill number names a different bill in each Congress, so every link that knows its Congress passes it, and the canonical keeps `?congress=` only for an earlier one): the CRS summary, sponsor and cosponsors, the full action history with a link to each day's report, text versions, and every recorded vote with each party's split and each member's position: the names wait behind a state pick or a "Show all N members' votes" button (`VoteMembers` `collapsible`), each group's count still shown, since S. 2403's page listed all 433 House members inline, 16 screens and 519 tab stops before its history (87 after); the roll call's own page lists them all. Every roll call also has a page of its own, `/congress/votes/{chamber}/{congress}/{session}/{number}`, on a bill or not: the question and what the vote was on (a nominee, or a bill linked to its page), the result as the chamber recorded it, the tally, each party's split, every member's position linked to their profile (filterable by state), the chamber's own record of it (Senate.gov or the House Clerk, the URL the fetch read it from) and an APA-style reference. Half of the 119th Congress's Senate roll calls (458 of 915 on 2026-10-14) are on nominations, which have no bill page, so until then a cabinet confirmation linked nowhere. Every place that lists a roll call links to its page (the day, week and month reports, the "this week" strip, the homepage's "On the record", a member's votes and party breaks, compare, an issue's "their record on it", the president's-position lists and a bill's vote panel), and the sitemap lists each one (`rollCalls` on `GET /api/sitemap`). An address with no roll call recorded is a real 404 (noindex) whose page points to the day reports and the bills list (its `not-found.tsx`); an outage is the Congress error page, never a 404. A House roll call prints only the Clerk's surname form ("Joyce (PA)"), so each member the site has a page for is served with their own name and district (`bill_record.vote_detail`: `name`, `district`), and a state's members are listed in district order, so a reader who knows their district finds their member at once. Past the first eight cosponsors, the rest sit behind a native "All N cosponsors" disclosure: a ninety-cosponsor bill put ninety links between a keyboard reader and the votes. The bills list's search box finds a bill by its number in any spelling ("S. 2398" as the hub prints it, "HR 997": that bill, not H.R. 9977; the number alone matches as a prefix, dots and spaces dropped, the member vote search's rule, `bill_record.typed_bill_number`) as well as by the words of its title or sponsor: each word typed, in any order, as the start of a word after the Porter stemmer has reduced both to their stems, so "flag burning" finds the "Flag Burners Act" and "student loans" a "Student Loan" bill (`services/word_search.py`, SQLite FTS5's own `porter` tokenizer over an in-memory index of the cached list, built once per snapshot; until 2026-10 the query had to appear in a title unbroken, and over 226 real titles two of their words found the bill 0.4% of the time, now 100%); by a short title it gained after it was introduced (below); typing a search leaves the "Active now" list for every bill, and a search opens every stage its matches sit at (it opened a stage only when all of them sat at one, which word matching made rare: "student loans" finds 34 bills at five stages), and every bill route reads an id the same way (`bill_record.canonical_bill_id`: `/api/bills/H.R.%20997/record` is HR.997's record). A bill number typed into the homepage's request slip ("H.R. 7008", "s 4668") opens that bill's page, and searched in Explore the bill's page is offered above the results: as full text, the number ranked documents that merely shared its digits above the bill. Every bill the site mentions links there rather than to Congress.gov: the votes and breaks on a member's profile, the bills a donor-vote link names (`billPageHref`; a recent House roll call is stored under a synthetic id, `HouseRC-2026-314`, and is served as its roll call's bill, else the vote's title, never the id), and an Action Center issue's related bills, each with the Congress it recorded (an entry that recorded none links only when the site holds the bill, to the newest Congress it holds it from). A day whose Digest is not out yet shows the chambers' live floor logs and says so. The House writes its log through the day; the Senate posts its log only after the session ends, so until then a Senate that has voted shows "Record votes so far" and says when its log comes, rather than an empty log. `/congress` renders per request, never at build time (the build has no backend, so a prerendered page was always the empty state until a reader's visit revalidated it). A report the backend could not serve reads "could not be reached" (`app/congress/error.tsx`), never "nothing recorded" or a 404; only the backend's own 404 means no day has been recorded. A day, week or month exists only for dates on record, from the first day either chamber is on record as meeting to today (`congress_service.record_span`); any other well-formed date is a 404 with `noindex`, titled "Congress day not found" (week, month), and a week or month links its neighbours only when they are on record. A 404's title comes from a layout in its own segment, since a route's `notFound()` discards its page's metadata: the day, week, month, with-the-president and Explore document routes each have one beside the roll call's and the bill's, and an address no route answers is "Page not found" (`app/not-found.tsx`); until 2026-10-15 those seven read only the site's name. Every date from 1789 to the next year used to answer a page of placeholders, and the week and month pages linked their neighbours without end: in October 2026 a crawler walked dates into 2027 from rotating addresses, and each address counted as a new visitor (about 1,900 a day against a usual 150 to 400). A second crawler in the same month walked the bill pages from about 2,700 addresses an hour with browser-like headers, and was counted as roughly 7,000 of 7,357 visitors on 2026-10-08; a page now counts only once the same client's browser has also made one of the requests the running page makes (Next's link prefetches), which that crawler never did (`frontend/src/proxy.ts`). The page's own beacon that replaced the header check for a day was refused by the EasyPrivacy list most blockers enable (`/track-visit?`), so readers with a blocker went uncounted; the requests counted now are ones the site needs in order to work, and nothing is sent to evade a blocker.

**A file is not a session.** On a day the Senate does not meet it still publishes its floor file, holding only when it reconvenes; the sync reads that as not in session (it first read as "The Senate met" for Friday and Saturday, 2026-09-25/26), and re-reads every not-yet-final day of the last week so a wrong row corrects itself.

**No Record, said plainly.** GPO publishes the Congressional Record for every day either chamber is in session. A past day with no Record (behind the back-fill cursor, which stops before any day it could not read, or found absent at least three days on) reads "No Congressional Record was published for this day" rather than "no record yet", and the week and month strips say "No Record". It states the fact and not "neither chamber met": GPO very rarely prints two small consecutive days as one issue.

**A missing file is not a failed fetch.** A 404, or senate.gov's redirect of a missing file to its "not found" page, means the chamber has nothing for that day. Anything else writes nothing, so a day is never made final from part of its Digest, and the back-fill stops before a failed day to retry it.

## Votes with the President (Nightly, informational)

How often each member votes with the president's stated position, beside what members of the same party in seats that lean the same way do. Informational: not in `SCORE_WEIGHTS`, and no score reads it. It runs as the Supplementary pipeline's last step (`analyze/presidential_support.py`), and is shown on every member's profile and at `/congress/president/house` and `/senate`.

```
  OMB INDEX ──── whitehouse.gov's list of Statements of Administration Policy
       │         (SAPs, OMB Circular A-19): only PDFs not yet read are fetched
  READ ───────── each PDF's date, the chamber it is addressed to ("(House)",
       │         "(Senate)"), the measures its title block cites, an amendment
       │         number it names, its opening and closing paragraphs
       │         (fetch/omb_statements.py)
  POSITION ───── support / oppose / no position on passage, by embedding
       │         similarity to written prototypes (analyze/sap_position.py);
       │         the sentence it was read from is stored and shown
  MATCH ──────── each stored roll call (Congress Record, above) to the statement
       │         that applies to it
  TALLY + FIT ── per member: votes on the president's side / counted votes;
                 per chamber and party: the expected rate by seat lean
```

**The source.** The president's position comes only from a signed, citable statement tied to the measure. OMB lists every SAP of the administration on one page; the index and the PDFs are served without a bot wall (the site's WordPress JSON API answers 403 and is not used) and `robots.txt` allows both. On 2026-10-13 it listed 194 statements for the 119th Congress: 125 addressed to the House, 69 to the Senate. One covers nineteen resolutions without citing any by number and cannot be matched to a vote.

**Whose positions.** "The president" is the sitting one (the presidents table's `is_current`, from the roster). A Congress that spans an inauguration counts only statements issued and roll calls cast since the sitting president's term began (`presidential_support.presidency_start`), so an outgoing administration's position never counts as its successor's.

**Which votes count.** A roll call counts when it is the chamber's final vote on a measure (passage, adoption, concurrence, a veto override), read from the chamber's own question wording, or a vote on an amendment a statement names by number; a statement addressed to that chamber covers the measure, issued on or before the vote; no later statement on the measure (in either chamber) has superseded it; and the statement supports or opposes passage. Procedural votes (cloture, motions to proceed, discharge, table, recommit) are left out because no statement addresses them. A Yea advances the measure, so the president's side is Yea under a supporting statement and Nay under an opposing one; present and not voting are not votes. On the 2026-10-13 record that is 122 House and 27 Senate roll calls. Many Senate statements were never followed by a final vote: the Senate disposed of most war-powers and disapproval resolutions on motions to discharge or proceed, which are not counted.

**Reading the position.** The statement's opening paragraph states the position on passage and the closing paragraph the recommendation to the President; a provision-by-provision statement (an appropriations bill, the NDAA) opens by thanking the committee and takes no position, and its votes are not counted. The candidate sentence most similar to a support or oppose prototype decides, unless it is nearer the "other" prototypes, with no tuned threshold. Checked against a hand reading of all 193 readable statements: 185 agree (95.9%); the eight that don't are seven positions read as "no position" and one statement recommending signature "with amendments" read as support. No support was read as oppose or the reverse.

**The expectation.** Per chamber and party, a fractional-response logit (Papke & Wooldridge 1996) of members' rates on their seat's party-signed lean (the Cook PVI measure Constituent Alignment uses), each member weighted by their counted votes and bent at a swing seat only where the bend predicts the party's members better out of sample (`score_calculator.select_opposed_kink`, as for Constituent Alignment's expectation; since 2026-10, when the 119th House's Democrats had a bend fitted on seven members that held-out fit did not support), fitted on this Congress's members every night (AGENTS.md §3a) and written to `/data/presidential_support_reference.json`. A member's expected rate is their party's fit at their seat's lean. No shrinkage is applied to either rate.

**Uncertainty.** Each rate carries its 95% Wilson score interval (Wilson 1927; Agresti & Coull 1998), which keeps a real width at 0% and 100%, where most members sit. A member is "more" or "less" often than expected only when that interval misses the expected rate; the expected rate's own uncertainty is not included. With 27 Senate votes the intervals are wide, and the page says how many votes each rate rests on.

**Bounded.** Every OMB request has a 30-second timeout and two retries; a run spends at most 15 minutes reading new statements (the first run reads all of them, later runs one or two), leaves the rest for the next night, and the step as a whole is cut off at 25 minutes. Like every Supplementary phase it is best-effort: a failure is recorded and alerted, and the phases before it are not affected. The embedding model loads only when there is a new statement to read, and classification and the rebuild run off the event loop.

**Served.** `GET /api/presidential-support/{chamber}` and `/member/{id}` (the site), `GET /api/public/v1/presidential-support/{chamber}` (the public API, with a contract test). Every number is computed by the backend; the wording is neutral by construction (voting with or against the president is neither good nor bad).

---

## Election Pipeline (Nightly)

An independent pipeline (`app/pipeline/election_pipeline.py`) with no data dependency on the Senate/House/President runs. Six phases, each fault-isolated so one failure doesn't take the others down:

```
1. ROSTER SYNC ──── every declared FEC candidate for the cycle → Race / Candidate rows
2. FINANCIAL ────── prioritized, watermarked batch of 500 (FEC allows 0.25 req/s and a
   REFRESH            midterm cycle has ~6,900 candidates — the roster rotates over runs)
3. BALLOT ───────── statewide ballot measures, read from each state's own office, + a liveness
   MEASURES           check on the official-ballot links the site hands users
4. COVERAGE ─────── RSS matched to races by candidate name with mandatory
   INGESTION          corroboration; 8 national feeds + 41 per-state newsrooms;
                      tighter cadence in election season. The open Bluesky
                      name search is DISABLED — see below.
5. BLUESKY ──────── one grounded, source-backed sentence per notable coverage item
6. SNAPSHOT ─────── changed-only fundraising snapshots for trend charts
```

**A candidate's figures are that race's election, dated by the FEC's report.** The refresh reads the FEC's totals row for the race's own election year (the whole election period, `election_full`) and stores nothing when the FEC has none: a candidate who has filed for 2026 but not yet reported has only earlier elections' rows, and the newest of those used to be shown as this race's money (41 candidates on 2026-10-08, from sitting members' 2024 re-elections to a 2004 race). A refresh writes every field, nulls included, so figures that stop applying are cleared. Each card dates its figures by the report's own end (`financials_through`, the row's `coverage_end_date`: "FEC REPORTS THROUGH 2026-06-30 · CHECKED 2026-10-08"); the sync date alone read as if the money were current that day. Anyone not refreshed in `FINANCIALS_OVERDUE_DAYS` (14) goes ahead of the incumbents-first order, which had left the ~1,450 candidates the FEC flags as not having raised money unrefreshed since August. A figure the page doesn't have is said in words ("awaiting FEC sync", "no funds reported"), never formatted as $0.

**What shapes a race, without a forecast.** Each federal contest's research on the ballot page opens with a short "what shapes this race" block (`app/race_context.py`, `RaceContext.tsx`), and every figure in it is the backend's. *The seat:* the lean the election map shows for it (`usualVote`: a district's Civitas-computed lean on the lines the election is fought on, a state's computed lean; written by the map's own `district_lean.lean_label`, with Cook's PVI beside it as a cross-check, and Cook's alone, labelled as such, only where no computed figure exists), in words ("voted about 5 points more Republican than the nation in the last two presidential elections") and how many other seats lean further each way, counted over every House district on the lines the election is fought on, or the 50 states for a Senate race, drawn as one SVG axis of lean against the nation (D+x at the left, EVEN, R+x at the right) with one mark per seat stacked by lean, tinted by the sign of each seat's own lean and this seat's column outlined, with a table of the same counts (`leanContext.distribution`, `seatLean`). It was a grid of squares tinted by which side of *this* seat each fell, which wrapped into rows on every screen and showed a strongly Democratic seat as a sea of red. A House seat that only has its state's number gets no ranking, and from election day the lean is left out, as it is everywhere on the page. *The member who holds the seat*, when running: one line with their Representation Score and a link to the scorecard. Above both: Civitas publishes no odds for the race and calls no race; the race links to its cycle's map's poll range for it, which is kept out of this block because the block shows a Representation Score. *The money:* each candidate's contributions split into the FEC totals row's own categories, which add up to the total (donors of $200 or less, whom the FEC doesn't itemize; donors of more; PACs and other committees; party committees; the candidate), as shares the backend apportions to 100% and to 20 blocks; the candidate's own loans, which are not contributions; and the date the figures run through, linked to that election's FEC page. When a filing's categories miss its total by more than a dollar (8 of 2,101 candidates' 2026 rows on 2026-10-14), no split is given. Every state's figures download as one CSV (`GET /api/elections/states/{ST}/races.csv`): each candidate's FEC figures, the dates they are as of, the seat's lean and the FEC link.

**Ballots refresh every 6 hours in election season.** The election pipeline runs last in the nightly chain, hours into the night, so ballots would otherwise go most of a day between refreshes. In the 60 days before an election and on through its results window (`is_election_season` in `election_calendar.py`, the same window as the 15-minute coverage refresh), the ballot step alone (`run_ballot_sync`: every state's certified list or primary results, then its filing list) is also scheduled on its own every 6 hours, at :50 UTC. It reads ballots up to and including election day; from the day after, each tick still fires but returns `skipped` without reading (`election_is_held`, below). It reads only state election offices' published lists, a few requests per state at one per second, so the cadence is cheap; the roster and FEC financial refresh stay nightly. It steps aside while the nightly election run is active, and the nightly ballot phase steps aside while a sync is in flight, so two passes never write the same candidates at once. Outside the season, the nightly run is enough: filings and primaries move on the scale of days. From the day after an election, neither the scheduled sync (whose ticks through the results window skip) nor the nightly run's roster, ballot and measure phases re-read that election (`election_is_held`): the site stays on it through the results window, but its sources move on — a state's single "next election" page lists the next one, a list past its election answers "not published yet", and FEC's candidate records give each candidate's *latest* district and incumbency, so a nominee filing for the next cycle elsewhere would move races — and re-reading them would unwrite a certified ballot. What was read before the day stands; financials still refresh (post-general reports are that election's money), and past elections' measures still age out. The roster reads each House candidate's district for the cycle it is building, from FEC's per-year `election_districts`, falling back to the latest `district_number` only when that year's entry is missing.

### Confirmed candidates (who is actually on the ballot)

The FEC roster in phase 1 lists everyone who *filed* — including candidates who already lost their primary months ago. Showing all of them on a ballot page is not a cosmetic problem: it presents losers as options. A second, independent layer answers "who is really on the November ballot" from each state's own election authority.

The whole flow — sources, matching, and what a race page may show — is drawn in [`docs/diagrams/10-elections.md`](docs/diagrams/10-elections.md).

The constraint that shapes it: **no 50 bespoke scrapers.** Adapters are per *vendor*, not per state, so a state whose vendor is already supported is a JSON entry in `backend/app/data/state_candidate_sources.json`, never new code. Only a genuinely different vendor earns a module. **No adapter branches on a state's name** — every URL, slug, election-name pattern and runoff threshold lives in that file, whose own `_contract` key documents which keys each strategy honours. A state that needs a knob nobody has needed yet gets that knob added to its adapter for everyone, never an `if state ==` special case.

Current coverage: **50 states configured across 32 strategies** (30 as a state's main source, plus two used only as a certified `general_list`). Some serve many states (`tabular` 15, `clarity` 3, `tally_enr` 2, `totalvote_enr` 2, and `certified_table` as fifteen states' general list and two states' main source); many are a single state whose election authority genuinely is unlike anyone else's. Two states (NV, NY) have no usable per-state source yet and fall back to `google_civic`, a national source keyed on one fixed, publicly-known address per state — never a visitor's.

**The certified ballot beats primary results wherever a state publishes it.** Louisiana, South Carolina and Missouri sat on `google_civic` until 2026-09-26, and all three publish their November ballot directly: Louisiana's results portal stages the general election's candidate list (`voterportal`), South Carolina's candidate-tracking system lists it by office (`vrems`), and Missouri's Secretary of State certifies it to the counties as a PDF (`certified_pdf` — a list of names, so none of the name-to-vote misalignment that makes results PDFs unsafe). Maine moved too, for a sharper reason: its results reader was right about the June primary and wrong about the ballot. The Democratic Senate primary's winner withdrew in July and the party nominated a replacement by convention, which primary results cannot show, so the withdrawn winner was still on the page. Maine's certified General Candidate List is now read instead (`certified_table`, a config-driven reader for any state that publishes its list as a spreadsheet). South Carolina had the same shape: after the June primary, a special primary and runoff decided a nominee the June results never named.

**A certified ballot is authoritative; primary results are not.** `confirmed_general` is never cleared for a primary-results state, because a nominee does not stop being one when a later fetch hiccups. For a state whose source *is* its certified ballot (`general_ballot_complete`: TX, NC, SD, LA, SC, MO, MI, OK; and every state with a `general_list`: AK, AL, AR, CO, CT, DE, FL, HI, IA, ID, IL, KY, MD, ME, MT, NC, ND, NE, NJ, NM, NV, TN, UT, VA, WY — for AL, CT and UT that list is Google Civic's, which covers only the races it returns, and Arkansas falls back to Google's when its own list cannot be read), anyone confirmed in a race the list covers but not on it is unconfirmed on the next successful fetch — that is what removes a withdrawn nominee instead of showing them beside their replacement. A race the list does not cover keeps what it had, and a failed fetch changes nothing. Those states also get no "unopposed" FEC filers added back, since a party missing from a certified ballot has nobody on it.

**Everyone on the ballot is shown, FEC filing or not.** A candidate a state lists who never filed with the FEC (often a minor-party or independent candidate under the reporting threshold) used to match nothing and vanish — 7 of Louisiana's 41 federal ballot candidates. They now get a ballot-only row (`Candidate.fec_filed` is False; the id is `ballot:` + race + name, never an FEC id) built from the state's printed name, shown under that name (`ballot_name`, as for a matched candidate; until 2026-10-09 these rows showed the FEC-style "DOE, JANE" built for matching, 60 people in 17 states), with "no FEC filing" instead of money and without an FEC link. It is removed when the state stops listing them or when they file and match a real FEC record. A bare surname or a results file's non-candidate row ("Write-in", "Scattering") never becomes one. To make this possible every strategy now carries the printed name (`display_name`) alongside the surname, through one shared builder (`federal_record`).

**A Senate race exists only if the FEC's own election calendar lists one.** The roster used to treat any Senate filer in a state with no regular seat up as running in a special election, which is right for Florida and Ohio in 2026 and invented one for New York and Hawaii out of serial filers with no money and no FEC candidate status — New York's page showed a Senate race holding nine filings from four people. The FEC election-dates calendar lists exactly the 35 states holding a Senate general in 2026, specials included, and is already read every night; the roster now skips Senate filings anywhere it lists none and removes such races already on file (`senate_election_known`, `_remove_senate_races_nobody_holds`). If the calendar has never been read in full, the class rotation still decides; only a complete read (every page back) can retract a Senate election, so a page that fails can't delete a real race. The nightly run of 2026-09-27 removed all fourteen phantoms (NY, HI, CA, CT, UT, NV, WI, AZ, IN, ND, PA, WA, MD, MO), leaving Florida's and Ohio's real specials. Those two then showed every FEC filer: a state's certified record for "U.S. Senator" was keyed to the regular race id, and a state electing a senator only to fill a vacancy has only a "-SPECIAL" race. A Senate record now goes to the state's one Senate race this cycle, whichever kind it is (`_race_id_for`); with both kinds in one state (Georgia, 2020) nothing in the record chooses, so it stays with the regular race. Florida's certified list and Ohio's official primary canvass both confirm the special's nominees, Ohio's Libertarian included.

**Declared write-ins are never "on the ballot".** They are not printed, so every certified-list source drops them. Texas's Civix portal gives its six declared 2026 write-ins the same general-election status as printed nominees and marks them only in its candidate-type field (`cdCandType` "WRTIN"); `tx_civix` read the status alone and showed four write-ins as Senate candidates on the state's official ballot, one labelled Independent from her FEC filing. It now drops them like every other adapter. A filer whose FEC party code is "W" is labelled "WRITE-IN" rather than a bare letter.

**West Virginia's primary link moved to an archive.** WV's Clarity results are found through a link on the Secretary of State's site, because its own `elections.json` is empty. In late September 2026 the elections page dropped that link, and every ballot sync reported WV failed. The link survives on the results archive, beside every election since 2016. A Clarity landing page that lists several elections is now scoped by each election's own settings file (its name and date, the same test `elections.json` states get), and a lone link from another cycle is no longer trusted either.

**Every state's ballot is certified by mid-September** (ballots must reach military and overseas voters 45 days before the election), so a certified list exists everywhere; the work is reaching it. Colorado's official general candidate list, Virginia's November federal-offices list and Tennessee's per-office November lists (its Senate race alone has eight independents, 36 across its federal races, all invisible to primary results) are now read the same way as Maine's (`certified_table`, a config-driven spreadsheet reader: which columns hold the office, district, party and name; an optional index hop; status and write-in filters; nothing else read — Virginia's file also carries campaign emails, phones and addresses). The certified list is a `general_list` beside the state's main source, not a replacement for it: it runs first and, when it answers, alone decides every federal race it covers (so a replaced nominee is never confirmed, even for a moment within a run); a race it does not cover keeps its primary-results nominees, non-authoritatively, and only covered races are labelled "confirmed" (the `ballot-basis` marker records `complete` for a list that covered every race, `races` for one that did not), while the main source keeps supplying statewide, legislative and judicial nominees the list may not cover — replacing Colorado's source outright had silently dropped its statewide nominees — and supplies federal nominees in the weeks before the list is posted. The API labels the page by whichever source actually answered (`BALLOT_BASIS_TIER` marker, read once per request) rather than by the state's config — a primary-results night says "nominees", not "confirmed".

**Florida** cancels a party primary when only one candidate qualifies, so its results file had no rows at all for five districts (8, 10, 18, 26, 28), which fell back to every FEC filer. Its Division of Elections candidate list (`dos_canlist`) gives every federal candidate a status; the ballot is everyone Qualified, plus a seat's sole Unopposed candidate (elected without being printed, as in FL-10), less declared write-ins: 77 candidates across all 28 districts. **New Jersey** read the July certification of party nominees, which skips independents and predates the amended certifications posted since (NJ-7, NJ-9, NJ-10, Senate); its Official General Election Candidates lists are the ballot as it stands, 38 candidates with 12 independents. Those lists print home addresses beside names, so a row counts as a candidate only when it has one — tested for presence, never read.

**Maryland, Iowa and Nebraska** publish their November lists as a CSV and two PDF tables, all now read by `certified_table`. Maryland's CSV gives every filing a status, and only "Active" is on the ballot (MD-5 still lists three unaffiliated filers as Withdrawn or Failed to Submit Required Number of Signatures). Iowa's list is the ballot as vacancies were filled, and Iowa's page had still been showing a senator who did not run as a Senate nominee. Nebraska's final list carries a petition candidate for Senate and three minor parties' nominees that no primary shows. A PDF is read as a table: the row holding every configured heading is the header, cells are split where the gap between words is wider than a space, and each cell belongs to the column it starts in, found from the cells that sit under exactly one heading (data is left-aligned under centred headings, so a short cell like "PO Box 33" can reach no heading at all). Iowa prints each office once per group, so `office_fill_down` carries it down, but only from a candidate row: a page footer never sets it, and a governor's running mate never inherits the congressional district above.

**New Mexico and Wyoming** followed the same day. New Mexico's candidate portal lists every contest with a status per candidate (only "Qualified" is on the ballot) as an HTML table at one fixed address that always shows the current election, so `discovery.url` requires `year_regex` to find this year's general election in the page before any row is read; last cycle's list must never confirm anyone this cycle. Wyoming posts its General Election Candidates Roster as a CSV beside the PDF; a row with a Date Withdrawn is off the ballot (`status_values: [""]`), and the list adds the at-large seat's Libertarian and Constitution candidates. Wyoming's own party codes (LBR, CT) are now read, "CT" only as a party column's whole value, where it cannot be Connecticut. **Hawaii**'s candidate report gives every filing a status ("In General" is the November ballot) and adds HI-1's nonpartisan candidate. It is a paged grid, and its own Export to CSV button returns the whole list, so `discovery.form_button` posts the page's form back with that button, exactly as a visitor's click does, after `year_regex` confirms the page names this year's election. Its names are printed "BERNING, Nathan M." (`name_last_first`: the text before the comma is the whole surname, so "LEGER FERNANDEZ" stays two words). A list's format is now decided from its bytes (zip, PDF, HTML, else CSV), not its address, since Hawaii's CSV comes from an .aspx page. **Delaware**'s general candidate list marks each filing Qualified or Withdrawn (a Republican House filer withdrew in July). Its xlsx writes every cell as an inline string and has no shared-string table, which the shared xlsx reader (also used by the results strategies) refused until now, even though its own docstring said inline values were read. **Kentucky**'s candidate-filings site, once set to the general election, lists the ballot per office: primary nominees plus seven independents and Kentucky Party and Libertarian candidates no primary shows, and declared write-ins, which are excluded. Its index links one page per office, so `every_link` reads each US Senator and US Representative page it finds (a year with no Senate race has no Senate page to require), and each page must name this year's general election. **Alaska** is top-four, and a finalist who withdraws is replaced by the fifth-place finisher, which primary results cannot show. Its page was wrong in both races: it showed a Senate finalist and a House finalist who are not on the ballot, and missed the replacements in both (one Senate, two House). The Division's general candidate list (one table per office under a heading, registration printed in the name, "Certified Write-In" entries excluded because they are not printed) is now the ballot. Reaching it found a bug in the shared fetch layer: `fetch_with_retry` did not follow redirects, so Alaska's moved candidates page (301 to /election-candidates/) came back as an empty success, a moved page read as a page listing nothing. Redirects are now followed by default for every source. **Montana**'s candidate filing list is the same kind of paged grid as Hawaii's, exported the same way. (It first read the cycle's primary list, whose NOMINATED status turned out never to reach a petition independent; see below.)

**North Carolina's state offices and judgeships from its November list** (2026-10-09). North Carolina holds no primary for an unopposed nomination, so its results export never lists one, and it keeps a nominee the party later replaced: read from it, the page showed 59 legislative nominees in 55 of 170 districts (three since replaced) and nothing for the Supreme Court seat. Its candidate filing file (`Candidate_Listing_{year}.csv`, already read for the federal general rows) is now also its `general_list`, read by `certified_table` with `statewide_offices` and `judicial_offices`: only rows dated in November, the file awaited until it carries one. `judicial_offices` on a `general_list` is the same separate claim it is on a results adapter (the state's judicial contests are partisan) and, like `statewide_offices`, makes the list the judgeships' source once it has answered with them (`_judicial_source`).

**Probed on 2026-09-26 and not converted.** Utah's certification is a scanned document whose text layer is OCR (a first and last name run together, with a lowercase "l" for "I"), and a misread name would unconfirm the real nominee, so it is not read. Alabama's party certifications are images with no text at all. The newest list on Connecticut's list-of-candidates page is from 2011. Kansas's candidate list is behind an AWS WAF human-verification page and Mississippi's qualifying list behind an Akamai denial. West Virginia's candidate site serves an incomplete TLS certificate chain, and this pipeline does not turn verification off. Rhode Island's candidate search is behind a Cloudflare challenge, and Oregon's ORESTAR filing search behind an F5 bot-defense script. Indiana's "2026 General Election Candidate List" link on its own candidate page leads to a 404. No public Pennsylvania general-election candidate list was found. Arizona's 2026 election page answered 403. For the four whose gaps are visible — a district whose primary was uncontested showed every FEC filer (AL-3, 4 and 5 and the Senate; AR-1 and 3; CT-2 and 3; UT-4) — Google's Civic index became each state's `general_list` (Arkansas's is now the fallback of its own list, below), covering exactly those races from public buildings whose addresses the Census geocoder places in the right district (a county courthouse or city hall; the state capitol for the Senate). It decides only the races it returns, and until Google publishes contests for the general election it returns nothing and changes nothing. That required the per-race merge above: a list that covers some races must not silence the primary results for the rest. California and Washington are top-two states, so their certified primary results name exactly the two November finalists already.

**Primary results miss three kinds of candidate, and an audit on 2026-10-08 found all three on live pages.** A withdrawal: Idaho's Democratic Senate nominee withdrew after the primary and was still shown. A petition independent: Idaho's two Senate independents were missing, and Montana's page, marked a complete certified ballot, missed the Senate and MT-2 independents whose petitions the state certified. An unopposed nominee: Arkansas's statewide section showed only the Democrat for Governor, and none of the sitting Governor, the Libertarian, or the five other offices, because none was contested in a primary. Each state's own November list is now read instead.

- **Montana** keeps one candidate list per election in the same system, chosen from an election picker on the list page. The filing page links only the primary's list, and there a petition independent stays at PENDING PETITION for good, so a NOMINATED filter could never see one. The general election's list holds exactly the November field (no primary losers), each FILED or WITHDRAWN. `certified_table` now reaches it the way a visitor does: `form_select` picks this year's "FEDERAL GENERAL" election (its options are patterns now, since the label carries the election's date), and with `form_select_postback` the choice is posted first, because the picker reloads the page on change and the export button on the old page still exports the old list. The export is refused unless the page that comes back shows the chosen election. Declared write-ins sit on the list with the party preference NON and are dropped, since they are not printed on the ballot.
- **Idaho**'s candidate portal (run.voteidaho.gov, the Secretary of State's "Search Filed Candidates") serves each election's list from its own JSON API, headed FINAL CANDIDATES LIST for the general and flagged final in the API (`discovery.final_path`; until then the list is not the ballot). `certified_table` reads JSON (`format.json_rows`, with `json_total` refusing a page that does not hold the whole list), POSTs a configured body (`discovery.json_body`) and finds the election's id in the portal's own election list by name (`discovery.election`), since every search is keyed by an id that changes each election. Only Approved filings are read, write-ins excluded, and only the ballot fields: the API also returns voter ids and mailing addresses, which are never stored. Idaho's office is split over three fields ("State Representative", "1", "A"), joined by `format.office_parts`. The list is the state-office source too, so Governor now shows the independent and Constitution nominees.
- **Arkansas**'s candidate search (candidates.arkansas.gov) has no status column, but after the primary the state removed every losing candidate from it, so it is the November field: the unopposed nominees, the Libertarians and the independents. It is a JSON table API that answers an empty body when a request lacks the table's own parameters or comes too soon after another; that is a failed read, never an empty ballot. Its page names the cycle, which `discovery.year_url` checks, since the feed names no year. Names are printed as filed, with the title of an office the candidate holds where they chose one (Arkansas permits it). With no status column, a withdrawal shows only when the state removes the row. Google Civic is its `general_list.fallback`: when the search cannot be read, Google still decides the races it answers for, and a state-office source the list has already supplied stays in place rather than reverting to primary results.

When none of these can be read, nothing claims more than it knows: the federal races keep their primary-results nominees, not marked complete, and the statewide and legislative sections say their names come from primary results and may be incomplete.

**Illinois** reads its State Board of Elections' candidate list for the general election (`grouped_list_pdf`): a PDF per office group with no column headings — each office a heading line, each candidate a line of party, name and filing date — where a candidate struck from the ballot is marked on the line below (two IL-4 independents removed 2026-07-21 are dropped; an independent and two minor-party nominees no primary shows are added). The list's address carries an encrypted election id, read each run from the Board's home page ("Next Election"), and the PDF must say it is this year's general election. A candidate line must carry its filing date in the date column, so a page's own header is never read as a candidate.

**North Dakota**'s contest/candidate list renders a contest's candidates only once it is chosen from a dropdown and searched, and the option's value is a per-election id. `certified_table`'s `form_select` posts the page's form once per federal contest, chosen by the option's visible text ("Representative in Congress", "United States Senator"); a year with no Senate race has no Senate option and is skipped, but at least one must be on offer. It adds the at-large seat's two independent nominees.

**Wisconsin** is behind a Cloudflare challenge on every page, but its document files are served plainly, and the certified canvass of the August primary is one of them (`canvass_summary_pdf`: the state marks each nominee "Winner" itself, so nothing is derived from vote totals; SCATTERING and name-less winner lines name nobody). Its file name changed between cycles, so it is addressed from the primary date with Google Civic as `fallback`.

**Michigan, Ohio and Oklahoma** left `google_civic` on 2026-09-28, each for the state's own publication; Google Civic stays as each one's `general_list`, so it takes over the races it covers once it has data, and until then (or with no key at all) it answers nothing and the state's own source stands. Nothing is fetched past a challenge: Oklahoma's results API still requires logging in with a credential embedded in its page script and is not used, but the State Election Board's own **List of Elections** for November (`hosting.okelections.gov/electionlist.html`, linked from its "next election" page) is a plain page listing every county's ballot, office by office, each candidate "NAME, PARTY" — the ballot itself, independents included (`certified_table` with `outline_rows`: each line is a one-row table indented by empty cells, so a line carries the lines above it by indent). The address always shows the *next* election, so it is read only when it names this year's November election. Michigan's Official Candidate Listing moved from mielections.us to the Bureau of Elections' own candidate system (`mi-boe.entellitrak.com`, which michigan.gov links as "Candidate listing"); its general-election report is a JasperReports grid (`report_grid`: a value belongs to the header column that starts where it starts), carrying primary winners, the parties' convention nominees and petition independents, with disqualified and withdrawn filings marked and dropped — 6 Senate and 62 House candidates. Because the report exists all cycle and fills in as candidates file, the list itself decides when it is the ballot: while any office holds more of one party's candidates than its printed seat count (`seats_regex`) nothing is read, and while a party that already lists its convention-nominated Secretary of State or Attorney General still lacks the other (`slate_complete`; an independent ticket or a governor-only minor party is never waited for) only the federal rows are. A list not published yet — a page not naming this year's election, or one still holding primary filers — answers "not yet" (an empty answer), not a failed fetch, so it neither sends the state to the crawler's spare source nor records anything as the ballot; from 45 days before the general (the UOCAVA mailing deadline, when every ballot is final) through election day the same answer is a failed fetch — and a party slate still short then sends a daily ops alert while the state offices stay held — so a moved page or changed layout alarms instead of reading as "not yet" all cycle. A seat count missing where one is configured is a changed layout, never an over-fill check quietly switched off. Ohio's Secretary of State publishes the official canvass of its May primary as one summary workbook per party, listed in the JSON index its own data portal reads (`oh_canvass_xlsx`); a plurality winner per contest, all 15 districts and the Senate special. A write-in who led where nobody was printed is withheld, since Ohio nominates one only with as many votes as the office's petition signatures (R.C. 3513.23), which the canvass does not print. Two of these hosts, michigan.gov and www.ohiosos.gov, had been reported walled: both refuse the User-Agent's bracketed "(+<contact email>)" comment from this environment and answer without it, and neither is fetched by these readers (the report and the files index live on hosts that accept the standard headers).

What **Nevada and New York** still have in common is not missing data but the door to it: Nevada's Secretary of State (its Certified Candidate List, `CertCandList.aspx`, and its results site) answers every server request with an Incapsula JavaScript challenge, and every New York State Board of Elections host (`elections.ny.gov`, its results and public-reporting hosts) with a Cloudflare 403, from any header set tried on 2026-09-28; neither Common Crawl nor the Wayback Machine holds a readable copy. Passing a challenge is not something this pipeline does. Clark County's own list of contests and candidates for November covers three of Nevada's four House districts and every statewide contest. Since 2026-10-08 it is Nevada's `general_list` for those three districts: a House contest's candidates are the same in every county it touches, the per-race merge leaves NV-2 to its other sources, and the state offices are not read from it, since a county list holds only that county's legislative districts. Its table prints each office once, centred beside its candidates, so `certified_table` reads it by its rules (`format.ruled_columns`): the vertical rules are the column edges, a rule spanning the whole table closes an office's group, and a group listing candidates with no office beside it fails the read rather than guessing. Nevada's "No Political Party" (NPP) is now read as an independent entry on a ballot list. New York has no such way in yet: New York City's Board of Elections posts a contest list for November (vote.nyc), but it is headed TENTATIVE / SUBJECT TO CHANGE, was printed on 2026-09-11, before the state certified its ballot on 2026-09-17, and covers only the districts that touch the city, so reading it as the certified ballot would claim what its own heading disclaims. Where the same authority publishes a plain file on an unprotected path — Wisconsin's official canvass is one — that file is the way in, and Wisconsin now reads it.

**Matching a state's record to an FEC candidate** works inside one race and refuses anything still ambiguous. Measured against every configured state on 2026-09-26, it now handles: accents (an accented ballot name against FEC's unaccented one had left one race showing all eleven filers); a generational suffix on either side (`DOE II, JOHN`, `ROE III`); a married surname filed as a given name (`ROE, JANE DOE` for a nominee who campaigns as Jane Doe); a two-word ballot surname filed as one word (`DOE, JANE MCRAE` for a ballot's "McRae Doe"); one slip in spelling when the given name agrees too (`DOHERTY` for Dougherty); two same-party namesakes, separated by given name (two pairs of relatives in two districts); and one person filed under two FEC ids (the same name twice in one district, or `DOE, JOHN` beside `DOE, JOHN D.`), where the record that raised money is confirmed.

**The weekly source crawl asks robots.txt first, as RFC 9309 reads it** (`state_source_crawler._robots_for`): redirects followed (the standard asks for at least five), a 4xx means no rules, a 5xx or no answer means read nothing for an hour. A 4xx that is a bot wall's *challenge* is the exception — nothing is read past it, and it is recorded as a wall, not as a file that couldn't be read. Walls already documented, with the evidence, in `state_candidate_sources.json`'s `_robots_walls` (Arizona, Georgia, Nevada, New York, Ohio and Pennsylvania's main sites, measured 2026-10-13) are left out of the ops alert; a new wall or an unreachable file is named with its class, and the same set alerts once until it changes. Three election sites — Alaska's, Alabama's and Virginia's — send an incomplete certificate chain, which browsers repair by fetching the missing intermediate from the address the certificate names (AIA); the shared client (`app/http_client.py`) now does the same, once per host, without accepting a chain that doesn't end at a trusted root. Until it did, those sites were unreachable, and Alaska's ballot measures failed to ingest.

**A runoff can take a results source away.** South Dakota sat on `totalvote_enr` returning nothing for 116 days after its primary, and neither the adapter nor the vendor was broken: that vendor serves whatever election is CURRENT, and SD's 2026-07-28 runoff carried only Governor and Secretary of State, so the June primary's federal results left the view — with no archive or election picker to reach them. Montana and Nebraska run the identical strategy unaffected, because neither held a runoff. SD now reads `vip.sdsos.gov/candidatelist.aspx`, the Secretary of State's list of who is ON the November ballot, which answers `confirmed_general` directly instead of inferring it from vote counts. That also surfaces a general-only independent for US Senate that no primary-results source can structurally see — which is why `normalize_party` gained a `ballot_list` mode: reading "Independent" as a party is wrong for primary results and right for a certified ballot.

Three rules do most of the work, all for the same reason — a wrong name here changes a vote:

- **Federal contests only, and the label must say so positively.** `parse_office` refuses anything not explicitly federal rather than guessing. Rhode Island is the sharpest case: its *state* legislature is named the "General Assembly", so `Senator in General Assembly District 5` sits on the same primary ballot as the real `Senator in Congress`, and 140 of that ballot's 192 contests are state seats that must not leak into a federal race.
- **A sub-threshold leader is not a nominee.** States that send a plurality leader to a runoff carry `runoff_threshold_pct`, and a contest whose leader is under it yields nothing rather than a guess — the same under-include-rather-than-fabricate rule applied throughout.
- **Certification is a signal, not a promise.** Where a vendor publishes an official/certified flag it is honoured, but `settle_days` sits underneath as a failsafe, because that flag is not reliably flipped (Utah's stayed false a month after its own signed canvass was published on the same portal).

What a visitor sees follows directly: a state with confirmed nominees renders a flat, confirmed list; a state without them falls back to FEC filers ranked by money raised, and the page says so rather than implying the ranking means anything about who will win.

**And the calendar decides whether that fallback is still honest.** Filers
are the right answer while a primary is ahead — nobody knows the ballot
yet. Once it has been held, the same list means the ballot *has* been
decided and this platform does not have it, which is a different claim
and a much worse one to leave unsaid. Measured across all 50 states on
2026-09-26: 39 had certified candidates, and eleven were still showing
filers after their own primary — Ohio by 144 days, Louisiana 133, New
York 95, with one New York race listing 25 filers for a ballot that holds
about two. The page had been saying those nominees "aren't confirmed
*yet*".

`_ballot_basis` (api/elections.py) now reports the WEAKEST basis across a
state's races together with whether its primary has passed, and the state
page leads with that rather than footnoting it — a state whose ballot is
certified says nothing at all, because a notice on every page is one
readers learn to skip. Of those eleven, South Dakota, Louisiana, South
Carolina and Missouri now read their certified ballots, and New Hampshire
unlocks on its own once its 21-day settle window passes.

### The state ballot page

`/elections/states/<ST>` is framed as ballot research, not a mock ballot: no marks, no "your picks", and every contest carries research signals (money raised against the race's top fundraiser, a member of Congress's Representation Score, news count). A member of the other chamber running in a race (a House member running for the Senate) is linked to their record by the bioguide crosswalk as `memberRecord`, named as what they are now and never as the seat's incumbent (`incumbentRecord`); and a member's profile links the race they are on the ballot in (`onBallot`, `member_on_ballot` in `backend/app/api/elections.py`, through the same confirmed-nominee filter every listing uses, and only once a race has confirmed nominees: a race whose nominees aren't read lists every FEC filer, which made a member who filed and then retired read as on the ballot). A sitting House member always keeps the link to their own seat's race (`seatRace`, `member_seat_race`, 2026-10): when they aren't a confirmed nominee for it, the profile says so from stored rows only, "not on the November ballot for this seat" where the state's complete certified list confirms the field, "not among the nominees the state's primary results confirm" where only primary results do, each with its source, and "confirmed nominees aren't available" for a state not read yet, never a reason (retiring, a lost primary) the rows don't record. On a redrawn map the race is the member's district number on the new lines, and the line says it is not the district they were elected in. Measured on 2026-10-16 over the 433 sitting members: 326 are confirmed nominees for their seat, 77 are not (retiring, running for another office or another district, or beaten in a primary; 59 of those profiles had shown no race at all), and 30 sit in a state whose nominees for that seat aren't read: New York's 26 (its election sites are behind a bot wall), Nevada's 2nd and Utah's 4th (both left to the national fallback), and Connecticut's 2nd and 3rd. Connecticut's were a gap in the reader, now closed: no party primaried either district, so the primary results named no one, and the Secretary of the State's per-district certificates of party endorsement (`state_candidates_ct.parse_house_endorsements`) now name the endorsed nominees of every district no primary decided. It replaced a single 768px column of stacked panels (the news feed on top, one row per House district — 52 in California — and a box for every section, empty or not) that read as one endless scroll.

- **Desktop**: three printed-ballot columns (Federal | State | Measures · local), each contest a box with a shaded header and the ballot's own instruction ("Vote for one"), fitting about one screen. A contest's research opens in a drawer beside it with Previous/Next through the ballot. Each row of the measures box opens the drawer at that measure, with focus on its heading (California's ninth measure had been a click and almost seven screens of scrolling away).
- **Phone**: an index of every contest, one line each; a contest opens on its own screen (one contest per screen, the voting-machine pattern). The measures line carries one button per measure, which opens that measure's screen at the measure itself. Checked by live screenshot at 390px (NC, CA, TX, AK, MD, 2026-09-27): the Money / Record / News tabs share the row equally, so the bar no longer shifts when the bold active tab changes width; the Previous/Next arrow is bound to its label with a no-break space so it never wraps onto a line of its own; and the election date in the page eyebrow never breaks at a hyphen.
- **Tabs only inside a race** (Money / Record / News). Research (NN/g "Tabs, Used Right"; the Center for Civic Design and EAC ballot guidelines) says tabs suit supplemental views a reader need not compare across, which describes a race's facets but not the ballot's sections: a voter needs every contest.
- Federal offices are named one way everywhere: "U.S. Senator", "U.S. Representative", and each says the term the vote is for — 2 years for the House, 6 for the Senate (both fixed by the Constitution), and "fills the rest of the term" for a special Senate election. State offices carry theirs too, from `backend/app/data/office_terms.json`: every statewide office, legislative chamber and court the page shows in the states it covers, listed explicitly per state and office with its source (a Georgia or North Dakota Public Service Commissioner serves six years, a Maryland delegate four, a North Carolina Superior Court judge eight). Nothing is defaulted — a state or office missing from the file shows no term, because New Hampshire and Vermont elect two-year governors and a guessed term is a wrong fact. A term that depends on the year is stored as the rule and computed for each cycle: the 2-4-4 state senates (Minnesota's two-year terms in years ending in 0; Arkansas, Delaware and Florida electing their whole senate for mixed terms in years ending in 2, when no single term is shown). Terms are worded for the office ("4-year terms"), since a seat filled for the rest of an unexpired term runs shorter; a test rejects any key the pipeline does not produce.
- A column with nothing on file says so: a state with none of its own offices loaded gets a "State offices — not loaded yet" contest (with the official lookup), the same "not loaded is not none" rule the measures box follows, instead of an empty column that reads as a state electing nobody.
- Candidates appear under the name their state prints on its ballot ("Jane Doe"), stored as `Candidate.ballot_name` whenever a state source matches them, and fall back to the FEC's ("DOE, JANE") until one does. A "Last, First" printing is not stored — a comma does not reliably mark where a surname ends ("Olszewski, Jr.") — so those keep the FEC name. A nickname the ballot prints inside the name ("Tobias (Toby) Doe": North Carolina, Colorado, California, Missouri) is kept; only a parenthetical *after* the name, such as Georgia's "(I)", is an annotation and dropped (`clean_display_name`), and the matcher never compares the nickname.
- A sitting member is marked the incumbent from the FEC's coding, which the API distrusts when a race mixes "I" and "O" (a retired member's leftover record beside an open seat) — except for a candidate the state lists on this cycle's ballot (`confirmed_general` or `on_primary_ballot`), who is running whatever anyone else's code says: on 2026-10-08 a losing filer's stale "O" hid nine sitting members' incumbency.
- A party a state prints that has no code of its own is shown as printed: on a certified list (`ballot_list_party`) and on a top-two state's results (California's "Peace and Freedom", "No Party Preference"), where the party had been dropped. "American Independent" and "Independent American" name parties, not independents.
- A legislative seat whose special election for the rest of its term is on the same ballot as its regular contest (Arkansas House District 8, 2026) keeps the two apart: a label saying "unexpired" or "special" is stored as its own seat (`UNEXPIRED_SEAT`) and shown "8 (unexpired term)".
- Where November is not decided by a plurality, the page says so above the ballot (`generalRunoffs`, from `backend/app/data/election_rules.json`, a cited legal fact per state). The file keeps the law apart from the cycle: `standing` rules hold every cycle until the law changes, with a runoff date as the statute counts it (Georgia's majority rule, a runoff 28 days after the general, computed for each cycle's own general), and `cycles` holds one cycle's one-offs (Louisiana's 2026 House contests as an all-party open primary with a December 12 runoff; Alabama's House districts 1, 2, 6 and 7 postponed to an August 11 special primary, `otherPrimaries`), which never carry into the next cycle. An all-party primary is said there too (`topPrimary`, standing): California's and Washington's top two and Alaska's top four can send several candidates of one party to November, which otherwise reads as a listing error. A ranked-choice state (Maine, Alaska) is told it can rank candidates before election day (`rankedChoice`, from the same `ranked_choice_general` citation the results pages read), not only on election night.
- A candidate the state has confirmed is on the ballot always shows, whatever their FEC record says. North Carolina's certified Libertarian for Senate had been hidden under "other filers" because she raised nothing and is not an FEC statutory candidate.

The contest list is built once (`frontend/src/lib/ballotContests.ts`); the drawer is a real modal dialog (focus kept inside, Escape closes, focus returns), and `#race-{id}` / `#ballot-{key}` links open a contest directly (`#measure-{id}` opens the measures at one measure). `StateBallotClient.a11y.test.tsx` runs axe-core over the page and every kind of drawer (a race on each tab, the district picker, a picked district) on every CI run, with a guard test proving axe actually fires; colour contrast and target size, which jsdom cannot compute, are checked in a real browser by the Lighthouse workflow (Lighthouse, then every axe-core WCAG 2.2 A/AA rule at 320 and 1280 px through `frontend/scripts/axe-routes.mjs`).

### Finding your district without being asked where you live

Civitas never asks for an address. An address box lived on the state
ballot page until 2026-09 and was removed; a text filter replaced it,
which is better but still a chore — you have to know what to type, and
typing is the step people skip.

Counties are pickable instead, because a person knows theirs without
looking it up where almost nobody knows their district NUMBER. The index
is built from the rows already on the page (every race carries its county
list), so it needs no new data, no lookup service and no network call.
**13% of US counties span more than one district** (414 of 3,142 on the 2026 lines) — those
offer the two or three as a second tap rather than sending the reader
elsewhere. The text filter stays for anyone who prefers it; it compares
through `searchFold` (`frontend/src/lib/elections.ts`), so accents,
punctuation, a phone's curly apostrophe and "saint"/"st" don't matter
("dona ana", "st louis", "prince georges").

**The filter also takes a city, town or community** (2026-10). A county
search misses where most people start, with the name of their city:
of the 100 most populous places in California,
Texas, Florida, New York and Pennsylvania only 15 found their district(s)
by name (4 more found the wrong one, a rural county sharing a big city's
name). Each House race now carries the places in its district
(`places`, from `backend/app/data/congressional_district_places.json`):
incorporated places and Census Designated Places, plus the towns and
townships of the strong-MCD states, with at least 50 of their 2020
residents inside the district, largest first. It is built by
`backend/scripts/build_district_place_crosswalk.py` with the same block
join as the state legislative crosswalk (the Census congressional block
equivalency files against the 2020 P.L. 94-171 block populations), for
two maps: `"120"`, the lines each state votes on in 2026 (the ballot
page), and `"119"`, the lines the sitting members were elected on. A row
the filter found by a place shows that place first, and a place named in
full gets a line saying which districts it is in, and that
it is split between them when it is in more than one. All 100 of the
sample places now find their district(s).

Where a county answers nothing — a county split between districts, or a
city holding several — a **map of the districts themselves** does. Every
multi-district state page draws its districts, shaded by the same PVI
rule as the district list (red R, blue D, fainter = closer) wherever a
district has a lean of its own. Those leans are on the lines the cycle
is fought on (`district_pvi_for_congress(congress_for_election(cycle))`,
the 120th Congress's pinned table for 2026), not the sitting members', so
the nine redrawn states are shaded by their new districts (TX-35 is R+4 on
the 2026 map; the old seat of that number was D+19), with a caption naming
them as the new 2026 districts. Where no table for a cycle's lines is on
file, `district_pvi_for_congress` drops every state redrawn since the one
it serves, and those races carry the flagged statewide lean: the map leaves
them unshaded, in one light neutral (with a caption saying no per-district
lean is published yet when none in the state has one), and the district
list shows that statewide figure in neutral ink, marked "(statewide)". Hovering or
tabbing to a district previews its race, and clicking narrows the page to it; a
district with no race on the ballot previews as "no race on file" and is not a
button. From election day the preview is the district's count, and on the state's
results section a count the ballot has no race for is still pickable, landing on
its own row.
The outlines are the lines each state votes on this cycle, split per
state and vendored under `frontend/public/data/cd/` (all 50 states, 435
districts, ~206KB total; a page loads only its own state) by
`backend/scripts/build_district_topology.py`, which refuses to write
unless every state's district numbers match
`county_district_crosswalk.json` and none was lost to simplification.
At-large states get no map — one shape is nothing to choose between.

**The 2026 lines are not the 2024 lines.** Nine states redrew between the
two elections (AL, CA, FL, LA, NC, OH, TN, TX, UT); until 2026-09 every page
drew and listed those states' districts on the old lines under the new
district numbers — Utah's 1st, now almost all Salt Lake County, still read
as the old northern-Utah seat. Which map each state votes on is one
checked-in list, `backend/app/data/redrawn_congressional_maps.json`, with the
enacting act or court order per state; Missouri is listed on its old map on
purpose (its 2025 map was stayed by the U.S. Supreme Court on 2026-09-25).
Both files are generated from the Census Bureau's block equivalency files
(119th Congress, and the 120th-Congress file its Redistricting Data Office
published 2026-08-31): the crosswalk by `build_county_district_crosswalk.py`,
and the redrawn states' outlines by dissolving 2020 Census blocks on those
lines (the Census publishes no cartographic file for them yet), clipped to
the same shoreline. The 120th-Congress file was checked against the official
block assignment files of the redrawn states that publish one: Texas,
California, Florida, North Carolina and Ohio match it exactly; Louisiana's
uses block ids other than the 2020 Census's but agrees on every block the
two share; Alabama, Tennessee and Utah publish no official block file, so
theirs rest on the Census file alone.
The crosswalk script refuses to write if a state not on the list differs
between the two files, so a later redraw can't slip through on stale lines.
The district-PVI pins (`backend/app/data/district_pvi_sources.json`, the
entry for the Congress a cycle seats) name the same redrawn states, and
`test_redrawn_maps_and_pinned_pvi_sources_name_the_same_states` fails if the
two lists drift, so a page never calls a district new while serving the old
seat's lean.

**Civitas-computed lean (display only).** Beside Cook's district PVI, the
race context block, the redistricting pages, the scorecard's methods
section and the public API's breakdown (`references.seat.computedLean`)
show a lean Civitas computes itself with Cook's formula (two-party
presidential share minus the nation's, 75% 2024 / 25% 2020), on the 119th
and 120th Congress lines: `backend/scripts/build_district_lean.py` writes
`backend/app/data/district_lean.json`, read through `app/district_lean.py`. A Congress with no table of its own yet (the next cycle's lines, before its returns exist) reads the newest earlier table only in a state whose map is the same in both elections, and only once `redrawn_congressional_maps.json` lists that cycle (`district_lean.lines_table`); a state that redrew reads nothing until its own table is built.
Inputs are open-licensed only — VEST 2020 precinct results and shapefiles
(CC BY 4.0; each populated Census block's internal point is placed in its
precinct, which shares its votes among its blocks by population), MEDSL
2024 precinct returns (CC0; each precinct's own U.S. House district, the
119th lines), Census P.L. 94-171 blocks and the 119th/120th block
equivalency files; the 120th lines in the nine redrawn states come by
swing transfer within county x 119th district x state house district cells.
MEDSL's known errors are corrected only from certified state or county
canvasses, one entry each in `app/data/district_lean_corrections.json`
(Indiana's ENR archive, New Jersey's municipal results, Louisiana's parish
totals, King County's precinct file, California's Supplement to the
Statement of Vote, Maine's district totals), each published whole in the
built file's `corrections` (its source URL, totals and the rows it touched);
a district whose correction
could only come from behind a wall is written as unavailable. The build
fails and writes nothing unless every state's totals are within 0.2% of
the official state files (FEC for 2020, MEDSL's state file for 2024),
every fetched county canvass matches, every district's 2024/2020 vote ratio
is within 0.85-1.15 (or a recorded, evidenced exception at its own ratio),
every district of both maps is present once, and the Texas and California
official per-district tables agree; districts more than 1.5 points from
Cook are listed as alerts. Against Cook: 119th lines r 0.9998, mean
absolute difference 0.27, 423 of 435 equal when rounded, all within one;
120th lines r 0.9997, 0.28, 403 equal, 432 within one. Texas's Red-206 tables are the independent cross-check; California's is also its correction source, so agreement there only confirms the correction was applied. It moves no score:
Constituent Alignment still reads Cook's figure.
The whole table can be downloaded and cited: `GET /api/public/v1/district-lean`
(every district on both maps with Cook's published PVI for the same lines,
the method, each input with its version and license, every correction with
its certified source, the agreement with Cook, and the data file's SHA-256)
and `GET /api/elections/district-lean.csv` (the same districts, one row
each), both from `district_lean.published()` / `csv_text()`; `/about/data#computed-lean`
links them, the script, the data and corrections files, and gives an APA
citation dated by the file's `_as_of`.
The same list tells the election-night sync that a redrawn seat has no
holder to measure a flip against, and lets an incumbent running under a new
number link to their record — labelled a *sitting member* there (a *member before this election* from
election day on, when the results window can include the new Congress's
first day), never the new district's incumbent
(`incumbencyLabel` in `frontend/src/lib/elections.ts`). Regenerate after any redistricting:

```bash
python backend/scripts/build_county_district_crosswalk.py
python backend/scripts/build_district_topology.py
python backend/scripts/build_district_place_crosswalk.py
python backend/scripts/build_redistricting_overlap.py
```

**What changed on the new maps** (`/elections/redistricting`, one page per
redrawn state). For each new district: the share of its residents that came
from each old district, its lean on the old lines against the new, and for
each old district, where its residents went; per state, the seats leaning
each way before and after (the net change, which is what the page leads
with), how far the average resident's district lean moved, and the
enacting act or court order (the official link is the `source` of each entry
in `redrawn_congressional_maps.json`). The counts are
`backend/app/data/redistricting_overlap.json`
(`build_redistricting_overlap.py`): the same block equivalency files joined to
each 2020 Census block's population, so every resident is in exactly one
(old, new) cell and a state's cells add up to its 2020 population. Sentences
leave out overlaps under 50 residents (about one block, the place files'
sliver rule); the matrix, the CSV (`GET
/api/elections/redistricting/{state}/overlap.csv`) and the JSON keep them all.
Lean is joined at read time from the pinned Cook PVI tables for the 119th and
120th Congresses (`district_pvi_for_congress`), the same 2020 + 2024 window
on both, so a change in lean is the lines, not new election results; a
"predecessor" is the old district most of a new district's residents came
from. Lean categories are the PVI's sign (R, D or EVEN; no competitive band
is invented). The headline is the net change in seats per category
(`netSeatChangeByLean`, after minus before, tested to match the strips); the
count of new districts leaning differently from their predecessor
(`districtsLeaningDifferentlyFromPredecessor`) is a different question, kept
in the method section with why the two can differ (a district built from
several old ones is compared with its largest source only, and an old
district split with no successor with nothing). Every figure comes from
`backend/app/redistricting.py`; the pages only word it. The state ballot
page's House box links it for a redrawn state, a picked district says where
its residents lived before, and a sitting member's profile says where the
residents of the district they were elected in went, until the new Congress
sits.

**Who represents a county today is a different question.** The election
crosswalk answers "which district do I vote in"; in a redrawn state it gives
the new district, whose number names a different place from the one its
sitting member won. `/politicians` answers "who represents me" from a second
file on the lines the sitting members were elected on,
`county_district_crosswalk_sitting.json` (every state on the 119th-Congress
block file, `build_county_district_crosswalk.py --sitting`), with the
cities, towns and communities from the `"119"` map of
`congressional_district_places.json`. Pick a state, type a city, town or
county, and the House cards narrow to the districts covering it (a place
typed in full says which districts it is in, and that it is split when it
is in more than one)
("Districts as of the current Congress"). The state's senators come before
the finder and its House members after it: the senators represent everyone
in the state, so an arrival from the homepage's state picker sees them in a
phone's first screen. The ballot page's finder says "2026
election districts". Each file states the Congress whose lines it holds, and
`GET /api/politicians/house-districts?state=XX` serves the one matching the
Congress in office, so on Jan 3, 2027 the election file (the 120th's lines)
takes over without a rebuild. With no file for the sitting Congress the
finder is hidden rather than showing another Congress's map. Rebuild the
sitting file when the election file moves to the next cycle.

### Race coverage: what counts as coverage

A story is attached to a race by candidate name, and a bare surname is never
treated as identifying — with thousands of FEC candidates the roster's surname
set covers a large fraction of common English surnames. A match therefore needs
the surname **plus** corroboration in the same text:

- `full_name` — the candidate's first name appears with it, as a name (up to two
  intervening tokens, so "Jane Q. Public" matches), **and** the text names the
  race: its state, or another of its candidates by surname
  (`CandidateMatcher.names_the_race`), and
- `surname_context` — the candidate's state name appears.

**A full name identifies a person, not a race.** Until 2026-10-17 it was enough on
its own, and a Hill newsletter whose lead item had a New York House member
campaigning in Michigan was posted as an update on her race. Of the 830 stored
`full_name` news items then, 274 named neither the race's state nor anyone else
running in it (a member's official business, a governor's race the member is
running in, 2028 speculation); read one by one, 9 of them were about the race.
Multi-story digests and newsletters are dropped before matching, by the Action
Center's own rule (`fetch/news_digest.digest_reason`). Both rules reach back:
stored items they reject are removed at the next ingest.

Only `full_name` news items are eligible for posting, and the poster checks
each one again before it says anything (`election_bluesky.refusal`): not a
digest, still names the race, its candidate is on the state's certified
November ballot where the state has certified one (132 of the 148 stored
stories about anyone else were not about the race: a member running for
governor, a primary loser, a senator who died), and its stored relevance
reaches a threshold fitted on hand-labelled items
(`scripts/calibrate_race_post_relevance.py`, `app/data/race_post_relevance.json`):
the lowest whose exact 95% lower bound on precision is at least 0.8 on the older
two thirds, 0.360 (precision 0.872 [0.803, 0.924], recall 0.678; on the newer
third 0.878 [0.782, 0.943], recall 0.637). The page's own Otsu threshold (0.269)
kept stories that were about their race 79% of the time on the same items. The
labels (`app/data/race_post_relevance_labels.json`) carry no names; no source
says which race a story is about, so they are hand labels.

The NY-14 post and the 16 others the 2026-10-17 audit of the month's race
updates found linked to a race by a name-only match or a newsletter (4 with a
feed entry, 12 Bluesky-only from before the feeds) are withdrawn in the public
retraction log (`app/data/retractions.json`): their stories are left off the race
pages and their posts out of the Atom feeds. Civitas deletes nothing on Bluesky
itself.

**The state-name corroboration goes vacuous on a state's own newsroom.** Adding
41 per-state outlets broke an assumption the rule had always relied on: the
Kentucky Lantern says "Kentucky" in virtually every article it publishes, so the
check fires on all of them and `surname_context` silently degenerates into the
bare-surname match it exists to prevent. Measured over the live corpus:

| | n | mean relevance | below 0.1 |
|---|---|---|---|
| national / `full_name` | 351 | 0.301 | 6% |
| national / `surname_context` | 112 | 0.258 | 14% |
| state / `full_name` | 30 | 0.295 | 3% |
| **state / `surname_context`** | **128** | **0.166** | **37%** |

`full_name` is indifferent to which feed an item came from; the weaker rule
degrades 2.6x. That 37% was the Williams sisters' doubles comeback on OH-9, a
Minnesota salmonella outbreak on SEN-MN, and a Kentucky man with a meat allergy
on KY-1. Those matches now have to clear the relevance bar `race_relevance`
derives by Otsu from the corpus — and only where the corroboration is
independently known to be vacuous. Gating the *whole* news feed on relevance was
measured too and rejected: it emptied 64 of 174 races.

They are **hidden, not deleted**. The article is still in the outlet's RSS feed,
so removing the row is exactly what stops `_already_ingested` from blocking it:
deleting them churned, re-ingesting and re-deleting the same items every 15
minutes.

**Syndication defeats a URL-keyed dedupe.** States Newsroom distributes one piece
to its whole network, so it arrives from twenty-odd outlets at twenty-odd
legitimate URLs. One race held 22 copies of a single headline. Deduping on the
headline keeps the outlet that ran it first.

**The open Bluesky name search was removed.** Searching the whole network for a
candidate's name produced 7,740 of 8,239 stored coverage items — 94% — and the
content was not coverage. Four successive filters were built against it and each
failed in a different direction: source-type discarded real local newsrooms;
relevance admitted campaign material (maximally on-topic for a campaign);
no-advocacy still admitted mockery and a football post; and a DNS-verified
domain-handle rule does not catch a shitposter who owns a domain. A name mention
is not coverage, and four filters could not make it one. Widening the *sources*
(the 41 state newsrooms) is the fix that filtering was standing in for.

### Ballot measures

Statewide measures are shown at `/elections/states/<ST>` alongside that state's federal races. The rules the code enforces, all of which exist because this is the surface where an error can change a vote:

- **Everything is verbatim.** Official ballot title, official summary, fiscal statement and the state's own YES/NO descriptions are stored and rendered exactly as published, with the source linked. **No LLM touches ballot content at any stage**, and there is no plain-language rewrite — `grounding.py` verifies that tokens in generated text came from the source, not that a claim points the same *direction* as its source, so a summary that inverts a measure's meaning ("a YES vote repeals this" where approval *retains*) would pass every check we have. `ungrounded_electoral_claims` is additionally inert on this corpus: it only fires when "ballot"/"voters" are absent from the source.
- **Yes/no framing is lifted or left NULL**, never derived. The intuitive derivation inverts on a veto referendum.
- **Keyed on election date, not cycle year.** Ohio can run an "Issue 1" in a May primary and a different "Issue 1" in November.
- **Voter deadlines are each state's own, quoted and re-checked.** Every state page opens with when registration closes (online, by mail, in person, and same-day where the state has it), the early in-person voting window, and the mail-ballot request and return deadlines, each beside a countdown ("Online registration closes Oct 19 · 6 days left"); the homepage's "this week" strip lists every state's next deadline, soonest first. Each rule comes from the state election authority's own page, or its statute where the authority's site sits behind a bot wall (never worked around), and never from an aggregator (`backend/scripts/election_calendar_sources.json`). `scripts/build_election_calendar.py` turns those into `app/data/election_calendar.json`: it counts every "N days before the election" date and keeps the rule's text beside it, refuses a stated date that disagrees with its count unless the source's reason (a weekend or holiday) is recorded, and re-reads every cited page to confirm the quoted passage is still on it — `verified` (on the authority's page) or `statute` (in the statute's official text), shown on the page with the source link. An item it cannot verify (its page walled, gone, or no longer carrying the passage) is refused: the script writes nothing and names it, so no deadline is ever published from memory. A state with no deadline readable from any official source is marked `unread`: its page lists no dates, says so, and sends the reader to the state's own voter lookup and county board, and it is left out of the homepage strip (none today). An item may also name `republished`: a county election office's printing of the same deadline, read only while the item's own source can't be read (a wall or an outage, never a page that answered without the passage), tried after the state's own source on every run, and labelled on the page and calendar ("These deadlines are as published by the Board of Elections in the City of New York; New York's own source blocks automated reads"). New York's deadlines cite its Election Law on nysenate.gov, which answers a Cloudflare challenge some of the time, with the New York City board's printing as the fallback. Where a deadline stands is computed in the state's own time zone (`app/voter_calendar.py`; a state spanning two zones uses the one where a deadline passes last, so a page never says "closed" while it is open somewhere in the state). A passed deadline stays and says so ("Registration by mail closed Oct 5. Still open: In-person registration closes Oct 23"), never disappears. Regenerate the file each cycle. **Where to register** comes from the same sources file: each state's `register` page (its online registration where it has one, else its registration page), which the script fetches and keeps only if it answers on an official host (`.gov`, `state.xx.us`, or the domain the state's own citations use) and reads as a registration page; one behind a bot wall is kept as `blocked` and the page words it as unconfirmed; a 404 or a page off the state's domain is refused like a deadline. North Dakota's entry is `none`, held to the quote "North Dakota does not have voter registration" on its Secretary of State's page; a state without an entry links vote.gov (`registerFallback`). Each state page leads with "Register online by Mon, Oct 19", the days left and the link, the same-day or late registration that follows in the state's words, and a ribbon of every deadline from today to election day (the list beneath is its text equivalent, open by default); `/elections`, the homepage's This week and the calendar list the states whose regular registration closes in the next 10 days (`voter_calendar.registration_closing`, `CLOSING_SOON_DAYS`). The script reports every failed citation in one run, not just the first.
- **Drafter attribution renders with each quote** (`title_authority` / `fiscal_authority`). Ballot titles are litigated precisely because they are contested, so naming the author is more neutral than the bare quote.
- **Removed measures render as removed** for a 45-day grace window rather than disappearing, and a sync returning implausibly fewer measures than are on file is treated as a bad response, not as news.
- **"No measures" and "not ingested" are different states.** `MeasureCoverage` carries `covered` / `confirmed_none` / `not_yet_covered` / `ingest_failed` per state per election, because an empty section on a state's ballot page reads as "nothing to research".
- **Scope is stated on the page, and the statement shrinks.** A ballot is defined per *ballot style*, not per state, so state legislative seats, county/municipal offices, judicial questions and local measures are enumerated as omissions above the content — and each entry is removed as that gap actually closes, rather than standing as permanent boilerplate (statewide executive offices came off the list this way; see below), with a link to the voter's own election office. That link is each state's (and D.C.'s) own voter lookup, the page where a voter sees their sample ballot or polling place, listed in `app/data/state_ballot_lookup.json` with what was checked for each (`_checked`): the state's own domain or a vendor portal the state links, never an aggregator, and where the lookup itself refuses automated checks, the election authority's own polling-place page that links it. A per-state link is shown only after the Election run's check (`ballot_lookup.refresh_link_verification`, every run, candidates read from the bundled file so a corrected URL ships with the next deploy) found it answering: below 400, not a bot wall's challenge (Imperva answers its challenge with a 200), and not a redirect to its site's homepage. A server that sends its certificate without the intermediate that signed it is checked the way a browser reaches it: the shared client (`app/http_client.py`) fetches the intermediate from the certificate's own AIA address and still verifies the chain to a trusted root (Alaska's polling-place page read as dead from 2026-10 for this alone); any other certificate fault fails. A link that fails — a 404 from a host that answered, a redirect to the homepage, no answer — is hidden at once and the page falls back to the USAGov directory. A link the check is *refused* on (a bot wall's challenge, or a 401/403/429) is not a dead link: the state publishes the URL and it opens in a real browser, so the run records `blocked_at`, and the page shows it as "<State>'s official voter lookup" with the note "This site blocks automated checks, so we can't confirm it's up" and USAGov's directory as the backup (`officialLookup.unconfirmed`, `officialLookup.fallback`). The wall is never worked around. Ten states' lookups sit behind one (October 2026: AZ, GA, IA, MA, MI, NH, NV, NY, OH, RI; the Akamai wall on New Hampshire's answers a bare 403 with no challenge script, which is why a refusal status counts on its own).

#### Where each state's measures come from

Forty-five states are read directly from their own offices (`ballot_measure_pdf_sources.json`, one reader each in `fetch/ballot_measures_<st>.py`, sharing only fetch plumbing in `ballot_measures_state_common.py` and the pipeline stage in `ballot_measures_pdf.py`). No two publish alike, so each reader is built against its state's real, currently-fetched document, never a guessed layout:

- **Northeast.** Vermont reads the General Assembly's statutory amendment notice (2026: Proposals 3 and 4); Maine the Secretary of State's Citizen's Guide, found from the SOS pages because its filename changes yearly, with each question's ballot text taken from the guide's own listing and the Attorney General's "A YES vote …" sentences; Massachusetts its Information for Voters guide (the Secretary filed the 2026 guide at a new address, `.../information-for-voters/archive/IFV_2026 English.pdf`, while the old pattern answered 404; the page that links it answers with an Incapsula challenge, so `url_pattern` lists every address the Secretary has used and they are tried in order — only a 404 moves on, and a 404 at all of them is not yet published, which the expected-by cutoff turns into a failure). Connecticut and New Jersey prove absence rather than list presence: Connecticut prints any statewide question on every town's ballot, so one complete official town sample ballot with no question column proves there is none; New Jersey's per-year page lists each public question (its template carries a commented-out 2023 question re-dated every year, so comments are stripped first), and "none" needs the page to show this year's general-election certification.
- **South and border states.** Alabama (the Fair Ballot Commission's statement per amendment), Arkansas, Florida (the answer to its initiatives database's search form), Kentucky, Louisiana, Maryland, Missouri (Amendments and Propositions — 2026's Proposition A is the referendum on the congressional map), North Carolina (a one-page report from the elections board's ballot database), South Carolina, Tennessee, Texas, Virginia and West Virginia (the Secretary of State's dated public notice). Arkansas keeps no page recording that a citizen petition was certified, so its list is published only while the Attorney General's petition section for the year reads "None".
- **Midwest and Plains.** Illinois, Indiana, Kansas, Michigan, Minnesota, Nebraska, North Dakota, Oklahoma and South Dakota. South Dakota's certified list must equal the Attorney General text in its pamphlet, and Nebraska's initiative range must equal its pamphlet; Nebraska's legislature-referred amendment's ballot statement is a scanned image, so the Legislature's own resolution supplies its ballot language. Oklahoma's filed State Question PDFs are scans (OCR with errors, and every superseded version of the title), so no OCR'd text is stored: a ballot title is quoted only from the State Election Board's accessible text copy of the final title where it posts one (2026: SQ 845, the August 13 corrected title), and otherwise the register's SQ number and subject line are stored, linking the filing (2026: SQ 847).
- **West.** Alaska (the Division of Elections' own sample ballot), Colorado (the Blue Book), California (the Official Voter Information Guide as HTML — the current guide or the Secretary's archive for the year — with the Attorney General's official title and summary, the Legislative Analyst's fiscal estimate and the state's own YES/NO sentences; the PDF edition isn't read, because its 2026 text layer turns the fi/fl/ff ligatures into a bare "f" and reads "infation", "fnance"), Hawaii, Idaho, Montana, New Mexico (the Secretary of State's "Statewide Constitutional Amendments and General Obligation Bonds", whose sentences are the printed ballot's — county sample ballots carry the same wording under "CONSTITUTIONAL AMENDMENT N" / "BOND QUESTION N"; it replaced the Legislative Council Service's capitalised joint-resolution titles and the per-year bond act's "HB 248 (1)" labels in 2026-10, with the Spanish half's counts as a cross-check), Oregon (proves absence, like Connecticut: the State Voters' Pamphlet, which ORS 251.185 makes carry every state measure, with no measure in its contents or on any page — 2026's has none), Washington (the Attorney General's ballot title as the official title and the ballot measure summary as the summary, two fields) and Wyoming.
- **Arizona, Georgia, Nevada, New Hampshire, Ohio, Utah and Mississippi.** Georgia (the Secretary's amendments booklet, read from its four-column summaries pages: the Publication Board's caption, the resolution's ballot question, and the Attorney General / Secretary / Legislative Counsel summary), Nevada (the Secretary's ballot-questions booklet: each Condensation, the booklet's own "A “Yes” vote would …" sentences and the Legislative Counsel Bureau's fiscal note, checked against its contents page), New Hampshire (the Secretary's "Questions on the General Election Ballot": 2026's CACR 13 and the statutory school tax-cap question HB 1300 puts on every municipality's ballot, stored with its placeholders as printed — kept as a statewide question because the Secretary lists it among the questions placed on the general-election ballot and every New Hampshire voter sees it, though each municipality decides it for itself), Ohio (the Secretary's official sample ballot, whose state-issue page gives the Ballot Board's ballot language and the ballot's own YES/NO sentences — read by what is on each page, because 2024's issue page carried the candidate pages' footer), Utah (the Lieutenant Governor's general-election certification, which states that no proposition qualified and lists the amendments — the latest amended certification where one is linked (2026's, `...-Certification-Amended-09-21.pdf`, replaced the original and was missed as "not yet published" until the reader accepted the amended name), read in the PDF's own text order because its OCR layer's overlapping words interleave in the default order; it is a scan whose text layer is OCR, so only each amendment's letter and heading are stored and linked to the certification, as with Oklahoma: OCR'd text is not verbatim text) and Mississippi, which proves absence the way Connecticut does: the Secretary's complete statewide composite sample ballot with no measure on it. New Hampshire's CDN, like Michigan's, rejects the User-Agent's contact comment, so it is sent without it. Georgia's and Nevada's Secretaries of State put their whole sites behind bot walls (Cloudflare and Imperva; a challenge page from the production host too, checked 2026-10-01), so those two readers raise `SourceBlocked` and the state's own booklet is read from a county election office's republication of it — Augusta-Richmond County's Board of Elections for Georgia (the same 152,039-byte file as the Secretary's), Eureka County's Clerk-Recorder for Nevada (`republished_by` in the registry). Cloudflare does let a request through to sos.ga.gov now and then (the pipeline was served the real amendments page on 2026-10-14): the page's own checks then apply, a challenge on the booklet behind it is the wall again (the county copy), and the booklet's link label is not taken as a list of what it holds — that page labelled 2026's booklet with a referendum or ballot question while the booklet holds Amendments 1-3 only, so a referendum refuses Georgia only when the booklet's cover or a section title names one or the page links a separate referendum document. Arizona is read the same way: azsos.gov and apps.azsos.gov answer a Cloudflare managed challenge (403, `cf-mitigated: challenge`, whatever the User-Agent, checked 2026-10-08), and the reader asks once — the same client's retry, carrying the cookie the challenge set, was let through, and retrying through a challenge is getting past it — so the Secretary's Publicity Pamphlet is read from the Citizens Clean Elections Commission's copy (a state commission; the county election sites checked answered 403 too, and Pima County posts its own compilation of the ballot language rather than the Secretary's document). From each proposition's Ballot Format page it stores the designation, the descriptive title (prepared by the Secretary and approved by the Attorney General, A.R.S. 19-125(D)) and the ballot's own "A “yes”/“no” vote shall have the effect of …" statements, checked against the pamphlet's table of contents (2026: Propositions 141, 142, 144 and 316–320). The copy must pass every check the state's own copy would (cover, contents list, every part present); a state page that was read and refused never falls back; and the card says "Quoted verbatim from the Nevada Secretary of State, as republished by Eureka County Clerk-Recorder" (`BallotMeasure.republished_by`). Utah's reader was built on the state's own document; its module docstring says what could not be checked from the development container.
- **Rhode Island** is read from the Department of State's Voter Information Handbook (`VoterRef<yy>.pdf`): each question's "What it will look like on the ballot" box, cut into its two columns — the printed heading and description on the left, the state's own "Your vote to “Approve” / “Reject” means …" sentences on the right — checked against the handbook's contents (2026: the five bond questions).
- **Confirmed none by law**: Delaware. Its Constitution has the General Assembly adopt amendments with no popular vote (art. XVI, §1) and provides no statewide initiative or referendum, so no statewide measure can be on its ballot. Its `unread` entry carries `none_by_law` (`source_name`, `basis`), and the sync records it `confirmed_none` citing that law. A fact about one election never goes there — Oregon's empty pamphlet is read by a reader every election.
- **Not yet covered**: D.C., Iowa, New York, Pennsylvania and Wisconsin, each with an official source that Civitas does not read automatically yet (D.C.'s Initiative 86 has qualified but no ballot document with its certified wording was posted when checked; Pennsylvania advertises its proposed amendments three months ahead under Pa. Const. art. XI, §1, but no official page confirming none was found, so it stays not covered; Iowa's and Wisconsin's sites answer a Cloudflare challenge, which is not worked around — see each `dev_note`). There is no third-party fallback (the Vote Smart integration was removed in 2026-09; `_purge_retired_source` deletes any row it left, and a leftover `VOTESMART_API_KEY` in `.env` is ignored rather than failing startup). Each state is recorded `not_yet_covered` — never `confirmed_none`, since nothing was checked (Delaware's law-based none above is the one exception) — with no alert, and its page gives the state's `reason` from the registry's `unread` map (`measureCoverage.unreadReason`) beside its official lookup link. A `reason` is shown to voters on the production server, so it must be true wherever Civitas runs: a fact about what the state publishes, or "Civitas does not read {state}'s official measure list automatically yet" — never a claim about network access (a site that blocks this development container may answer production fine), and never a date, a year or "this election", since the same sentence is shown every cycle (tests enforce both). A reader de-registered mid-cycle leaves its rows for the election as they were — still certified, since marking them removed would claim the state struck them — and the page says Civitas has stopped reading the state's measures automatically (without repeating the reason, whose "… yet" would contradict it), dated by that source's last read, never as a failed check; an unread state shows no "last attempt" date, since none is made. What was actually found (a Cloudflare challenge, a firewall, a captcha, from which machine and when) goes in the entry's `dev_note`, which the API never sends. Every such state must have a reason there, and a state leaves `unread` when a reader is registered for it; a test enforces both.

**Three answers, never two.** Every reader returns a list, `None`, or raises `NotYetPublished`, and those map onto `covered`/`confirmed_none`, `ingest_failed` and `not_yet_covered`:

- **"None" only from the state's own statement, for this election.** A reader returns `[]` only when the state affirmatively says so for *this* election — Illinois's Voters' Guide sentence "There are no statewide questions of public policy for this election", Minnesota's stated count of zero, a certified-list section present for this election with nothing in it, a complete Connecticut town ballot with no question column. Addresses reused every cycle are checked to name this election first. Confirmed none for November 3, 2026 as of 2026-09-28: Connecticut, Illinois, Mississippi (the Secretary's complete statewide composite sample ballot, with no measure on it), New Jersey, South Carolina (its election commission lists 42 referendums, every one a county question, while the same query for 2024 returns that year's statewide amendment) and Texas (the Legislative Reference Library's index of amendment elections has no entry after November 4, 2025). An empty answer is cached for six hours (`EMPTY_RESPONSE_TTL_HOURS`), so every nightly run asks again; a non-empty list is cached for 72.
- **Anything measure-shaped that can't be read fails the whole state.** A heading, row, notice or section the reader doesn't recognise refuses the state's answer (`ingest_failed`) rather than being skipped, and so does a document with no readable text at all — a list published one measure short would read as the complete ballot. So does an answer in which two measures would share an id (the second would overwrite the first). A heading a reader *skips* never reaches that refusal: when Nebraska's elections page became an FAQ (October 2026), its "2026 General election" heading missed the reader's exact-case marker, the three initiatives vanished, and only the implausible-shrink guard (1 found, 4 stored) kept the ballot from publishing short. Its headings are now matched without case, and both page shapes are read.
- **"Not yet published" is neither.** Where a state posts its document late or only in a year that has a measure, its absence raises `NotYetPublished`: `not_yet_covered`, no alert, re-checked next run. That covers Maine's guide, West Virginia's notice, Michigan's November document, Tennessee's per-election page (a 404 in every non-gubernatorial year), Kansas's and Missouri's pages while they still show the primary's measures, Alaska's sample ballots (posted ~50 days out), California's guide for a year the Secretary hasn't posted or archived, Colorado's Blue Book, Massachusetts's guide, and Vermont's and Wyoming's documents (`absent_until_published` in the registry), New Hampshire's questions document (posted only in some cycles), and Indiana and Oklahoma, whose sources are not certified lists and so can never say "none". It is raised only after every page was actually read (a crawl or paged listing that stopped at its page budget is not a complete read); a page that couldn't be fetched is a failure, not an absence. A failure records the reader's own reason — the HTTP status, the page it didn't recognize, the document it couldn't find — on the state's coverage row and in that night's ops alert, rather than a bare "fetch failed". It turns into `ingest_failed` — every night, until the document is back — when this same source has already read this election (its measures are on file, or it recorded a successful check; coverage another source left behind doesn't count), and — for documents a state publishes for every general, like a sample ballot or a voter guide with a mail-by date — once the state's expected-by cutoff passes (`expected_by_days_before` in the registry, citing the statute where one sets it, e.g. California's 21 days and Colorado's 30; otherwise `DEFAULT_EXPECTED_BY_DAYS_BEFORE`, 14). A document that exists only in a year with a measure stays `not_yet_covered` — its absence can be the answer — but at that default date a low-severity ops notice goes out once per state and election, in case the reader broke before it ever read anything.

**Official titles and drafters.** `official_title` is only ever the state's own ballot title, and only when a reader supplies it — the display `title` may be a label the reader composed ("Proposition 3", "Question 1: Citizen Initiative") and is never shown as official. `title_authority` names the drafter of the official title, or of the quoted summary where there is none, and the card renders "Drafted by …" beside whichever it names; one quote carries one drafter, so two drafters' texts are never joined (Idaho keeps the ballot question and the Legislative Council's statement in separate fields). Where the state names a different drafter for the summary or the yes/no sentences, `summary_authority` and `framing_authority` carry it and the card prints each beside its own text: Alabama's Fair Ballot Commission (summary and YES/NO under the Legislature's question, Ala. Code §17-6-81), Georgia's Attorney General, Secretary of State and Legislative Counsel (the summary; the caption is the Constitutional Amendments Publication Board's), Missouri's Secretary of State (fair ballot language, RSMo 116.025), North Dakota's Secretary of State with the Attorney General (NDCC 16.1-06-09), Nebraska's Attorney General for petition measures (Neb. Rev. Stat. §32-1410), Virginia's Privileges and Elections Committees (the explanation, which says it approved it) and California's Attorney General (title and summary). Missouri's drafters depend on how a measure reached the ballot: a petition's summary is the Secretary's and its fiscal note summary the State Auditor's; for a General Assembly referral the Assembly may write both and otherwise the Secretary and the Auditor do (RSMo 116.155, 116.160, 116.170), and since the page does not say which, both are named. A summary and a ballot title are never swapped or relabelled: Arkansas's notice's "Ballot Title" is the official title (the Popular Name had been stored there and the Ballot Title as the summary), Virginia's "Should the Constitution of Virginia be amended …?" is its official title, and no label of ours is written into stored text (Washington's "Ballot Measure Summary:" and Georgia's "Summary:" were). Drafters are read from the source where it names them — Florida's Amendment 3 carries the Attorney General's court-ordered rewrite, Maryland's Question 3 the language the Supreme Court of Maryland ordered, Colorado's ballot titles the General Assembly or Title Board according to the Blue Book's own "Placed on the ballot by" line — and a fiscal statement whose drafter the document doesn't name (Montana's) is left unattributed. YES/NO framing is stored only where the state writes one; "For"/"Against" choice labels name a choice rather than explain it, and stay null. It is stored as the state's whole sentence, lead-in included ("A “yes” vote on Amendment 81 requires …", "A “yes” vote will repeal …", "Yes – Means you approve …", "A YES VOTE would …", "A “yes” vote shall have the effect of …"): Colorado's, Missouri's, North Dakota's, Massachusetts's and Arizona's readers used to keep only what followed, so every yes/no line in those states was a fragment of the source's sentence. Missouri's fair ballot language closes with the tax statement RSMo 116.025 requires ("If passed, this measure will …"), which is about the measure rather than a NO vote; it is kept, verbatim, after the fiscal note summary, and both drafters are named. A table inside a ballot title (Colorado's Amendment 87 lists the estimated tax change by income category) is read with its wrapped column headers joined column by column; read row by row they had interleaved into nonsense.

**Order.** Measures are listed in the state's own order: every answer records each measure's place in the reader's list (`source_position`), which follows the state's document. Sorting the printed number as a string had put Louisiana's 10 before its 2 and Colorado's Propositions 132–137 before Amendments 81–87; a row written before the column existed sorts after, its number compared digit-run by digit-run.

**Writing a state's answer.** A successful direct read is written as one transaction: rows it supersedes for that election only (another source's, such as a renamed or re-pointed registry entry's — replaced, not struck, so never shown as "removed") are deleted, the measures upserted, this source's unseen rows reconciled to removed, and coverage set; if any write fails, none of it happens and the state reads `ingest_failed`. A read returning fewer than half the measures on file for that election (`MEASURE_SHRINK_FLOOR`) is held back as a likely bad response — unless the state itself reports the drops (Florida's "Removed" status), or the same shorter list comes back from `MEASURE_SHRINK_CONFIRM_RUNS` (3) consecutive fresh reads, when it is the state's real answer (a held-back list is dropped from the cache, so a cached copy of one bad response never counts twice). After a read that failed or found the document missing, the page keeps the last list it had but says so, with the date of the last successful read (which only a covered or confirmed-none read ever sets). The page shows only its own election's measures; earlier elections' rows are pruned after the removal grace window, but never the election the site is still on (a slow count keeps the page on the election just held past that window, up to January 3). A source that says a measure is gone — Florida's "Removed" status, Oklahoma's register no longer dating a State Question for this election — has it marked removed. One that can only stop publishing (Michigan's November document disappears when its only proposal is struck) keeps alerting nightly until an operator checks the state's announcement and accepts the absence: `POST /api/admin/ballot-measures/{ST}/{YYYY-MM-DD}/accept-absence?note=...` (admin token) marks every row for that election removed (any source's — no row may stay current under an operator's "none"); a state with no registered source is refused (400), since it reads not yet covered and records confirmed none with the note. It is refused (409) while the state's latest read covered that election unless `force=true`; every action is logged and kept in `measure_coverage.operator_actions`. The answer is restored each night the reader again finds the document absent (a genuinely failed night still alerts), and replaced when the reader answers. The page words it as our operator's determination (`measureCoverage.basis = "operator"`), never as the state's statement. The ingest-failure alert is keyed per night and per set of failing states, so a failure is heard every night it lasts; the late-cycle notice is once per state and election.

An optional town selector on the same page surfaces LOCAL races (city council, school board, local measures) that a statewide page structurally can't show — a real ballot is defined per precinct, not per state, and the only address-keyed source (Google Civic's `voterInfoQuery`) requires an address. Rather than a visitor's own address, which this platform's architecture exists to never send off-box, it's called with a fixed, publicly-known representative address per town (town hall) from a small hand-curated pilot list (`backend/app/data/town_directory.json`) — every visitor who picks the same town sends the identical request, so nothing visitor-specific ever leaves the server. This is a real approximation, not a precinct-accurate lookup (a town can contain more than one precinct), and the page says so next to the selector. Gated on `GOOGLE_CIVIC_API_KEY`; unset means the selector doesn't appear and the statewide page above is unaffected.

### Statewide executive offices

Governor, Lieutenant Governor, Attorney General, Secretary of State and Treasurer are confirmed through the same adapters, the same config file and the same `_contract` described above — no new source, no new vendor, no new key. A state that publishes its primary results publishes these contests **in the same response** as the federal ones; they were simply being parsed and discarded.

- **Two gates, opposite directions.** The federal-only rule above still holds — `parse_office` must never read a state legislative seat as federal. Its new sibling `parse_statewide_office` must never read a county or municipal office as statewide. Against Rhode Island's real 192-contest primary the second finds exactly 9 and refuses all 177 others (133 General Assembly seats, 21 party committees, 23 local offices). A joint "Governor and Lieutenant Governor" ticket resolves to the top of the ticket.
- **Stored as `StatewideNominee`, not `Candidate`.** A state office has no FEC filing, no finance totals and no Representation Score, so forcing one into the federal table means a row whose every financial field is permanently null. For the same reason the name is kept whole rather than reduced to a surname — there is no FEC row to match it to. Ballot annotations still come off (Rhode Island marks party-endorsed candidates with a bare asterisk).
- **`statewide_offices` is a truth condition, not a feature toggle.** Every adapter returns only what its own state's feed contains, so a state nobody has checked returns zero executive contests — indistinguishable from a state that genuinely elects none. The flag says that state's real contest labels were checked against the parser, which is what makes a count of zero a claim rather than an admission.
- **`omits` shrinks as the gaps close.** The page's "Governor and other statewide executive contests" omission is dropped for any state actually covered. The judicial and legislative lines name only gaps that exist (`app/data/state_ballot_scope.json`, hand-maintained with its sources): no judicial line where the state appoints its judges or elects them at another election (Hawaii, Massachusetts, New Hampshire, New Jersey, Tennessee's August ballot, Wisconsin's April one), "Judicial contests" without "retention questions" where the state holds no retention votes, retention alone once contests are covered, and no "State legislative districts" in a year the state elects no legislature (Louisiana, New Jersey and Virginia in 2026). A disclaimer list that keeps disclaiming what the page now shows stops describing the page and becomes boilerplate a reader learns to skip — past the entries that are still true.

### State legislative seats

The same feeds carry the state's own legislature — 133 of the 192 contests on Rhode Island's real 2026 primary ballot are General Assembly seats — so these are read by a third parser gate beside the federal and executive ones, and stored as `StateLegNominee` under the same `statewide_offices` opt-in.

The hard part was not the data. It was **how a reader finds their seat without being asked where they live** (see core design principle 8). A U.S. House row can name its counties; a state legislative district is far smaller than a county, so the useful unit is the town or community — with the county listed too.

`scripts/fetch_state_leg_crosswalk.py` builds that list from two Census files joined on the 2020 block: the Redistricting Data Office's **2026 state legislative block equivalency files** (August 2026; every block -> the district it lies in on the lines drawn for 2026, with corrected files for Michigan's senate, Minnesota and Mississippi replacing the national rows) and the 2020 **P.L. 94-171 geographic header** (every block's population, county, county subdivision and place). The overlap is weighed by the people living in it, so an empty sliver of land lists nothing, and a place or county is listed for a district once at least 50 of its residents live inside it — about one census block's worth, roughly what a block cut by the district line and assigned whole to one side can misplace — or half the residents of a smaller place. Names are today's (the 2025 Gazetteer), so a CDP that has incorporated since 2020 carries its new name, and a place created since 2020 (a merger, a new city) is drawn from TIGERweb's current boundary and given the blocks whose centres fall inside it. A district lists:

- **Towns**, in the twelve "strong MCD" states (Census's term — Rhode Island, Pennsylvania, Wisconsin…), where county subdivisions are real municipalities people vote in.
- **Every place**: incorporated places *and* Census Designated Places, Census's name for a recognised community with no municipal government. Incorporated places alone missed where most suburban Americans live — Silver Spring, Bethesda, Columbia, The Woodlands and every town in Hawaii are CDPs. A CDP is written without the " CDP" tag, which nobody types.
- **Every county** it touches, named the way the U.S. House crosswalk names them ("Montgomery County"), so typing a county finds every district in it.

Until those block files existed (the 2020 Block Assignment Files carry the 2011-12 lines), the script sampled a grid of points inside each town against TIGERweb's current district polygons. The block join reproduces that method's verified Rhode Island answers exactly — Jamestown -> House 74, Barrington -> 66 and 67, Cranston -> 9 districts, Providence -> all 14 — and adds the villages the old list had no room for (Wakefield-Peace Dale, Watch Hill).

The result is that typing "jamestown" returns House 74 and Senate 13, and typing "silver spring" returns the Maryland districts that hold it, without a visitor ever entering an address.

Coverage is not limited to one vendor: the bulk `tabular` exports carry the same contests, so a state there is a config opt-in too. Minnesota's real 2026 export resolves 18 federal, 8 statewide (including its joint `Governor & Lt Governor` ticket) and every one of the 32 legislative contests the file contains — while refusing `County Attorney` and `County Auditor/Treasurer`, which contain the exact words the statewide gate keys on. Note that some `tabular` entries deliberately fetch a federal-only export (New Mexico's URL carries `type=FED`), so opting such a state in means widening its discovery URL first.

Georgia is the third state live, and the most complete: 30 federal races, all 236 legislative seats (exactly its 180 House + 56 Senate), and every one of its statewide contests — the eight constitutional offices plus both Public Service Commission seats, which are elected statewide but *held by district*, so each is its own row.

Getting Georgia right needed two things the first two states didn't. Its county subdivisions are Census County Divisions named `Fairburn-Union City CCD` — names on no sign and in no address — so place-based states use places instead, chosen by Census's own "strong MCD" distinction. And every district's counties are listed beside its places, with the county suffix kept, because Georgia has both a Forsyth County and a Forsyth city and the list would otherwise show the same word for two different places.

Idaho and Washington are the fourth and fifth, and they needed a distinction the first three didn't. Both elect **two representatives from one district** — Idaho as `Seat A`/`Seat B`, Washington as `Pos. 1`/`Pos. 2` — so the seat, not the district, is what tells their contests apart. Census settles which is which: it draws 134 lower-chamber polygons for Minnesota (`10A`, `10B`, …) but only 35 for Idaho and 49 for Washington. So Minnesota's letter belongs to the *district* and Idaho's to the *seat*, and the two seats of an Idaho district correctly share one town list. Read as one district instead, half of each state's House would simply have vanished.

North Carolina is the sixth, and it exposed a gap that had been silently halving it: its lower chamber is labelled `NC HOUSE OF REPRESENTATIVES DISTRICT 1`, with no "State" anywhere, so only its Senate was matching. A **bare** "House of Representatives" is exactly what the federal gate must always refuse — it is a state chamber's name in most of the country — and that is what makes it safe to claim as a state seat here, since the legislative gate refuses anything the federal one recognises before testing a pattern at all.

California, Florida and Utah bring it to nine. Each was one vocabulary gap from honest coverage: California's entire **Assembly** was unmatched (`State Assembly Member District N`), as were its bare `Treasurer` and `Controller` — it prints them with no "State" prefix at all — so it looked like a 20-seat legislature rather than the 100 seats actually up. Florida was missing `Chief Financial Officer`, its fourth cabinet officer; Utah its `State Board of Education`, seated by district like Georgia's PSC.

California is also the first **top-two** state covered, where two candidates of the *same party* legitimately advance from one contest — its Board of Equalization District 2 sends two Democrats to November. That only survives because a nominee's name is part of the storage key.

Maryland makes ten, and brought the **third** multi-member shape. Not Idaho's `Seat A`/`Seat B`, nor Washington's `Pos. 1`/`Pos. 2`, but `House of Delegates District 10 … Vote for up to 3` — three members from *one* contest with no seat designator, so the number advancing is a property of the contest and is read from its own label. That is applied only where a state runs one-nominee party primaries: under top-two exactly two advance no matter how many seats are filled, so a seat count there would answer the wrong question. Its District 3 correctly shows three Democratic and two Republican nominees.

Nebraska is the eleventh, and the first on a different vendor — the TotalVote platform, which mixes state offices onto the same pages as the federal races. It needed one addition: `Auditor of Public Accounts`, Nebraska's own name for the office and the last of its five statewide contests. Its Legislature is unicameral **and officially non-partisan**, so no legislative contests reach the gates at all and its page correctly keeps the "State legislative districts" omission — five omissions rather than four, which is the honest count.

Montana and South Dakota run on that same vendor. South Dakota was the instructive one: its page yielded exactly one contest, Governor, with nothing unmatched — so coverage would *look* complete while silently omitting the Attorney General, Secretary of State, Auditor and Treasurer it also elects. A clean unmatched list proves nothing when the page itself is scoped. South Dakota is now covered from a different source entirely — the Secretary of State's own November candidate list (`sd_vip`), which names every statewide office including its `Commissioner of School and Public Lands` and `Public Utilities Commissioner`. Montana is covered by its calendar (below).

Colorado, Iowa and West Virginia complete the Clarity vendor, bringing it to fourteen states. Each was blocked by exactly one wording: Colorado's `Regent of the University of Colorado — Congressional District N`, a statewide body seated by *congressional* district; Iowa's `Secretary of Agriculture` and `Auditor of State`, its own names for offices already known under others; and West Virginia's `HOUSE OF DELEGATES, 1st District`, which puts the number **before** the word — so the `District N` pattern read nothing and all 117 of its seats were invisible.

Arkansas and North Dakota make sixteen. Their vendor pre-filtered contests by type — Arkansas to `Federal`, North Dakota's results to `SW` — which discarded every state contest before a gate could see one. Those filters exist only to narrow the federal case cheaply, so they are dropped for a state reading its own offices; the unscoped fetch was measured first at 1.9 MB in under a second. North Dakota also states a contest's seat count as a **structured field** (`voteFor: 2`) rather than in the label, which is how its two-members-per-district House reads correctly: District 1 nominates two Democrats and two Republicans.

Arkansas exposed something the earlier states had hidden. It publishes only two of its seven statewide offices, because the other five were unopposed and its feed does not itemise them — so the statewide section now carries the same caveat the legislative one always had: only offices the state's own feed names appear, and an uncontested primary is often not published at all. (Since 2026-10-08 Arkansas's state offices come from its candidate search instead, above, and the caveat shows only when that cannot be read.)

Hawaii and Illinois were config opt-ins on `tabular`: Hawaii's summary file already carried its Governor and Lieutenant Governor contests, and Illinois's by-office CSV links only needed the discovery pattern widened past `CONGRESS|SENATOR` to its five executive offices (its capitalised `GOVERNOR AND LIEUTENANT GOVERNOR` resolves to the top of the ticket like Minnesota's). A contest whose title *mentions* an office is not that office's race — `Constitutional Amendment 1 - Governor appointments` — so ballot questions are refused before any office pattern is tried.

**Six states elect no executive officers in some cycles**, and saying so is a claim like any other. Virginia and New Jersey choose governors in odd years after a presidential election; Kentucky, Mississippi and Louisiana in the odd years before one; Montana in presidential years. The `statewide_offices_basis` key carries the calendar sentence, with no year in it, and is said only in a cycle when it is true: `state_ballot_scope.json`'s `statewide_executive` rule for each of them decides (`state_candidates.statewide_none_basis`), so Montana's sentence holds in a midterm and, in a presidential year, an empty feed is a feed with nothing in it yet rather than a confirmed absence. The page shows the sentence in place of "as published by", which would claim a reading that never happened. It says nothing about the legislature: Kentucky and Montana elect legislators in even years, and those pages keep "State legislative districts" among their omissions. A main source that fails while a certified federal list answers claims nothing about state offices at all, rather than syncing an empty list that would read as a confirmed absence.

Kansas and Missouri needed no new vocabulary, only their bespoke PDF readers taught to keep what they had been skipping. Kansas's official primary totals print every state contest after the federal ones: its five executive offices (the joint `Governor / Lt. Governor` ticket stays whole under the Governor, as Minnesota's does), the five odd-numbered `Member, State Board of Education` seats, `Kansas Senate 24` and `25` (unexpired terms — the Senate is otherwise elected in presidential years) and all 125 House seats. The document writes a seat as a bare trailing number, which the shared gates read only after the word "District" — so the header is restated that way and the gates still decide; without it the five board seats collapse into one office and overwrite each other. Missouri's certified November ballot lists `For State Auditor` — its only executive office in 2026 — and `For State Senator` / `For State Representative` in each party section, in the same `District N, Name` shape as its U.S. House lines: 3 Auditor candidates (R, D, Libertarian), 34 Senate candidates across 17 districts and 305 House candidates across all 163, two of them independents. It is the ballot itself, so every listed name is a record, exactly as for its federal offices. The partisan `For Circuit Judge` blocks inside those same party sections are refused; judicial coverage is its own claim.
South Carolina and Texas, like South Dakota, are read from a **certified November list** rather than primary results, so there is no winner to resolve at all — whoever the state lists as on the ballot is the nominee, runoffs included. Texas's Civix list carries CG ("Candidate in the General Election") only for each May 26 runoff's winner: the four runoff winners (both parties' Attorney General nominees, Railroad Commissioner and Lieutenant Governor) are on it, and the four runoff losers are not. Civix's own office type scopes the read to state offices (`SW`, `SR`) before any label is parsed, and two wordings were new: `RAILROAD COMMISSIONER` and `COMMISSIONER OF THE GENERAL LAND OFFICE`. That yields 7 executive offices, 9 State Board of Education seats, all 150 House districts and the 16 Senate districts up this year; its Supreme Court, Court of Criminal Appeals, courts of appeals and district benches are on the same list but deliberately unclaimed: the shared judicial parser reads no `PLACE N` seat (so Place 7 and the Chief Justice collapse into one Supreme Court row), and recognises neither the Court of Criminal Appeals nor a `55TH JUDICIAL DISTRICT` bench — a `judicial_offices` claim on that would be a false "none" for most of the list. South Carolina's VREMS dropdown lists its House as a bare `State House of Representatives`, so the opt-in replaces the per-office searches with **one search of every office** and reads each row by its own label (`…, District 1`) — 1,175 rows, ~900 of them county, school and special-district contests. That exposed `Public Service District, Fripp Island Public Service Commission`, which the statewide PSC phrase read as Georgia's PSC; "Public Service District" is now a strict locality marker, and a statewide label on a row that names a county is refused as well. It also needed `State Superintendent of Education` and the United Citizens Party (FEC code `UC`), whose nominees for Governor, Superintendent and Agriculture would otherwise have been dropped for having no party code; the one Workers Party House nominee, a party with no FEC code at all, is now kept under its printed name (see minor parties, below). Seven executive contests and all 124 House districts; the Adjutant General has been appointed since 2019 and the Senate is not up until 2028.
Pennsylvania reads its own returns API (`pa_returns`), whose office list names the state's contests beside the federal ones — `GOV`, `LTG` and both General Assembly chambers in 2026. Those are deliberately **not** picked by code: a code list written from a governor's year would silently miss the Attorney General, Treasurer and Auditor General Pennsylvania elects in the other cycle, and under the flag a missed contest reads as "none". Every non-federal office is read and its label (`Governor Statewide`, `Senator in the General Assembly 2nd Senatorial District`) goes through the shared gates, which refuse the two party state committees on "Committee". Two wordings were needed: Rhode Island's `Senator in General Assembly` pattern now allows Pennsylvania's article ("in *the* General Assembly"), and `Auditor General` — not on 2026's ballot, but read by the same office sweep in 2028. Its 2026 primary resolves Josh Shapiro and Stacy Garrity for Governor and Austin Davis and Jason Richey (428,740 to John Ventre's 225,238) for Lieutenant Governor. The feed carries no write-in rows, so a write-in who took a party's nomination where nobody was printed is missing rather than misnamed — the limitation its federal races already had. Arizona, Nevada and New York stay uncovered: Arizona's canvass is FTP-only and its Corporation Commission elects two seats per party, which the canvass reader does not model, and Nevada's and New York's own sites answer a bot wall (see *Michigan, Ohio and Oklahoma*, above).
Oregon and Maine are single-state adapters, and each needed its own reading. Oregon's Abstract of Votes carries two executive contests. The Governor's field runs over four pages (`Democrat`, `Democrat (cont.)`, `Republican`, `Republican (cont.)`, each with its own Total row), so a page cannot be judged on its own. The federal reader's text table also cuts names mid-word on these pages (`Alexander At` | `kinson IV`) and hands a given name to the wrong column. So the statewide reader places each word by position: every name and figure is right-aligned to its column, and the Total row's numbers mark the column edges. That yields Tina Kotek (D, 385,999) and Christine Drazan (R, 172,474 to Ed Diehl's 140,458), each also carrying the Abstract's own `*` nominee mark. The other contest, `Commissioner of the Bureau of Labor and Industries`, is **non-partisan**. Under ORS 249.088(1), a primary majority elects outright and anything less sends two to November. Christina E Stephenson took 63.2% counting write-ins, and the Abstract marks her `**` Elected, so the seat is not on the November ballot and nothing is published for it. The entry's `nonpartisan_resolution: "elects"` states that rule; it is never assumed. Maine elects only its Governor by popular vote (the Legislature chooses the other three constitutional officers). Both parties' 2026 primaries went to ranked-choice tabulation, and each winner is read from the official RCV Summary Report. First choices would have named the wrong Democrat: Hannah M. Pingree trailed Nirav D. Shah 50,552 to 58,606 before winning round 4. Robert B. Charles won the Republican count in round 7. Neither state's legislature is read. Oregon prints several districts per page and gives a one-county district no Total row; Maine stacks every district of a chamber into one sheet per party. Both pages keep "State legislative districts" among their omissions.
Vermont and New Hampshire both elect governors to **two-year** terms, and each needed something the others had not. Vermont's portal already published a `stateWide` report beside the federal one (Governor, Lieutenant Governor, State Treasurer, Secretary of State, Attorney General, and the `AUDITOR OF ACCOUNTS`, which the auditor pattern now accepts beside Nebraska's "Auditor of Public Accounts") — but its primary winners are not its November nominees. H. Brooke Paige won the 2026 Republican primary for Treasurer, Secretary of State, Auditor and Attorney General; the general-election report on the same portal lists Lynn LaFleur, Ivar Kronick and Edwin Howell Kemon on three of those lines. So `vt_enr` reads the executive offices from the **general** ballot once it is final (the federal UOCAVA mailing deadline, 45 days out), and from the primary only before then: all 17 ballot lines across six offices, including one independent for Governor and the Progressive, Freedom and Unity and Peace and Justice lines (see minor parties, below). New Hampshire's only statewide-elected executive is its Governor (its Secretary of State and Treasurer are chosen by the legislature), read from the "Governor Summary" workbook by the same summary-only and own-party-column rules as its Senate race — Kelly Ayotte (R) and Cinde Warmington (D). Its Executive Council is listed too, one seat per district labelled with its district (see *One rule for district-seated bodies*, below). Both adapters refuse (return `None`) rather than report no statewide contests in an even year, since both states elect a governor in every one — and the sync itself no longer records an empty answer (a primary still inside its settle window) as a checked state with no statewide offices, which every opted-in state used to say until its primary settled.
Wyoming, New Mexico and Tennessee are read from their **certified November candidate lists** rather than from primary results: the `general_list` entry carries `statewide_offices` itself, and the pipeline then takes the state offices from that list (every qualified name, independents included) and only when it answered. Each needed something different. Wyoming's roster prints its Legislature as `STATE SENATOR 11` / `STATE REPRESENTATIVE 01`, with no word "District", so a bare trailing number is now read — anchored to the whole label, so a number elsewhere in a contest name never becomes a seat; it yields all five executive contests (Governor Eric Barlow R, Kenneth R. Casner D, Rebecca Bextel CT) and 114 names for 17 Senate and 62 House seats, while the Supreme Court retention rows on the same list are refused. New Mexico had to come from the list because its results portal serves only the *currently featured* election, which since certification is the November general with zero votes — widening the `type=FED` export to `type=SW` returns the Governor and Lieutenant Governor contest with nobody to pick, which would have been recorded as a confirmed absence. Its list needed `Commissioner of Public Lands` (distinct from South Dakota's `Commissioner of School and Public Lands`), its governor is a joint ticket (Deb Haaland and Stephanie Garcia Richard, D), and the portal's UTF-8, sent with no charset in the page, had been read as Latin-1, turning its Secretary of State nominee's `LÓPEZ` into mojibake. Tennessee elects only its governor statewide; its Governor, State Senate and State House files join the two federal ones as required files, and its legislative files head the party column `Party` where the others say `Party Name`. Checking its list also showed Tennessee holds **no primary runoff**: every 2026 field under 50% (Governor's leader at 43.6%, six federal ones) has its plurality leader on the certified ballot, so the precinct reader no longer withholds them (and now reads all ten candidate columns, not five).
Massachusetts and Wisconsin each read their state offices off the same file their federal nominees come from. Massachusetts's PD43+ archive needed one more search, with no office filter (510 rows in 2026), whose Office and District cells name every contest: the six constitutional officers (`Secretary of the Commonwealth` is its own office code rather than being relabelled "Secretary of State", and a bare `Auditor` is read only because the archive's District cell says `Statewide`) plus the eight-seat `Governor's Council`, a phrase matched before the bare "Governor" pattern can claim it. Its legislature is left out on purpose: the districts are *named* (`1st Barnstable`, `Norfolk, Worcester & Middlesex`), and the shared seat parser would fold every "1st <county>" into one district 1. That search also showed the archive marking a winner that the vote count can't pick out: a write-in who tops an otherwise empty field is not always nominated (a CD5 Republican with 13 votes against 978 "All Others", and one in Council District 1). Both had been picked on votes alone; now the state's own `winner` mark is required, federal races included. Wisconsin's certified canvass carries all five executive offices and both chambers (17 Senate and 99 Assembly contests; 11 executive and 221 legislative nominees), and needed three reading fixes the 16 federal winners never exposed. Ten nominees' names are printed up to 6pt above or below their own vote count (Christine M. Sinicki, Elizabeth McCrank), so plain text extraction left their Winner lines nameless, and no single line tolerance fixes that when the same page has other rows' labels 7-9pt apart; lines are now rebuilt from word positions, each name joining the count row it is unambiguously nearest. "Chanz Green Republican" lost its surname to a loop that stripped every party-looking word. And minor-party headings wrap before the party name.
Alabama and Connecticut needed a second publication, because neither state's existing feed could see its executive contests. Alabama's adapter read only the August special primary for four redrawn House districts; its statewide offices were decided in the ordinary May primary and June runoff, which the Secretary of State publishes as official precinct results — one zip per election of 67 county `.xls` workbooks, summed statewide (the Republican Governor winner's summed total matches the state Republican Party's own certified figure exactly). Alabama nominates by majority, so four offices come from the runoff file, and while a runoff is owed but unposted the whole read is withheld rather than published without them. Its PSC seats are `PLACE 1`/`PLACE 2` — elected statewide with no district — so a seat now keeps its own word and renders as "Place 1". The same files also settle Alabama's **U.S. Senate** race, which until now had no confirmed nominee at all: the Republican Party's runoff workbook was never posted, but the state's own precinct results carry both parties' runoffs, and the winners go through the same FEC match as every other federal nominee. So are the three House districts the court-ordered map left alone (CD3, CD4, and CD5 after a runoff), which had also listed every FEC filer; CD5's Republican was unopposed and is in no primary file. The four redrawn districts (1, 2, 6, 7) stay with the special primary: their contests in the regular files, like State Senate districts 25 and 26, are voided pre-redistricting primaries. The excluded set is the union of every district on the special primary's own page and a configured list from its proclamation, so a redrawn district can never be read from the regular files even if one source misses it. Connecticut's problem was the opposite of a missing file: a convention-endorsed candidate is the nominee with **no primary at all** unless a 15%-eligible challenger forces one, and in 2026 only the Democratic Governor race was primaried — one office of twelve nominations. The rest come from the Secretary of the State's own certificates of party endorsement, read by font (the typed answers print over the form's own labels and tab stops, which otherwise split "Treasurer" into "Tre asurer") and by the drawn marks in its flattened checkboxes, where a `15% Eligibility` certificate is correctly not a nomination. Indiana is **not** covered: its Secretary of State, Auditor and Treasurer are nominated at party conventions and never appear in the primary results its adapter reads, and the one official list of November candidates, linked from the Election Division's own page, returns 404.

**Minor parties are kept as the state printed them.** A state-office nominee is stored under a one-letter party code, and a party the shared vocabulary could not name used to be dropped by every certified-list adapter — Vermont's Freedom and Unity and Peace and Justice governor candidates, its two Progressive nominees, South Carolina's Workers Party House nominee Kiral Mace. A certified general list's party column is the party by construction, so such a row is now stored under a neutral `O` with the printed label beside it (`party_label`, an additive nullable column), and the page shows "FREEDOM AND UNITY" where a code would go. A party FEC codes gets that code instead: Progressive is `PRO`, and Florida's `LPF`/`CPF` are its Libertarian and Constitution affiliates. Primary results never get this: a party-primary label nobody recognises is still refused (Vermont's Progressive primary was write-in noise), and "Unenrolled", Maine's word for no party, now reads as independent on a list. Two words are *not* independent on a state row: a candidate the list calls "Nonpartisan" (North Dakota's Superintendent of Public Instruction, Alaska's and Hawaii's non-partisan candidates) is stored as non-partisan (`N`, FEC's own code, with the printed word) rather than shown as IND — the federal matcher still reads it as before — and a party *named* "Independent" (the Independent Party of Florida, the Independent Party of Delaware) is a party. Florida prints only codes beside its candidates, so `dos_canlist` reads each through the Division's own political-parties page ("MGTOW Party", "American Solidarity Party of Florida"), and holds its state offices back for a run in which that page cannot be read; Delaware prints "Ind Pty of DE", which its entry's `party_names` spells out, because the Department's own party pages answer every request with "Request Rejected".

Eleven more states now take their state offices from the **certified November list** that already decided their federal races, so independents and minor parties appear and a replaced nominee does not. The lists needed four kinds of help. Maine keys its list by its own codes (`GOV`, `SS`, `SR`) and Colorado keys Congress by a bare district number, so `format.state_office_codes` spells each state code out for the shared gates — that adds Maine's independent candidate for Governor and all 186 of its legislative districts, and Colorado's Unity, Approval Voting, American Constitution and unaffiliated tickets. Nebraska's PDF wraps "Legal Marijuana / NOW" onto two lines (`wrapped_columns` rejoins them), Maryland lists its Lieutenant Governor as the Governor's "Related Candidate" (`running_mate_columns`), and Alaska's list is three pages long, its tickets printed "Last, First / Last, First" (`next_page_regex`, and each half of a ticket put in reading order). Illinois's Board prints its state offices as two more PDFs of the same shape (`state_url_templates`, its seat headings `2ND SENATE` spelled out by `heading_labels`), and Florida's Division as two more office groups plus the same-day State Senate District 21 special — Florida's primary results had no Attorney General at all, because both nominees were unopposed. Iowa, Hawaii, Delaware and North Dakota were opt-ins (North Dakota's contest dropdown now also asks for its executive offices and chambers, which showed its Public Service Commission filling two seats, where the primary reader had named one per party). Nebraska's Legislature is non-partisan and labelled `For Member of the Legislature`, which the shared legislative gate does not read, so its page still omits state legislative districts. Checking the lists against the primary results turned up two defects in the primary path, both fixed: Illinois's precinct CSV spells one party four ways across jurisdictions ("REPUBLICAN", "Republican Party", ...), and the reader tallied each spelling as its own contest and kept the last, which named a Secretary of State primary's runner-up as its winner; and a "Write-in" bucket that won a primary nobody filed for (Illinois's Republican Treasurer) was stored as a nominee. Every major-party name on the lists matches the primary winner each state's own reader named (Hawaii's and Alaska's primary readers name no state office today, so theirs had nothing to compare), except where the list is newer: Illinois's Republican Treasurer nominee, slated after an empty primary, and — not a major party — Colorado's Libertarian Secretary of State nominee, who replaced the primary winner.

A review of those changes found eight defects, all fixed. **Primary results stand in until a certified list names state offices**: the lists above replaced primary results for the state offices outright, so between each state's primary and its list's publication Alaska, Colorado, Delaware, Florida, Iowa, Maryland, North Dakota, Nebraska, Illinois, Hawaii and Maine went stale or back to "not yet covered". Now the main source's own opted-in primary results supply them until the list first answers with state-office rows; after that the list stays the source, and a night it is down says nothing rather than swapping the certified ballot (independents included) back to primary winners. **The page says when names may be incomplete**: primary results itemise only contested nominations, so a covered office can hold one party's nominee while the other's, unopposed, is absent — Alabama prints no uncontested contest at all, and its certifications of general-election candidates are image-only scans (checked 2026-09-28), which we do not OCR. The coverage marker now records whether its names are a ballot list — decided by the document the state-office rows were read from (the certified-list strategies; Vermont's reader says per run whether it read the general report), never by `general_ballot_complete`, which describes a state's federal ballot: North Carolina sets it for its federal filing list while its state offices and judges come from primary results — and for every primary-results state the statewide, legislative and judicial sections say an office may be missing, or missing a party's nominee. **No partial read is published as the whole ballot**: with a Connecticut party that held no primary and the other party's primary still settling, the adapter published one party's endorsements, and the sync deleted the other's nominees and marked the state covered; Connecticut now marks its state offices incomplete while any held primary is unsettled, as Alabama withholds them while a runoff is owed, and so do the tabular vendors (Virginia's per-party elections, a runoff still being certified) and Arkansas's runoff under the state-office opt-in. Only the state offices wait: the settled stages' federal nominees are still confirmed and the state still reports "ok" (the adapter returns its federal rows flagged `state_offices_incomplete`, and the sync leaves the stored statewide, legislative and judicial rows as they were). **One rule for district-seated bodies**: every statewide body whose members sit for a district is listed seat by seat, labelled with its seat, and the page says how the body is elected, from a cited fact per state (`data/statewide_seats.json`), never the label: where each voter votes in one district's seat it says so and offers the towns each seat covers — New Hampshire's and Massachusetts's, taken from each district contest's official results by `scripts/fetch_statewide_district_towns.py`, and Colorado's boards' counties, whose districts are its congressional ones — with the same filter the legislature has, or the official lookup where the state publishes no list; where every voter votes for each seat (Georgia's PSC, Alabama's PSC places) it says that. So New Hampshire's Executive Council is now read from its five per-district workbooks (own-party columns only; a candidate's figure in one party's district file is that file's own total), Nebraska's State Board of Education and Regents from its list (their names wrap onto a second PDF line), and New Mexico's Public Education Commission from its list; Louisiana's and Montana's district PSCs, which no adapter reads, and Hawaii's OHA trustees are named among the page's omissions. **Joint tickets are one contest**: Colorado, Iowa, Connecticut, Hawaii, Massachusetts, Pennsylvania and Wisconsin elect the governor and lieutenant governor on one vote, so the lieutenant governor is joined to the governor's ticket (checked against the live lists, 2026-09-28) instead of shown as a separate race. A joint ticket printed as one row now shows both names wherever the list prints the running mate: South Carolina's list in its own Running Mate column, Florida's after a slash, Illinois's on the next line with the governor's surname in parentheses, all checked against the live 2026 lists; Alaska's tickets now read "and" like every other. A primary threshold is now met only by EXCEEDING it, as the statutes say: a leader at exactly 50.0% has no majority (GA, TX, AL, AR, MS), and exactly 30% is not North Carolina's substantial plurality ("any excess of" 30%); Iowa alone nominates at "thirty-five percent or more" (Iowa Code 43.52), set by its entry's `runoff_threshold_inclusive`. Smaller fixes: Oregon's non-partisan Labor Commissioner is stored as Nonpartisan rather than a blank party; a federal ballot-list row for the "Independent Party of Delaware" is matched as an independent again (it had been dropped), while a state-office row keeps the party's name; and the statewide phrases that bypass the locality gate now refuse a local body that shares their words — "Sanitary District Public Utilities Commission", "Hibbing Public Utilities Commissioner", "Fulton Tax Commissioner" — by allowing nothing before the office's name but a party, "State"/"Statewide" or "Member", and refusing any named special district or authority.

**Oklahoma, Michigan and Ohio** came with their new federal sources. Oklahoma's List of Elections prints only contests still to be decided — a candidate with no opponent is declared elected and never reaches the ballot — so its page lists the eight executive offices on the November ballot (the State Auditor and Inspector was settled that way) with every independent and Libertarian (5 Governor candidates, 3 for Labor Commissioner), and the 32 Senate and 111 House candidates in the contested seats. One wording was new: `CORPORATION COMMISSIONER`, a statewide office with six-year staggered terms. Its county commissioners, district judges, city councils and retention questions share the page and are refused by the shared gates. Michigan's listing is the only source that could name its Attorney General and Secretary of State, whom the parties nominate at conventions after the primary (Eli Savit and Doug Lloyd; Garlin Gilchrist II and Anthony G. Forlini), and prints the governor as a joint ticket ("Jocelyn Benson and Winnie Brinks"). Two readings were new: its House is the `Representative in State Legislature`, and a university's own board is never an executive office — "Governor of Wayne State University" had been read as a governor's race, so `university`, `college` and `trustee` now mark a label as not the state's executive (the University of Michigan's regents are still read by their own phrase). Michigan State's trustees and Wayne State's governors are on the ballot but have no office code, so the page names them among its omissions. Ohio's canvass is primary results, so its page says an office may be missing a party's nominee (no independent is in it); its tickets print whole ("Vivek Ramaswamy and Robert A. McColley").

Minnesota is also why a district identifier is a **string**: it splits each of its 67 senate districts into two house districts, `10A` and `10B`. And why the town list is truncated for display but searched in full — one of its senate districts covers 292 townships, so the row shows three and "& 133 more", yet typing the 136th town still finds the seat.

---

## The Senate and House map: usual vote and poll ranges (pre-election)

`/elections/map` (`GET /api/elections/map`, `/api/elections/map.csv`) shows every Senate and House seat on the active election's ballot (`election_phase.active_election()`, so the next cycle's map needs no code change) three ways: **Usual vote**, the seat's lean from the last two presidential elections (the files' `_window`), computed by Civitas from official returns with no model (a state's from `state_pvi.json`; a district's from `district_lean.json`, on the lines of the Congress the election seats, `congress_for_election(cycle)` — the race page's lines; with no table for that Congress yet, every House seat is `no_lean` and an ops alert asks for one); **Polls**, an 80% range for the Republican-minus-Democratic margin; and **Difference**, the seats whose polls pull that range clear of where the lean, incumbency and the national swing alone would put it. It shows ranges, never a winner: no chance of winning on any surface, no seat tally by party. A range that crosses even is grey ("too close to tell"), and every view's share image carries the source, the as-of date, the licence and "Not a race call". It is separate from Representation Scores (`app/forecast/` imports nothing from scoring and sits outside `app/pipeline/`, so it never moves the analysis fingerprint), and it is withdrawn from election day, when the results map takes over.

**Getting there: section tabs.** Every elections page (`/elections`, each state's ballot, `/elections/map`, `/elections/redistricting` and each redrawn state's page) carries the same tab bar, as every Congress page does (`components/layout/SectionTabs.tsx`, from the one `SECTIONS` table in `frontend/src/lib/routes.ts`: Elections is Ballots by state, Seat map, New House maps, Calendar and Archive; Congress is Reports, Latest week, Latest month, Bills, Committees, Nominations and Votes with the president, renamed from "With the president" to say what the page holds). The tabs are links to pages, not an in-page tab widget, so they carry `aria-current` rather than the ARIA tabs pattern; they wrap rather than scroll, and every target is 44 px tall. No tab is ever dropped for the election phase: from election day the seat map's page says the results map replaces it, so the tab still leads somewhere that explains. Until 2026-10 the only ways to the map and the new House maps from the site were two inline links in the `/elections` masthead, shown during the campaign only; at 375 px the map's link ran off the screen and scrolled the page sideways (64 px), and both were 13 px tall. A redrawn state's page has a breadcrumb back to the new-maps index and `/elections`, and the index opens with a row of every redrawn state, so a reader who knows their state goes straight to it (Texas sat three phone screens down the list).

**Getting there: the whole site.** The same `SECTIONS` table is the main navigation and the homepage's directory, so a page added to it appears in both, and `routes.test.ts` fails on a page under `app/` that is in no section (records, the footer's own pages and the admin aside). The footer lists only those site pages (feeds, the API, the changelog, accessibility, environmental, feedback, Bluesky, source) and the standing statement: until 2026-10 it also listed every section's pages, the header's menu again at the foot of every page (1,359 px of a 390 px phone page). The header's desktop panels are in the server HTML while closed (`hidden`), so every page's link stays crawlable without that copy. Seven sections: Action Center, Congress, Agencies, Politicians (with the leaderboard and both compare pages, which were two more top-level links), Elections, Explore and About. From 56rem (896 px at the default text size; the breakpoint is in rem, so a larger text size reaches the phone layout sooner) each section's link has a button beside it that lists its other pages (the WAI-ARIA disclosure navigation pattern: `aria-expanded` and plain links, no menu role; Escape, a click outside or focus leaving closes it); below that, and at any width where the measured row would not fit beside the wordmark (a longer label, a fallback face; `useRowFits` in `Navbar.tsx`), the header takes the MENU layout, which lists every section's pages under it, so any page is two taps from MENU, each target 44 px tall. "Latest week" and "Latest month" (`/congress/week`, `/congress/month`) open the report of the last week and month Congress met. Each hub (`/congress`, `/elections`, `/agencies`) shows its other pages as cards, and the homepage's **Sections** band lists every section's pages, each with one live figure: record votes in the latest week, seats on the election map with polls, states on new House lines, documents open for comment, how often House members' votes went the president's way. The figures come from `GET /api/sections` (`app/api/sections.py`, five-minute cache), counts and rates over what the pages themselves read, worded in `frontend/src/lib/sectionPreviews.ts`; a figure the site can't give yet says what it is waiting on ("the Federal Register's agency list hasn't been read yet", the map page's own reason out of its phase), never a bare zero. Measured on a production build from the homepage at 375 px: the seat map, votes with the president and the weekly Congress report went from 3 taps (7.95 s KLM) to 2 (5.3 s) through MENU, or 1 tap and a scroll (3.65 s) from the band; Texas's new map from 4 taps to 3; the open comment periods, which had no way in from the navigation, are 2 taps.

**The election just held stays on the site** (`app/election_archive.py`, `ElectionArchive`, `/elections/archive` and `/elections/{cycle}`). Once its results window closes the site turns to the next election: `/api/elections/results` empties, the map's file is overwritten by the next cycle's first build, past elections' measures are pruned, and a data reset outside the window wipes the races, candidates and counts. So from election day, every pass of the live-results sync (inside its lease) copies the held election into its archive row: the map's file as the campaign's last build left it (the build stops on election day, so that file is the final pre-election map; copied once, never replaced, and an ops alert if it is missing), and the federal races with the candidates on the ballot (`_confirmed_or_all`, names as the state printed them), each race's count in the live results' shape, the statewide offices, legislative seats and judgeships with their nominees and whose list they came from (the state page's own `_statewide_section`, `_state_leg_section`, `_judicial_section`, without the place lists the page filters by), and the statewide measures verbatim with their drafters. The record is re-copied each pass until the window closes, and nothing rewrites it after; a pass that finds no races (a wiped database) never replaces a record. The table is in `RESET_KEEPS`. `GET /api/elections/archive` lists the archived elections with the site's current one (so an empty archive can say when the next one starts), `GET /api/elections/archive/{cycle}` serves one record, and `/{cycle}/map` and `/{cycle}/map.csv` the frozen map in the live map's shape (`frozen: true`, never `stale`). `/elections/{cycle}` draws the frozen map beside the counts and the ballot, by state, each race at `#race-{id}`; an old `/elections/{raceId}` link (every Bluesky race post) goes there once the site has moved on (`race_detail`'s `archiveCycle`, which also answers from the archive for a race whose rows a reset has since removed). The archive keeps the whole ballot the state pages showed; counts exist for federal races only (the only ones read live), and it calls no race: an official count still only leads. News coverage and FEC figures are not kept.

**The map is checked against the count, in public** (`app/forecast/accountability.py`, `GET /api/elections/archive/{cycle}/accountability`, `/{cycle}/swing.csv`). On the archive page, `/elections/{cycle}#map-check`, each seat the frozen map gave a range is checked once its count is official, by the source's own flag (`RaceResult.official`) and never before: whether the margin fell inside the 80% and 95% ranges the map published. The margin is computed from the count in the map's own units, as the fit's training results were: the Republican minus the Democratic share of the two-party vote, with the top two a Democrat and a Republican (a race whose top two are not is listed as official but not graded). The check reads only the archive's frozen map, never the live file or a refit, so it can't learn from the result. Each chamber's share inside each range is shown with its exact binomial (Clopper-Pearson) 95% interval, beside the nominal level and the held-out and forward coverage the map published, and broken out by what each range rested on (from polls, no poll of this seat, before polls count). Misses are listed. Before any count is official the page says "0 of N seats with a range" and grades nothing. The swing map (`#swing-map`) draws each seat's official margin minus its usual vote on the margin's scale (the lean doubled, as the map's units sentence explains), as tiles in the seat map's colours, with a table and a CSV. The elections hub's Archive card (`SECTIONS`, figure from `GET /api/sections`' `elections.archive`, `election_archive.latest_check`) leads with the newest election's check, and `/elections/archive` gives each cycle's line (`accountability` in the index); before the first archive or any official count, both say so in words. The same feature is the `election-map-check` entry in `whats_new.json`. A measurement of the map and the count; it calls no race.

**Method** (`app/forecast/model.py`). A straight-line fit of the margin on lean, incumbency (+1 Republican incumbent, -1 Democrat, 0 open; the FEC's own code) and the generic-ballot average gives each seat a structural estimate and spread. The race's poll average (each pollster's latest poll from the poll window, weighted by recency and the square root of the sample, minus the pollster's measured house effect, or the measured lean of party-sponsored polls for a pollster with no record) is combined with it by inverse variance, and one scale factor is fitted so the ranges hold the result as often as they say. The poll window is not chosen: it is how far before election day the training polls reach (their oldest poll, `model.poll_window`, 21 days), recorded with each fit as `windowDays`. Further out than that the method was never checked with polls, so the map shows each seat's structural range alone (its own calibrated spread), labelled **fundamentals only**, with the seat's newest polls listed beside it. Every parameter is fitted by `backend/scripts/fit_forecast_model.py` into `app/data/forecast_model.json` from `app/data/forecast_training.json`: every contested Senate general from 1998 that the training polls and MEDSL's results both reach (MEDSL Senate and presidential returns, CC0, the newest Dataverse version; FEC candidate files, public domain). A race's historical lean uses only the two presidential elections before it. A race whose result Silver Bulletin states differently from MEDSL's count by more than half a point is left out (`_excluded`: four races, among them Georgia 2008, whose runoff MEDSL files as the general).

**Training polls: Silver Bulletin's raw polls** (`app/forecast/training_polls.py`, `scripts/fetch_silver_bulletin_polls.py`, `app/data/forecast_polls.json`). The archive behind Silver Bulletin's pollster ratings, every rated poll since 1998 with its certified result, refreshed each winter once a cycle is rated. Its terms: free of charge for any purpose, attributed to Silver Bulletin, which the map, its method note and `/about/data` do. (It replaced FiveThirtyEight's `raw_polls.csv`, which ends with 2022 and will never get 2024.) `app/data_refresh.py` reads the page weekly until it holds the last certified general and monthly after, takes its "Rawpolls" attachment with the bot user agent, and installs a new copy only when every gate passes: the terms sentence is still on the page; the columns are exactly the expected set; no accepted poll has gone and at most 0.5% have changed (each correction logged by id); every poll ends 0 to N days before its election, N the accepted file's own reach; shares are valid; a new cycle has Senate and generic-ballot polls; and its stated results agree with MEDSL's certified margins within half a point. A refused file keeps the last accepted copy and its fits, and the alert names the gate; a page or file that can't be read is an outage alert, never "no polls". When the polls in use change (and monthly besides, for a new MEDSL version or FEC compilation) the fit reruns in the pipeline process and writes `/data/forecast_*.json`; the checked-in files are the fallback before the first refit.

**Held-out checks.** Leave one cycle out (each cycle predicted by a fit that never saw it, 447 Senate races 1998-2024, 291 with polls by two weeks out): two weeks before election day the 80% ranges held the result 81% of the time and the 95% ranges 94%; on polled races the Brier score of the implied chances was 0.0676 for the combined method against 0.0744 for the poll average alone and 0.121 for the structural line alone (on election eve: 79% / 94% coverage, Brier 0.0407 vs 0.0442 and 0.0994). **Forward check, no look-ahead:** the newest cycle, 2024, predicted by a fit on 1998-2022 only: on election eve its 80% ranges held 94% of 32 races (the binomial 95% band for 32 races is 66%-94%), its 95% ranges all of them, Brier 0.0517 on the 22 polled races against 0.050 for the polls alone and 0.112 for the structural line; at one and two weeks out 97% (31 of 32), one race past the band. The refit alerts (`data-check-forecast-model`, still publishing) only when a forward check fails at familywise 5% across every horizon and both chambers (Holm): ranges too narrow against 80% (the harmful direction), or far too wide, against 90% (80% ranges behaving like 90% ones), naming the knob, the spread's scale factor. One race too many in 32 is neither (p=0.16 against 90%), so today's fits raise nothing; the page shows the figures either way. A width calibrated from leave-one-cycle-out coverage was tried and dropped: leave-one-out coverage is already 0.79-0.81, so the factor came out 0.97-1.05, and in the forward check it widened 2024's Senate ranges further (0.938 to 0.969 on election eve) and the House's by 25-34% (two training cycles leave one cycle per fold), moving every forward figure the wrong way. There is no seat total because the races' errors move together: in 3 of 14 held-out cycles (two weeks out) the seat count by party fell outside the 90% band independent races would give (in two-party races one party's miss is the other's). The calibration table is party blind (`model.calibration_table`): races are binned by the implied chance of the side the range leaned toward, whichever party, and each band says how often that side carried the seat, so swapping the parties gives the same table (a table of how often "the Democrat" won read as partisan though it was symmetric). Two weeks out, Senate: near even (50-65%, average 58%) the leaning side carried 32 of 53 races, 65-85% (average 76%) 64 of 76, 85-95% (average 91%) 69 of 72, 95-100% all 246. A refreshed fit written before the bins changed is refitted at the next daily data refresh, and its table is not shown until then. The method section leads with a five-sentence summary (what the ranges are, the inputs, the held-out coverage, not a call, not in scores, with the poll licence and Silver Bulletin credited) and keeps the model, the checks, the table and the sources behind a "Full method" disclosure.

**The House** is the same method with its own fit (`app/data/forecast_house_training.json`, `forecast_house_model.json`) on every contested House general from 2018 whose results the FEC has compiled (2018, 2020 and 2022, 1148 races; results and incumbency from the FEC's *Federal Elections* workbooks, public domain, each found through the FEC's results index, so a newly published year is fitted by the next monthly refit). Its lean needs each district's lean *before* each election, on the lines that election used, and made the same way as the 2026 figure, so `backend/scripts/build_house_lean_history.py` rebuilds it with `build_district_lean.py`'s block method from VEST's 2016 and 2020 precinct results (CC BY 4.0) on Census 2020 blocks (`app/data/forecast_house_lean.json`): 2018 and 2020 from the 2016 presidential vote alone on the 116th Congress's lines (TIGER/Line polygons; 2012 has no open precinct returns), 2022 from 2016 and 2020 (Cook's weights) on the 118th's, the lines drawn after the census. Each state's 2016 total is gated against MEDSL's certified count (within 0.5%), and every district must appear once. North Carolina's 2020 races are left out (its 2020 lines exist on 2010 blocks only). 2024 is not fitted yet although its polls and its lean are in hand: the FEC has published no *Federal Elections 2024* House compilation, and MEDSL's U.S. House 1976-2024 file (doi:10.7910/DVN/IG0UN2, CC0, which does cover 2024) answers a download only after a guestbook form, which Civitas does not fill in. The staleness alert names exactly that input. Three cycles cannot identify the generic-ballot coefficient (every race in a cycle shares one national figure), so the House takes the Senate fit's (13 cycles), refitted without the held-out cycle in each check. Held out, two weeks before election day: 80% ranges held the result 79% of the time, 95% ranges 94%, mean error 5.5 points; Brier 0.0369 over all races against 0.0394 for the structural line, and on the 95 polled races 0.1375 against 0.1455 for polls alone and 0.167 for the structural line (election eve: 80% / 94% coverage). Forward, 2022 fitted on 2018 and 2020 only: 80% ranges held 86% of 386 races, above the binomial band (76%-84%), so the House ranges ran wide that year, but not far too wide (p=0.995 against 90%), and on two training cycles; the page shows it, nothing alerts. In 2 of 3 cycles the seat count by party fell outside the band independent races would give, so the House map shows no total either. The House map is an equal-size tile per seat, each state a block in rough geographic order; a state is one keyboard stop, and on a phone a tap opens its seats as a list.

**When a source fails, the map says so.** Each seat carries `pollSource`: the article was read (and its table listed these polls, or none in the window), lists no poll of these nominees, has no article, failed a check (with the reason), or Wikipedia didn't answer; a failure that keeps the last good revision's polls names that revision's date. None of these reads as "no poll". A kept generic-ballot figure shows its date; a seat with no usual-vote figure is `no_lean`, drawn as an empty dashed square, never as "no contest"; a failed refresh or an unreachable Wikipedia raises an ops alert (`fetch_current` returns `None` for an outage, never `[]`), and a map older than nine hours is marked NOT UPDATED. In the Polls view a seat with no poll of its own (usual vote plus the national swing) is outlined in dashes, as in the Difference view, and every range is labelled by what it rests on (`rangeBasis`, with its `rangeBasisLabel`: "from polls" only when the seat's own polls are in the average, "no poll of this seat" when none is, "before polls count" beyond the poll window; also the CSV's `range_basis`), so no range is called a poll range on a seat with no poll. The lean and the margin are both written D+N but are different units (a lean is one party's two-party share against the nation's; a margin is the gap between two shares, so twice the lean's scale): the page says "lean" and "margin" beside each figure and prints both definitions (`units`) in the legend. A poll the article prints asked two ways (with and without leaners) is one entry in the seat's poll list, its questions together (`poll_list`), because the average counts it once. On the Senate map a faint tile with faint letters is a state with no Senate race this year, and the legend says so. A share image of the map is drawn at a fixed 900 px layout with the tile map on any screen (`data-share-fixed-layout`), so a phone's capture is the same near-square picture as a desktop's, not the House state list. Every label, range and "too close" is written by the backend on the rounded value, so a label and its colour can't disagree.

**This year's polls** come from Wikipedia's race articles for the cycle ("{cycle} United States Senate election in …", `app/forecast/polls.py`), read at their current revision every three hours and recorded per seat. Only the table whose columns are the confirmed Democratic and Republican nominees is read; a table that fails a gate (fieldwork after the revision or before January 1 of the year before the election, shares summing over 102, a poll listed twice, ambiguous nominee columns) is rejected whole and the previous good revision's polls are kept. Partisan-sponsored polls are flagged as the article marks them, not hidden. The generic-ballot input is the average of the published averages Wikipedia lists (it lists averages, not individual generic-ballot polls). **Licence:** the poll figures and everything built on them (the API, the CSV, share images) are derived from Wikipedia and shared under CC BY-SA 4.0 with attribution to Wikipedia contributors; reuse them under the same licence. Only Democrat-versus-Republican races get a range: a seat with no Democratic or Republican nominee, where a candidate of neither party polls ahead of a major-party nominee (the method was fitted only on D-vs-R races), or whose November ballot Civitas has not confirmed yet (only campaign filings), shows its usual vote and says which of these it is. A House seat's polls are read from the "District N" section of its state's House article (an at-large seat's own article).

**Linked from the records.** The map's seats reach the pages a reader is already on through `GET /api/elections/map/seats?state=` or `?seat=` (`election_map.seat_summary`), one line per seat carrying the build's own labels (usual vote, the 80% poll range and whether it reaches even), with nothing re-derived. Like the map, it answers 404 outside the campaign. A state's ballot page opens, under its deadlines and in both layouts, with **at a glance**: each Senate seat's usual vote and poll range, linking its place on the map, and for a state on new House lines, "New House map: what changed". On a phone that link appeared only after opening the House contest and picking a district. A member's profile adds the seat's usual vote under "On the ballot", linking its place on the map, and never its poll range: the profile shows the member's Representation Score, and no range is shown beside one (as `RaceContext` links to the map without the range; a test holds it). Wherever a range is shown, the poll tables' attribution and licence are named beside it. A redrawn state's page links every House seat on the map. On the homepage, the lede's "election map" is a link, and the "For voters" line follows the election's phase, read from the same results summary ElectionNight reads: Seat map and New House maps during the campaign, Results and New House maps from election day. Outside those phases, or when the phase can't be read, the line adds nothing.

## Special elections and the election calendar

Seats in Congress fall vacant in every month of every year, and the elections that fill them are on no cycle's ballot. Every night, whatever the season, the Election run reads them from the sources that state them (`pipeline/fetch/special_elections.py`): the FEC's federal election calendar (`/v1/election-dates/`, every primary, runoff, general, convention and special by state, with the FEC's own type code, label and note, for the calendar year and the site's cycle, so an odd year's specials are read at all), the FEC's reporting dates (`/v1/reporting-dates/`), the House Clerk's member list (a vacant seat is an entry with no bioguide ID, whose `<predecessor-info>` names who left, their party and the day, and whose footnote is the Clerk's own sentence), and the Senate's member list (a class seat on file with no sitting senator of that class). The state calendar the ballot pages read still leaves specials out of each state's regular primary date (`calendar_from_rows`); the specials themselves are now stored, every row of them (`FederalElectionDate`), where until 2026-10 they were dropped. An incomplete calendar read adds rows and retracts none, a vacancy read that fails keeps what is stored, and either raises an ops alert. The FEC lists a reporting deadline once per jurisdiction without naming it, so only a report type due on one date for everyone (the quarterlies, pre- and post-general, year-end) is shown as a date; a pre-primary report, due before each state's primary, is not.

**A special's count is read like a general's.** From its day until 14 days after its count last moves (the general's window, `election_phase.RESULTS_GRACE_DAYS`), the five-minute results job reads each special general or runoff in a state whose feed has a reader (`live_results/specials.py`): the same readers, which find the special by its date (`election_results.pick_election`: off a federal general's day, the lone entry named special, else the only election held that day, else refuse), and the same gates (nothing before the state's last polls close, test or preview data refused, a feed going backwards refused, an impossible count dropped, two contests for one seat refused rather than guessed between, and on a general's day only a contest the feed marks special). The count is stored on the special's row; a special has no `Race`, since it is on no cycle's ballot pages. One with no count an hour after its polls close pages someone, and so does one whose contest drops out of the feed after it was counted (its last count stays shown). A data reset keeps the calendar table (`RESET_KEEPS`), since a special's count can't be read again once its window has closed. Civitas calls no special either: the count leads until the source says official. A special primary is a contest per party and a special convention a party's choice, so neither is read.

**Swing is a measurement.** A settled special (official, or every reporting unit fully in) where both major parties ran is put on the lean's own scale, the Republican share of the two-party vote minus 50, beside the seat's usual vote as the elections pages show it: a House seat's computed lean on the lines of the Congress the seat sits in, a Senate seat's state lean, both written by `district_lean.lean_label`. The difference is the swing, in points toward the party it moved to, worded from a template and marked not final until the source says official; the board averages it over the year's specials and says how many it measured (`app/special_elections.py`). The lean is a presidential share measured against the nation's, so a special's distance from it mixes the national mood with the seat's own, and the page says so.

`GET /api/elections/specials` serves the vacancies (each with its next scheduled special), the specials ahead and the year's held ones with their counts and swings, the last special when the year has none, and the season (`season`: results, post-election from the results window's end to January 3, off-season from then to the cycle's first federal primary on the FEC's calendar, else campaign). `GET /api/elections/calendar` serves the FEC's election dates, each state's voter deadlines (`voter_calendar`) and the national reporting deadlines from the month just gone to a year ahead, each with its status on the state's own clock (the zone its polls close in, `poll_close.state_zone`), and how many fall in the next 30 days. A special general or runoff newly on the FEC's calendar gets one entry in the Elections Atom feed and its state's feed, never a Bluesky post (posting volume is a measured decision, §"What gets posted" in AGENTS.md).

**The pages.** `/elections/calendar` (the Calendar tab) opens with the specials board (vacant seats, each with its next scheduled special or a line saying none is on the FEC's calendar; specials ahead; the year's held specials with the state's count, LEADING · NOT FINAL until the state says official, and the swing sentence; and when the year has none, the last special and its date), then every date in one list by month, filtered by state in the browser (§8: a choice, never remembered), with what passed in the last month folded away. Every empty list says what it means ("Every seat in Congress is filled", "No federal special election is on the FEC's calendar"), and a special with no count says which absence it is: a primary or convention, a state whose feed has no reader, polls not closed, or a feed that couldn't be read. Between general elections (`season.betweenCycles`), `/elections` leads with the same board and the next six dates instead of a bare countdown, above the state map (`components/elections/specials/`). The page links the Elections feed, which carries each new special.

## Election Night: Live Results

From election day the elections pages stop being a ballot-research tool first and lead with the count. The national map at `/elections` is shaded by who is leading each covered race — fainter while fewer than half the units are in, solid once the state lists its count as official (a race there still only "leads"; Civitas calls no race) — instead of by Cook PVI lean. A state with more than one race on the map (its House delegation, or both Senate seats) takes the party leading the most of them, every party compared, grey when two lead equally many, and the same fade from its least-counted race: faint while any has under half in, solid only once every one is official (`stateShade` in `frontend/src/lib/results.ts`). Each state's accessible name says its standing in words, and the three dark count-less fills (polls not yet closed, no votes yet, feed not read) each carry a texture, so neither is read from colour alone; each state page puts a refresh line (when its count was read, and — if the page's own refresh fails — that the count shown is that older one), its Senate race or races and a House table of every district (a race or district Civitas shows no count for while it shows one for the state's others says so, as does a whole chamber on the national map — hatched, "no count shown here", never "no votes yet"; worded about Civitas's page, never the feed, which may have reported a contest Civitas set aside as indistinguishable, impossible or unmatched — and, in a state electing both its senators with a count shown for only one, the other race on its map label and directory row, from the `senateRaces` list `GET /api/elections/results` gives) and a district map shaded by the count above the ballot research — once that state's polls have closed; until then the page stays in its present-tense research framing and the national map marks the state POLLS NOT CLOSED, saying nothing about its count (the masthead says ELECTION DAY and when the first state's count can be shown, never "polls open", which it can't know from midnight Eastern, when the phase starts); and a **Live updates** feed tells each change in the count as it happens (first returns, a change of leader, every unit reporting, a count the state lists as official, a seat changing party). An official count is tagged LEADS · OFFICIAL COUNT (or FLIP · OFFICIAL COUNT, keeping who held the seat), never a bare OFFICIAL beside the leader's name. The homepage leads with the count for the same window, and only then: one entry per state with votes counted, newest change first, each a fixed sentence per Senate race and one for the House delegation ("3 U.S. House races counted: Democrats lead in 2, Republicans in 1.") tagged LEADING · NOT FINAL until the state lists every count as official, with the publisher, the read time and a link to the state's results; past five states the rest are one tap away in an ALSO COUNTING line (`GET /api/elections/results/summary`, `live_results/summary.py`; `components/home/ElectionNight.tsx`). A state whose last feed read failed says its figures are the last good read's. Outside the window, or before any state has counted, the homepage is unchanged. The flow and every rule are drawn in [`docs/diagrams/10-elections.md`](docs/diagrams/10-elections.md#election-night-the-live-count).

**Which election the site is about.** The site no longer flips to the next cycle the night polls close. `election_phase.active_election()` keeps it on the election just held while any count is still moving and for 14 days after the last change (`RESULTS_GRACE_DAYS`), never past January 3, when the new Congress is sworn in; only then does the campaign page for the next election return. The pipeline's cycle, the API's `phase` and the election-season jobs all read that one function.

**Where the numbers come from.** Every five minutes from election day (hourly once no count has moved for a day), `live_results/sync.py` reads each covered state's own election-night feed — the same four results vendors the confirmed-candidate adapters already read (Clarity, Tally ENR, TotalVote, Enhanced Voting), one reader per vendor, configured per state in `state_candidate_sources.json`. A state is covered when its entry names a feed one of those readers can read (`live_results_states()`); the results endpoint lists them as `liveStates`, and the about pages render their count and names from that list rather than typing it (`frontend/src/lib/liveStates.ts`). Every other state is drawn as having no live count here and links to its election office, never as a state where nothing has happened. All states are read at once and stored separately, so one slow or broken feed doesn't hold up the rest.

**A wrong number on election night is worse than none.** Nothing is read for a state before its last polls close (`poll_close_times.json`; a state whose hours "vary" waits for the latest close of any state, unless its statute fixes the close — Tennessee's opening is set by county but Tenn. Code Ann. 2-3-201(a) closes every polling place at 8 p.m. Eastern / 7 p.m. Central, `STATUTORY_CLOSES` in the generating script). Test, preview, demo or wrong-date data is refused and stores nothing; so is a feed that goes backwards in time or version, and an impossible count is dropped. A count whose total fell is stored but announces nothing. Civitas never calls a race: a count is "leading" — "not final" until the state itself says official, said in the same tag as the lead (LEADING · NOT FINAL, EARLY · NOT FINAL, TIED · NOT FINAL, FLIP · LEADING · NOT FINAL, and "leads · not final" in the national list's lines; `NOT_FINAL` in `frontend/src/lib/results.ts`), since the card's own "The count is not final." sits under every candidate, more than a screen below the tag on a phone (1.16 screen heights for a seven-candidate Senate race on a simulated election night, 2026-10-14), and still "leading" after, never "wins", since an official count's leader can still face a runoff (Georgia requires a majority in the general), a recount or a court. A seat is reported as changing party only once half its reporting units are in — and where a state reports by county or town, once every one is in and six hours have passed since the first votes (the first votes ever: a momentary zero read doesn't restart the clock), or the state calls its count official. What "in" means is the vendor's: an Enhanced Voting county or town counts on its first batch (Georgia's 2nd showed the challenger's party ahead in 20 of 30 counties an hour after its 2024 polls closed; the holder's party won by 13 points), while a Clarity county counts only once it marks itself completely reported, so in Clarity's states a change of party waits for every county to finish — weeks after election night in Colorado's 2024 count, with the map showing who leads all along. The reporting figure is worded the same way (`StateCount.units_complete`): "0 of 656 precincts fully reported" where the source counts units fully in (Clarity, TotalVote, Tally — New Mexico's 2nd read exactly that beside 222,000 votes in 2024), "Results from 29 of 29 counties" where a unit counts on its first batch (Enhanced Voting — Utah's read that two days in with a third of its vote still to count), and a share short of every unit never rounds to 100% (Montana's 724 of 727). A race a state's feed stops listing mid-count — a contest renamed past the reader, or set aside — keeps its last count tagged NOT IN LATEST READ, and pages someone, since the state's other races keep its feed looking healthy. Maine and Alaska decide federal generals by ranked-choice rounds (`ranked_choice_general`): an election-night count there is first choices, whose leader may not be who the rounds elect, so neither is read live and each page says why. For the 14 days before election day a daily job reads every covered state's staged feed exactly as the sync will, storing nothing, and alerts on any that returns no count, a refused one, or no contest for one of the state's races (`live_results/readiness.py`) — on 2026-10-12, three weeks out, New Mexico's and Montana's results hosts were sending their current-election page to their Secretary of State's landing page (Montana's says results come after 8 PM on November 3), where in 2024 both served the staged general a month ahead. That bar only decides when a change of party is first *said*; once said, it stands until the lead itself goes back to the seat's party or ties (`lead_is_back`) — a count that dips below the bar with the same challenger ahead (reporting figures missing for a poll, units added, an official flag switched off) is not a reversal, and says nothing. The results page's map and race lines mark the same announced state (`RaceResult.flip_announced`, set when the sync raises the flip or its reversal), never the bar re-read on each request — the same state the Action Center issue and the Live updates feed show, so the three never disagree. A poll whose total fell changes none of them, even when the holder leads in its figures: it announces nothing, so until the next poll reverts all three together the page says both things that are true — the change of party was announced, and the latest count shows the holder's party ahead (or a tie): FLIP ANNOUNCED · HOLDER'S PARTY LEADS on the race, "change of party announced earlier, holder's party ahead in the latest count" in the map's names, and a separate line under the national SEATS CHANGING PARTY counter rather than a place in it. No surface says "changing party" or FLIP · LEADING beside the holder's lead (`flipShown` / `flipNotShownText` in `frontend/src/lib/results.ts`). A held poll with no votes in it, or whose leader the feed gives no party for, says that ("no votes in the latest count", "leader's party not given") — never "no votes yet" or "another party" — since every surface checks the announced change before its "no votes" and "tied" wording. Feed rows are matched to the race's confirmed November nominees where it has any, never to a primary loser, and a row whose feed names no party borrows a matched candidate's party only when that candidate is the one nominee with the surname and the given name fits. A data reset during the results window keeps the held election's ballot and count (`RESET_KEEPS_WHILE_RESULTS`), and the statewide, judicial and ballot-basis coverage markers its pages read (`HELD_BALLOT_MARKER_TIERS`): once election day has passed nothing can rebuild them, because the roster and ballot syncs stand down. How each state's last read went is kept (`LiveResultRead`), so a page can say "the count hasn't started" and "the state's feed couldn't be read" apart. A state whose feed Civitas has stopped reading — its last check more than a sync pass plus ten minutes old (15 minutes while counts move, 70 once hourly), or no read record at all that long after its polls closed — is marked **STALE**: its row says when its feed was last checked ("hasn't been checked since …") and when the count shown was read, the maps keep the last leader's colour under an amber-and-dark stripe, and when every closed state is stale or failing the `/elections` masthead says STALE (and which of the two) instead of LIVE. The pages judge this, and whether a state's polls have closed, by the **server's clock** — the `Date` header of their latest good response, run on by at most one poll interval (`resultsNow` in `frontend/src/lib/results.ts`) — never the visitor's, which can be off by minutes or hours. The results endpoint is fetched with `cache: "no-cache"`, so the browser's own HTTP cache never answers a refetch with an old copy's `Date` (nginx still answers from its cache, whose responses carry a fresh `Date`, so a stale body's old read times still read as stale). One clock per page (`useResultsNow`), handed to every part of it, that never runs backwards. While the page's own refreshes fail its clock stops where it stood when the refresh failed — never back at the last answer, which re-opened polls that had just closed on screen — and REFRESH FAILED, with when it failed (on the server's clock, like the read times beside it) and when the counts on screen were read, is the only thing it says: no state is marked LIVE (the national list says NOT REFRESHED), no state's feed is blamed, and every count on the maps — the results section's district map and the House research drawer's alike — carries the not-live stripe. The polite live regions hold only the state (live, stale, refresh failed, polls closed), not the times beside it, so a screen reader hears each change once.

**Developing issues and posts.** When a covered seat's count shows it changing party, the Action Center opens a DEVELOPING issue for it, ranked below confirmed stories and promoted if the press confirms it — only by a story that names that race, the state and the seat together in one phrase ("Georgia's 2nd District", "GA-02", "the U.S. Senate race in Georgia" — never a state and a number from different races in a roundup, a dateline, "West Virginia" for Virginia, or a state Senate seat, hyphenated or not — nor "Ohio Senate" followed by a seat number, a district or a place inside the state ("Ohio Senate seat 5", "Florida Senate seat in Tampa"), since that is also the name of the legislature's upper chamber, which elects the same night: "seat" and "election" count only with "U.S.", "special" or a possessive ("Ohio's Senate seat"); a postal code inside a link, bare domain or not, doesn't count; spelled-out ordinals do, but only before a district, seat or race word ("Georgia's second district", never "It was Georgia's first."); "Alaska's lone House seat" names an at-large seat. Regular and special are told apart only where a state holds both this cycle — "regular" names the regular one, Florida's and Ohio's one 2026 race is a special, and "the Ohio Senate race" names it. A district or seat number after the phrase, whatever punctuation stands between ("Ohio Senate race, District 5"), or a district, legislature or possessive after a state named last ("the Senate race in Ohio District 5"), makes it a legislature's. The phrase is only half: the story must also name one of that race's own candidates by full name — the leader or runner-up in the stored count, or before a count the certified nominees (failing those, the race's candidates on file) — so a legislature or commission story, which names other people, never promotes it whatever its wording. A full name is a given name the records state (FEC's first given name or a nickname it quotes, the count's or ballot's printed first name, or its initial with a period: "W. Smith"), matched exactly — "Donna Lee" is not Don Lee — then optionally a middle name or initial that doesn't contradict the record ("Pat Q. Smith"), then the whole surname: multi-word or hyphenated as a unit ("Ana Ruiz Ortega", "Jo Hart-Keller"), accents and curly apostrophes folded ("Kip O’Dwyer"), a suffix allowed after. A results feed's printed name is read against the person's FEC filing (the ballot's printing is the state's own and stands, nickname and all): its given name starts at the first word that is a name they filed or a short or long form of their first filed name or nickname — the filed name beginning the word ("Christopher" for CHRIS), or a word no longer than it sharing its first two letters ("Steve" for STEPHEN) — so a feed's "Congressman Steve Rowe" (filed ROWE, STEPHEN ALLEN) is Steve and "Congressman Rowe" states no given name; a word that fits only as a short form and is followed by a full word that fits nothing filed means the printing states none ("Representative Bob Jones" for JONES, ROBERT E), and a parenthesised word counts as a nickname only if it fits too ("(Incumbent)" is not). A count row linked to no candidate is read against the race's filings of that surname the same way ("Congressman Smith" beside SMITH, CHARLES states no given name). A printed middle name not on file drops the printing's given name the same way ("Steve Allen Rowe" beside a filing of STEPHEN alone). So a printed given name counts only if it is a filed name, a short form sharing the filed name's first two letters, or a word beginning with / extending the filed name: a nickname that changes the initial or the second letter — Bob/ROBERT, Bill/WILLIAM, Liz/ELIZABETH, Jim/JAMES, Tom/THOMAS, Jack/JOHN, Peggy/MARGARET — is not read from a feed's printing, a miss, never a wrong promotion (the filed given name and the ballot's own printing still work). The exact limits: a title no longer than the filed given name and sharing its first two letters still reads as a short form ("Sheriff" for SHERIDAN, "Judge" for JUDITH, "Chair" for CHARLES), a filing stating only an initial lets any word with that initial through, and a printing with no filing of that surname anywhere in the race stands as printed. Initials count only together — a quoted "A.J." matches "A.J.", "A. J." or "AJ", and a record of initials alone ("DOE, J. D.") is named by both ("J.D. Doe") — never as one bare letter, and a single quoted letter like "(I)" is a ballot annotation, not a name. The name must be written as one: every word before the surname, and the surname's last word, capitalised ("DALE CARVER" counts; "voters will mark brown ribbons" is not Mark Brown). Two people sharing a full name are not told apart: a namesake of a candidate, by full name, in a story that also names the candidate's seat promotes it — an accepted limit, as no record distinguishes them. A surname alone never counts ("Smith", "Rep. Bishop", "Sen. Jones"): eight review rounds graded surname evidence, and each found a namesake, a title ("The King Center", "Captain Smith"), a later bare mention of a namesake, or a common word at a sentence's start ("Green energy", "Case counts") that it let through. A district phrase followed by another body's name — capitalised ("Georgia's District 2 Public Service Commission seat", "California's 2nd District Court of Appeal"), a lower-case compound running into a seat word ("2nd District county commission seat"), or a seat "on" something ("2nd seat on the Public Service Commission") — is that body's, unless what follows names the race's candidate, the House or Congress; this is read from the sentence's structure, not a list of bodies. In a state with two Senate races an unqualified phrase is told apart by whose candidates it names — the race whose candidate is named by full name, and none of the other's by any mention (a story naming both races' candidates names neither, and the other race's candidate by capitalised surname alone is enough for that, since holding a story back can only cost a miss) — and with no candidate on record the phrase decides alone: `action_center._results_race_named`. A story naming the race but none of its candidates by full name leaves the issue developing — a miss the next story can fix, where a wrong promotion can't be undone), since the template shares a state and "District" with every other seat's, and never once retired. Until then it follows the count, and if the lead reverts it leaves the Action Center and is rewritten to say the count no longer shows a change of party (the homepage record and the issue's own page still show it). Once promoted it is the news story's: the count no longer rewrites it, and it matches later coverage like any other issue. An issue the Action Center's two-week cleanup has since deleted is not redrafted as news on the next poll; only a new flip, after a reversal, opens a new one. Each race's flip is its own story: the homepage never hides one as a "duplicate" of another race's. The same flips, official counts (a House count's only while it still shows the seat changing party) and big Senate moves are published like every other post — to the Elections feed (`/feed/elections.xml`, and each state's feed) and then Bluesky — louder than usual but budgeted (below). Every sentence in the feed, the issue and the posts is a fixed template around the state's own figures — no model writes any of it. Each one about a count the state hasn't listed as official says "not final" itself, since a title or a map label is often read alone: the issue's title opens "Not final:" (its reverted form too), a correction post and the feed titles of a lead change or a correction end with it, the site's Live updates end every first-returns, lead-change, flip and reversal line with "Not final.", and each race's accessible name on the maps reads "leads, 80% in, not final".

---

## Publishing: feeds and Bluesky

Every post goes through one function, `app/broadcast.publish`, which stores it as a `BroadcastPost` row and only then delivers it to Bluesky (when `BSKY_HANDLE`/`BSKY_APP_PASSWORD` are set). The rows are the Atom feeds, listed on `/feeds`:

| Feed | Holds |
|------|-------|
| `/feed.xml` | Everything |
| `/feed/issues.xml`, `/feed/congress.xml`, `/feed/members.xml`, `/feed/elections.xml`, `/feed/trends.xml`, `/feed/new.xml` | One topic (`broadcast.FEEDS`) |
| `/feed/states/<ST>.xml` | One state's race posts, election-night counts and member spotlights |
| `/feed/members/<id>.xml` | One member of Congress: each roll-call vote this Congress with the position, the latest action on each sponsored bill, each representation-score change between runs (saying when the method changed too), each trade report and annual disclosure |
| `/feed/bills/<congress>/<bill>.xml` | One bill: each action Congress.gov lists (as last read, with the glossary's plain meaning) and each stored roll call on it |
| `/feed/agencies/<slug>.xml` | One agency and those under it: each proposed and final rule, each comment period opening, and each one's last day |
| `/feed/monitors/<slug>.xml` | One national monitor: each sourced update, under the outlet's headline (never the stored summary, which was model-written before 2026-09-24) |

The last four are not posts: `app/entity_feeds.py` builds them from stored rows when asked for (nginx caches them for 5 minutes, misses through the api-misses limit), every entry a fixed template linking the Civitas page and the official source. An entry's id is a tag URI on what it is (a roll call and the member's bioguide id, a bill action's date and text, a Register document number), so a renamed member or a moved page never shows an entry twice, and the same bill action has one id in a member's feed and the bill's. Each page links its own (member profiles, bill pages, agency pages, an issue's monitor or each monitor on the Ongoing tab).

Anyone can follow a feed with a reader, a Discord or Slack bot, or their own program, and Civitas keeps no list of who does (AGENTS.md §8). Every page's `<head>` advertises the everything feed, and a page with a more specific one names that first (`pageMetadata`'s `feed`, `frontend/src/lib/site.ts`): a state's ballot page its state's feed, the `/congress` pages the Congress feed, a member's profile, a bill and an agency their own, an issue page its national monitor's (else the Action Center feed), `/action` the Action Center feed, `/elections` the Elections feed. A state's ballot page and the Congress pages also say so on the page ("Follow Georgia without an account", "Follow Congress without an account", `components/shared/FollowFeed.tsx`), linking the feed and `/feeds`. Until 2026-10 every page named only the everything feed, and the feeds drew about 45 requests a week, none from a recognisable feed reader. The feeds are served by `app/api/feed.py` (50 newest entries, ETag/304) and cached by nginx for 5 minutes. Each entry carries what the Bluesky post's link card shows: the linked page's picture and description, read once from its Open Graph tags (`bluesky_utils.og_card`, the same reading the Bluesky card uses) and kept on the row (`card_image`, `card_image_alt`, `card_description`). The entry's content is HTML (the picture, the post, a "Read on Civitas" link, and the source article when the post restates one), with the description as its summary, the picture as an enclosure and a Media RSS thumbnail for readers and chat bots, and the source as a `related` link. The card is read right after the post is stored, so an unreadable page never holds a post back; the hourly pass (`broadcast.fill_missing_cards`) fills in any it missed from the last week. The feed itself never fetches anything. The feed's `<icon>` is `app/apple-icon.png`, the favicon (`app/icon.svg`) rendered at exactly 6x, since Discord and most readers don't draw SVG; `icons.test.ts` fails if the two drift. The same file is the site's `apple-touch-icon`. Each page a post links to draws its own card at `/api/og` (`frontend/src/app/api/og/route.tsx`): an issue, a member, a state's ballot, and a Congress day or week, whose tiles give each chamber's record votes or say why there are none (not in session, no record yet), never a zero for a day with no record. A roll call, a bill the site tracks and an Explore document share as their record card (`?vote=`, `?bill=`, `?explore=`, the trend posts' picture, below) as their `og:image` and `twitter:image`; a bill the site doesn't track has no stored card and keeps the site's. A post is in the feed whatever Bluesky does with it. A send Bluesky refuses is retried hourly (`broadcast.deliver_pending`), for at most three tries in all, only on the Eastern day it was written (a post held for the posting window, below: in the next window), since a post can say "Yesterday: …", and stopping after two refusals in a row, since each try is a login; election-night result posts are never resent (below). A send interrupted by a crash is never retried, so nothing is posted twice. The table survives an admin data reset (`RESET_KEEPS`): it is what the posting modules check (by each post's `subject`, e.g. `race:2026-SEN-GA`) before publishing again, so a reset neither empties the feeds nor re-posts the last few days.

The posting modules below decide what to publish and when. The Civitas Bluesky account (`@civitas-research.org`) carries the same posts as the feeds, except any Bluesky still hadn't taken by the end of that day (an election-night result it refused, at once) and a correction of a flip it never showed:

| Post type | Trigger | Content |
|-----------|---------|---------|
| **Issue post** | New topic enters action center, or existing topic gets articles with a newer date — only for the day's top two postable issues by Action Center rank, and at most two issue posts (reposts included) per Eastern day (`bluesky_poster.MAX_ISSUE_POSTS_PER_DAY`). Posting every flagged issue made 6.5 a day over the account's first 103 posting days (3.6 a day 2026-09-13 to 10-10, up to 23 in one day); the same history under this rule is 1.9 a day. An issue not posted keeps its page and stays flagged, so one that rises into the top two later that day can still go | The issue's lede — a claim the model located and `post_composer` verified verbatim; on a repost, the first fact the last post didn't carry (the new information that released it) — or the top article's real headline when the lede is too long to post whole (a claim is never cut: truncation can drop the qualifier that makes it true). No word is model-written. If the event predates today, code prefixes "Yesterday: …" or "On [Month day]: …" (`bluesky_poster._compose_new_post`) |
| **Member spotlight** | Once per day (random pick of a senator or representative not yet spotlighted, cycling through all before repeating) | Leads with the member's most distinctive finding: the scorecard's own sentence (`services/score_findings.py`) for the dimension whose stored score is furthest from the chamber's, by the largest absolute z-score among the chamber's scored members. Then the Representation Score and rank, then the three dimension scores if they fit in 300 characters with the link (a part that doesn't fit is left out whole) (`bluesky_spotlight.compose_spotlight`). No word is model-written. Over the 100 senators on 2026-10-11 the rule led with funding for 40, effectiveness for 32 and alignment for 28 |
| **Congress day** | Once per session day, when the Daily Digest has made its record final (usually the next evening) and the day's page shows it (the link card is read from that page, which can still be its earlier render); only days from the last three, so the Digest back-fill never posts history; at most once per day; never a day on which neither chamber took a record vote, passed a measure or confirmed a nomination (`congress_bluesky.has_business`: 83 of the 371 final session days on record on 2026-10-11, pro forma sessions and short sittings, and four of the seven days posted from 2026-09-24 to 10-09) (`congress_bluesky.py`) | The day report's own sentence ("The Senate passed 3 bills, agreed to 4 resolutions and took 3 record votes. …") and the passed bills' numbers, never their titles (an official short title can read as advocacy), linking to that day's `/congress` page. No word is model-written |
| **Congress week** | Once per week, for the week (Monday to Sunday) just ended, once every day either chamber met is final; only that week, so nothing older is ever posted; not a week with no record vote, nothing passed or confirmed and no bill becoming law (`congress_bluesky.post_weekly_congress`) | The week report's own sentence ("The Senate met 3 days and took 12 record votes. …") and the numbers of bills that became law, linking to `/congress/week/{monday}`. No word is model-written; it replaced a weekly recap the model wrote from the timeline |
| **Next week in Congress** | Once per week, from the Saturday before it, when the Congressional Record's program says some chamber meets that week for business (a printed program that isn't a pro forma session or "To be announced"); replayed over the 92 weekends on record to 2026-10-10, 34 would have posted (`congress_bluesky.post_congress_lookahead`) | When each chamber next meets, from the newest notice of its next meeting (`congress_service.next_meeting`, the same reading as the "this week" strip), "in pro forma session" where the program says so, "out until …" past the week; then the bills that passed one chamber the week before and go to the other next (the week report's `passedOneChamber`), by number. Linking to `/congress`. Template text only, kind `congress_ahead`, in the Congress feed |
| **Race update** | A news item matched to a race by a candidate's full name and the race's state or another of its candidates, not a digest or newsletter, about a candidate on the certified November ballot, and past the labelled relevance threshold (`election_bluesky.refusal`); at most 4 a rolling day, one per race every 48 hours, never the same article twice, and never a sentence already published (two articles sharing a description once gave the same post twice) (`election_bluesky.post_race_coverage_updates`) | Who did what: an actor and a predicate the model located, each copied word for word from the news item and checked by `post_composer` to be asserted of each other, then the outlet it came from ("(via Roll Call)", `election_bluesky._citation`). The link, and so the Bluesky card, is the race on its state's ballot page, never the outlet's article (the article is the feed entry's source link), so the text is where the outlet shows; until 2026-10 it named none. A sentence already published counts whatever outlet it came from. A sentence too long to post whole with its link is not posted (a cut could end on "Sens." or before the object its verb needs). No word is model-written |
| **Election-night result** | Every 5 min while any count is moving (`live_results/bluesky.py`): a seat changing party, a count the state calls official (Senate, or a flip), a Senate lead change with most of the count in, every unit reporting in a Senate race | A fixed template around the state's own figures that says "leads" — with "Not final." while the state hasn't listed its count as official, and still "leads" once it has; figures are dropped to fit, never the qualifier. At most 6 an hour and 40 an election, one per race per 20 minutes; a post held back by the budget or a race's cooldown waits for a later pass, but is dropped once two hours old or when a later post about the same race supersedes it, and every post is worded from the count as it stands when it goes out — so an official or every-unit-reporting post waits (up to the same two hours) while the count no longer says so (the flag switched off, units added). Each is published through `broadcast.publish` (kind `result`, in the Elections feed). A send Bluesky refuses is not resent later (`broadcast.NO_RETRY_KINDS`): word for word an hour on, it could describe a count that has since moved or reverted, so the next event's post says where the count stands; a correction goes to Bluesky only if the flip it corrects did. What has been said, the budget and the cooldown are read from the stored posts, so a data reset doesn't post the night again, and a posted flip whose announced count went back to the seat's party while the reset had wiped the count's events still gets its correction (never from a held poll, and only once the seat's holder is known — a holder the reset left unknown is looked up again on each read). A House seat in a state whose congressional map was redrawn for the cycle (`app/data/redrawn_congressional_maps.json`: AL, CA, FL, LA, NC, OH, TN, TX, UT for 2026) has no known holder, so no flip is announced for it. A posted flip that reverts gets a correction outside every cap. The race-coverage posts stand down while a count is moving. No word is model-written |
| **On the record (trend post)** | Hourly after the Action Center refresh (`trend_poster.run_trend_posts`): a topic on Bluesky's public trends list that Bluesky itself files under "politics" and that passes the neutrality gate on its name, and matches an official record on one of four pinned matches — an Action Center issue at cosine ≥ 0.5 sharing a distinctive entity with the trend's name, an Act's full title (or short title with the bill number), a nominee's confirmation vote when the trend is about the nomination, a named member's own recent bill on the topic (never a person alone) — dated no later than two days after the trend began, and no more than 30 days before it unless the trend's name names the record. Backtested at 3 of 3 posts correct over a month of sampled trends. At most one a run, two an Eastern day, four hours apart, inside the posting window (held outside it and dropped if the trend leaves the list), the same record once in 14 days, never a record an issue post carried. A tragedy needs an official record or a confirmed Action Center issue from two or more outlets with a verified lede, which the post quotes and credits (the political-violence vote was removed 2026-10-16). Selection is party-blind (rank weight × match strength); every candidate is audited with the parties named and sponsoring, and the posted share is tested against the eligible pool's (admin Trends tab, ops alert on divergence) | `<record> (<date> · <source>)`, e.g. `On Passage of the Bill: <title>. Bill Passed, 52–47. (Oct 13 · Senate roll call 301)`: the record's card fields verbatim (a vote's question, title, recorded result and yea–nay count; a bill's number, title and latest action; the Daily Digest's entry; a rule's agency, type and title; an issue's headline, or a tragedy's verified lede in quotation marks credited to its outlet), a loaded bill title left out. No "trending" and no echo of the trend's name (until 2026-10-16: `Trending on Bluesky: "<name>". On the record: <record>`): the post goes out while the topic trends, so the record line stands alone. The picture is the record's card (`/api/og?vote=…`, `services/record_cards.py`) with its source line and data date, and alt text from the same object; for the first 28 days even Eastern days carry the image and odd days the link card, to measure engagement (`embed_kind`). No word is model-written; kind `trend`, in the "On the record" feed. See `docs/research/trend-posts.md` |
| **New on Civitas (feature post)** | Hourly with the trend posts (`whats_new.post_whats_new`), for each entry in `app/data/whats_new.json` released in the last 28 days and not yet posted, newest first: inside the posting window, at most one a day, and only while the last 24 hours carried no more of every other kind of post than the median day of the 14 before (computed each run from the stored posts, so the cap follows the other rules). Replayed against the account's 1,129 public posts to 2026-10-16: interactions a post don't fall with the day's count (0.24 first post of the day, 0.20 second, 0.13 third, 0.28 fourth, 0.26 sixth and later) and 96.6% of the last fortnight's drew none, so the rule adds the least it can; over the 28 days to 10-15 it would have posted on 25. A page the frontend can't serve holds the run (an outage is not a launch); one answering 404 is skipped with one ops alert. Older entries are never posted, which is also the backfill | `New: <title>. <summary>` (the feed entry's title keeps `New on Civitas Research: <title>`) and the page's link, whose card is the page's Open Graph image. The entry's own words; no word is model-written. Kind `feature`, in the What's new feed (`/feed/new.xml`); the same entries are `/changelog`'s "New on the site" list (`GET /api/whats-new`). Likes are read like trend posts' (`ENGAGEMENT_KINDS`) so the rule can be replayed with them |
| **Repost + like** | Outlet post matches an active issue (cosine sim ≥ 0.78) | Reposts + likes posts from AP News, NPR, and PBS NewsHour (`NEWS_OUTLET_HANDLES` — a narrower list than the RSS feed set, since it needs a Bluesky presence); posts under 24h old; max 3 per hourly run |

**When posts reach Bluesky.** News goes out the moment it is written: an issue, a race update, an election-night count (`broadcast.SCHEDULED_KINDS` excludes every news kind, and a test sends each at 03:00 Eastern to prove it). The member spotlight and the three Congress posts name their own date or none, so they can wait for readers: one written outside the posting window is stored as a `pending` row and sent by `deliver_pending` once the window opens that Eastern day (it runs half-hourly from the Congress sync as well as hourly from the Action Center run, both of which keep running through the nightly pipeline) 10:00 to 01:00 Eastern, daylight saving followed. Before this they went out whenever their trigger fired: the spotlight at 00:15 UTC, the look-ahead from 00:10 Eastern on Saturday, the week from 00:10 Monday, a day whenever its Daily Digest landed. When no window is left that day the post goes at once, so a held post never runs into the next day's; a held post whose window was missed (the pipeline process down) goes in the next window, even the next day, for up to `HELD_MAX_AGE` (two days), since its words carry their own date. The hold is the row, so a deploy can't lose it. Cadence is unchanged: each module still decides what and how often, and only the Bluesky send waits. The feed entry is never held: a feed is pulled, so its reader already chooses when to read, and holding it would only make it late. The window is measured, not chosen (`app/data/bluesky_post_windows.json`, written by `scripts/fetch_bluesky_activity.py`, AGENTS.md §3a): Jetstream (the public firehose, which keeps about 36 hours) replayed for 5 minutes of every hour, posts counted by declared language, and the window is the Eastern hours whose English post volume is above the mean hour. English stands in for the U.S. and also counts the U.K., Canada and Australia; a sample of a day and a half can't separate weekdays from weekends. It measures when people are posting, which is both the audience and the competition for its attention; nothing here can tell those apart. Measured 2026-10-11 17:00 UTC to 10-13 04:00 UTC (36 hours; Monday was a federal holiday), English posting overnight (01:00 to 07:00 Eastern) ran at 0.46 to 0.65 of the mean hour and the evening (18:00 to 24:00) at 1.3 to 1.5 times it; likes in every language peak in the same evening hours. The 216 timed likes on the account's own posts are too few to choose hours from (one hour holds 34 of them) and are kept in the file as a cross-check. Before this, Congress day posts usually went out between 07:00 and 08:00 Eastern, when the Digest lands; they now wait until 10:00. The spotlight (about 20:20 Eastern) and the week and look-ahead posts (00:10 Eastern, still above the mean hour: 21:10 on the Pacific coast) were already inside it. Over the account's 1,111 posts to 2026-10-13, those sent inside the window drew 0.21 likes each and those outside 0.18 (17.6% and 17.7% with any interaction): no measurable difference at 46 followers, with news posts at every hour mixed in. Rerun the script's three steps (`sample`, `account`, `write`) to refresh it; a daily `sample` for a week covers every hour of the week.

**Withdrawn issues are listed, not quietly deleted.** `backend/app/data/retractions.json` is the public record of issues Civitas published and withdrew, and why. An entry's issues are removed by the Alembic data migration it names, the API answers 410 with the reason, and the issue page shows that reason (marked noindex) instead of breaking. The page itself is a 200: Next's App Router has no way to give a page a 410. The first entry (2026-09-27) withdrew two issues built from unrelated stories by the clustering bug fixed in #659.

The spotlight pick is deliberately *not* the highest or lowest scorer. Always picking an extreme, combined with framing it as praise or criticism, produced a real incident: a "praise" post about a senator's score read as badly out of touch after negative news broke about him the same day. A random pick stated as plain numbers can't fail that way. The text was model-written until 2026-09 and still judged the numbers under a list of banned words ("placing him in the average range", "All individual metrics fall within typical expectations"); it is now a template.

The spotlight reads member scores, not the news, so it runs on every refresh — including the two paths that abort early because no articles arrived or none were policy-relevant. That makes a missing spotlight a usable signal in its own right: if it hasn't posted, the pipeline isn't completing, and the quiet isn't a slow news day.

Each issue links back to its permanent Civitas permalink (`/issue/<id>`). The permalink is stable — issue IDs never change even as content is updated, and every confirmed issue is retained indefinitely, posted or not: the sitemap lists every one as the issue archive, and since only the day's top two are posted, keying retention on publishing would have 404'd most of it. The 14-day cleanup deletes only developing issues that were never confirmed or published (noindex, in no sitemap), and keys "published" on `bsky_last_post_text` (only ever written on a successful publish) rather than `bsky_posted_at`, which the repost path clears and so does not mean "never published".

---

## Scoring

### Non-Partisan by Construction

The scoring formulas deliberately avoid any ground truth that encodes partisan assumptions. Every dimension measures *structural behavior* against a neutral baseline, not agreement or disagreement with any policy position.

```
Overall = 0.33 × FundingIndependence
        + 0.33 × ConstituentAlignment
        + 0.34 × LegislativeEffectiveness
```

The weights live in `SCORE_WEIGHTS` (`backend/app/config_definitions.py`) and are served at `GET /api/config` — that dict, not this file, is authoritative. Two former dimensions are excluded from the weighted overall: **Promise Persistence** (removed in v6.0 — a live measurement found 0 of 100 senators reached even "medium" evidence confidence, so the dimension had collapsed to its neutral prior; still stored, but published as `null` since 2026-10, because with no promises to read its stored value is only the vote-participation term on a neutral base, 55 for 97 of 100 senators) and **Funding Diversity** (folded into Funding Independence in v6.5 after an audit found the two correlated at r=0.72 — the same underlying funding-profile signal measured twice).

The exact formulas are actively iterated (v1 onward, each change measured against real data; the current version is `ALGORITHM_VERSION` in `score_calculator.py` and the first entry of `frontend/src/lib/scoreVersions.ts`, which `tests/test_score_version_sync.py` keeps in step) and are documented in full — every component's weight, calibration source, and academic citation — in the module docstring of `backend/app/pipeline/analyze/score_calculator.py`, which is the source of truth for the current rule; why each version changed is in `docs/methodology/` (one decision record per version). Rather than duplicate formulas here that will drift out of sync as the algorithm evolves (as this section previously did), a summary:

- **Funding Independence**: PAC dependency (PAC share against what the seat predicts: senators by state population, representatives by the House median, both fitted every run, in share points; v6.26), state-relative small-donor share, the top ten donors' share of all outside money (the candidate's own loans and contributions, per the FEC totals, are not outside money; v6.32), and the HHI of the industry mix (folded in from the former Funding Diversity dimension), both ranked within the chamber, industry concentration within the member's party (v6.26: one party's donors work in fewer, broader industries, which a chamber-wide rank scored as the member's). Donors and industries come from the FEC's complete detail (every committee contribution from its bulk file, itemized individual money by occupation) since v6.26, where 100 sampled receipts had covered under 1% of a large campaign; since v6.31 the detail covers exactly the cycles of the FEC totals it sits beside, and committee money in the industry mix is capped at what the campaign reported receiving from committees. v6.13 removed outside spending and source breadth after testing both against FEC data: outside spending tracks race competitiveness, and breadth was the small-donor share counted again (see `docs/research/funding-independence.md`).
- **Constituent Alignment** (keyed `constituentAlignment`; it was `independentVoting` until 2026-09 — the dimension was rebuilt in v4.2): how a member's voting compares to what their *seat* elected them to do. Party-line voting in a safe seat that elected that platform scores as representation, not as a failure of independence — the delegate model of representation (Miller & Stokes 1963), not independence as an intrinsic virtue. A House seat's lean is the Cook PVI of the district the member was *elected on* — the sitting Congress's lines, pinned per Congress in `app/data/district_pvi_sources.json` (`fetch/district_pvi.py`), never the map the next election uses; members switch to a new Congress's lines — together with the scored windows, which read the same setting — at the first House pipeline run in a job that starts after noon ET on January 3, when that Congress takes office (the setting follows the clock, advanced at the start of each pipeline job, no restart — so leave `CURRENT_CONGRESS` unset: a value in `.env` pins the windows and the lines; a Congress with no pinned entry stays on the newest pinned lines and raises an ops alert). Each stored House score records the Congress whose lines it used, and its "show the math" breakdown is recomputed on those same lines. The elections pages show the lines of the Congress each election seats — for a cycle nobody has pinned yet, the newest pinned lines. A senator's seat lean is the state's Cook PVI, computed from official 2020 and 2024 returns (the FEC's certified 2020 state totals and the MIT Election Lab's 2024 state file; the per-state counts are stored beside the figures and a test recomputes every figure from them) with Cook's weighting (75% to the recent election) by `scripts/fetch_state_pvi.py`; since v6.33 it reproduces all 51 of Cook's published state figures, where the equal-weight mean before it was a point off in 18. The member's break rate is scored against the break rate same-party members show at the same seat lean, measured from the chamber every run (`compute_constituent_reference`; since v6.35 the per-party line bends at a swing seat only when the bend predicts the party's members better out of sample, by leave-one-out quasi-likelihood and more than one standard error — `select_opposed_kink` — where a fixed count of five opposed seats used to decide, and each fit records the comparison under `kink`); the member's Nokken-Poole roll-call position (Voteview) is scored against a seat-conditional per-party expectation (Canes-Wrone, Brady & Cogan 2002's district-relative extremity). Both apply the same way in safe and competitive seats, and position is scored symmetrically — v6.13 decided each of those choices by testing them against House re-election results. Since v6.27 a position counts at the strength its roll calls support, min(1, w(n) / w(200)) with w(n) = n / (n + n0): a full record (200 votes or more, a convention) counts in full, and n0 is measured on Voteview's own thin and full records with drift per transition (`scripts/calibrate_position_confidence.py`, `app/data/position_confidence.json`), one curve for both chambers over every Congress since 1989, chosen by predicting the next Congress with each transition's n0 fitted on the earlier ones only (its drift, shared by every structure, from its own full pairs) and keeping the simplest structure whose error is within one standard error of the best's (the one-standard-error rule, a convention, adopted in review after four other rules had been tried, two leaving one member out, then two forward): the latest era's curve (transitions since the 110th Congress, a split by convention) has lower error than one curve by about two standard errors of the paired difference (just under two), about one over the last three transitions alone (computed from `forward_test` and its `last_three`), and a window of the last six transitions about as much (just over two), but both are well within the best's own standard error, a curve per chamber predicts no better, and with the split or the width chosen from earlier Congresses alone each has lower error than one curve only within the noise; nothing in it follows the sitting Congress); the saturation scale comes from full records, and a member with no position in a current section sits at 50 (until a new Congress's positions pass the gates the part is left out); the same study kept the 50-at-the-norm shape and the scale pooled across both parties (the pooled scale a stated choice: the evidence decides neither way) (`docs/research/constituent-alignment.md`, section 14). The vote score is highest when a member breaks with their party about as often as same-party members of similarly-leaning seats, and falls both ways (v6.16): the gap is measured in standard deviations per vote (`seat_residual`, the Pearson residual against a fractional-logit expectation), so a few extra points count for more where members of that seat rarely break, and it reaches 0 at 1.5 of the party's 90th-percentile gaps above the expectation or 3 below, so loyalty costs half as much as breaking too often. Each party is read on its own scale, so a party that is more unified in a given Congress isn't scored higher for it. The member's own party's primary voters reward this shape; the general electorate rewards breaking more than the norm — the score follows the first, as a stated choice (see `docs/research/constituent-alignment.md`, sections 8–12). Ideal points are ingested automatically every pipeline run (`app/pipeline/fetch/voteview.py`, ingestion-gated) — no manual step.
- **Legislative Effectiveness**: Volden & Wiseman's (2014) Legislative Effectiveness Score — each bill counted at every stage it reaches, divided by the chamber's total at that stage, so advancing a bill counts for far more than introducing one (v6.14; `docs/research/les-stage-weighting.md` checks it against their published scores). Five stages, as in V&W: introduced, action in committee, action beyond committee, passed the chamber, became law (v6.17). A bill reported out of committee shows as "Reported by Committee" and one under floor debate as "On the Floor" (added 2026-09, when S. 4668 read "In Committee" through a week of Senate floor votes); both are credited as action beyond committee. `scripts/research_les_stage_classifier.py` checks the stage each bill is given against V&W's per-member counts for the 118th Congress — benchmarked against the median member of the sponsor's majority/minority status in their chamber, re-measured from its current members every pipeline run, cosponsorship-network leadership (PageRank, tenure-confidence-scaled), and bipartisan coalition attraction (v6.11, moved from Constituent Alignment — the receive-only share of cross-party cosponsors a member attracts to their own bills, the construct Harbridge-Yong, Volden & Wiseman 2023 show predicts lawmaking success; compared with the median member of the member's own party since v6.25, because Republican-sponsored bills draw more cross-party cosponsors under either majority).

### Senate & House Scores

Each senator and House representative carries five sub-scores (0-100, higher = better); the three in `SCORE_WEIGHTS` are weighted into the overall, the other two are informational:

| Metric | Weight | What It Measures | Key Reference |
|--------|--------|------------------|---------------|
| **Funding Independence** | 33% | PAC dependency + small-donor share + top-donor concentration + industry concentration | Stratmann 2005; Parmigiani 2025 |
| **Constituent Alignment** | 33% | Break rate vs. same-party members in same-lean seats + roll-call position congruence (Nokken-Poole vs. seat-conditional norm); senators also: approval among the state's other-party voters and independents (CES), around the typical senator | Carson et al. 2010; Canes-Wrone, Brady & Cogan 2002; Nokken & Poole 2004; Ansolabehere & Kuriwaki 2022 |
| **Legislative Effectiveness** | 34% | Stage-normalized Volden & Wiseman LES (majority-status-benchmarked) + cosponsorship leadership (PageRank) + bipartisan coalition attraction | Volden & Wiseman 2014; Harbridge-Yong, Volden & Wiseman 2023 |
| Promise Persistence | unweighted (v6.0) | Published as `null`: campaign-promise tracking was removed in 2026-07, so its formula (commitments kept vs. broken + vote participation) has no promises to read, and the stored value is a near-constant | Naurin 2011; Martin 2011 |
| Funding Diversity | unweighted (v6.5, folded into FI) | Source breadth + industry diversity (inverse HHI) | Rhoades 1993; Parmigiani 2025 |

House representatives use the same scoring framework, data sources, and classification pipeline as senators, ensuring comparable scores across chambers (with chamber-specific references where the chambers' real baselines genuinely differ — the PAC-share fit, the effectiveness medians and spread, the break-rate expectation — each measured from that chamber's members every run, not typed in).

Score history is tracked in `ScoreSnapshot` records so the frontend can render historical score trends per senator/representative.

Additional member metrics, both chambers. Ideology and partisan depth are informational, not scored; the Leadership Score is also shown on its own, and is the leadership part of Legislative Effectiveness (25% of it, 30% when there is no coalition data):

| Metric | What It Measures | Technique |
|--------|------------------|-----------|
| **Leadership Score** | Legislative influence — how many peers cosponsor this member's bills | PageRank on cosponsorship graph (Brin & Page 1998) |
| **Ideology Score** | Behavioral ideological position derived from cosponsorship patterns | SVD on cosponsorship matrix (Tauberer 2012) |
| **Partisan Depth** | How deeply aligned with their party across policy areas | Yea/Nay ratio on D- vs R-leaning bills per area, with SVD ideology as a prior while votes are few; the label is the member's tercile within their own party |

### Supreme Court Justices (not scored)

No justice is scored or ranked (justice v3, see Phase 5 above). Justice v2's score, independence from the appointing president, failed a placebo test: a window of the same length placed later in each justice's career reproduces most of the differences between justices. Two alternatives (a same-case baseline, a career-tenure curve) failed the same test. Each justice's appointer estimate is shown with its 95% confidence interval as information, and the Court-level finding is stated on the leaderboard and About pages: about 2.5 points once career timing is set aside, 95% interval −0.7 to +5.6. `docs/research/justice-scores.md` has the full study, including why justice v2 had replaced consistency and independence from the appointing party's bloc (both ranked justices by distance from the Court's median, Spearman −0.82 and −0.75).

### Presidential Scores

Presidents are scored on three dimensions — Public Mandate (25%), Effectiveness (25%) and Historical Legacy (50%), each president's shown as the share it actually carries when a dimension is missing — using a mix of live data (BLS payroll jobs, FRED unemployment and consumer prices, MeasuringWorth real GDP, Maddison/World Bank peer-economy GDP, Voteview House polarization) and historical records (C-SPAN Historians Survey, UCSB American Presidency Project approval and election margins). Independence, Follow-Through, and Competence were removed in 2026-07 rather than left as hand-set values with no live formula behind them; a president with no data source for a dimension shows N/A and the overall score renormalizes over whichever dimensions actually apply. See the [scoring changelog](/changelog) for the full account.

A member's dimension falls back to a neutral 50 when its data is missing or too thin to trust (a new member, a source with nothing for them); a part resting on few observations is shrunk toward a reference (Constituent Alignment's voting part toward its party's typical score, its position part and Legislative Effectiveness's leadership part toward 50), while Funding Independence drops a part it can't measure and Legislative Effectiveness's bill part is not shrunk; a president's dimension with no data source shows N/A instead. A record that is present but empty is not missing: a member with no substantive bills after half a year in office scores Legislative Effectiveness on a credit of 0. An outage is not a record either: when Congress.gov can't give a run a member's sponsored legislation, the member is scored on the bills an earlier run stored, and the breakdown names that run's date; with none stored, the bill part is not measured and leadership and coalition attraction carry its weight. A bill whose action history can't be read is staged from its latest action and degrades only itself, and the run's ops alert names it. No LLM input is used in score calculation — formulas are deterministic and auditable.

---

## Site search

One box in the header searches everything with a page: members (and their
former ids and surnames), presidents, justices, bills (every spelling of a
number: "HR 1", "H.R.1", "hr1"), roll calls, agencies (name and acronym),
committees, states, districts ("TX-23", "Texas 23rd"), races, ballot
measures, Action Center issues and ongoing stories, nominations, Explore
documents (by FR document number, FR citation, executive order,
proclamation or RIN, and by title through Explore's own keyword index), and
the site's own pages (`backend/app/data/site_pages.json`, held equal to the
frontend's `SECTIONS` and About chapters by `frontend/src/lib/sitePages.test.ts`).

**Index, at write time.** The pipeline process rebuilds `/data/site_search.db`
(`app/site_search_index.py`) when a cheap fingerprint of its source tables
moves, checked every ten minutes: a separate SQLite file, written whole and
renamed into place, with an FTS5 table (prefix indexes), an FTS5 trigram
table for typos and an exact-key table for identifiers. Nothing in the main
database is migrated or locked for it.

**Query, on the read path.** `app/site_search.py`: an identifier parser,
FTS5 prefix and trigram candidates, and a deterministic score (an exact
identifier, then a name equal to the query, then the share of the query's
words in a name, with near spellings credited by similarity), plus a small
weight for importance (a sitting member over a former one, a recent issue
over an old one). No model, no LLM.

**Straight to the page.** Enter on a query that clearly names one page goes
there (`GET /search?q=` answers with a redirect, so it works without
JavaScript, and the autocomplete highlights that match first): an
identifier's page, or a match that clears a floor and leads the next by a
margin. Two pages the query names exactly (a state and an agency both
called "NE", two members with one surname) are a list, never a jump.
Section words read against the page's own headings
(`app/data/site_search_sections.json`) land on the section: "HR 1 votes"
on the bill's votes, "<member> donors" on Funding Independence, a district
and a year on that race on the state ballot page.

**Measured, not asserted.** `scripts/calibrate_site_search.py` builds known-item
probes from the index itself (a name, a surname, an acronym, an identifier,
a one-letter typo, a page name, section words, a prefix, a document title),
fits the four scoring weights and the two jump thresholds on half the
entities and reports on the other half
(`app/data/site_search_calibration.json`). A wrong jump is worse than a
list, so the thresholds allow no wrong jump in any probe type on the fit
half. On a copy of the production database (32,418 entities), held out from the fit:

| Probe type | Probes | Right page first | Straight there | Wrong jump |
|---|---|---|---|---|
| Name | 395 | 100% | 96.9% | 0 |
| Surname | 292 | 100% | 98.3% | 0 |
| Acronym | 214 | 100% | 93.6% | 0 |
| Identifier | 415 | 100% | 100% | 0 |
| Typo | 403 | 99.8% | 72.9% | 0 |
| Page | 75 | 100% | 93.2% | 0 |
| Section words | 409 | 100% | 99.8% | 0 |
| Document title | 385 | 97.4% | 92.5% | 0 |
| Prefix (in the first 8) | 397 | 100% | | |

All 2,588: 93.2% straight to the right page with one Enter, no wrong jump;
p50 6 ms, p95 25 ms per query on an x86 desktop (the whole read path,
Explore's title search included). "Straight there" counts the unambiguous
probes; an ambiguous one (two pages of one name) that lists is right.

**Privacy.** Nothing a reader types is kept. Answers are cached only in the
reader's browser (`Cache-Control: private`), never in nginx's cache on disk;
nginx's access log drops every query string and referrer, uvicorn's drops
query strings, and the `/search` page asks the backend with `no-store`.

## Hybrid Search (Explore)

The Explore feature searches primary-source government documents. It is not a
single similarity score: general web search has never been one, and neither is
this. Four independent rankers are combined — two retrieval channels and two
query-independent priors.

**What is indexed:** Senate and House floor speeches, presidential actions
(executive orders, proclamations, memoranda), Supreme Court opinions, and
Federal Register rulemaking documents — five source types, not bill text.
A search, or a browse with no query, can be limited to one agency and its
sub-agencies (`agency`, a Federal Register slug; see Agencies).
Floor speeches come from the Congressional Record, a day at a time
(`fetch/congressional_record.py`, `analyze/floor_speech.py`):

- **Which sections.** The day's package MODS lists every granule (Record
  section) with the members it records as speaking; only Senate and House
  granules with a speaking member are fetched — about 73 of 146 a day, and
  the MODS missed a member's turn in 1 of 733 granules measured. Every such
  section is read: the ingest used to read the first 8 of each day, which on
  most days were the opening prayer, pledge and leader time.
- **What a speech is.** A member's turn begins with the Record's designation
  ("Mr. SMITH.") and ends at the next member, the presiding officer or a
  clerk, or the next heading — never at a fixed length (turns used to be cut
  to 400/500 characters). Inserted documents (an article, a bill's text,
  under their own headline) are not the member's speech.
- **What it is titled.** The Record prints its own heading over each speech
  inside a section; a speech that follows a heading directly takes it. Any
  other turn — a reply in a colloquy, a member speaking in a debate someone
  else opened — is "Remarks on" the nearest heading a speech opened, else
  the section's title. Every turn used to carry its section's title, so one
  member was credited with speeches titled after another's tribute, and a
  member's three tributes shared the first one's title.
- **Floor business is not a speech.** Quorum calls, unanimous-consent and
  scheduling requests, yielding time and motions are recognised paragraph by
  paragraph by embedding similarity to the Record's own formulas against
  speech-like sentences, calibrated against hand-labelled turns (the
  measurement is in `floor_speech.py`). Before, one leader's quorum-call
  rescissions made them Explore's most prolific "speaker".
- **Links and scope.** Each speech links to its own section's page on
  GovInfo, not the whole day. The whole sitting Congress is indexed, and
  only it: GovInfo's collection lists packages by last modification, and a
  reprocessed 1996 issue once came through; the run itself deletes
  speeches outside the Congress.
- **Back-fill, without a gap.** Each run reads up to `SPEECH_DAYS_PER_RUN`
  (15) unread days, newest first — new issues, then back toward the
  Congress's first day — and records each day read. A day's stored
  speeches, in any format, are replaced only when that day is read again,
  so speeches stored before this format stay searchable until their day
  is re-read. A day that cannot be read whole is retried next run, never
  taken as a day with none, and holds the read frontier: older-format
  speeches on dates with no issue are swept only behind a frontier every
  issue has passed. Bumping `SPEECH_FORMAT` re-reads the Congress the
  same way.
- **Cost.** A full session day is about 73 requests at GovInfo's 1
  request/second (~75 s, the event loop free), ~32 s of floor-business
  classification and ~40 s of embedding on the Pi, both in worker threads:
  about 2.5 minutes, so a run adds at most ~36 minutes. The 119th Congress
  had 372 issues on 2026-10-08, so the back-fill (or a format re-read)
  takes about 25 nights; after that a run reads only the new issues.

Every document feeds three structures, all rebuilt from the
`explore_documents` table at the end of each ingest run:

| Structure | Where | What it holds |
|---|---|---|
| `vec_explore` | `/data/vectors.db` (sqlite-vec) | 384-dim embeddings of each document's whole text, in windows of the encoder's context length each led by the title, with doc type / chamber / politician as filterable metadata |
| `explore_fts` | app DB (SQLite FTS5) | A BM25F inverted index over title, summary and body. External-content, so the text is not duplicated; triggers keep it live between runs |
| `authority` / `cited_by_count` | `explore_documents` columns | PageRank over the citation graph between these documents (an Executive Order, FR citation or FR document number cited in the text; a RIN names a rulemaking and makes no edge) |

**Query flow:**
```
User query
    │
    ├─▶ semantic  — embed (all-MiniLM-L6-v2) → sqlite-vec KNN, cosine
    │
    └─▶ keyword   — parse to a safe FTS5 MATCH → BM25F, title-weighted
              │
              ▼  filters (doc type / chamber / politician / open-for-comment)
              │   pushed into BOTH channels, not applied afterwards
              ▼
        weighted reciprocal rank fusion over four rankers:
        semantic · keyword · freshness · citation authority
              │
              ▼  collapse near-duplicate documents
              ▼  cap results per member/agency (host crowding)
              │
              ▼ returned with a keyword-in-context excerpt (matched terms
                marked), source URL, doc type, citation count, and — for
                open rulemakings — a comment link and deadline
```

**Why two retrieval channels.** A 384-dim bi-encoder embeds "Executive Order
14110" and "Executive Order 13985" to almost the same point: the number
carries the meaning and the model never saw it. The same failure covers docket
numbers, RINs, agency acronyms, statutory citations, and member surnames —
exactly the queries where the user knows precisely what they want. Classical
inverted-index retrieval is best at those and weakest where the embedding is
strong (paraphrase, synonymy, topical queries). Running both and fusing them
is why this is a hybrid engine rather than a bigger embedding model.

A query that is *only* publisher identifiers, in any case (`89 FR 52508`, `rin 1615-ad22`,
an executive order or proclamation number, a regulations.gov docket id — the
formats `document_authority` already parses for the citation graph) goes to
the keyword channel alone. The encoder's ranking for it is noise, and fused,
a document both channels ranked middling outscored the keyword channel's
exact hit: measured on 124 identifier probes, fused R@1 was 0.25 against
keyword's 0.65 (2026-10).

**A document is found by its own name.** A Federal Register document's
citation ("91 FR 48248") is printed in the page header, never in the text, and
its document number ("2026-15603") survives in the body only when the body is
short enough to keep the closing "[FR Doc. ...]" line, so neither channel could
answer a reference a researcher holds: on known-item probes the citation put
its document first 1% of the time and in the top 20 10%, the document number
47% and 75%. Exact identifier matching is a third, partial ranker
(`explore_search._identifier_matches`): the identifiers a query names (any
case, a bare document number too) are matched against the identifiers each
document is *known by* — `document_authority.declared_identifiers`, the same
names the citation graph uses: the stored FR citation, document number and
RINs, the external id, an executive order's number — and the documents they
name lead, ahead of crowding control. Documents that cite the identifier are
not matches; they follow, ranked as before. Measured on 300 sampled documents
(2026-10-14): FR citation R@1 0.011 → 0.854, R@5 0.090 → 1.000; FR document
number R@1 0.465 → 1.000; every other style unchanged. An FR citation is a page,
and 34% of cited documents share their first page with another notice, so a
citation can name up to four documents; all of them lead.

**Dockets, Record citations, and what isn't held.** A rule is also known by
the Regulations.gov docket its comment link names ("IRS-2026-1090", and the
comment document "IRS-2026-1090-0001"; `document_authority.docket_identifiers`),
and a floor speech by its Congressional Record citation, the page its GovInfo
granule starts on ("172 Cong. Rec. H5836"), and the granule id itself
(`congressional_record_identifiers`); "EO 14148" is read as the executive order
it abbreviates. A query that is only identifiers asks the keyword channel for
each identifier as written (`explore_search._identifier_phrases`): it used to
match any word of it, so "EO 14148" returned thirty other orders on "EO". When
no document is known by an identifier the query names, the response says so,
with its official copy (`notHeld`, `services/explore_coverage.not_held`): an
executive order numbered below the oldest held reads "Executive orders before
May 23, 2025 aren't held here", with a link to the Federal Register. Each
document type's first and last date held is in `GET /api/explore/stats`
(`coverage`) and on the search page, under the box. Known-item probes of 400 sampled documents (2026-10-16 database), hybrid, before and after: docket R@1 0.667 → 1.000 (18 probes); Record citation R@1 0.000 → 0.184, R@20 0.000 → 0.923 (196; every speech in a granule shares its first page); identifier lifted from a document's text MRR 0.640 → 0.695; FR citation MRR 0.906 → 0.911; title, paraphrase, rare, passage and FR document number unchanged; ALL MRR 0.593 → 0.631. Of 40 identifiers the corpus does not hold, every one got documents back before and none was told it isn't held; after, 13 got documents back (each citing it) and all 40 were told.

**Why rank fusion rather than score blending.** Cosine distance and BM25 live
on unrelated scales; min-max normalising each makes the blend depend on
whatever the best and worst scores happened to be for that one query.
Reciprocal rank fusion (Cormack, Clarke & Büttcher 2009) discards the scores
and fuses the rankings: `score(d) = Σ w_r / (K + rank_r(d))`. A ranker that
didn't return a document contributes nothing for it. K is not the published
60 but the retrievers' measured resolution δ (the median rank disagreement
between the two channels, 10 on the 2026-10-08 corpus;
`explore_ranking.rrf_k`): at K = 60 a document both channels ranked around
30th outscored one channel's first hit, and the known-item harness had the
fused ranking missing 17% of known items against the keyword channel's 4-5%. With K = δ
fusion's MRR rose from 0.49 to 0.57 and 0.51 to 0.58 on two samples, and
improved on every query style.

**Only an identical text is a duplicate.** Results collapse byte-identical
rows to their best-ranked copy. They used to collapse by a prefix fingerprint
of the title and opening text, which on the 2026-10-08 corpus (9,458
documents) hid 249 distinct documents — recurring Federal Register notices
such as each month's antidumping review notice or each OFAC sanctions action,
which repeat their title and boilerplate — while catching 5 real copies. The
fingerprint plus the body's length now only groups candidates, and a
candidate is a copy only when its whole normalised text matches
(`explore_search._collapse_duplicates`).

**Citation authority is the PageRank analogue.** Federal documents cite each
other constantly and by canonical identifier — executive order numbers,
"volume FR page" citations, RINs. Those formats are published in the Office of
the Federal Register's Document Drafting Handbook, so they are parsed, not
classified. An executive order agencies keep invoking a decade later is doing
the same job as a heavily-linked web page. A document only enters the
authority ranking if the corpus actually cites it: citability is unevenly
distributed by document type (a rule carries an FR citation, a floor speech
carries nothing), so the prior can lift a well-cited document and can never
push down one that had no way to earn the signal. On a corpus with no
cross-references the ranking is empty and the prior does nothing.

**Ranking weights are measured, not asserted.** They live in
`config_definitions.py` under "Explore search ranking".
`backend/scripts/evaluate_explore_search.py` measures MRR and Recall@k for
semantic, keyword and hybrid separately, using known-item retrieval over nine
query styles (title, paraphrase, identifier, the document's own FR citation,
document number, docket and Record citation, rare-term, passage) with relevance
judgments derived from the corpus rather than hand-labelled. Change a weight,
re-run it. Every probe is built from the document's own words, so the
keyword channel leads by construction; compare the semantic channel with
itself across a change, not with keyword.

**Members and bills, beside the results.** Explore searches documents, so a senator's surname gave thirty floor speeches and no link to the senator's page, and "flag burning" never offered the Flag Burners Act. A search now gets two typed groups above the results, like the committee card (`GET /api/explore/matches`, `services/explore_matches.py`, `components/explore/MatchGroups.tsx`): **Members**, every word as the start of a word of a sitting member's name or state (`word_search`, BM25 with a name term counting five times a state term), or, when no name holds the words, a misspelt surname by difflib's similarity to each word of a member's page address, from 0.75, as the member 404 page suggests; and **Bills**, the bill a whole bill number names (offered even when no sitting member sponsored it, since every bill has a page), then bills whose title or short title holds every word, by BM25, five shown and a link to the rest in the bills list. Titles only there, not sponsors: matching sponsors put a member's every bill beside a search for the member. They are grouped, never fused into the document ranking, so its weights don't move. `evaluate_explore_search.py` measures them the known-item way too (`--skip-documents` for the groups alone), on 150 sitting members and 150 bills of the 2026-10-15 database: a surname finds its member in the group at MRR 0.926 (R@5 0.987), a full name 1.000, a surname with two letters swapped 0.894; a bill number its bill 1.000, its short title (or title) 0.812, two of its title words out of order 0.685 (R@5 0.893). Before, every one of these was 0. Of 729 document probes, 1.1% got a Bills group and none a Members group, and the document table is identical before and after on every one of its 32 style and channel rows (ALL: hybrid MRR 0.660, keyword 0.730, semantic 0.339).

**The document page.** `/explore/{id}` is rendered on the server from
`GET /api/explore/{id}`, so the text is in the first response: a reader arriving
from a search engine sees it at once, Back returns to the place they left (a
page that fetched its text after loading came back at 190 px of 1,651), a
missing document is a 404 and an unreachable backend the error page, never a
200 reading "Document not found: 500". Only the analysis and public comments
load in the browser. The page says in one line what Civitas is, offers "Back to
results" only to a reader who came from them (`?q=`), and closes with "Cite
this": the document's own Federal Register citation and document number when
it has them (`frCitation`, `frDocNumber`, read from the identifiers it is known
by, `api/explore.py fr_reference`), a floor speech's Congressional Record
citation (`crCitation`), and an APA-style reference that credits who issued
the document (the agency in the Federal Register, the Executive Office of the
President, the speaker in the Congressional Record, the Supreme Court), with
its official URL, and names Civitas only as where it was accessed
(`lib/citedSources.ts`). Roll calls cite the Senate or the House Clerk, bills
and nominations Congress.gov; Civitas's own scorecards and analyses still cite
Civitas, as a dataset with the method version.

**Permanent addresses.** A document's page is `/explore/{key}`, the key made
from its official identifier (`services/explore_keys.py`): `fr-2026-17123`
for a Federal Register document, `crec-` plus the Record granule and a hash of
the speaker's turn for a floor speech, `scotus-` plus the docket for an
opinion. The row id it replaced was not permanent: the Register refresh
removes provisional numbers, a re-read Record day replaces changed speeches,
and every floor speech outside the sitting Congress was deleted, all 9,131 of
them on each January 3. An old `/explore/{id}` answers a 308 to the key; the
canonical, the sitemap, every link and the public API's `siteUrl` use it; a
speech whose copy from an overlapping granule was dropped answers under the
copy kept; and a key no document answers any more still links its official
copy from the 404. Floor speeches are kept after their Congress
(`explore_pipeline._purge_old_format_speeches`), about 120 MB a Congress.

Bill text itself is not indexed here; it's used separately, title-only, for
the tier-3 kNN bill-classification step in the scoring pipeline (see Phase 3
above).

### Two embedding models

The platform loads **two** sentence-transformers, both 384-dim and both in the
~22M size class, split by what they are good at:

| Model | Used for | Why |
|---|---|---|
| `Snowflake/snowflake-arctic-embed-xs` (primary) | Classification and the learning store — bill policy areas, donor/industry kNN, party alignment, the numpy batch-similarity paths | The classification thresholds were measured and calibrated against this model's geometry |
| `sentence-transformers/all-MiniLM-L6-v2` (index) | The semantic-search index, plus the Action Center's similarity gates (policy filter, trending mask, explore re-rank, title dedup) | Arctic is retrieval-*asymmetric*: it packs same-register text into a narrow ~0.55–0.87 raw-cosine band, which left several similarity thresholds unable to separate real matches from noise. MiniLM measured ~4x the separation margin on explore-doc anchoring and ~3x on policy relevance against this platform's own live failure cases |

They coexist deliberately rather than as a migration left half-finished:
swapping a classification gate to a different embedding space without
re-measuring its threshold is how thresholds go vacuous, so the classification
subsystem stays on the primary model until its own recalibration pass. Both are
~22M parameters, so carrying two costs little on the Pi.

The index records which model built it (in `vectors.db`'s `meta` table). On a
mismatch at startup the vec tables are dropped and a background reindex runs
from the `ExploreDocument` rows already in the app database; search returns
"index not ready" until it finishes.

---

## This week

The homepage, and in a one-line form the pages most readers arrive on (a member's scorecard, the daily Congress report and a bill's page), carry a "this week" strip from `GET /api/this-week` (`api/this_week.py`), rendered on the server with the page:

- **Election Day.** Days until the active election (`election_phase.active_election`; after it, the count), and every state's ballot page as a link. It is a list to pick from, never a field asking where the reader lives (AGENTS.md §8); the compact form keeps the list behind a disclosure.
- **Congress this week.** Whether each chamber is in session today, its record votes this week (the latest three listed, each linked to its bill), and the next meeting as the Record itself gives it (the newest notice on or before today, its date read from the Digest's or floor log's own wording). A week with no session reads "Congress is out until Tuesday, October 20"; a chamber with no notice on record says so.
- **Comment deadlines.** The next three on federal rules: rules an Action Center issue from the last two weeks cites first, then rules other documents cite, then any other rule, soonest deadline first within each. Notices (with them every information-collection and meeting notice) are left out by type, and so is a templated series: a Regulation Identifier Number shared by three or more open rules, as every FAA airworthiness directive shares one (on 2026-10-11, 79 RINs covered one open rule, 2 covered two, then 4, 4, 11 and 54). A series also inflates the citation count, since every document under the RIN reads as citing the rest. The compact strip's "next comment deadline" is the soonest of those same rules whatever its tier, a rule closing today included, on the deadline's own clock (Eastern): the first of the three can be a rule in the news days out while others close today.

Every sentence is a template around stored figures. When the backend can't answer, the strip is left out and the page renders as before. On a Congress day page about another day (a past day's report, or `/congress` when the latest sitting wasn't today), the one-line strip is headed "Now · Thu, Oct 15" (the API's `congress.today`, Eastern) rather than "This week": a reader on September 16's page read "This week … The Senate meets today" as September 16.

## Agencies

`/agencies` and `/agencies/{slug}` make federal agencies records of their own (`api/agencies.py`). An agency is the Federal Register's: its `/api/v1/agencies.json` lists 473 (abolished ones included), each with a slug, a description, a parent and sub-agencies, stored in `agencies`. Each agency page shows what it spends, the documents it issued and the executive orders that name it, every block with its source and the day it was read.

- **Spending** comes from USAspending.gov: the toptier list (`/api/v2/references/toptier_agencies/`, one request) for the current fiscal year's budgetary resources, obligations and outlays, and `/api/v2/agency/{code}/budgetary_resources/` for the same by fiscal year (one request per agency, about 110). USAspending names no Federal Register agency, so the two lists are joined by name (`fetch/agencies.crosswalk`): token-sorted names ("Agriculture Department" and "Department of Agriculture" sort alike) compared by SequenceMatcher ratio, one USAspending agency to at most one Register agency, at a threshold derived by `scripts/calibrate_agency_crosswalk.py` and stored in `app/data/agency_crosswalk.json`. Its labels come from evidence the score never reads: where an agency's USAspending abbreviation equals exactly one Register agency's short name, that pair is a positive, and the same agency's best score against every other Register agency a negative; the threshold maximizes Youden's J over them (0.834 on 2026-10-15: 76 of 80 positives and 1 of 80 negatives at or above it). 94 of 111 toptier agencies matched; the other 17 are stored with no match and named on the page as not linked, never paired with a runner-up. A sub-agency's page points to its department's budget, which includes it. A fiscal year USAspending hasn't reported is never shown as $0: its toptier list answers 0.0 for an agency with no submission yet (on 2026-10-16 all 10 agencies listed at 0.0 for FY2026 had 0.0 obligations and outlays too, and no FY2026 entry in their year-by-year record, while 5 had a nonzero FY2025, the Railroad Retirement Board's $17.8B among them), so a year counts as reported only when the year-by-year record has it; otherwise the page says "not yet reported" and leads with the latest year that is, and the agency is left out of the year's total and shares. An agency USAspending's list stops including is kept with the date it dropped out (`agency_budgets.retired_at`), never read as zero. With no budget to show, the page says which case it is: not yet read (an outage), no USAspending agency matched by name, listed with no figures for any year, or dropped from the list. An agency page also lists the Action Center issues that cite one of its documents (the issue's own `related_explore_ids`).
- **Documents.** A Federal Register document's agency is the most specific one its `agencies[]` lists (the FAA, not the Transportation Department it sits under), stored with its slug in `explore_documents.agency_slug` at ingest, so a document reaches its agency with no name matching. Documents stored before the column are read for it by the nightly Federal Register refresh, 2,000 a night beyond the refresh's own 90-day window. A department's page lists its sub-agencies' documents too: open comment periods (rules first, as the Today tab orders them, with paperwork notices folded under "Paperwork notices (N)"), proposed and final rules (newest first), and a link to Explore filtered to the agency (`GET /api/explore?agency={slug}`; the agency select on Explore lists every agency with documents). A joint rule counts for its first listed issuer only.
- **Executive orders naming it**: every executive order lists the Executive Office of the President as its agency, so these are found by text, the keyword index's exact phrase for the agency's Register name or its USAspending name. When the keyword index can't be asked the section says so rather than reading as "none".
- **Lobbying aimed at an agency** is not shown. LDA filings list the government entities each activity lobbied, but the LDA API can't filter filings by entity, so a per-agency figure would mean downloading every filing of a year.

Both lists are read by a weekly job outside the nightly chain (`scheduler._agency_sync`, under the `AGENCY_SYNC` lease): tried when the pipeline process starts and hourly at :30, each source read once a week has passed since it last stored (or never has), so a source that failed is retried within the hour (one request while it is down) and a new deploy or a fresh database fills the agency pages as the pipeline comes up. Until the Register's list has been read once, `/agencies` says it hasn't been read rather than counting 0 agencies. The same job links documents stored before `explore_documents.agency_slug` existed to their agency (`agencies.link_documents`), hourly until none is left: a document whose stored agency has no sub-agencies is linked by that name (the Register's issuer can be no more specific), and a department's has its issuer read from the Register, newest first, 500 an hour (5,855 such documents in 2026-10, about twelve hours, where the nightly refresh's 2,000 a night took five nights). A number the Register has no document for is removed, as the nightly refresh does. Until every document is linked the API says how many are (`documentsLinking`, `documents.linking`) and the pages word every count as a count so far: an empty list reads "None linked to this agency yet", never "None". A data reset starts the sync as soon as it finishes. The directory's name search matches a short name exactly first ("EPA", "IRS", "FBI"), then a whole name, then every typed word as the start of a word of the name (`matchAgencies`): a substring match put the EPA sixth of 28, behind every "Department". Browsing, it lists departments and other agencies with sub-agencies, with the independent agencies and those with nothing on Civitas each behind a disclosure, two columns from a laptop up (9.8 screens and 121 tab stops at 375 px before, 4.7 and 55 after). Explore's match groups include **Agencies** beside Members and Bills (`explore_matches.agency_matches`): every typed word as the start of a word of an agency's name, short name or the Register's description of it, so "aviation" offers the FAA. A function the Register's own words don't name ("drones") finds nothing there. A source that can't be read returns None, leaves what is stored as it was, and raises an ops alert; an empty list counts as unread.

## Source ledger

The homepage's "Built from the public record" panel lists each source with what it has supplied and how recent it is, from `GET /api/sources` (`api/sources.py`), counted from the stored records rather than typed: roll calls and sponsored bills in the sitting Congress, floor speeches, Federal Register documents, the Court's votes and opinions, and disclosed stock trades, each with its newest record's date. FEC filings carry no single date the site keeps, so that row gives the day the site last read the FEC and says "last read". A source with nothing stored shows 0 and "nothing on record yet" rather than dropping out; when the backend can't answer, the panel falls back to the plain list of source names.

## Compare

`/compare` sets two members of Congress side by side: the Representation Score and its three parts, and the money. Two members of the same chamber also get how often they voted alike this Congress, from `GET /api/compare/agreement?chamber=&left=&right=` (`api/compare.py`): of the chamber's roll calls in the sitting Congress on which both cast a Yea or a Nay, how many they voted the same way on, as a sentence ("Voted the same way on 603 of the 723 roll calls this Congress where both cast a Yea or a Nay (83%).") and the backend's whole percent, then the five latest votes they split on, each with both votes and a link to its roll call. Housekeeping questions (quorum calls, motions to table, the previous question; `normalize_votes.is_housekeeping`) are left out, as Constituent Alignment leaves them out: they split on party lines by habit. A senator is matched to the roll calls the way a bill page links a Senate vote to a member (`bill_record.MemberLinks`): the roll call's surname as whole words of the senator's name (so a name ending "Jr." still matches), the state, and, where two senators of one surname share a state while both are on file, the LIS id the vote carries; a representative by bioguide id. Members of different chambers share no vote, so the section is left out for them.

## Reading the leaderboard

The Senate and House leaderboards rank on the whole-number score the page shows, so members who show the same score share a rank. Most of a chamber sits in a narrow band (on 2026-10-13 the 433 House members held 47 distinct scores, half of them between 53 and 65), so a rank alone overstates how far apart two members are. Three things say so on the page, each from the backend:

- **The field, by score.** A strip above the table with one mark per serving member, stacked on their score, and the middle half and median (`GET /api/leaderboard/spread?chamber=senate|house`, `score_display.score_spread`). The column of the row under the pointer or keyboard focus lights up with how many members show that score. The same answer carries each party's serving count for the party filter (the House list is paginated, so the page can't count it; it read DEM (0) and REP (0) before).
- **Ties.** Beside a shared rank, "TIED ×4" with how many share it, read from the spread's counts; shown on the default ranking of the whole chamber only, since a party filter re-ranks within the party.
- **Thin data.** `thinData` on each leaderboard row (and in the public API's rows): the weighted dimensions whose data-sufficiency grade (`score_calculator.calculate_confidence`) is "low", where the score is mostly the pull toward a reference rather than a measurement (`score_display.thin_dimensions`). Shown as a "THIN DATA" tag naming them.

No rank interval is shown: a score has no standard error to draw one from, and a band of ±1 point would be a choice, not a measurement.

## On the record

Below the day's record and "This week", the homepage lists what stood out in Congress's latest week of record votes, from `GET /api/notable` (`api/notable.py`), rendered on the server with the page. Each entry is a fixed sentence around the chamber's own roll call and ends in the commands that check it: the roll call, the member, and (for a senator) the member beside the state's other senator on `/compare`.

- **Closest vote.** The simple-majority question with the smallest gap between yeas and nays ("Decided by 1 vote in the Senate, 49–50: Amendment Rejected."), newest on a tie. A 3/5 or 2/3 question is left out, since how close it came depends on its threshold, and so is a housekeeping question (`normalize_votes.is_housekeeping`).
- **Lone break.** The one member of a party who voted the other way from every other member of it, with at least ten of the party voting ("The only Republican to vote Yea: …, against 50 Republicans voting Nay."). At most one per party, the newest, so neither party fills the list by rule; two on one vote are listed in party-letter order. The majority leader's vote with the prevailing side of a question the party lost is the reconsider switch Constituent Alignment also exempts, not a break (the leader's current title stands for the week).

The week is the latest Monday-to-Sunday week either chamber recorded a vote in, labelled with its dates, so a recess shows the last week in session. Past 28 days without a record vote the list is empty and the section is left out: an empty week shows nothing, never filler. A score's week-on-week "biggest mover" was considered and left out: a change is only comparable on one scoring method, and on 2026-10-13 none of 534 members had a comparable seven-day change, because methods change more often than that.

The homepage is ordered by how often each part changes (2026-10): the masthead, then "Entered into the record" (the newest issues, active monitors and bills, rendered on the server; within a day the issues come first in the Action Center's rank order, so the first row is the day's top issue), then "This week", then "On the record", then the page's one index of every way into the site beside the source ledger. The homepage asks the reader for nothing: the masthead is the statement of what the record covers, one row of links ("Your members", "Your ballot", the election's pages for its phase, "Congress this week"; with a state chosen in the header they go straight to that state's pages) and one plain line on who pays for it. Search is in the header on every page: SEARCH opens the site search (see Site search), a plain GET form to `/search?q=` underneath. The reader's state is asked once, in the header, and every page that needs one starts from it: held for the open tab only (sessionStorage), with an optional "Use my location" that works out the state on the device and keeps nothing else (AGENTS.md §8). The index lists every section's pages, About's chapters included. The masthead's "For researchers" line is gone for the same reason. On a day Congress is out (`recess` on `GET /api/sections`, the same test `/congress` leads with), the index's three report figures, which would each read "None", become one backend-worded line ("No record votes or floor business since Wednesday, September 30"), and "On the record" is headed "Last week of business: <week>" rather than reading as current. "This week" keeps every state's ballot link behind a disclosure, since the masthead's state list already leads to one, and on a phone the index shows each page's figure without its caption (kept for screen readers). Measured under `next build` against the previous layout: the day's top issue moved from 5.7 phone screens down to 1.2 (7.65 s to 3.65 s by KLM) and into the first desktop screen (5.65 s to 2.65 s); keyboard stops on the whole page went from 176 to 100 at 1280 px (163 to 87 on a phone).

## Score Data Transparency

A senator's or representative's profile is the scorecard at a glance (`components/scorecard/MemberScorecard.tsx`): a header with who they are, how to reach them, the Representation Score, the rank the leaderboard gives it (served as `chamberRank` on `GET /api/politicians/{id}`) and its trend; on a phone, under the score, the scorecard in short (each dimension's one-sentence finding under a plain word, Money, Votes or Bills, linked to its column; the columns themselves fill a desktop's first screen); then where the member sits in the chamber, every serving member as a square in their score's column coloured by party, with the party's and the chamber's medians and the members ranked next above and below linked, each with a compare link (`chamberField` on the profile, computed over the same field as the rank, `api/politicians.py _chamber_field`; `components/scorecard/ChamberStrip.tsx`); then the three scored dimensions side by side, each led by one plain sentence of what its score found, then the evidence behind it (the components, the weights and their statistics sit under "How this is computed", closed by default); then their financial-disclosure holdings as a pie beside the list; then tiles for what is on record but not scored (stock trades, donor-vote links, positions by policy area). The Funding Independence column also links straight to the donor-vote links ("See how this money lines up with votes"), which on a phone otherwise sit about seven screens below it. Every full list — every vote, every donor, every bill — opens in a drawer over the page; each vote there is one line (what was voted on, linked to the bill's page, the question, the date, the member's vote, and whether it went against the party). On a phone the columns stack. At the foot of the profile, one link from the header ("methods, data & citation", `#methods`, which opens it), is the expert layer (`MethodsLedger.tsx`): for each dimension its score, weight and data grade (`representationScore.confidence`, a volume-count grade, said not to be a confidence interval, since none is computed), every component with the scorer's own sentence (the sample sizes are in those sentences), the method's section of /about/scores and its citations, and its sources; then the record as JSON from the public API (profile, votes, history), and an APA-style reference dated by the latest scoring run (`scoredOn`: the newest score snapshot's date and method version).

Each column's sentence and parts come from `GET /api/{senators|representatives}/{id}/score-breakdown`, which the profile page fetches with the profile. It serves the breakdown the run that stored the scores worked out with the scorer's own functions (`score_calculator.explain_scores`, kept with the scores in `score_breakdown`), so its components add up to the score shown even after the hourly bill refresh moves a bill; the scores are also recomputed on today's records, and a dimension that would come out differently says so ("Updated since this score: 1 bill changed after the run of ...", `sinceScored`). It returns every component (value, weight, and the scorer's own sentence on how it came about) plus `facts`: the numbers the scorecard's sentences state — PAC and small-donor shares and the comparison they are measured against, party-line votes and breaks and the seat's expected break rate, bills and joint resolutions by the furthest stage each reached and the simple and concurrent resolutions beside them (counted apart, as the component's own sentence counts bills, so a resolution the chamber agreed to never reads as a bill that passed). Each member dimension also carries `finding`: the column's first line, one sentence filled from those same `facts` by a fixed template (`services/score_findings.py`) — "Breaks with the party about twice as often as Democrats in states that lean the same way", "Gifts of $200 or less made up 44% of the money, more than the 26% typical for a state this size", "Of 54 bills sponsored this Congress, 1 became law, and 3 more passed the Senate". It names no one and uses no pronoun, and its words are held to about an eighth-grade reading level (Flesch-Kincaid: over 90 sampled members on 2026-10-11, a median of 5.3 and 97% at 8 or under). It lives outside `pipeline/` so rewording it never moves the analysis-code fingerprint. The page formats them and computes none. The header's score trend states the change `GET /api/{senators|representatives|presidents}/{id}/history` serves as `change`: since the earliest snapshot on the latest one's scoring method (and, for a member, Congress), never across a methodology change. The full voting record can be searched: `?q=` on `GET /api/{senators|representatives}/{id}/votes` matches the bill number in any spelling ("HR 123", "h.r.123", the number alone; a prefix with dots and spaces dropped, so "S 1" never finds S. Res. 15), or every word of the search in the bill's title, the vote's description, or its roll call's question and title, matched as the bills list matches (stemmed, as word starts, in any order), and the list's counts follow the search (`_scorecard_common.vote_search`; it reads the member's own indexed rows and their roll calls once, and matches the words in memory). Finding one vote among 193 meant paging through 13 pages of 15. `?period=` narrows the same list to a date range the server works out (`month`: the last 30 days on the Capitol's calendar, not the calendar month, which is empty for its first days and all through a recess; `congress`: from Jan 3 of the Congress in office; `all`), shown as LAST 30 DAYS / THIS CONGRESS / ANY DATE beside the search (`_scorecard_common.vote_period_start`). The full voting record's party-line figures (`votingRecord.votedWithPartyCount`, `votedAgainstPartyCount`, `partyLoyaltyPct`) are the counts Constituent Alignment scores (`score_calculator.party_line_tally`), so they agree with the column. Nothing in it is written by a model, and because it is the same code the pipeline scores with, the scorecard can't drift from its numbers. A president's profile is laid out the same way (`components/scorecard/PresidentScorecard.tsx`): the Presidential Score, with the rank the president leaderboard gives it (the header's comparison link opens `/compare/presidents` with this president already chosen: a server-rendered page whose two native selects submit as a plain GET form, setting both Presidential Scores, the three dimensions and the same `facts` side by side; `/compare` pairs members of Congress) (a sitting president is not ranked until the term ends), then Public Mandate, Effectiveness and Historical Legacy side by side, each stating its figures beside the all-president averages it is scored against (`facts` on `GET /api/presidents/{id}/score-breakdown`: approval and its trend, jobs per year and GDP growth, the historians' points, and the same person's other rated presidency), then stock trades and executive orders, on record and not scored. The president leaderboard opens with a summary of the sitting president's scorecard (`PresidentSummary`: the Presidential Score and its three parts, with a link to the full scorecard), shown above the ranked table rather than in it. A column says a dimension is unscored only when its score is null, never because its figures failed to load. A justice's profile follows (`components/scorecard/JusticeScorecard.tsx`): "Not scored" with the reason and a link to the research in place of a score, then the appointer estimate with its 95% confidence interval and the rates under the appointing president and under others, Martin-Quinn positions and the voting record (not scored), then agreement with each sitting justice (served named and ordered, `agreement`: `[{id, name, share}]`), all read off `GET /api/justices/{id}`.

The profile also lists the records the dimensions read:

| Score | Records shown |
|-------|---------------|
| Funding Independence | Top donors by amount with type and industry, PAC share, small-donor share, industry breakdown (`IndustryDonation`) |
| Constituent Alignment | Every counted break this Congress with the chamber's own per-party tally (served in the breakdown's `facts.breakVotes`), breaks from the party's flank listed apart (`facts.flankBreakVotes`), and the seat's expected break rate |
| Legislative Effectiveness | Sponsored bills and joint resolutions by the furthest stage each reached (simple and concurrent resolutions counted apart), with commemorative bills marked |

The layer surfaces the underlying `KeyVote`, `Donor`, `IndustryDonation` and `SponsoredBill` records rather than LLM-written explanations — the numbers are auditable against the source data.

---

## Sharing a section as an image

A section marked `data-share-section` with a `ShareSectionButton` in it can be shared as a PNG, drawn in the reader's browser from the live page (`frontend/src/lib/shareImage.ts`; nothing is requested from another host). A picture travels without its page, so the frame says what it is: a strip naming the page's subject (a member's badge reads "72/100"), and under the capture where the figures come from, the section's link, the date the figures are as of, the scale any score is on, and the capture date ("Source: FEC filings; roll calls as the House Clerk records them; … · civitas-research.org/politicians/…#funding-independence · Data as of Oct 14, 2026 · scores out of 100 · captured Oct 15, 2026"). `ShareSubject.source` and `asOf` are required, so a page can't offer a picture without them; the as-of date is the backend's (a member's latest scoring run, the chamber's for the leaderboard strip, a vote's own date, the latest roll call read for a compare pair, the pinned Cook revision for a new map, a count's read time), or the capture day where the page reads its figures live. A section with figures of its own names them with `shareMetaAttrs` (a profile's holdings: the annual disclosure and its date; votes with the president: OMB's statements and the run's time; a race's live count: the state's feed). Until 2026-10 the frame printed only the link and "Captured {date}". Shareable: each scorecard column and summary, holdings, votes with the president, an issue and its facts, a bill's overview, summary and votes, a roll call, a ballot contest and the research on it ("What shapes this race"), a race's live count, the leaderboard's field strip, a compare pair's voting agreement, and a new House map's delegation by lean.

An issue's link preview (`/api/og?issue=`, the card a social post or chat app shows for an `/issue/` link) carries the same things the page says beside the story: the day, whose words the summary is ("— PBS NewsHour"), the photo's credit, the outlets or the one official record it comes from ("Sources: Roll Call, PBS NewsHour", "Source: Federal Register"), DEVELOPING for a story no outlet has covered yet, and the issue's own link (`issueCardTexts` in `frontend/src/app/api/og/route.tsx`). Until 2026-10 it carried the headline and the summary alone, with the photo uncredited and every story, news ones included, labelled "PUBLIC FEDERAL DATA".

## Public API and MCP server

An open, read-only API at `/api/public/v1` — no key, no account, CORS open to
every origin — documented at [`/developers`](https://civitas-research.org/developers).
That page renders the OpenAPI spec at `/api/public/v1/openapi.json`, which
FastAPI generates from the routes and their response schemas
(`backend/app/api/public.py`, `app/schemas.py` "Public API v1"), so the
reference always describes the running code. `tests/test_public_api_contract.py`
validates every endpoint's real body against its documented schema (the
schemas forbid extra fields), so the two cannot drift apart.

```
GET /api/public/v1/senators                      Serving senators, ranked by score
GET /api/public/v1/senators/{id}                 One senator's full record
GET /api/public/v1/senators/{id}/history         A senator's scores over time
GET /api/public/v1/senators/{id}/breakdown       How a senator's score is computed
GET /api/public/v1/senators/{id}/votes           Every position a senator recorded this Congress,
                                                 each with its roll call
GET /api/public/v1/representatives               Serving representatives, ranked by score
GET /api/public/v1/representatives/{id}          One representative's full record
GET /api/public/v1/representatives/{id}/history  A representative's scores over time
GET /api/public/v1/representatives/{id}/breakdown How a representative's score is computed
GET /api/public/v1/representatives/{id}/votes    Every position a representative recorded this
                                                 Congress, each with its roll call
GET /api/public/v1/bills/{id}/votes              The roll calls on a bill, with each party's split
GET /api/public/v1/search                        Floor speeches, presidential actions,
                                                 Supreme Court opinions and federal rules
GET /api/public/v1/states                        States and their member counts
GET /api/public/v1/district-lean                 Every House district's computed lean, both maps,
                                                 with Cook's PVI, method, inputs and licenses
GET /api/public/v1/presidential-support/{chamber}  How often each member votes with the
                                                 president's stated position, and the
                                                 rate expected for their seat
GET /api/public/v1/roll-calls?chamber=           A chamber's roll calls in a Congress, newest first
GET /api/public/v1/roll-calls/{chamber}/{congress}/{session}/{number}
                                                 One roll call, with every member's position
GET /api/public/v1/presidents                    Every president, ranked by Presidential Score
GET /api/public/v1/presidents/{id}               One president's record
GET /api/public/v1/justices                      The sitting justices (not scored)
GET /api/public/v1/justices/{id}                 One justice's record
GET /api/public/v1/justices/{id}/cases           A justice's vote in each case
GET /api/public/v1/nominations                   Presidential nominations sent to the Senate
GET /api/public/v1/nominations/{congress}/{citation}  One nomination, its actions and votes
GET /api/public/v1/agencies                      Every federal agency, with its documents held
GET /api/public/v1/agencies/{slug}               One agency
GET /api/public/v1/comment-periods               Federal documents open for comment today,
                                                 soonest deadline first
GET /api/public/v1/senators.csv                  The Senate leaderboard as CSV, with each
                                                 member's scoring version and date
GET /api/public/v1/representatives.csv           The House leaderboard as CSV, likewise
GET /api/public/v1/presidents.csv                Every president's Presidential Score as CSV
GET /api/public/v1/senators/{id}/votes.csv       Every position a member recorded, as CSV
GET /api/public/v1/representatives/{id}/votes.csv  (the same for a representative)
GET /api/public/v1/roll-calls/{chamber}/{congress}/{session}/{number}.csv
                                                 Every member's position on one roll call, as CSV
GET /api/public/v1/                              Index: every endpoint, weights, links
```

**License.** Civitas's own data (scores, breakdowns, classifications, the
computed lean, the API's answers and the CSVs) is CC BY 4.0; the code is
AGPL-3.0. Outputs built on a source with terms of its own carry them: the
Supreme Court record (the Supreme Court Database, CC BY-NC 3.0 US, and Oyez,
CC BY-NC 4.0) is non-commercial only, and Wikipedia-derived figures (the
election map's current polls and ranges, the district Cook PVI read from
pinned revisions) are share-alike. The statement is one component
(`frontend/src/components/about/DataLicense.tsx`) shown at `/about/data#license`
and `/developers#license`; the API names that page in the index's `license`,
the spec's `info.license` and every CSV's `Link: <...>; rel="license"` header
(`public.LICENSE_URL`). Each CSV route's columns are one list
(`_score_columns`, `_VOTE_COLUMNS`, `_POSITION_COLUMNS`, `_PRESIDENT_COLUMNS`)
that writes the file and documents it (`_csv_doc`), and a CSV is not an MCP
tool (its JSON twin is).

Conventions: lists are pages (`entries`, `total`, `page`, `perPage`,
`totalPages`); a member's `rank` is their place in the whole chamber whatever
the filters; an unknown id is a 404 (history included); every record has a
`siteUrl`; filter values (party, chamber, document type) are enumerated in
the spec and a value outside them is a 422. A score can be cited to its method: a
profile carries `algorithmVersion` and `scoredOn` (the newest history
snapshot's, the same two fields as `/breakdown`), and each `/history`
snapshot its own `algorithmVersion`, so a jump between two snapshots can be
read as a method change rather than a change in the record.

A member's `/votes` is every position they recorded on the chamber's roll
calls this Congress (`services/member_positions.py`, from `roll_call_positions`),
not the profile's selection of votes (`key_votes`, about a fifth of the
record). Each says whether it sided with the party, read as the scoring run
reads it, and `countedAsBreak` marks the breaks Constituent Alignment
counted, so the party-line figure can be checked against the record;
`chamberRollCalls` is how many roll calls the chamber held. Every roll call the API gives carries `siteUrl`, its page, and
`/roll-calls/{chamber}/{congress}/{session}/{number}` lists every member's
position on it with the id the chamber's record gives them (bioguide in the
House, LIS in the Senate) and their Civitas id where there is a page. A roll call of a Congress Civitas
doesn't hold is a 404 that says "Not held" with the Congresses held (never
"no roll call recorded"), and `/nominations` carries `backfill.complete`, false
while the daily read hasn't reached the whole Congress. The
justices' case records carry the Supreme Court Database's and Oyez's
non-commercial terms in their documentation (CC BY-NC).

A score can be reproduced from the API alone. `/breakdown` serves, for each
dimension, the stored score, the components the run that stored it worked
out (`basis: "scored"`; a score stored before they were kept is recomputed,
`basis: "recomputed"`), each with its weight, score, how it combines
(`mean`: part of the weighted mean; `points`: adds weight × (score − 50),
as Constituent approval does, with the `points` it adds) and the scorer's
sentence on how it was measured, the components' `sum` before rounding and
the 0-100 clamp and whether the clamp applied (`clamped`), and
`sinceScored` when today's records would give a different score; beside them the three weights, the scoring version and date, and
the reference values the last run measured and compared against: the
chamber's funding, Constituent Alignment and Legislative Effectiveness
references (`/data/*_reference.json`, `population_reference`), the
ideal-point fit and this member's position, the seat's Cook PVI and
district lines, and where the LES score's bills came from (`legislativeBills`:
fetched, carried from an earlier read, or unavailable). `tests/test_public_score_breakdown.py` stores fixture
members' scores with the pipeline's `calculate_scores` and recomputes every
dimension and the overall from the response: the overall exactly, each
dimension to within the one-decimal rounding of its components (on the
2026-10-14 production copy, 533 of 533 overalls exactly). The same file stores each fixture's breakdown as a run does, then marks a
bill law after the "run" and checks the served components still give the
stored score while `sinceScored` reports the new one.

A member the latest run couldn't score keeps an earlier run's score, and
`representationScore.held` says which run and why (the profile, leaderboard,
compare and the public API all carry it).

A member's id is their `first-last` name (see Phase 2 — TRANSFORM), and an id a member had
before a rename still works: the record comes back under the current id
(`id`, `siteUrl`), so a client holding an old one is not broken.

The same API is an **MCP server** at `/api/public/v1/mcp` (streamable HTTP,
stateless, JSON responses — `backend/app/api/public_mcp.py`). Its tools are
generated from the same spec, one per endpoint, named by operation id
(`list_senators`, `get_senator`, `search_documents`…), and a tool call runs
the real route in-process from the caller's IP, so answers, errors and the
rate limit are the API's own.

Rate limit: 60 requests/minute per IP (`rate_limit.PUBLIC_READ_LIMIT`), shared
by the API, MCP tool calls and the site's own Explore search. Headers:
`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (a Unix time).

CORS is open on every answer under `/api/public/v1`, errors included, and
exposes the rate-limit headers and `Retry-After` to a page's script
(`public.PublicApiPreflight`, which also answers the preflights). An MCP tool
argument sent as `null` counts as not given, as its input schema allows. An argument the
tool's input schema doesn't name is refused as a tool error naming it and the
accepted ones (`additionalProperties: false`): the route alone would drop it,
and a misnamed filter (`politician` for `politician_id`) came back unfiltered.

Use is counted per day, endpoint, channel (HTTP or MCP) and status, with
nothing about the caller (`ApiRequestCount`); a request refused as invalid
(422) also counts which parameter broke which rule (`ApiRejectionCount`),
never the value sent. The admin dashboard's API tab (`GET
/api/admin/api-usage`) shows rejected requests (the caller's mistake: an
invalid parameter, an unknown id), server errors (5xx) and rate-limit
refusals separately, and why requests were rejected — apart from the
visitor figures, since a program calling the API is not a visitor.

The Traffic tab leads with the two figures that measure people. **Confirmed
visitors** are `SiteVisit` rows from 2026-10-11 (`CONFIRMED_VISITS_SINCE`),
the first whole day a visit counted only once the browser that opened the page
had run it; earlier days show no figure, since the stored count was then mostly
automation. **First-paint loads** are cold page loads whose browser painted the
page while it was in view, from the load-time histogram; a browser under
automation (`navigator.webdriver`) reports none. That histogram is also split by
a device class the page decides for itself (desktop, tablet or mobile, from its
pointer and screen size) and sends as one of those three words, so the endpoint
still reads no header about its caller (`PageLoadTiming`).

A parameter an endpoint doesn't take is refused (422) rather than ignored,
since an ignored misspelt filter returned unfiltered results that looked
like an answer; a choice is read in any case and with hyphens or underscores
for its spaces (`Senate`, `senate-floor-speech`), a party may be named
(`Republican`) and a state written out (`Georgia`). Search's
`politician_id` takes a Civitas id or a bioguide id, and one that is neither
is a 404 rather than an empty result.

Score weights, industry codes and policy areas are also at `GET /api/config`,
the lighter endpoint the frontend itself uses.

---

## Academic Grounding

Key algorithmic decisions and their academic backing:

| Decision | Rationale | Reference |
|----------|-----------|-----------|
| Embeddings over keywords for classification | Semantic similarity generalizes to unseen text; keywords are brittle | Reimers & Gurevych 2019 (Sentence-BERT) |
| kNN over LLM for donor classification | 5s vs 40min, no hallucinated categories, deterministic | Cover & Hart 1967; Snell et al. 2017 |
| Party alignment from the roll call's actual split only | "Broke with party" is defined by how the parties voted; content stands in only where no roll call recorded each party's vote | Poole & Rosenthal 1985; Laver et al. 2003 |
| Learning store as experience replay | Past classifications bootstrap future accuracy | Lin 1992; Yarowsky 1995 |
| Inverse HHI for funding diversity | Standard concentration metric from IO economics | Rhoades 1993 |
| Linear shrinkage toward 50 on thin evidence (fixed rate `min(n/k, 1)`, not empirical Bayes; Constituent Alignment's position part instead uses a measured reliability weight relative to a full record, v6.27) | Keeps a few votes or cases from producing an extreme score | Stein-type shrinkage idea: Efron & Morris 1975 |
| Votes with the president's stated position, beside the rate expected for the seat (informational) | A raw support rate mostly measures party and seat | Papke & Wooldridge 1996; Wilson 1927; Agresti & Coull 1998 |
| Seat-relative break rate for Constituent Alignment (expected rate by party and Cook PVI, fitted from the chamber each run) | Raw party-break rates mislead without constituency context | Carson et al. 2010; Papke & Wooldridge 1996 |
| Donation-vote correlation ≠ causation | Methodological caution in interpreting funding influence | Ansolabehere et al. 2003 |
| Fuzzy name matching for self-funded detection | SequenceMatcher handles spelling variations, middle names | Ratcliff & Obershelp 1988 |
| PageRank for legislative leadership | Cosponsorship network centrality measures peer influence | Brin & Page 1998; Tauberer 2012 |
| SVD ideology as a prior for partisan depth (linear blend) | Behavioral ideological signal regularizes sparse vote data | Poole & Rosenthal 1985; Efron & Morris 1975 |

See the [Methodology page](/about) for full details and inline citations.

---

## Prerequisites

- **Docker** and **Docker Compose** (v2)
- A free **api.data.gov** API key — sign up at https://api.data.gov/signup/
- ~16 GB RAM recommended
- ~10 GB disk for Docker images and model weights
- A `.gguf` model file for llama-server (see LLM Backend Options below)

## Quick Start

```bash
# 1. Clone the repo
git clone git@github.com:kamoras/civitas.git
cd civitas

# 2. Create your env file from the template
cp .env.example .env

# 3. Edit .env — at minimum set your API key, admin token and contact address
#    DATA_GOV_API_KEY=your-key-from-api-data-gov
#    ADMIN_TOKEN=your-secret-admin-token
#    CONTACT_EMAIL=an-address-you-read@example.org   (named in every request's User-Agent)
nano .env

# 4. Start all services
docker compose up -d

# 5. Verify everything is running
docker compose ps

# 6. Open the app
#    Frontend: http://localhost:3000
#    API docs: http://localhost:8000/docs
```

### LLM Backend Options

The pipeline supports two LLM backends, configured via `LLM_BACKEND` in `.env`:

**Option A: llama.cpp via Docker (default, what production runs)**

`docker-compose.yml` already includes a `llama-server` service running the
official [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) server
image — nothing to build. Download a `.gguf` model (e.g. from Hugging Face)
into a local directory and point `.env` at it:

```bash
mkdir -p llama-models
# download your .gguf model into llama-models/, matching LLAMA_MODEL_FILE in .env
# LLAMA_MODELS_DIR=./llama-models
# LLAMA_MODEL_FILE=lfm2.5-1.2b-instruct-q4_k_m.gguf
# LLM_BACKEND=llama-server (default)
```

Dependabot tracks this image the same way it tracks every other
dependency in this repo (`.github/dependabot.yml`'s `docker` ecosystem) —
version bumps arrive as ordinary PRs, no manual "check for a new
llama.cpp release" step. A from-source ARM-optimized build
(`-mcpu=cortex-a76+dotprod+fp16`) was live-benchmarked against this image
on the production Pi 5 (2026-08-29): prompt-processing was ~12% faster,
but token-generation speed — what actually dominates wall-clock for every
real call here — was statistically identical, so the custom build wasn't
worth losing automatic updates over. If your workload is more
concurrency- or prefill-heavy than this project's once-daily batch
pipeline, building from source may still be worth it for you. On a Pi 5,
skip the compile: grab the precompiled binary from
[Releases](https://github.com/kamoras/civitas/releases/tag/llama-server-pi5-v1)
(checksum included; Cortex-A76 only, won't run on a Pi 4 or other boards).

```bash
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp && mkdir build && cd build
cmake .. -DCMAKE_C_FLAGS="-mcpu=cortex-a76+dotprod+fp16" \
         -DCMAKE_CXX_FLAGS="-mcpu=cortex-a76+dotprod+fp16"
cmake --build . --config Release -j4
# Point LLAMA_SERVER_URL at wherever you run it instead of using the
# bundled Docker service.
```

**Option B: Ollama (simpler setup)**
```bash
# Ollama is not bundled in docker-compose.yml — run your own instance
# (host install or a compose service you add yourself), then:
# Set LLM_BACKEND=ollama and OLLAMA_BASE_URL in .env
ollama pull LiquidAI/lfm2.5-1.2b-instruct
```

The data pipeline runs automatically on the cron schedule in `.env`
(default: 3 AM daily). To trigger it manually:

```bash
curl -X POST http://localhost:8000/api/admin/pipeline/trigger \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

## Deployment

### Operator alerts

Pipeline problems (a failed step in a run that carries on, a skipped phase of
the nightly chain, an overrun, stale data) go through
`app/ops_alerts.py:send_ops_alert`. Each alert is logged at ERROR, listed in the
admin Overview's **Ops alerts** panel (the ten newest), and pushed to
[ntfy](https://ntfy.sh) when `ALERT_NTFY_URL` is set, which is the only channel
that reaches a phone. Container logs rotate at 30 MB, about a day, so the panel
or ntfy is where an overnight alert is still found the next afternoon. Each
alert carries a dedupe key (per day, per run or per cycle), so a recurring
problem notifies once for that span.

An alert about an ongoing condition names it and stays **active** until the
code that detects the condition sees it gone and calls `resolve_ops_alert`:
the overrun and staleness watchdogs on their next clean tick, a failed step
when that step next completes, a skipped nightly phase when it next runs, the
justice alert when loyalty is next measured, and so on for every alert. The
panel lists active alerts first, then resolved ones (with when) and one-off
events. A newer alert for the same condition replaces the older (shown as
replaced, not resolved) and leaves its dedupe key held, so details that swing
back and forth (failing states A, then B, then A) don't notify on every swing.
Resolving frees the keys of every alert for the condition, so a recurrence
alerts again. Open alerts are never pushed off the panel by newer history, and
never pruned from storage. Resolved, replaced and one-off alerts stay listed
for seven days (the slowest regular jobs run weekly, so every job's latest
outcome stays in view), at most ten.

### Docker Swarm Architecture

The project runs on a single-node Docker Swarm (`docker swarm init` is a
one-time host setup step, not part of any deploy script). Zero-downtime
deploys come from Swarm's own rolling-update mechanism, not a hand-rolled
blue/green script:

```
                    ┌──── nginx (in-stack, port 8081) ────┐
                    │  upstream backend { }               │
                    │  upstream frontend { }              │
                    └─────────────────┬───────────────────┘
                                      │ proxy_pass (overlay network DNS)
                 ┌────────────────────┴────────────────────┐
                 ▼                                         ▼
   backend (task: read-only API)   pipeline (task)      frontend (task)
    start-first rolling update    stop-first update    start-first update
                                         │
                                         ▼
                                 llama-server (task)
                              stop-first rolling update
```

`backend` and `pipeline` run the same image in two roles
(`PROCESS_ROLE=api` / `worker`): nginx sends page reads to `backend` and
`/api/admin/` plus every pipeline-trigger endpoint to `pipeline`, which also
runs the nightly and hourly schedule. Pipelines therefore never share a
Python process, or a memory limit, with the requests visitors make.

`docker stack deploy -c docker-compose.yml -c docker-compose.swarm.yml
civitas` follows this sequence, all built into Swarm rather than scripted:
1. Build the new Docker image locally, tagged with the deploying commit's
   short SHA (GHCR pull is disabled — see `AGENTS.md` "CI/CD" for why)
2. Swarm starts a new task on the same overlay network (`update_config.order:
   start-first`)
3. Swarm waits for the new task's `HEALTHCHECK` to pass
4. Traffic shifts to the new task via the overlay network's own service DNS
   — nginx's config never changes, `backend`/`frontend` always resolve to
   whichever task is currently healthy
5. The old task is stopped and removed
6. If the new task never becomes healthy, `failure_action: rollback`
   reverts to the previous image automatically — no manual intervention

Data is persisted in the `civitas_app_data` Docker named volume, mounted at
`/data` inside the backend and pipeline containers. The SQLite databases (`civitas.db`,
`vectors.db`) and model-version marker
live here and survive image rebuilds and stack redeploys — the volume is
`external: true` in `docker-compose.swarm.yml`, so `docker stack deploy`
never recreates or touches it directly.

### Service Layout

```
Host ports    Swarm service   Purpose
───────────────────────────────────────────────────────────────────────────────
8081          nginx           Reverse proxy + caching (the only service
                              published to the host — port-forwarded
                              externally, don't change without updating
                              that forwarding rule)
—             backend         FastAPI read-only API, 2 worker processes
                              (overlay-network only)
—             pipeline        Same image: scheduler, pipelines, admin API
                              (overlay-network only)
—             frontend        Next.js frontend (overlay-network only)
—             llama-server    llama.cpp inference (overlay-network only)
```

Backend, pipeline, frontend, and llama-server publish **no host port** — Swarm's
host-mode port publishing can't restrict to `127.0.0.1` the way plain
`docker run -p 127.0.0.1:PORT:PORT` can (confirmed live: it always binds
`0.0.0.0`), so rather than accept LAN-wide exposure of ports that were
deliberately loopback-only before, none of them are published to the host
at all — nginx, inside the same overlay network, is the only path to any
of them.

llama-server is its own Swarm service (not baked into the backend image)
with its own rolling-update lifecycle, so its model weights don't reload
every time backend redeploys — only when llama-server's own image or
config changes. It updates `stop-first` rather than `start-first` like
backend/frontend (see `docker-compose.swarm.yml`): a brief restart gap is
an acceptable trade for not running two model copies in memory at once on
the Pi, since it isn't on any live user-facing request path. The backend
connects to it via `http://llama-server:8070` (overlay-network service
DNS). If llama-server is unavailable, LLM calls fail with a timeout and each
caller degrades on its own: the member pipelines never call it, the
Action Center publishes no issue from a cluster it can't read (never an
unverified one).

### Health Check

`GET /api/health` returns:
```json
{
  "status": "ok" | "degraded",
  "database": "ok" | "unavailable",
  "ollama": "ok" | "unavailable",
  "lastPipelineRun": "2026-07-12T03:00:00"
}
```

Swarm considers a task healthy purely by its Docker `HEALTHCHECK` exit code
(`curl -sf http://localhost:8000/api/live` for the backend) — the same
"HTTP 200, don't parse the body" criterion the old deploy script used.
`/api/live` answers from the event loop alone, with no database read and no
llama-server call: `/api/health` waits up to 5s on llama-server and the check
times out at 5s, so a slow llama-server could get a healthy backend replaced,
killing any pipeline run in it (suspected 2026-09-29: a House run stopped
7 minutes in, its task ending "Complete" on the same image). A loop frozen by
blocking work still fails `/api/live`.
`database`/`ollama` in the response body are informational for the admin
dashboard and don't gate the rolling update. The `ollama` key name is
historical (kept for API/dashboard compatibility even though Ollama isn't
bundled anymore): it reports whichever backend `LLM_BACKEND` selects, so
under the default it is the llama.cpp server's health, not Ollama's.

## Development Setup

```bash
# Start deps, then run backend and frontend with live reloading
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

### Running Backend Tests

```bash
docker compose run --rm --no-deps backend python -m pytest tests/ -v

# Fast tests only (skips the tests that load the sentence-transformer model),
# on 4 pytest-xdist workers
docker compose run --rm --no-deps backend python -m pytest tests/ -v -m "not slow" -n 4

# Only the embedding-model tests
docker compose run --rm --no-deps backend python -m pytest tests/ -v -m slow
```

CI runs the fast suite behind a 46% total-coverage floor plus a 60%
changed-lines gate (`diff-cover`), and runs the `slow` suite on pushes to
`main`. See `.github/workflows/ci.yml`.

### Running Frontend Tests

```bash
cd frontend
npm run lint     # eslint, including jsx-a11y
npm test         # vitest
npm run build    # type errors block CI
```

The build makes no network requests for fonts. The site's three typefaces (Archivo, Press Start 2P, Share Tech Mono; SIL OFL) are committed under `frontend/src/app/fonts/`, one file per script subset, and loaded with `next/font/local`. `next/font/google` fetched them during every build, and about one build in five failed when Google answered with a URL Turbopack rejects. `backend/scripts/fetch_site_fonts.py` refetches them the way `next/font/google` did and rewrites `manifest.json` and `fallback.css`; `fonts.test.ts` fails if `fonts.ts` drifts from the manifest.

### Project Structure

```
civitas/
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI route handlers
│   │   │   ├── senators.py, representatives.py, presidents.py, justices.py
│   │   │   ├── action.py     # Action center issues, monitors, timeline
│   │   │   ├── explore.py    # Hybrid (semantic + keyword) document search
│   │   │   ├── public.py     # Open read-only API (rate-limited, no auth)
│   │   │   └── admin.py      # Pipeline control panel
│   │   ├── services/         # Business logic (senator_service, representative_service)
│   │   ├── pipeline/
│   │   │   ├── fetch/        # API clients (Congress.gov, FEC, GovInfo, Senate.gov,
│   │   │   │                 #   Oyez, BLS, Federal Register, news RSS)
│   │   │   ├── transform/    # Data normalization + embedding-based industry classifier
│   │   │   ├── analyze/      # Scoring, classification, action center, Bluesky
│   │   │   │   ├── bill_analyzer.py          # Embedding-based bill classification + stance
│   │   │   │   ├── bill_learning.py          # Adaptive kNN reference corpus
│   │   │   │   ├── party_platform.py         # Party alignment (roll-call split, content fallback) + partisan depth
│   │   │   │   ├── nn_classifier.py          # kNN donor classifier + category normalization
│   │   │   │   ├── donor_classifier_ai.py    # Tiered donor classification + batch skip
│   │   │   │   ├── sponsorship_analysis.py   # PageRank leadership + SVD ideology
│   │   │   │   ├── policy_alignment.py       # Industry↔policy area mapping
│   │   │   │   ├── cross_reference.py        # Per-senator lobbying/key-vote analysis
│   │   │   │   ├── action_center.py          # News clustering, verbatim claims,
│   │   │   │   │                             #   national monitors, timeline
│   │   │   │   ├── score_calculator.py       # Deterministic scoring formulas
│   │   │   │   ├── ollama_client.py          # LLM backend abstraction
│   │   │   │   ├── bluesky_poster.py         # Post new/updated action issues
│   │   │   │   ├── bluesky_spotlight.py      # Daily member spotlight (templated)
│   │   │   │   ├── bluesky_engagement.py     # Repost/like matching outlet posts
│   │   │   │   ├── trend_poster.py           # "On the record" trend posts (templated)
│   │   │   │   ├── trend_evidence.py         # Pinned trend-to-record matching
│   │   │   │   ├── trend_relevance.py        # Trend gates: relevance, tragedy, loaded kNN
│   │   │   │   └── bluesky_utils.py          # Shared link-card builder
│   │   │   ├── assemble/     # Senator scorecard builder + validator
│   │   │   ├── vector_store.py  # sqlite-vec + both embedding models
│   │   │   ├── senate_pipeline.py, house_pipeline.py  # FETCH -> TRANSFORM ->
│   │   │   │                 #   ANALYZE -> ASSEMBLE+SAVE orchestration per chamber
│   │   │   ├── stock_pipeline.py  # STOCK Act trade-disclosure ingestion (sibling
│   │   │   │                 #   phase, runs after the member pipelines)
│   │   │   ├── supplementary_pipeline.py  # Explore docs, justices, PVI, presidents
│   │   │   ├── election_pipeline.py  # Candidates, FEC financials, ballots, coverage
│   │   │   └── president_pipeline.py, justice_pipeline.py, explore_pipeline.py
│   │   ├── scheduler.py      # Nightly cron entrypoint — calls the pipelines above
│   │   ├── models.py         # SQLAlchemy ORM (Senator, Representative, KeyVote,
│   │   │                     #   Justice, ActionIssue, NationalMonitor,
│   │   │                     #   TimelineEntry, ScoreSnapshot, etc.)
│   │   ├── schemas.py        # Pydantic response schemas
│   │   ├── database.py       # DB engine, session management, init_db (runs Alembic)
│   │   ├── config.py         # Pydantic settings from .env
│   │   └── config_definitions.py  # Score weights, industry codes, policy areas
│   ├── migrations/           # Alembic revisions (see its README)
│   ├── scripts/              # Calibration, research and audit scripts
│   ├── tests/                # pytest test suite
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/app/              # Next.js app router pages
│   │   ├── about/            # Methodology page with full citations
│   │   ├── action/           # Action Center (issues, monitors, timeline)
│   │   ├── issue/[id]/       # Permanent issue permalinks (Bluesky link target)
│   │   ├── politicians/      # Unified directory + per-member profile
│   │   │   └── [id]/         #   (senator, representative, president, and justice
│   │   │                     #    scorecards all render into this one page)
│   │   ├── congress/         # Daily/weekly/monthly Congress reports; bills/ (in-motion list, any bill's page)
│   │   ├── compare/          # Side-by-side senator/representative comparison
│   │   ├── explore/          # Hybrid search over government documents
│   │   ├── elections/        # State index, per-state ballot pages (federal contests
│   │   │                     #   + statewide measures), race/candidate detail
│   │   ├── leaderboard/      # Rankings across all branches (House paginated)
│   │   ├── environmental/    # Environmental policy tracking
│   │   ├── accessibility/    # Accessibility statement
│   │   ├── feedback/         # On-site feedback form -> files a GitHub issue
│   │   ├── changelog/        # Scoring-algorithm version history
│   │   └── admin/            # Pipeline control panel with granular progress
│   ├── src/components/       # React components
│   └── Dockerfile
├── check-and-deploy.sh       # Cron poller: builds images + docker stack deploy
├── docker-compose.yml
├── docker-compose.swarm.yml  # Swarm-only overlay (production stack deploy)
├── nginx/                    # Dockerfile + static config (in-stack reverse proxy)
├── .env.example
├── AGENTS.md                 # Project design principles and developer guide
└── README.md
```

## Environment Variables

See `.env.example` for all options. Key variables:

| Variable | Required | Description |
|---|---|---|
| `DATA_GOV_API_KEY` | Yes | API key from api.data.gov (covers Congress.gov, FEC, GovInfo) |
| `BLS_API_KEY` | No | Free BLS v2 registration key (data.bls.gov/registrationEngine): 500 jobs-API requests a day instead of 25 |
| `ADMIN_TOKEN` | Yes | Bearer token for admin panel and pipeline triggers |
| `CONTACT_EMAIL` | Yes | Your instance's contact address, named in the User-Agent of every request the pipeline and scripts send (`backend/app/contact.py`). Unset or malformed, requests name Civitas with no address, the SEC (whose fair-access policy requires a contact) is not fetched and raises an ops alert, and the pipeline logs a warning at start |
| `LLM_BACKEND` | No | `llama-server` (default) or `ollama` |
| `LLAMA_SERVER_URL` | No | llama-server URL (default: `http://llama-server:8070`, the in-stack service) |
| `LLAMA_MODELS_DIR` | No | Host directory bind-mounted into llama-server at `/models` (default: `./llama-models`) |
| `LLAMA_MODEL_FILE` | No | `.gguf` filename inside `LLAMA_MODELS_DIR` (default: `lfm2.5-1.2b-instruct-q4_k_m.gguf`) |
| `OLLAMA_MODEL` | No | Model name for cache keys and Ollama (default: `LiquidAI/lfm2.5-1.2b-instruct`) |
| `DATABASE_URL` | No | SQLite path. `docker-compose.yml` sets `sqlite:////data/civitas.db` (the `/data` volume); the code default `sqlite:///data/civitas.db` is relative to the working directory, for running outside Docker |
| `PIPELINE_CRON_SCHEDULE` | No | Cron schedule for nightly pipeline (default: `0 3 * * *`) |
| `PIPELINE_CACHE_TTL_HOURS` | No | API response cache TTL (default: `72`) |
| `GOOGLE_CIVIC_API_KEY` | No | Google Civic Information API key — enables the optional town-level local-races selector on the state ballot pages (a small hand-curated pilot list, not all US towns). Unset means the selector doesn't appear |
| `BSKY_HANDLE` | No | Bluesky handle (e.g. `civitas-research.org`) |
| `BSKY_APP_PASSWORD` | No | Bluesky app password (from Settings → App Passwords) |
| `FEEDBACK_TOKEN` | No | Fine-grained GitHub PAT (Issues: write only) for the on-site feedback form; leave unset to disable it (returns 503, never silently drops) |
| `GITHUB_FEEDBACK_REPO` | No | Repo the feedback form files issues against (default: `kamoras/civitas`) |

## Contributing

Contributions that improve data accuracy, scoring methodology, or public
usability are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the
CI gates, and what a scoring-methodology change needs to argue.

- [Code of Conduct](CODE_OF_CONDUCT.md) — Contributor Covenant 2.1, plus a
  section on political subject matter specific to a project that scores named
  politicians
- [Security policy](SECURITY.md) — please report vulnerabilities privately
  rather than in a public issue
- [Architecture diagrams](docs/diagrams/) — rendered Mermaid views of the
  pipeline, scoring, schema, and deployment

## References

Full methodology with inline citations is available on the [About page](/about).
Each chapter opens with "The short version" (on `/about/scores`, what each score
measures in one or two plain sentences, each reading at grade 7 or below), and a
chapter with four or more sections lists them under "On this page", built from
the sections themselves (`components/about/AboutPage.tsx`).
Key references:

- Bonica, A. (2014). Mapping the Ideological Marketplace. *AJPS*, 58(2), 367-386.
- Budge, I. et al. (2001). *Mapping Policy Preferences*. Oxford UP.
- Brin, S. & Page, L. (1998). The Anatomy of a Large-Scale Hypertextual Web Search Engine. *Proc. WWW 1998*.
- Canes-Wrone, B., Brady, D. & Cogan, J. (2002). Out of Step, Out of Office. *APSR*, 96(1), 127-140.
- Carson, J. et al. (2010). The Electoral Costs of Party Loyalty. *AJPS*, 54(3), 598-616.
- Cormack, G., Clarke, C. & Büttcher, S. (2009). Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods. *SIGIR 2009*.
- Clinton, J., Jackman, S. & Rivers, D. (2004). The Statistical Analysis of Roll Call Data. *APSR*, 98(2), 355-370.
- Cover, T. & Hart, P. (1967). Nearest Neighbor Pattern Classification. *IEEE Trans. Info Theory*, 13(1), 21-27.
- Efron, B. & Morris, C. (1975). Data Analysis Using Stein's Estimator. *JASA*, 70(350), 311-319.
- Grimmer, J. & Stewart, B. (2013). Text as Data. *Political Analysis*, 21(3), 267-297.
- Harbridge-Yong, L., Volden, C. & Wiseman, A. (2023). The Bipartisan Path to Effective Lawmaking. *Journal of Politics*, 85(3).
- Laver, M., Benoit, K. & Garry, J. (2003). Extracting Policy Positions from Political Texts. *APSR*, 97(2).
- Lewis, P. et al. (2020). Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks. *NeurIPS 2020*.
- Miller, W. & Stokes, D. (1963). Constituency Influence in Congress. *APSR*, 57(1), 45-56.
- Nokken, T. & Poole, K. (2004). Congressional Party Defection in American History. *Legislative Studies Quarterly*, 29(4), 545-568.
- Agresti, A. & Coull, B. (1998). Approximate Is Better than "Exact" for Interval Estimation of Binomial Proportions. *The American Statistician*, 52(2), 119-126.
- Papke, L. & Wooldridge, J. (1996). Econometric Methods for Fractional Response Variables. *Journal of Applied Econometrics*, 11(6), 619-632.
- Poole, K. & Rosenthal, H. (1985). A Spatial Model for Legislative Roll Call Analysis. *AJPS*, 29(2), 357-384.
- Reimers, N. & Gurevych, I. (2019). Sentence-BERT. *EMNLP 2019*, 3982-3992.
- Snell, J. et al. (2017). Prototypical Networks for Few-Shot Learning. *NeurIPS 2017*, 4077-4087.
- Stratmann, T. (2005). Some Talk: Money in Politics. *Public Choice*, 124(1-2), 135-156.
- Tauberer, J. (2012). *Open Government Data*. GovTrack.us ideology/leadership methodology.
- Volden, C. & Wiseman, A. (2014). *Legislative Effectiveness in the United States Congress: The Lawmakers*. Cambridge UP.
- Wilson, E. B. (1927). Probable Inference, the Law of Succession, and Statistical Inference. *Journal of the American Statistical Association*, 22(158), 209-212.

## License

Licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).
Unlike a permissive license (MIT, Apache), AGPL requires that anyone who runs
a modified version of Civitas as a network service — not just anyone who
redistributes it — must also publish their modifications. That closes the
loophole a permissive license leaves open: someone could otherwise fork the
scoring algorithm, quietly bias it, and host it without ever disclosing what
changed. If you can't see the source, you can't trust the score — AGPL keeps
that true for every fork, not just this repository.
