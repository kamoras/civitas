# Civitas — Political Representation Tracker

[![CI](https://github.com/kamoras/civitas/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kamoras/civitas/actions/workflows/ci.yml)
[![Lighthouse — Accessibility & SEO](https://github.com/kamoras/civitas/actions/workflows/lighthouse.yml/badge.svg?branch=main)](https://github.com/kamoras/civitas/actions/workflows/lighthouse.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

Civitas is an open-data AI/ML platform that aggregates data from official
U.S. government sources into unified transparency scorecards for senators,
House representatives, presidents, and Supreme Court justices. It also
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
│  Congress.gov   FEC API    GovInfo API  Senate.gov  Oyez    BLS / BEA        │
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
│  │ (sqlite- │   │ (Oyez)   │   │(BLS/BEA/   │◄─┘   │ (persist scores,     │  │
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

**Server rendering:** `/action`, `/leaderboard`, `/congress`, `/explore` and
`/compare` keep their view state in the address bar, and `useSearchParams()` has
no value during SSR — so React suspends and the server renders the nearest
`<Suspense>` fallback. Each of those pages supplies a real one
(`components/layout/PageFallback.tsx`: navbar, masthead, content skeleton), so
the frame arrives in the first paint. They previously wrapped the *entire* page
in `<Suspense fallback={null}>`, which made the server send a ~12KB body with no
chrome and nothing visible until ~653KB of JavaScript had parsed and hydrated.
The data itself still arrives after hydration: these routes are prerendered and
the content is per-request.

**Hardware:** Raspberry Pi 5 (16 GB RAM), NVMe SSD. All models, databases, and services run on-device. No cloud GPU, no third-party AI APIs, no data leaves the device.

---

## Nightly Pipeline: Phase by Phase

The nightly pipeline processes every senator and House representative through FETCH → TRANSFORM → ANALYZE → FINALIZE before persisting scores and snapshots; the Supplementary pipeline carries the other three phases below (EXPLORE, JUSTICES, PRESIDENTS). They are links in a five-pipeline **chain**, run in this order by `scheduler.py`:

```
Senate ──▶ Supplementary ──▶ House ──▶ Stock trades ──▶ Election
           (explore docs,              (stock_pipeline)  (election_pipeline)
            SCOTUS, PVI,
            presidents)
```

**The links run one at a time, but none waits on the one before it succeeding** (`app/pipeline_chain.py`). Each reads whatever the database holds, so a link that is skipped, fails or crashes gets its own alert and the next link runs anyway. Until September 2026 any of those ended the chain: it once stopped inside Supplementary, and House, Stock trades and Election did not execute for 19 nights, because a pipeline that never starts leaves no row behind to look wrong. One chain runs at a time: a trigger of any of its pipelines is refused while one is going, and a nightly run that finds a triggered chain going leaves the night to it. A link skipped because another run holds the machine (another run of that same pipeline, or a member pipeline holding off Stock trades) is not moved past: the chain waits until no pipeline is running, so it does not start the next heavy pipeline beside that run, then tries the link once more and reports what that attempt did. The other run may have covered one senator, or failed, so it never counts as the link's run. A run past the pipelines' staleness limit stops counting as running, so the wait always ends. The one skip that ends a chain is a data reset holding the database. Both member pipelines check the analysis-code hash at start, since House can now run after a failed Senate. A full manual trigger runs the same five links the same way, so it can recover any of them. A killed process still ends its chain. `ops_alerts.check_pipeline_staleness` still catches a pipeline with no successful completion in `PIPELINE_STALE_ALERT_DAYS`, and `check_pipeline_overrun` the opposite case of a run that started and is taking too long.

### Phase 1 — FETCH

Pulls raw data from each government API and stores the complete response verbatim in `ApiCache` before any transformation:

| Source | What is fetched | Rate limit |
|--------|-----------------|------------|
| Congress.gov | Members, bills sponsored/cosponsored and their actions (current congress), bill summaries and titles | 1.2 RPS |
| Senate.gov / House Clerk | Roll-call vote XML (every member's position on each vote) | 1.2 RPS (shared with Congress.gov) |
| FEC API + bulk committee master (`cm{yy}.zip`) | Campaign finance transactions, committee receipts; committee type, designation and connected organization | 0.25 RPS (bulk files: one download per cycle, weekly) |
| GovInfo API | Bill text; Congressional Record floor remarks (Explore) | 1.0 RPS |
| Lobbying Disclosure Act registry (lda.gov) | Registered lobbying spend by client, and the bills named in filings, for organizations in donor–vote matches | 0.2 RPS anonymous / 1.0 with `LDA_API_KEY` |
| Oyez / supremecourt.gov | Justice voting records, case metadata, docket pages | ~2 RPS (fixed pauses) |
| Supreme Court Database / FJC / Martin-Quinn | Justice votes in cases the federal government argued (newest release), each justice's nomination dates, ideal points per term | 1.0 RPS, cached |
| BLS | Unemployment, inflation, job growth by administration | batch |
| Cooperative Election Study (Harvard Dataverse, CC0) | Approval of each member among their own constituents, by the respondent's party (informational; `scripts/fetch_ces_approval.py`, per survey release) | one-off download |
| BEA | GDP growth by quarter | batch |
| Federal Register | Executive orders signed per administration | 1.0 RPS |
| House Clerk / Senate eFD | STOCK Act periodic transaction reports, and annual and new-filer financial disclosures (PDF/HTML, parsed) | 1.0 / 0.5 RPS |
| OGE | Sitting president's disclosures: the annual report (OGE Form 278e), whose Part 7 lists every transaction of its year as text, and the OGE Form 278-T periodic transaction reports since, which are scans read by OCR (PDF, parsed) | 0.5 RPS |
| SEC | Ticker and company-name -> issuer, and the SIC industry code the SEC assigned it (`data.sec.gov` submissions), for a traded security's industry | 5 RPS, cached 90 days per issuer |
| Voteview | Congress-specific (Nokken-Poole) member ideal points (per-congress CSV exports), feeding Constituent Alignment's position-congruence component | batch |

**A trade's industry** is the SEC's own code for its issuer, never a guess from the asset's name (`sec_tickers.py`, `stock_pipeline._industries`). A ticker (the Senate's eFD table prints it in its own column; the House puts it in the asset text) or a company name that matches an SEC-registered title exactly, or writes one title in a brokerage statement's short form (each word the title's word, a prefix of it or its letters in order — "UNION PAC CORP", "HONEYWELL INTL INC" — followed only by words describing the security such as "CL A" or "COM", and fitting no second title; `sec_tickers.abbreviated_match`: on a president's annual report it labelled 683 names that matched nothing exactly, against 192 exact, and 120 sampled were all the right company), leads to the issuer's record on `data.sec.gov`, whose Standard Industrial Classification code is decoded by the SIC manual's hierarchy (`SIC_INDUSTRY`) — "3674 Semiconductors" is Technology, "6798 Real estate investment trusts" is Real Estate. A cryptocurrency is Crypto only when the filer declared it one (the House's `[CT]` code, the Senate's Asset Type column). Everything else — Treasury and municipal bonds, funds, private companies, OTC ADRs, and codes with no category of ours such as waste management — stays unclassified. A trade two of a filer's reports both list (same owner, asset, date, type and amount bracket — an amended or re-submitted report) counts once, at the earlier report's date (`stock_pipeline.collapse_refiled_trades`; 31 Senate and 2 House groups on 2026-10-08). A Senate amendment (eFD titles it "Periodic Transaction Report for <the report's date> (Amendment N)") is the whole report again: only its newest version is kept, and each trade takes the date it was first disclosed, the original report's for a trade the original listed and the amendment's for one it added (`stock_pipeline.settle_amended_reports`). One senator's nine amendments filed one day had read as up to 867 days late. An original older than the night's search is fetched once by a wider search. This replaced running names through the donor-industry embedding classifier, which put Broadcom and ConocoPhillips under Lobbyists and GitLab under Private Prisons: measured against the SEC's codes on the 1,299 production trade names that have one, the embedding agreed 30% of the time, and 65% at its most confident margins. Embedding the SIC descriptions themselves fared no better ("Semiconductors & Related Devices" read as Pharmaceuticals, "National Commercial Banks" as Real Estate). Every stored trade is relabelled each night, so a change here reaches trades already stored; with the SEC unreachable, labels are left as they are.

**Congress's filing dates.** A trade's disclosure date is the date its report was filed, which the STOCK Act's 45 days run to: the Senate search's filed date, and for the House the FilingDate of the Clerk's yearly index. Until parser version 6 a House trade was stored as disclosed the day it was made (all 4,681 of them), so none could show as late: the form's notification-date header wraps ("Notification / Date") and went unmatched, and even that date is when the filer learned of the trade, not when it was reported. It is now only the fallback when the filing date is unknown. The Senate's eFD table carries its ticker in a column of its own, now read (971 of 1,316 Senate trades had none). A scanned House or Senate filing's dates are read only up to the filing date (parser version 5): without that window one House scan stored a bond's maturity as a trade made in 2033. A scan the current reader finds no row in loses what an older reader stored from it on re-read (a fetch that fails keeps it); a date a text filing prints is kept as printed (one 2025 filing lists two trades dated 05/08/2015 beside a notification date of 05/15/2025).

**The president's trades.** The White House files its 278-T periodic reports as scans. tesseract's plain text reads their table's columns as separate blocks, so each scanned page is read by where its words sit (`ptr_common._ocr_table_page`): rows are found by their transaction-type word or row number, an amount is accepted only as one of the form's own ranges (`AMOUNT_BRACKETS`; one misread bound is recovered from the other), and a date only inside the filing's window (the term's start to the filing date). A row that fails either is counted, not guessed. The date cell starts at the type column's edge, since one filing right-aligns its dates in a wide column, and is read again enlarged when the first reading isn't a date. On the seven 2026 filings (to September 22) this reads 5,212 transactions, against 1,770 before; 1,714 of the rows left unread were in one filing (May 8, 2026), scanned at 150 dpi and printed at half size: its dates are about 6 pixels tall. No reading of them was reliable (tesseract, preprocessed or not, got a quarter right; matching each date against every candidate rendered in the form's font got 38% with no usable confidence), while each row's asset, type and amount read. The president's rows whose date alone is illegible are therefore kept undated (`ocr_extract_rows(keep_undated=True)`, `PresidentTrade.transaction_date` NULL, migration 0019) and listed as "date not legible in the scan" with the filing date, after the dated rows. An annual report supersedes an undated row whose filing was filed by the end of the year it covers. Members' scanned PTRs are read the same way (`PARSER_VERSION` 5, migration 0022): a House or Senate paper filing's row whose date alone is illegible is kept undated with the filing date, and a House scan's dates must fall on or before its filing date. The line parser it replaced took a line's first date, on a bond its maturity, and stored trades dated as late as 2078; it also read only a fraction of each filing's rows (39 of the 189 on one page-dense filing). At 150-200 dpi tesseract still misreads a digit of some dates, so a date read by OCR supports no timeliness figure (`StockTradeSchema`: `daysToDisclose` and `late` are null) and the row is labelled as read from a scan. For every year an annual report covers, its Part 7 replaces the year's 278-T rows (`president_fd`, `stock_pipeline._ingest_president_annual`): 21,285 transactions for 2025 as text, against 2,362 rows the scans had yielded. Part 7 states no date a transaction was first reported, so its rows carry no timeliness figure either.

**Members' holdings.** Each member's newest annual financial disclosure backs the holdings pie on their scorecard (`holdings_pipeline`; House Schedule A read from word positions in `house_fd`, Senate Part 3 from the eFD page in `senate_fd`). A category is only the asset type the filer declared. A member seated in a special election has filed only a new-filer report until their first annual one, so the House's new-filer reports are read too, labelled by filing date (they state no valuation date) and outranked by any annual report: on 2026-10-08 four sitting members had nothing else. A House filer may state an exact value instead of a bracket ("$1,251.00"; 15 holdings on two 2025 reports); it is charted and shown as that figure, not counted as "no value stated". Filers are matched to members by name (`filer_matching`): eFD writes a suffix after the surname ("Doe, Jr."; four senators had gone unmatched), and the House index keeps a member's district from before a redistricting (two members' annual reports, and one member's seven trade reports, had gone unmatched), so a filer listed under a district where no member has that surname is matched within the state when the first names agree. An audit of 14 representatives' and 12 senators' stored holdings against the filed reports (2026-10-08) found every sampled report read row for row; the defects above turned up in population-wide checks.

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
3. Party alignment per bill: how the parties actually split on its roll call; a vote with no roll call carries no party label for loyalty (content similarity to party platforms feeds only the per-area depth breakdown; see "Party Alignment" below)
4. Sponsorship network over the chamber: PageRank legislative leadership and SVD ideology from the cosponsorship matrix
5. Donor–vote connections: substantial industry funding matched against policy-anchored vote similarity, with registered lobbying spend (LDA) by client attached, and bills the member voted on that the organization's own filings name
6. Select key votes: against the party line, related to a top donor's industry, substantive
7. Compute the representation sub-scores against population references measured from the chamber this run, and persist the member's scorecard

### Phase 4 — EXPLORE

Builds the search indexes over government activity documents — floor
speeches (Senate and House), presidential actions (executive orders,
proclamations, memoranda), Supreme Court opinions, and Federal Register
rulemaking documents (including ones still open for public comment).
A Supreme Court case links to the Court's own slip opinion where one is
posted (`supreme_court.parse_slip_opinions`, the term's slip-opinion table),
leads with the holding the Court states there and names the author, decoded
from the table's code against the sitting justices; until then it links to
the docket page. A Federal Register correction or republication of another
document (its `correction_of` field) is skipped, since it repeats that
document's title:
- Floor speeches are read a day of the Congressional Record at a time and split into each member's
  speeches by the Record's own layout; floor business is not indexed (see "Hybrid Search" below)
- Embeds each document whole, in windows of the encoder's context length (`chunk_text`), title and summary leading each window
- Encodes with the **index** sentence-transformer (384-dim, all-MiniLM-L6-v2)
- Upserts into the `vec_explore` sqlite-vec table with metadata: doc type, source, date, politician name/ID, chamber
- Rebuilds the `explore_fts` FTS5 keyword index (BM25F over title/summary/body)
- Recomputes citation-graph PageRank into `explore_documents.authority`
- At query time: both channels run, filters pushed into each, results fused by weighted reciprocal rank fusion with recency and authority priors — see "Hybrid Search (Explore)" below

This is a separate index from the tier-3 bill-classification embeddings used
in Phase 3 — it's for browsing/searching primary-source government documents,
not for scoring.

### Phase 5 — JUSTICES

Fetches and scores Supreme Court justices, weekly on Sunday UTC (or whenever the table is empty — the uncached Oyez per-case crawl, now only of the terms the Supreme Court Database doesn't cover yet, is slow):
- Pulls each justice's votes in the Court's decided cases. Oyez sometimes lists one justice twice in a decision (one justice in two 2025-term cases, with two others missing): identical rows count once, conflicting ones leave that vote out, and a missing justice's vote is never filled in. The duplicate had broken the one-vote-per-case key, so every Sunday refresh rolled back and the scorecards went stale with no alert. Now any pipeline step that fails and is carried past (`ProgressTracker.fail`) sends an ops alert, deduplicated per pipeline, step and day.
- The voting record (`justice_analyzer.py`): majority, dissent and unanimous shares, opinions written, agreement with each sitting justice, over the last four terms. Shown, not scored. Read from the Supreme Court Database for every term its newest release covers (orally argued cases, `justice_records.scdb_case_votes`) and from Oyez only after that: Oyez enters votes case by case, and on 2026-10-08 had them for 13 of the 2024 term's 59 decided cases, so the record read a fraction of that term (117 cases where the Database has 176).
- The score is independence from the appointing president (`justice_loyalty.py`, Epstein & Posner 2016): whether a justice sides with the federal government more often while that president is in office than under others, fit per justice with the government's side of the case held fixed, shrunk across every justice since 1937 (DerSimonian-Laird). Votes through 2014 are Epstein & Posner's, bundled; later terms come from the newest Supreme Court Database release, with each appointing president taken from the Federal Judicial Center's nomination dates (`fetch/justice_records.py`). 100 is no favoritism either way, 0 is two between-justice sds; each estimate is stored with its standard error. A source that can't be read leaves the stored scores standing, and the run's justice step says "loyalty not measured" and which source couldn't be read, in its progress detail and in an ops alert, rather than only a count. The backend image is Debian trixie because the Database's host refuses httpx's TLS handshake under bookworm's OpenSSL 3.0 (a 403 that left every justice unscored on 2026-09-29; see `backend/Dockerfile`).
- Martin-Quinn positions per term, shown beside the score, not scored. No LLM step.

### Phase 6 — PRESIDENTS

Scores sitting and historical presidents from a mix of live and archival sources:
- **Live**: BLS employment rate, BEA/FRED GDP growth, UCSB American Presidency Project approval polling (with Gallup's by-party breakdown, compared within its era against Voteview's House polarization), FRED/BLS unemployment and consumer prices (Effectiveness since 1947, each judged against where the term started)
- **Historical**: C-SPAN Presidential Historians Survey (historical legacy), UCSB election-margin data, MeasuringWorth real-GDP series (1790–present), Maddison Project GDP per person for the US and 13 peer economies (1946–2022, extended by the World Bank's live series): postwar growth is scored relative to those peers, with their catching up with US incomes set aside
- All three score dimensions (Public Mandate, Effectiveness, Historical Legacy) are computed from source data with no LLM involvement. Dimensions a president has no real data source for are left N/A rather than filled with a placeholder.

### Phase 7 — FINALIZE

Persists everything computed in phases 3–6:
- Writes senator/representative scores, key votes, donor–vote matches and sponsored bills to SQLite
- Appends a `ScoreSnapshot` record (all 5 sub-scores + overall) for each member — enables historical score trend charts
- Records a `PipelineRun` with phase timings, counts, and any per-member errors
- Runs a SHA-256 fingerprint over all analysis source files (docstring-stripped ASTs, so comment-only edits don't count); if changed since last run, clears `AnalysisCache` and `LearnedClassification` so stale results from the old code are not served

---

## Caching Architecture

Three independent caching systems serve different purposes:

```
┌────────────────────────────────────────────────────────────────────┐
│  ApiCache  (SQLite: api_cache)                                     │
│  Key: (tier, endpoint+params_hash)   TTL: 72h (configurable)       │
│  Stores: raw API JSON, verbatim                                    │
│  Cleared: never (source data is immutable; re-fetched after TTL)   │
│  Purpose: replay pipeline without re-hitting external APIs         │
└────────────────────────────────────────────────────────────────────┘

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

Each pipeline holds a database-level lock rather than a process-level one: a run row with `status = "running"`, which a partial UNIQUE index lets only one process insert at a time (`run_tracker.acquire_pipeline_lock_why`), so two backend processes overlapping during a Swarm rollout can't both start the same pipeline. Pipelines run as threads of the pipeline process (of the single backend under plain `docker compose up`), so a restart kills them without letting them record it; on startup that process marks every pipeline's leftover `running` row `stale` (`main._invalidate_orphaned_pipelines`), sparing only a Senate run whose lease still holds (it may be live in the other task), and a row older than 12 hours is cleared at the next acquisition. `check-and-deploy.sh` does not deploy while any pipeline is running.

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
the source's own, checked verbatim).

Everything else uses geometric methods in sentence-embedding space:

| Task | Method | Rationale |
|------|---------|-----------|
| Bill policy area | Prototype similarity, kNN over the learning store for the residual (17 areas incl. procedural) | Deterministic, explainable, ~100ms vs. ~30s |
| Donor industry | Tiered: FEC metadata → learning store → prototype similarity → kNN | Generalizes from precedent; full audit trail |
| Party alignment | The roll call's actual party split; content similarity to party platform positions only where no roll call exists | Loyalty is defined by how the parties voted (see "Party Alignment" below) |
| Donor–vote connections | Industry funding share × policy-anchored vote similarity | Transparent, reproducible threshold |
| Key vote selection | Composite score: party deviation + donor overlap | Fully deterministic |
| Monitor deduplication | Title cosine on the similarity model ≥ 0.75 | No two different-topic update titles reached it (measured 2026-10-08); merging deletes a monitor |
| Issue deduplication | Post-LLM title embedding similarity | Catches LLM-generated near-duplicates missed by pre-filtering |
| Issue topic continuity | The same content, a near-identical title, or a shared source article with titles agreeing (`SHARED_SOURCE_TITLE_SIM`), across a 2-day lookback | Shared names and numbers were 25% precise on 65 judged pairs; a shared source alone merged 45 of 94 different-story pairs (both 2026-10-08) |

LLM calls per full nightly run: 0 (Phase 3 is fully deterministic, see above). The LLM runs on its own schedule for Action Center claim location (hourly, a handful of calls per run). Embedding operations per full nightly run: ~50,000. The pipeline is a **semantic classification and retrieval system** that uses a language model only where natural-language synthesis is unavoidable.

### Why Local Inference?

1. **No data exfiltration.** Senator donor records and issue analyses never leave the local network.
2. **Cost at scale.** Cloud API pricing for even a modest daily call volume runs to hundreds of dollars annually. At 0 marginal cost on the Pi.
3. **Reproducibility.** Model weights are pinned. An analysis run today produces identical output to one run six months ago on the same input. Cloud-hosted models update without notice.
4. **Latency independence.** No rate limits, no network jitter, no API quota.

The choice of LFM2.5-1.2B-Instruct over larger alternatives (7B+) is deliberate. The inference tasks here are structured extraction — completing a constrained template (key facts and actions from a cluster of news articles) — not open-ended generation. Empirically, a ~1B-class model produces acceptable quality on these tasks in a few seconds per call on ARM, vs. 25–45s for a 7B model. The quality ceiling is determined by the structure of the prompt, not model size.

---

## Classification Strategy

Classification decisions — what industry a donor belongs to, which direction a bill leans, whether an entity should be skipped from donor attribution — are made by embedding similarity against natural-language category prototypes, kNN over an accumulated reference corpus, or exact structured-data lookups, never by an arbitrary keyword-to-category judgment call. The pipeline uses a tiered strategy following computational parsimony (Jurafsky & Martin 2023):

| Tier | Technique | Speed | Used For |
|------|-----------|-------|----------|
| 1 | FEC metadata / learning store exact match | Instant | Donor types, previously classified bills and donors |
| 2 | Sentence-transformer embeddings (cosine similarity) | Fast | Bill policy areas, industry, party alignment, donor types, stance direction, procedural detection, commemorative detection, skip entity detection, employer filtering, memo transfer detection |
| 2b | SVD / PageRank on cosponsorship matrix | Fast | Ideology scoring (Tauberer 2012), legislative leadership (Brin & Page 1998) |
| 3 | k-Nearest Neighbor in embedding space | Fast | Remaining unclassified donors (~5%), bill classification from reference corpus |
| 4 | LLM (LFM2.5-1.2B-Instruct via llama.cpp) | Slow | Action Center issue synthesis |

Key embedding-based classification features:
- **Semantic prototypes** define each category via natural-language descriptions, not keyword lists. The embedding model matches entities to the nearest prototype by cosine similarity.
- **PAC decontextualization** detects "[Industry] PAC" naming patterns via a PAC-context prototype and margin-based runner-up selection, replacing regex suffix stripping.
- **Self-funded detection** uses SequenceMatcher ratio (Ratcliff & Obershelp 1988) for fuzzy name similarity instead of exact string matching.
- **Batch skip detection** classifies employer names and memo texts against skip prototypes in vectorized batches for performance.
- **Semantic category normalization** maps stale/unknown category labels to valid industries via embedding similarity, replacing a hardcoded alias table.
- **Stance direction** is derived primarily from embedding similarity against pro/anti/neutral action prototypes — see the disclosed exception below.
- **Procedural bill detection** uses embedding similarity against a procedural prototype instead of substring matching.
- **Commemorative bill detection** (`analyze/commemorative.py`) gives Legislative Effectiveness Volden & Wiseman's 1x tier for bills like post-office namings and Gold Medals: the margin between commemorative and substantive prototypes on the similarity model, over a threshold calibrated against V&W's own per-member commemorative counts (`scripts/calibrate_commemorative.py`, written to `app/data/commemorative_calibration.json`).

### Disclosed exceptions

A small number of narrow, code-commented precision pre-filters run ahead of the embedding classifier for specific, measured embedding-model weaknesses. Each is a fast-path for a case the embedding model demonstrably mis-scores, not a replacement for it — everything not caught by the pre-filter still goes through the real classifier below it:
- **Bill stance direction** (`bill_analyzer.py::derive_stance`): a tier-0 check against the same word set used to build the pro/anti embedding prototypes, used only to break ties or lower the acceptance margin when the embedding result is already ambiguous. Verified empirically (2026-07, n=2979 real bill titles): removing it changes 1.5% of outcomes, always by recovering a genuinely directional bill ("STOP CCP Act") the embedding alone scored as neutral.
- **Hotel/lodging industry classification** (`industry_classifier.py::classify_industry_with_provenance`): hotel brand names measurably score as MEDIA rather than REAL_ESTATE in the embedding space (a specific, verified anomaly); a small brand/suffix check corrects this before the embedding path runs.
- **PAC and payment-processor detection** (`donor_classifier_ai.py`): an org name containing "PAC" (an FEC filing convention) or matching a handful of named payment-processor brands (ActBlue, WinRed, Anedot, etc. — a closed, real-world set, not a category judgment) is caught by a keyword check ahead of the embedding path, since ALL-CAPS FEC-formatted names score inconsistently against mixed-case prototypes.

These three are documented at the point of definition in code, alongside purely structural lookups that decode already-known facts rather than classify anything: the SIC manual's hierarchy for a traded security's industry (`sec_tickers.SIC_INDUSTRY` — decoding the code the SEC assigned the issuer); an FEC entity-type-code map (`CCM`→`CandidateAffiliated`, etc. — decoding an enum FEC itself assigned); the FEC committee type and designation codes that mark party, candidate, joint-fundraising and leadership committees as political money (`fec.py::is_political_committee` — the FEC's own registration, not a reading of the committee's name); the industry of any other PAC where the records state it (`fec.py::structured_industry`): a labor organization's PAC (the FEC's organization type `L`) is LABOR_UNIONS, a PAC registered with no organization type and no connected organization (a nonconnected committee) is POLITICAL (v6.31; on 80 such PACs judged by hand, 69% correct against the name classifier's 35%, McNemar p < 0.001 — the misses are partnership and physician-group PACs, whose money then counts toward no industry), and a corporation's PAC whose sponsor is an SEC-registered issuer takes that issuer's SIC industry, its name matched exactly once legal-form words are dropped (`sec_tickers.issuer_key`; measured 2026-10-08, the name classifier had put 21% of labor organizations' PAC rows in another industry and agreed with the SEC's code on 59.5% of matched corporate rows, and `scripts/evaluate_industry_classifier.py` measures it against these record-derived labels); the industry a donor's stated occupation works in (`transform/occupation_industry.py`, read against `app/data/occupation_industry.json.gz` from `scripts/fetch_occupation_industry.py`: an occupation counts toward an industry only when a majority of the people in it work there, by the Census Bureau's ACS microdata, and an occupation string is recognized only by an exact match with one of O*NET's titles for it — the industry side is a crosswalk from NAICS codes, the federal taxonomy the Census uses, to Civitas's industries, since the embedding classifier filed "Computer Systems Design" under HEALTHCARE, and matching an unrecognized string to its nearest occupation by embedding was right about the industry 44-71% of the time, so it is left unattributed); a Congress-number→majority-party table for past congresses (the current congress's majority is read from the live roster each run) used for effectiveness baselines; and a name filter on lobbying-registry results (`lda.py::is_same_client` — the registry's client search is loose, so a filing counts only when its client's name begins with the donor's, or names it after "on behalf of"; measured on 48 clients' 2025 filings, and a similarity ratio was rejected because it accepted the American Veterinary Medical Association for the American Medical Association). The filter does not decide that a client *is* the donor — a subsidiary and a separate company can share a name — so every amount and linked bill is shown with the client name it was filed under.

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

For partisan depth (the lean bar and its per-area breakdown), which is not a count of breaks, the system labels each bill a member voted on with a nearest-centroid classifier (Rocchio 1971) in sentence-embedding space, and counts a Yea toward the party the bill matches in that area and a Nay toward the other (a housekeeping roll call is procedural, not a vote on its bill, since v6.28):
1. Each party's platform positions per policy area are embedded as seed centroids
2. Each run blends the seeds with the bills earlier runs labelled for each party, in both chambers, by the parties' actual split where there was a roll call (`_build_data_centroids`; both chamber pipelines rebuild them at start since v6.28)
3. Bill text is embedded and compared to both party centroids
4. Stance direction (pro/anti) disambiguates policy-area overlap

One procedural exception, read from the chamber's own result field: a **majority leader** who votes with the prevailing side against their own party — a Nay on a motion the chamber recorded as rejected, or a Yea on one carried over their party's opposition — is doing so to be able to move to reconsider (Senate Rule XIII; House Rule XIX cl. 2), so the vote carries no party signal. It applies only within the member's tenure as majority leader (`leadership_tenures.json`); the Speaker and minority leader are never exempted. In the 119th Congress every off-party vote by the two chambers' majority leaders (17 in all) was such a switch.

Independent senators have their caucus party inferred mathematically from voting patterns (proportion of votes aligning with each party), ensuring they are scored fairly against the party they actually caucus with.

---

## Action Center Pipeline (Hourly)

The Action Center is intentionally separate from the nightly pipeline because it operates on different timescales and data characteristics:

```
Every hour at :15
       │
       ▼
  1. FETCH ───── RSS (AP, NPR, PBS, BBC, The Hill, Politico, Roll Call —
       │         8 feeds across 7 newsrooms; NPR's two desks count as one)
       │         + Google Trends + Bluesky trending
       │         48-hour article window; direct URLs only (no redirect wrappers)
       │         Reddit was a third trending source until 2026-09: it now
       │         requires OAuth for datacenter traffic and 403s everything
       │         else, so it was retired rather than left silently empty.
       │         A source that FAILS is reported as failed, never merged in
       │         as a source that simply had nothing to say.
       ▼
  2. FILTER ──── Embed each article against 24 policy prototypes (19 US, 5 intl.)
       │         Discard cosine_sim < 0.20 (off-topic articles)
       ▼
  3. CLUSTER ─── Complete linkage on title embeddings at the calibrated
       │         cluster_title cut (bundled start: cosine 0.40):
       │         EVERY pair in a cluster must clear it, so a chain of
       │         look-alikes (floods ~ storm ~ epidemic) can't form one
       │         story. Single linkage plus a centroid merge from 0.20 built
       │         2026-09-27's chimeras: a Bangkok-floods title over a Hawaii
       │         hurricane lede and facts about a nor'easter and Fiji's HIV
       │         epidemic. Title similarity can't tell same-event from
       │         same-theme (18 labelled pairs overlapped 0.16-0.82 vs
       │         0.24-0.46), so a split beats a wrong merge. The same-run
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
       │         were cut-short predicates.
       │         Title = the top article's real headline. Summary = the
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
       ▼
  8. MONITORS ── Detect cross-day recurring topics (title similarity ≥ 0.83)
       │         Create/update NationalMonitor records (min 5 distinct
       │         days in 14, ≥3 unique sources, LLM significance gate)
       │         Re-merge duplicate monitors (similarity-model title ≥ 0.75)
       ▼
  9. TIMELINE ── Record daily TimelineEntry
       │         At week/month/year boundaries: LLM generates period summary
       ▼
 10. BLUESKY ─── Post new/updated issues: the verified lede, verbatim (or the
       │         real headline if it doesn't fit), opening "Yesterday:" / "On
       │         <date>:" if the event predates today. Nothing model-written.
       │         Daily member score spotlight, a fixed template of the
       │         scores.  (Also runs on the early-abort paths above — it
       │         doesn't depend on the news)
       │         Repost + like outlet posts that match active issues.
```

**Why cluster before ranking?** Articles about the same event arrive from multiple outlets within minutes. Without clustering, every "top issue" would be the same story from AP, NPR, BBC, and PBS. Clustering first, then ranking by source breadth, surfaces the most distinct newsworthy topics.

**Why filter at 0.20 cosine similarity?** The policy prototype filter is deliberately permissive. False negatives (dropping a real policy story) are worse than false positives. Borderline cases are handled downstream by extraction rather than by asking a model to be neutral: a cluster that yields no verbatim, adjacently-asserted claim simply produces no issue.

**Why a 5-day monitor threshold?** A topic appearing in the top issues on 5+ distinct days within two weeks is structurally different from a one-day news spike — it indicates a developing situation citizens may need to track. Shorter thresholds created too many ephemeral monitors.

**Why topic-keyed matching instead of rank-slot matching?** The original design keyed issues by `(date, rank)`. When the same story briefly fell off the top slots and returned, a new row was created with `bsky_posted_at=None`, triggering a duplicate Bluesky post. Topic-keyed matching (2-day lookback by cosine similarity) ensures the same story always maps to the same row. New articles advance `primary_article_date`; more outlets covering the same event do not.

**What makes a repost?** A newer article date is necessary but not sufficient — recap coverage rewords the same story under a fresher timestamp, which used to repost with nothing new to say. The new facts must also introduce either a named entity/figure or a story development (a veto, a court blocking an order, a failed override) that the facts *as of the last post* lacked. The baseline is `bsky_posted_facts`, not the live `facts` column: `facts` is rewritten on every hourly refresh whether or not anything was posted, so baselining on it let a development that surfaced between posts be absorbed and never read as new again. Rows that predate the column are backfilled from `facts` at startup — the poster is the only writer of `bsky_posted_facts` and it only ever sees issues the repost gate has already released, so a NULL baseline on an already-posted row could never resolve itself. The backfill is unconditional rather than fired once on the migration: the poster writes `bsky_posted_at` and `bsky_posted_facts` in the same commit, so a row with the first set and the second NULL can only predate the column, and a seeded row stops matching.

**Developing issues.** Before the news fetch, each refresh reads the latest final-passage roll calls and significant Federal Register rules (`early_signal.py`) and drafts a **Developing** issue, not posted anywhere, for one the news hasn't covered yet. A draft is a template filled from its record, with no model: a vote's measure, question, result, tally and date; a rule's agency, title, abstract (quoted whole in the facts, by whole sentences in the summary), publication date and document number. The model's drafts had characterized the record ("a narrow 77-22" for S. 4668, 2026-09-28). A vote on a bill that a current reported issue already covers, by recording the bill or naming it by number or short title, isn't drafted, and a draft whose bill later reporting covers as its own issue is retired, at the start of a run and again once the run's issues are written. Retiring only takes a draft off the Action Center: the homepage's record lists retired rows too, so `mark_recent_duplicates` also marks such a draft a duplicate of the covering issue, decided afresh each run from the record's own rows, so it holds after the covering issue retires as well (the S. 4668 draft stayed listed beside the reporting on 2026-09-29 until this). News issues resolve their bills by short title as well as by number (`bill_service.short_title_index`: the current Congress's "... Act" titles of three words or more, a title two companion bills share naming neither), since outlets name a bill that way far more than by number. Every issue carries the refresh's own date: a Senate draft once carried the vote's raw date string, which sorted after every ISO date, so the Action Center (which lists the latest date's issues) showed that one draft alone (migration 0021 rewrote the stored dates).

**How do you tell a quiet news day from an over-suppressing gate?** Both look the same from outside: nothing on the Action Center, nothing on Bluesky. Roughly ten independent checks in this pipeline fail closed — the right default when the platform publishes under its own name, but it means silence is the shared failure mode of all of them. Every refresh records what came in (`articles_fetched`, `articles_policy_relevant`, `clusters_considered`), what published (`issues_new_topic`, `issues_matched_existing`, `bsky_reposts_allowed`), and what each gate dropped, including on the two abort paths that publish nothing at all. `GET /api/admin/action-metrics` reads the window back with those three groups totalled: healthy intake against near-zero output is a suppression problem, near-zero intake is a quiet cycle or a broken feed. Runs are hourly, so a gap in the series is itself a signal — a refresh that crashed or was still holding the lock leaves no row.

**The clustering cut is calibrated from these rows, not typed.** Article pairs are logged every run as `thr_cluster_title_{yes|no}_{percent}` counters, labelled by whether their named entities and numbers overlap (never by the similarity being calibrated), and once a day the cut moves to the value that misclassifies the fewest, once there are at least 30 pairs on each side (`action_thresholds.py`). Three more thresholds used to be fitted the same way and no longer are, because their labels failed a check on 2026-10-08. The same-story title shortcut was labelled by that entity overlap, which, read against the issues' titles, was right for 16 of 65 issue pairs; it keeps its hand-measured 0.92. The issue-to-monitor and monitor-merge floors were labelled by the LLM gate, which approved 81 of 88 matches, off-topic ones included, and whose 0.55 merge floor on the retrieval model merged every pair of different topics into one monitor. They are now constants measured on the monitor's 453 updates (`_MONITOR_ISSUE_SIM`, `_MONITOR_MERGE_TITLE_SIM`). For the same reason, deciding that two issues are one story now takes the same content, a near-identical title, or a shared source article with titles that agree: names and numbers in common no longer do, and a shared source alone no longer does either (one roundup article is cited by unrelated stories; on 94 judged pairs, a 0.62 title floor kept every different-story pair apart). A vote draft's source is its record, so a story citing it still matches the draft. The values in use until a fit are bundled in `app/data/action_thresholds.json` with their provenance (`scripts/calibrate_action_thresholds.py` regenerates it). The remaining Action Center tunables are still typed, and are marked as such where they are defined.

**Why can't a deploy silence the refresh?** Two containers overlap during a deploy, so the refresh takes a cross-container lock: a row in `api_cache` whose primary key makes the second insert fail. The lock is a lease. Its holder rewrites the row's timestamp every minute from a heartbeat thread, a row ten minutes without a beat is taken over, and heartbeat and release touch only the row carrying the holder's own token. It used to be honored for a flat four hours from acquisition. A refresh runs in a thread, so a deploy's SIGTERM kills it without its `finally`, and the row it left made every run for the next four hours skip with "held by another container". On 2026-09-26 twenty-two deploys between 15:08 and 22:50 UTC kept that going all day, and no issue was published. A deploy still kills the refresh it lands on, and a refresh takes about twenty minutes of every hour, so on such a day most runs would die anyway: `check-and-deploy.sh` now defers a deploy while a refresh is running, but only for one that started within the last 40 minutes (twice the longest measured run), so an `is_running` flag wedged true cannot hold deploys the way it did on 2026-07-27. The first runs after the fix showed the heartbeat failing on every beat from minute three to the end of the run: the refresh flushed an LLM training-sample row into its own session and did not commit until every remaining cluster's model work was done, holding SQLite's one write transaction for about fifteen minutes. The heartbeat, the election coverage refresh and the LLM cache all waited out their 30-second busy timeout and failed, and the lease went stale under a live holder. Samples are now written on their own short session, and the monitor stage commits each write before its next model call.

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

**Nothing is rewritten.** Each passed, failed or reported measure, confirmation and committee meeting keeps the Digest's own sentence; the parser only decides which heading it sits under and reads the bill number. It is tested against real issues from both chambers (`backend/tests/fixtures/daily_digest`).

**Served as reports.** `GET /api/congress/latest`, `/day/{YYYY-MM-DD}`, `/week/{any day of it}` and `/month/{YYYY-MM}` return each chamber's day or period: every count, the "passed both chambers" list, the closest votes (measured from each vote's own threshold, so cloture failing 59-41 is one vote short, not an 18-vote margin) and the one-line summary are computed in `congress_service.py` from the stored rows by rule, so a summary reads "The Senate passed 3 bills, agreed to 4 resolutions and took 3 record votes" and never characterises what passed. `GET /api/congress/votes/{chamber}/{congress}/{session}/{number}` is one roll call with every member's position, linked to their page. `GET /api/bills/{id}/record` is any bill's record (not only one a current member sponsored): Congress.gov's CRS summary, sponsors, cosponsors, actions and text versions, plus every stored roll call on it with each party's split. A Congress.gov part that could not be fetched is named in `unavailable` and never cached, so an outage does not read as a bill with no actions; the route waits at most 10 seconds (the Congress.gov rate limiter is shared with the nightly pipeline), and a part still unfetched then is served the same way and fetched on a later visit.

**The pages.** `/congress` is the latest day either chamber met; `/congress/2026-09-24`, `/congress/week/2026-09-21` and `/congress/month/2026-09` are a day, a week and a month, each with the Senate and the House side by side. `/congress/bills` is the in-motion list that used to be `/bills` (old links redirect), and `/congress/bills/{id}` is any bill's page — of the current Congress, or of Congress N with `?congress=N` (a bill number names a different bill in each Congress, so every link that knows its Congress passes it, and the canonical keeps `?congress=` only for an earlier one): the CRS summary, sponsor and cosponsors, the full action history with a link to each day's report, text versions, and every recorded vote with each party's split and each member's position, filterable by state. Every bill the site mentions links there rather than to Congress.gov: the votes and breaks on a member's profile, the bills a donor-vote link names (`billPageHref`), and an Action Center issue's related bills, each with the Congress it recorded (an entry that recorded none links only when the site holds the bill, to the newest Congress it holds it from). A day whose Digest is not out yet shows the chambers' live floor logs and says so. The House writes its log through the day; the Senate posts its log only after the session ends, so until then a Senate that has voted shows "Record votes so far" and says when its log comes, rather than an empty log. `/congress` renders per request, never at build time (the build has no backend, so a prerendered page was always the empty state until a reader's visit revalidated it). A report the backend could not serve reads "could not be reached" (`app/congress/error.tsx`), never "nothing recorded" or a 404; only the backend's own 404 means no day has been recorded. A day, week or month exists only for dates on record, from the first day either chamber is on record as meeting to today (`congress_service.record_span`); any other well-formed date is a 404 with `noindex`, and a week or month links its neighbours only when they are on record. Every date from 1789 to the next year used to answer a page of placeholders, and the week and month pages linked their neighbours without end: in October 2026 a crawler walked dates into 2027 from rotating addresses, and each address counted as a new visitor (about 1,900 a day against a usual 150 to 400).

**A file is not a session.** On a day the Senate does not meet it still publishes its floor file, holding only when it reconvenes; the sync reads that as not in session (it first read as "The Senate met" for Friday and Saturday, 2026-09-25/26), and re-reads every not-yet-final day of the last week so a wrong row corrects itself.

**No Record, said plainly.** GPO publishes the Congressional Record for every day either chamber is in session. A past day with no Record (behind the back-fill cursor, which stops before any day it could not read, or found absent at least three days on) reads "No Congressional Record was published for this day" rather than "no record yet", and the week and month strips say "No Record". It states the fact and not "neither chamber met": GPO very rarely prints two small consecutive days as one issue.

**A missing file is not a failed fetch.** A 404, or senate.gov's redirect of a missing file to its "not found" page, means the chamber has nothing for that day. Anything else writes nothing, so a day is never made final from part of its Digest, and the back-fill stops before a failed day to retry it.

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

**Ballots refresh every 6 hours in election season.** The election pipeline runs last in the nightly chain, hours into the night, so ballots would otherwise go most of a day between refreshes. In the 60 days before an election and on through its results window (`is_election_season` in `election_calendar.py`, the same window as the 15-minute coverage refresh), the ballot step alone (`run_ballot_sync`: every state's certified list or primary results, then its filing list) is also scheduled on its own every 6 hours, at :50 UTC. It reads ballots up to and including election day; from the day after, each tick still fires but returns `skipped` without reading (`election_is_held`, below). It reads only state election offices' published lists, a few requests per state at one per second, so the cadence is cheap; the roster and FEC financial refresh stay nightly. It steps aside while the nightly election run is active, and the nightly ballot phase steps aside while a sync is in flight, so two passes never write the same candidates at once. Outside the season, the nightly run is enough: filings and primaries move on the scale of days. From the day after an election, neither the scheduled sync (whose ticks through the results window skip) nor the nightly run's roster, ballot and measure phases re-read that election (`election_is_held`): the site stays on it through the results window, but its sources move on — a state's single "next election" page lists the next one, a list past its election answers "not published yet", and FEC's candidate records give each candidate's *latest* district and incumbency, so a nominee filing for the next cycle elsewhere would move races — and re-reading them would unwrite a certified ballot. What was read before the day stands; financials still refresh (post-general reports are that election's money), and past elections' measures still age out. The roster reads each House candidate's district for the cycle it is building, from FEC's per-year `election_districts`, falling back to the latest `district_number` only when that year's entry is missing.

### Confirmed candidates (who is actually on the ballot)

The FEC roster in phase 1 lists everyone who *filed* — including candidates who already lost their primary months ago. Showing all of them on a ballot page is not a cosmetic problem: it presents losers as options. A second, independent layer answers "who is really on the November ballot" from each state's own election authority.

The whole flow — sources, matching, and what a race page may show — is drawn in [`docs/diagrams/10-elections.md`](docs/diagrams/10-elections.md).

The constraint that shapes it: **no 50 bespoke scrapers.** Adapters are per *vendor*, not per state, so a state whose vendor is already supported is a JSON entry in `backend/app/data/state_candidate_sources.json`, never new code. Only a genuinely different vendor earns a module. **No adapter branches on a state's name** — every URL, slug, election-name pattern and runoff threshold lives in that file, whose own `_contract` key documents which keys each strategy honours. A state that needs a knob nobody has needed yet gets that knob added to its adapter for everyone, never an `if state ==` special case.

Current coverage: **50 states configured across 32 strategies** (30 as a state's main source, plus two used only as a certified `general_list`). Some serve many states (`tabular` 15, `clarity` 3, `tally_enr` 2, `totalvote_enr` 2, and `certified_table` as fifteen states' general list and two states' main source); many are a single state whose election authority genuinely is unlike anyone else's. Two states (NV, NY) have no usable per-state source yet and fall back to `google_civic`, a national source keyed on one fixed, publicly-known address per state — never a visitor's.

**The certified ballot beats primary results wherever a state publishes it.** Louisiana, South Carolina and Missouri sat on `google_civic` until 2026-09-26, and all three publish their November ballot directly: Louisiana's results portal stages the general election's candidate list (`voterportal`), South Carolina's candidate-tracking system lists it by office (`vrems`), and Missouri's Secretary of State certifies it to the counties as a PDF (`certified_pdf` — a list of names, so none of the name-to-vote misalignment that makes results PDFs unsafe). Maine moved too, for a sharper reason: its results reader was right about the June primary and wrong about the ballot. The Democratic Senate primary's winner withdrew in July and the party nominated a replacement by convention, which primary results cannot show, so the withdrawn winner was still on the page. Maine's certified General Candidate List is now read instead (`certified_table`, a config-driven reader for any state that publishes its list as a spreadsheet). South Carolina had the same shape: after the June primary, a special primary and runoff decided a nominee the June results never named.

**A certified ballot is authoritative; primary results are not.** `confirmed_general` is never cleared for a primary-results state, because a nominee does not stop being one when a later fetch hiccups. For a state whose source *is* its certified ballot (`general_ballot_complete`: TX, NC, SD, LA, SC, MO, MI, OK; and every state with a `general_list`: AK, AL, AR, CO, CT, DE, FL, HI, IA, ID, IL, KY, MD, ME, MT, ND, NE, NJ, NM, NV, TN, UT, VA, WY — for AL, CT and UT that list is Google Civic's, which covers only the races it returns, and Arkansas falls back to Google's when its own list cannot be read), anyone confirmed in a race the list covers but not on it is unconfirmed on the next successful fetch — that is what removes a withdrawn nominee instead of showing them beside their replacement. A race the list does not cover keeps what it had, and a failed fetch changes nothing. Those states also get no "unopposed" FEC filers added back, since a party missing from a certified ballot has nobody on it.

**Everyone on the ballot is shown, FEC filing or not.** A candidate a state lists who never filed with the FEC (often a minor-party or independent candidate under the reporting threshold) used to match nothing and vanish — 7 of Louisiana's 41 federal ballot candidates. They now get a ballot-only row (`Candidate.fec_filed` is False; the id is `ballot:` + race + name, never an FEC id) built from the state's printed name, shown with "no FEC filing" instead of money and without an FEC link. It is removed when the state stops listing them or when they file and match a real FEC record. A bare surname or a results file's non-candidate row ("Write-in", "Scattering") never becomes one. To make this possible every strategy now carries the printed name (`display_name`) alongside the surname, through one shared builder (`federal_record`).

**A Senate race exists only if the FEC's own election calendar lists one.** The roster used to treat any Senate filer in a state with no regular seat up as running in a special election, which is right for Florida and Ohio in 2026 and invented one for New York and Hawaii out of serial filers with no money and no FEC candidate status — New York's page showed a Senate race holding nine filings from four people. The FEC election-dates calendar lists exactly the 35 states holding a Senate general in 2026, specials included, and is already read every night; the roster now skips Senate filings anywhere it lists none and removes such races already on file (`senate_election_known`, `_remove_senate_races_nobody_holds`). If the calendar has never been read in full, the class rotation still decides; only a complete read (every page back) can retract a Senate election, so a page that fails can't delete a real race. The nightly run of 2026-09-27 removed all fourteen phantoms (NY, HI, CA, CT, UT, NV, WI, AZ, IN, ND, PA, WA, MD, MO), leaving Florida's and Ohio's real specials. Those two then showed every FEC filer: a state's certified record for "U.S. Senator" was keyed to the regular race id, and a state electing a senator only to fill a vacancy has only a "-SPECIAL" race. A Senate record now goes to the state's one Senate race this cycle, whichever kind it is (`_race_id_for`); with both kinds in one state (Georgia, 2020) nothing in the record chooses, so it stays with the regular race. Florida's certified list and Ohio's official primary canvass both confirm the special's nominees, Ohio's Libertarian included.

**Declared write-ins are never "on the ballot".** They are not printed, so every certified-list source drops them. Texas's Civix portal gives its six declared 2026 write-ins the same general-election status as printed nominees and marks them only in its candidate-type field (`cdCandType` "WRTIN"); `tx_civix` read the status alone and showed four write-ins as Senate candidates on the state's official ballot, one labelled Independent from her FEC filing. It now drops them like every other adapter. A filer whose FEC party code is "W" is labelled "WRITE-IN" rather than a bare letter.

**West Virginia's primary link moved to an archive.** WV's Clarity results are found through a link on the Secretary of State's site, because its own `elections.json` is empty. In late September 2026 the elections page dropped that link, and every ballot sync reported WV failed. The link survives on the results archive, beside every election since 2016. A Clarity landing page that lists several elections is now scoped by each election's own settings file (its name and date, the same test `elections.json` states get), and a lone link from another cycle is no longer trusted either.

**Every state's ballot is certified by mid-September** (ballots must reach military and overseas voters 45 days before the election), so a certified list exists everywhere; the work is reaching it. Colorado's official general candidate list, Virginia's November federal-offices list and Tennessee's per-office November lists (its Senate race alone has eight independents, 36 across its federal races, all invisible to primary results) are now read the same way as Maine's (`certified_table`, a config-driven spreadsheet reader: which columns hold the office, district, party and name; an optional index hop; status and write-in filters; nothing else read — Virginia's file also carries campaign emails, phones and addresses). The certified list is a `general_list` beside the state's main source, not a replacement for it: it runs first and, when it answers, alone decides every federal race it covers (so a replaced nominee is never confirmed, even for a moment within a run); a race it does not cover keeps its primary-results nominees, non-authoritatively, and only covered races are labelled "confirmed" (the `ballot-basis` marker records `complete` for a list that covered every race, `races` for one that did not), while the main source keeps supplying statewide, legislative and judicial nominees the list may not cover — replacing Colorado's source outright had silently dropped its statewide nominees — and supplies federal nominees in the weeks before the list is posted. The API labels the page by whichever source actually answered (`BALLOT_BASIS_TIER` marker, read once per request) rather than by the state's config — a primary-results night says "nominees", not "confirmed".

**Florida** cancels a party primary when only one candidate qualifies, so its results file had no rows at all for five districts (8, 10, 18, 26, 28), which fell back to every FEC filer. Its Division of Elections candidate list (`dos_canlist`) gives every federal candidate a status; the ballot is everyone Qualified, plus a seat's sole Unopposed candidate (elected without being printed, as in FL-10), less declared write-ins: 77 candidates across all 28 districts. **New Jersey** read the July certification of party nominees, which skips independents and predates the amended certifications posted since (NJ-7, NJ-9, NJ-10, Senate); its Official General Election Candidates lists are the ballot as it stands, 38 candidates with 12 independents. Those lists print home addresses beside names, so a row counts as a candidate only when it has one — tested for presence, never read.

**Maryland, Iowa and Nebraska** publish their November lists as a CSV and two PDF tables, all now read by `certified_table`. Maryland's CSV gives every filing a status, and only "Active" is on the ballot (MD-5 still lists three unaffiliated filers as Withdrawn or Failed to Submit Required Number of Signatures). Iowa's list is the ballot as vacancies were filled, and Iowa's page had still been showing a senator who did not run as a Senate nominee. Nebraska's final list carries a petition candidate for Senate and three minor parties' nominees that no primary shows. A PDF is read as a table: the row holding every configured heading is the header, cells are split where the gap between words is wider than a space, and each cell belongs to the column it starts in, found from the cells that sit under exactly one heading (data is left-aligned under centred headings, so a short cell like "PO Box 33" can reach no heading at all). Iowa prints each office once per group, so `office_fill_down` carries it down, but only from a candidate row: a page footer never sets it, and a governor's running mate never inherits the congressional district above.

**New Mexico and Wyoming** followed the same day. New Mexico's candidate portal lists every contest with a status per candidate (only "Qualified" is on the ballot) as an HTML table at one fixed address that always shows the current election, so `discovery.url` requires `year_regex` to find this year's general election in the page before any row is read; last cycle's list must never confirm anyone this cycle. Wyoming posts its General Election Candidates Roster as a CSV beside the PDF; a row with a Date Withdrawn is off the ballot (`status_values: [""]`), and the list adds the at-large seat's Libertarian and Constitution candidates. Wyoming's own party codes (LBR, CT) are now read, "CT" only as a party column's whole value, where it cannot be Connecticut. **Hawaii**'s candidate report gives every filing a status ("In General" is the November ballot) and adds HI-1's nonpartisan candidate. It is a paged grid, and its own Export to CSV button returns the whole list, so `discovery.form_button` posts the page's form back with that button, exactly as a visitor's click does, after `year_regex` confirms the page names this year's election. Its names are printed "BERNING, Nathan M." (`name_last_first`: the text before the comma is the whole surname, so "LEGER FERNANDEZ" stays two words). A list's format is now decided from its bytes (zip, PDF, HTML, else CSV), not its address, since Hawaii's CSV comes from an .aspx page. **Delaware**'s general candidate list marks each filing Qualified or Withdrawn (a Republican House filer withdrew in July). Its xlsx writes every cell as an inline string and has no shared-string table, which the shared xlsx reader (also used by the results strategies) refused until now, even though its own docstring said inline values were read. **Kentucky**'s candidate-filings site, once set to the general election, lists the ballot per office: primary nominees plus seven independents and Kentucky Party and Libertarian candidates no primary shows, and declared write-ins, which are excluded. Its index links one page per office, so `every_link` reads each US Senator and US Representative page it finds (a year with no Senate race has no Senate page to require), and each page must name this year's general election. **Alaska** is top-four, and a finalist who withdraws is replaced by the fifth-place finisher, which primary results cannot show. Its page was wrong in both races: it showed a Senate finalist and a House finalist who are not on the ballot, and missed the replacements in both (one Senate, two House). The Division's general candidate list (one table per office under a heading, registration printed in the name, "Certified Write-In" entries excluded because they are not printed) is now the ballot. Reaching it found a bug in the shared fetch layer: `fetch_with_retry` did not follow redirects, so Alaska's moved candidates page (301 to /election-candidates/) came back as an empty success, a moved page read as a page listing nothing. Redirects are now followed by default for every source. **Montana**'s candidate filing list is the same kind of paged grid as Hawaii's, exported the same way. (It first read the cycle's primary list, whose NOMINATED status turned out never to reach a petition independent; see below.)

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

`/elections/states/<ST>` is framed as ballot research, not a mock ballot: no marks, no "your picks", and every contest carries research signals (money raised against the race's top fundraiser, a member of Congress's Representation Score, news count). It replaced a single 768px column of stacked panels (the news feed on top, one row per House district — 52 in California — and a box for every section, empty or not) that read as one endless scroll.

- **Desktop**: three printed-ballot columns (Federal | State | Measures · local), each contest a box with a shaded header and the ballot's own instruction ("Vote for one"), fitting about one screen. A contest's research opens in a drawer beside it with Previous/Next through the ballot.
- **Phone**: an index of every contest, one line each; a contest opens on its own screen (one contest per screen, the voting-machine pattern). Checked by live screenshot at 390px (NC, CA, TX, AK, MD, 2026-09-27): the Money / Record / News tabs share the row equally, so the bar no longer shifts when the bold active tab changes width; the Previous/Next arrow is bound to its label with a no-break space so it never wraps onto a line of its own; and the election date in the page eyebrow never breaks at a hyphen.
- **Tabs only inside a race** (Money / Record / News). Research (NN/g "Tabs, Used Right"; the Center for Civic Design and EAC ballot guidelines) says tabs suit supplemental views a reader need not compare across, which describes a race's facets but not the ballot's sections: a voter needs every contest.
- Federal offices are named one way everywhere: "U.S. Senator", "U.S. Representative", and each says the term the vote is for — 2 years for the House, 6 for the Senate (both fixed by the Constitution), and "fills the rest of the term" for a special Senate election. State offices carry theirs too, from `backend/app/data/office_terms.json`: every statewide office, legislative chamber and court the page shows in the states it covers, listed explicitly per state and office with its source (a Georgia or North Dakota Public Service Commissioner serves six years, a Maryland delegate four, a North Carolina Superior Court judge eight). Nothing is defaulted — a state or office missing from the file shows no term, because New Hampshire and Vermont elect two-year governors and a guessed term is a wrong fact. Terms are worded for the office ("4-year terms"), since a seat filled for the rest of an unexpired term runs shorter; a test rejects any key the pipeline does not produce.
- A column with nothing on file says so: a state with none of its own offices loaded gets a "State offices — not loaded yet" contest (with the official lookup), the same "not loaded is not none" rule the measures box follows, instead of an empty column that reads as a state electing nobody.
- Candidates appear under the name their state prints on its ballot ("Jane Doe"), stored as `Candidate.ballot_name` whenever a state source matches them, and fall back to the FEC's ("DOE, JANE") until one does. A "Last, First" printing is not stored — a comma does not reliably mark where a surname ends ("Olszewski, Jr.") — so those keep the FEC name.
- A candidate the state has confirmed is on the ballot always shows, whatever their FEC record says. North Carolina's certified Libertarian for Senate had been hidden under "other filers" because she raised nothing and is not an FEC statutory candidate.

The contest list is built once (`frontend/src/lib/ballotContests.ts`); the drawer is a real modal dialog (focus kept inside, Escape closes, focus returns), and `#race-{id}` / `#ballot-{key}` links open a contest directly. `StateBallotClient.a11y.test.tsx` runs axe-core over the page and every kind of drawer (a race on each tab, the district picker, a picked district) on every CI run, with a guard test proving axe actually fires; colour contrast, which jsdom cannot compute, stays with the Lighthouse job.

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
elsewhere. The text filter stays for anyone who prefers it.

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
The same list tells the election-night sync that a redrawn seat has no
holder to measure a flip against, and lets an incumbent running under a new
number link to their record — labelled a *sitting member* there (a *member before this election* from
election day on, when the results window can include the new Congress's
first day), never the new district's incumbent
(`incumbencyLabel` in `frontend/src/lib/elections.ts`). Regenerate after any redistricting:

```bash
python backend/scripts/build_county_district_crosswalk.py
python backend/scripts/build_district_topology.py
```

### Race coverage: what counts as coverage

A story is attached to a race by candidate name, and a bare surname is never
treated as identifying — with thousands of FEC candidates the roster's surname
set covers a large fraction of common English surnames. A match therefore needs
the surname **plus** corroboration in the same text:

- `full_name` — the candidate's first name appears with it, as a name (up to two
  intervening tokens, so "Jane Q. Public" matches), and
- `surname_context` — the candidate's state name appears.

Only `full_name` items are eligible for the Bluesky posting path; the weaker
basis is display-only.

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
- **Drafter attribution renders with each quote** (`title_authority` / `fiscal_authority`). Ballot titles are litigated precisely because they are contested, so naming the author is more neutral than the bare quote.
- **Removed measures render as removed** for a 45-day grace window rather than disappearing, and a sync returning implausibly fewer measures than are on file is treated as a bad response, not as news.
- **"No measures" and "not ingested" are different states.** `MeasureCoverage` carries `covered` / `confirmed_none` / `not_yet_covered` / `ingest_failed` per state per election, because an empty section on a state's ballot page reads as "nothing to research".
- **Scope is stated on the page, and the statement shrinks.** A ballot is defined per *ballot style*, not per state, so state legislative seats, county/municipal offices, judicial questions and local measures are enumerated as omissions above the content — and each entry is removed as that gap actually closes, rather than standing as permanent boilerplate (statewide executive offices came off the list this way; see below), with a link to the voter's own election office. Per-state official links are only shown after an automated check confirms they resolve; otherwise the page falls back to the USAGov directory.

#### Where each state's measures come from

Forty-three states are read directly from their own offices (`ballot_measure_pdf_sources.json`, one reader each in `fetch/ballot_measures_<st>.py`, sharing only fetch plumbing in `ballot_measures_state_common.py` and the pipeline stage in `ballot_measures_pdf.py`). No two publish alike, so each reader is built against its state's real, currently-fetched document, never a guessed layout:

- **Northeast.** Vermont reads the General Assembly's statutory amendment notice (2026: Proposals 3 and 4); Maine the Secretary of State's Citizen's Guide, found from the SOS pages because its filename changes yearly, with each question's ballot text taken from the guide's own listing and the Attorney General's "A YES vote …" sentences; Massachusetts its Information for Voters guide (the Secretary filed the 2026 guide at a new address, `.../information-for-voters/archive/IFV_2026 English.pdf`, while the old pattern answered 404; the page that links it answers with an Incapsula challenge, so `url_pattern` lists every address the Secretary has used and they are tried in order — only a 404 moves on, and a 404 at all of them is not yet published, which the expected-by cutoff turns into a failure). Connecticut and New Jersey prove absence rather than list presence: Connecticut prints any statewide question on every town's ballot, so one complete official town sample ballot with no question column proves there is none; New Jersey's per-year page lists each public question (its template carries a commented-out 2023 question re-dated every year, so comments are stripped first), and "none" needs the page to show this year's general-election certification.
- **South and border states.** Alabama (the Fair Ballot Commission's statement per amendment), Arkansas, Florida (the answer to its initiatives database's search form), Kentucky, Louisiana, Maryland, Missouri (Amendments and Propositions — 2026's Proposition A is the referendum on the congressional map), North Carolina (a one-page report from the elections board's ballot database), South Carolina, Tennessee, Texas, Virginia and West Virginia (the Secretary of State's dated public notice). Arkansas keeps no page recording that a citizen petition was certified, so its list is published only while the Attorney General's petition section for the year reads "None".
- **Midwest and Plains.** Illinois, Indiana, Kansas, Michigan, Minnesota, Nebraska, North Dakota, Oklahoma and South Dakota. South Dakota's certified list must equal the Attorney General text in its pamphlet, and Nebraska's initiative range must equal its pamphlet; Nebraska's legislature-referred amendment's ballot statement is a scanned image, so the Legislature's own resolution supplies its ballot language. Oklahoma's filed State Question PDFs are scans (OCR with errors, and every superseded version of the title), so no OCR'd text is stored: a ballot title is quoted only from the State Election Board's accessible text copy of the final title where it posts one (2026: SQ 845, the August 13 corrected title), and otherwise the register's SQ number and subject line are stored, linking the filing (2026: SQ 847).
- **West.** Alaska (the Division of Elections' own sample ballot), Colorado (the Blue Book), California (the Official Voter Information Guide as HTML — the current guide or the Secretary's archive for the year — with the Attorney General's official title and summary, the Legislative Analyst's fiscal estimate and the state's own YES/NO sentences; the PDF edition isn't read, because its 2026 text layer turns the fi/fl/ff ligatures into a bare "f" and reads "infation", "fnance"), Hawaii, Idaho, Montana, New Mexico (the Legislative Council Service's amendments publication plus the ballot language the year's GO bond act writes into statute — a year with no configured act is `ingest_failed`, never an amendments-only list), Washington and Wyoming.
- **Arizona, Georgia, Nevada, New Hampshire, Ohio, Utah and Mississippi.** Georgia (the Secretary's amendments booklet, read from its four-column summaries pages: the Publication Board's caption, the resolution's ballot question, and the Attorney General / Secretary / Legislative Counsel summary), Nevada (the Secretary's ballot-questions booklet: each Condensation, the booklet's own "A “Yes” vote would …" sentences and the Legislative Counsel Bureau's fiscal note, checked against its contents page), New Hampshire (the Secretary's "Questions on the General Election Ballot": 2026's CACR 13 and the statutory school tax-cap question HB 1300 puts on every municipality's ballot, stored with its placeholders as printed — kept as a statewide question because the Secretary lists it among the questions placed on the general-election ballot and every New Hampshire voter sees it, though each municipality decides it for itself), Ohio (the Secretary's official sample ballot, whose state-issue page gives the Ballot Board's ballot language and the ballot's own YES/NO sentences — read by what is on each page, because 2024's issue page carried the candidate pages' footer), Utah (the Lieutenant Governor's general-election certification, which states that no proposition qualified and lists the amendments; it is a scan whose text layer is OCR, so only each amendment's letter and heading are stored and linked to the certification, as with Oklahoma: OCR'd text is not verbatim text) and Mississippi, which proves absence the way Connecticut does: the Secretary's complete statewide composite sample ballot with no measure on it. New Hampshire's CDN, like Michigan's, rejects the User-Agent's contact comment, so it is sent without it. Georgia's and Nevada's Secretaries of State put their whole sites behind bot walls (Cloudflare and Imperva; a challenge page from the production host too, checked 2026-10-01), so those two readers raise `SourceBlocked` and the state's own booklet is read from a county election office's republication of it — Augusta-Richmond County's Board of Elections for Georgia (the same 152,039-byte file as the Secretary's), Eureka County's Clerk-Recorder for Nevada (`republished_by` in the registry). Arizona is read the same way: azsos.gov and apps.azsos.gov answer a Cloudflare managed challenge (403, `cf-mitigated: challenge`, whatever the User-Agent, checked 2026-10-08), and the reader asks once — the same client's retry, carrying the cookie the challenge set, was let through, and retrying through a challenge is getting past it — so the Secretary's Publicity Pamphlet is read from the Citizens Clean Elections Commission's copy (a state commission; the county election sites checked answered 403 too, and Pima County posts its own compilation of the ballot language rather than the Secretary's document). From each proposition's Ballot Format page it stores the designation, the descriptive title (prepared by the Secretary and approved by the Attorney General, A.R.S. 19-125(D)) and the ballot's own "A “yes”/“no” vote shall have the effect of …" statements, checked against the pamphlet's table of contents (2026: Propositions 141, 142, 144 and 316–320). The copy must pass every check the state's own copy would (cover, contents list, every part present); a state page that was read and refused never falls back; and the card says "Quoted verbatim from the Nevada Secretary of State, as republished by Eureka County Clerk-Recorder" (`BallotMeasure.republished_by`). Utah's reader was built on the state's own document; its module docstring says what could not be checked from the development container.
- **Not yet covered**: Delaware, D.C., Iowa, New York, Oregon, Pennsylvania, Rhode Island and Wisconsin. Two different reasons, and the page says which: Delaware and Pennsylvania publish no official list of certified statewide measures (Pennsylvania's none before the election); the other six, D.C. included, have an official source that Civitas does not read automatically yet (D.C.'s had only a proposed wording posted when checked — see its `dev_note`). There is no third-party fallback (the Vote Smart integration was removed in 2026-09; `_purge_retired_source` deletes any row it left, and a leftover `VOTESMART_API_KEY` in `.env` is ignored rather than failing startup). Each state is recorded `not_yet_covered` — never `confirmed_none`, since nothing was checked — with no alert, and its page gives the state's `reason` from the registry's `unread` map (`measureCoverage.unreadReason`) beside its official lookup link. A `reason` is shown to voters on the production server, so it must be true wherever Civitas runs: a fact about what the state publishes, or "Civitas does not read {state}'s official measure list automatically yet" — never a claim about network access (a site that blocks this development container may answer production fine), and never a date, a year or "this election", since the same sentence is shown every cycle (tests enforce both). A reader de-registered mid-cycle leaves its rows for the election as they were — still certified, since marking them removed would claim the state struck them — and the page says Civitas has stopped reading the state's measures automatically (without repeating the reason, whose "… yet" would contradict it), dated by that source's last read, never as a failed check; an unread state shows no "last attempt" date, since none is made. What was actually found (a Cloudflare challenge, a firewall, a captcha, from which machine and when) goes in the entry's `dev_note`, which the API never sends. Every such state must have a reason there, and a state leaves `unread` when a reader is registered for it; a test enforces both.

**Three answers, never two.** Every reader returns a list, `None`, or raises `NotYetPublished`, and those map onto `covered`/`confirmed_none`, `ingest_failed` and `not_yet_covered`:

- **"None" only from the state's own statement, for this election.** A reader returns `[]` only when the state affirmatively says so for *this* election — Illinois's Voters' Guide sentence "There are no statewide questions of public policy for this election", Minnesota's stated count of zero, a certified-list section present for this election with nothing in it, a complete Connecticut town ballot with no question column. Addresses reused every cycle are checked to name this election first. Confirmed none for November 3, 2026 as of 2026-09-28: Connecticut, Illinois, Mississippi (the Secretary's complete statewide composite sample ballot, with no measure on it), New Jersey, South Carolina (its election commission lists 42 referendums, every one a county question, while the same query for 2024 returns that year's statewide amendment) and Texas (the Legislative Reference Library's index of amendment elections has no entry after November 4, 2025). An empty answer is cached for six hours (`EMPTY_RESPONSE_TTL_HOURS`), so every nightly run asks again; a non-empty list is cached for 72.
- **Anything measure-shaped that can't be read fails the whole state.** A heading, row, notice or section the reader doesn't recognise refuses the state's answer (`ingest_failed`) rather than being skipped, and so does a document with no readable text at all — a list published one measure short would read as the complete ballot. So does an answer in which two measures would share an id (the second would overwrite the first).
- **"Not yet published" is neither.** Where a state posts its document late or only in a year that has a measure, its absence raises `NotYetPublished`: `not_yet_covered`, no alert, re-checked next run. That covers Maine's guide, West Virginia's notice, Michigan's November document, Tennessee's per-election page (a 404 in every non-gubernatorial year), Kansas's and Missouri's pages while they still show the primary's measures, Alaska's sample ballots (posted ~50 days out), California's guide for a year the Secretary hasn't posted or archived, Colorado's Blue Book, Massachusetts's guide, and Vermont's and Wyoming's documents (`absent_until_published` in the registry), New Hampshire's questions document (posted only in some cycles), and Indiana and Oklahoma, whose sources are not certified lists and so can never say "none". It is raised only after every page was actually read (a crawl or paged listing that stopped at its page budget is not a complete read); a page that couldn't be fetched is a failure, not an absence. A failure records the reader's own reason — the HTTP status, the page it didn't recognize, the document it couldn't find — on the state's coverage row and in that night's ops alert, rather than a bare "fetch failed". It turns into `ingest_failed` — every night, until the document is back — when this same source has already read this election (its measures are on file, or it recorded a successful check; coverage another source left behind doesn't count), and — for documents a state publishes for every general, like a sample ballot or a voter guide with a mail-by date — once the state's expected-by cutoff passes (`expected_by_days_before` in the registry, citing the statute where one sets it, e.g. California's 21 days and Colorado's 30; otherwise `DEFAULT_EXPECTED_BY_DAYS_BEFORE`, 14). A document that exists only in a year with a measure stays `not_yet_covered` — its absence can be the answer — but at that default date a low-severity ops notice goes out once per state and election, in case the reader broke before it ever read anything.

**Official titles and drafters.** `official_title` is only ever the state's own ballot title, and only when a reader supplies it — the display `title` may be a label the reader composed ("Proposition 3", "Question 1: Citizen Initiative") and is never shown as official. `title_authority` names the drafter of the official title, or of the quoted summary where there is none, and the card renders "Drafted by …" beside whichever it names; one quote carries one drafter, so two drafters' texts are never joined (New Mexico stores the Legislature's ballot text, Idaho keeps the ballot question and the Legislative Council's statement in separate fields). Drafters are read from the source where it names them — Florida's Amendment 3 carries the Attorney General's court-ordered rewrite, Maryland's Question 3 the language the Supreme Court of Maryland ordered, Colorado's ballot titles the General Assembly or Title Board according to the Blue Book's own "Placed on the ballot by" line — and a fiscal statement whose drafter the document doesn't name (Montana's) is left unattributed. YES/NO framing is stored only where the state writes one; "For"/"Against" choice labels name a choice rather than explain it, and stay null. It is stored as the state's whole sentence, lead-in included ("A “yes” vote on Amendment 81 requires …", "A “yes” vote will repeal …"): Colorado's and Missouri's readers used to keep only what followed, so every yes/no line in both states was a fragment of the source's sentence. A table inside a ballot title (Colorado's Amendment 87 lists the estimated tax change by income category) is read with its wrapped column headers joined column by column; read row by row they had interleaved into nonsense.

**Writing a state's answer.** A successful direct read is written as one transaction: rows it supersedes for that election only (another source's, such as a renamed or re-pointed registry entry's — replaced, not struck, so never shown as "removed") are deleted, the measures upserted, this source's unseen rows reconciled to removed, and coverage set; if any write fails, none of it happens and the state reads `ingest_failed`. A read returning fewer than half the measures on file for that election (`MEASURE_SHRINK_FLOOR`) is held back as a likely bad response — unless the state itself reports the drops (Florida's "Removed" status), or the same shorter list comes back from `MEASURE_SHRINK_CONFIRM_RUNS` (3) consecutive fresh reads, when it is the state's real answer (a held-back list is dropped from the cache, so a cached copy of one bad response never counts twice). After a read that failed or found the document missing, the page keeps the last list it had but says so, with the date of the last successful read (which only a covered or confirmed-none read ever sets). The page shows only its own election's measures; earlier elections' rows are pruned after the removal grace window, but never the election the site is still on (a slow count keeps the page on the election just held past that window, up to January 3). A source that says a measure is gone — Florida's "Removed" status, Oklahoma's register no longer dating a State Question for this election — has it marked removed. One that can only stop publishing (Michigan's November document disappears when its only proposal is struck) keeps alerting nightly until an operator checks the state's announcement and accepts the absence: `POST /api/admin/ballot-measures/{ST}/{YYYY-MM-DD}/accept-absence?note=...` (admin token) marks every row for that election removed (any source's — no row may stay current under an operator's "none"); a state with no registered source is refused (400), since it reads not yet covered and records confirmed none with the note. It is refused (409) while the state's latest read covered that election unless `force=true`; every action is logged and kept in `measure_coverage.operator_actions`. The answer is restored each night the reader again finds the document absent (a genuinely failed night still alerts), and replaced when the reader answers. The page words it as our operator's determination (`measureCoverage.basis = "operator"`), never as the state's statement. The ingest-failure alert is keyed per night and per set of failing states, so a failure is heard every night it lasts; the late-cycle notice is once per state and election.

An optional town selector on the same page surfaces LOCAL races (city council, school board, local measures) that a statewide page structurally can't show — a real ballot is defined per precinct, not per state, and the only address-keyed source (Google Civic's `voterInfoQuery`) requires an address. Rather than a visitor's own address, which this platform's architecture exists to never send off-box, it's called with a fixed, publicly-known representative address per town (town hall) from a small hand-curated pilot list (`backend/app/data/town_directory.json`) — every visitor who picks the same town sends the identical request, so nothing visitor-specific ever leaves the server. This is a real approximation, not a precinct-accurate lookup (a town can contain more than one precinct), and the page says so next to the selector. Gated on `GOOGLE_CIVIC_API_KEY`; unset means the selector doesn't appear and the statewide page above is unaffected.

### Statewide executive offices

Governor, Lieutenant Governor, Attorney General, Secretary of State and Treasurer are confirmed through the same adapters, the same config file and the same `_contract` described above — no new source, no new vendor, no new key. A state that publishes its primary results publishes these contests **in the same response** as the federal ones; they were simply being parsed and discarded.

- **Two gates, opposite directions.** The federal-only rule above still holds — `parse_office` must never read a state legislative seat as federal. Its new sibling `parse_statewide_office` must never read a county or municipal office as statewide. Against Rhode Island's real 192-contest primary the second finds exactly 9 and refuses all 177 others (133 General Assembly seats, 21 party committees, 23 local offices). A joint "Governor and Lieutenant Governor" ticket resolves to the top of the ticket.
- **Stored as `StatewideNominee`, not `Candidate`.** A state office has no FEC filing, no finance totals and no Representation Score, so forcing one into the federal table means a row whose every financial field is permanently null. For the same reason the name is kept whole rather than reduced to a surname — there is no FEC row to match it to. Ballot annotations still come off (Rhode Island marks party-endorsed candidates with a bare asterisk).
- **`statewide_offices` is a truth condition, not a feature toggle.** Every adapter returns only what its own state's feed contains, so a state nobody has checked returns zero executive contests — indistinguishable from a state that genuinely elects none. The flag says that state's real contest labels were checked against the parser, which is what makes a count of zero a claim rather than an admission.
- **`omits` shrinks as the gaps close.** The page's "Governor and other statewide executive contests" omission is dropped for any state actually covered. A disclaimer list that keeps disclaiming what the page now shows stops describing the page and becomes boilerplate a reader learns to skip — past the entries that are still true.

### State legislative seats

The same feeds carry the state's own legislature — 133 of the 192 contests on Rhode Island's real 2026 primary ballot are General Assembly seats — so these are read by a third parser gate beside the federal and executive ones, and stored as `StateLegNominee` under the same `statewide_offices` opt-in.

The hard part was not the data. It was **how a reader finds their seat without being asked where they live** (see core design principle 8). A U.S. House row can name its counties; a state legislative district is far smaller than a county, so the useful unit is the town — and no ready-made town-to-district list exists at the right vintage:

- **The results feed** reports a legislative contest as a single reporting unit. No locality breakdown.
- **Census Block Assignment Files** are ideal in shape and join cleanly (Rhode Island: exactly 75 lower and 38 upper districts), but the newest was published February 2021, so its boundaries predate nearly every state's 2021–22 redistricting.
- **A plain spatial `intersects` query** is hopelessly over-broad, because a district that merely *touches* a town counts: Jamestown (~5,500 people, comfortably inside one ~14,000-person district) came back as 7 districts.

`scripts/fetch_state_leg_crosswalk.py` instead takes the **current** district polygons from Census TIGERweb, lays a grid over each town, and asks which district contains each interior sample point. There is no sliver problem in that formulation — districts tile the state, so every sample is inside exactly one district and every count is a real overlap measure. A scanline makes it tractable (~12 seconds per state rather than billions of edge tests).

Validated against the independent block-assignment answer: 97 of 113 Rhode Island districts match exactly, 7 more are safely over-inclusive, and **all 16 differences were confirmed to be genuine redistricting** — each re-measured at 4× resolution and found to have exactly 0.000% overlap under the current map. Nothing is under-inclusive, which is the direction that would hide a reader's race.

The result is that typing "jamestown" returns House 74 and Senate 13, and nothing else, without a visitor ever entering an address.

Coverage is not limited to one vendor: the bulk `tabular` exports carry the same contests, so a state there is a config opt-in too. Minnesota's real 2026 export resolves 18 federal, 8 statewide (including its joint `Governor & Lt Governor` ticket) and every one of the 32 legislative contests the file contains — while refusing `County Attorney` and `County Auditor/Treasurer`, which contain the exact words the statewide gate keys on. Note that some `tabular` entries deliberately fetch a federal-only export (New Mexico's URL carries `type=FED`), so opting such a state in means widening its discovery URL first.

Georgia is the third state live, and the most complete: 30 federal races, all 236 legislative seats (exactly its 180 House + 56 Senate), and every one of its statewide contests — the eight constitutional offices plus both Public Service Commission seats, which are elected statewide but *held by district*, so each is its own row.

Getting Georgia right needed two things the first two states didn't. Its county subdivisions are Census County Divisions named `Fairburn-Union City CCD` — names on no sign and in no address — so place-based states use incorporated places instead, chosen by Census's own "strong MCD" distinction. And three of its House districts lie entirely in unincorporated land with no place at all, so those fall back to their county; the county suffix is kept there, because Georgia has both a Forsyth County and a Forsyth city and the list would otherwise show the same word for two different places.

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

**Six states elect no executive officers in 2026**, and saying so is a claim like any other. Virginia and New Jersey choose governors in odd years after a presidential election (next 2029); Kentucky, Mississippi and Louisiana in the odd years before one (next 2027); Montana in presidential years (next 2028). Their adapters read only federal contests, so the page's "none" cannot rest on the feed — the `statewide_offices_basis` key carries the calendar sentence instead, and the page shows it in place of "as published by", which would claim a reading that never happened. It says nothing about the legislature: Kentucky and Montana elect legislators in 2026, and those pages keep "State legislative districts" among their omissions. A main source that fails while a certified federal list answers now claims nothing about state offices at all, rather than syncing an empty list that would read as a confirmed absence.

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

## Election Night: Live Results

From election day the elections pages stop being a ballot-research tool first and lead with the count. The national map at `/elections` is shaded by who is leading each covered race — fainter while fewer than half the units are in, solid once the state lists its count as official (a race there still only "leads"; Civitas calls no race) — instead of by Cook PVI lean. A state with more than one race on the map (its House delegation, or both Senate seats) takes the party leading the most of them, every party compared, grey when two lead equally many, and the same fade from its least-counted race: faint while any has under half in, solid only once every one is official (`stateShade` in `frontend/src/lib/results.ts`). Each state's accessible name says its standing in words, and the three dark count-less fills (polls not yet closed, no votes yet, feed not read) each carry a texture, so neither is read from colour alone; each state page puts a refresh line (when its count was read, and — if the page's own refresh fails — that the count shown is that older one), its Senate race or races and a House table of every district (a race or district Civitas shows no count for while it shows one for the state's others says so, as does a whole chamber on the national map — hatched, "no count shown here", never "no votes yet"; worded about Civitas's page, never the feed, which may have reported a contest Civitas set aside as indistinguishable, impossible or unmatched — and, in a state electing both its senators with a count shown for only one, the other race on its map label and directory row, from the `senateRaces` list `GET /api/elections/results` gives) and a district map shaded by the count above the ballot research — once that state's polls have closed; until then the page stays in its present-tense research framing and the national map marks the state POLLS NOT CLOSED, saying nothing about its count (the masthead says ELECTION DAY and when the first state's count can be shown, never "polls open", which it can't know from midnight Eastern, when the phase starts); and a **Live updates** feed tells each change in the count as it happens (first returns, a change of leader, every unit reporting, a count the state lists as official, a seat changing party). An official count is tagged LEADS · OFFICIAL COUNT (or FLIP · OFFICIAL COUNT, keeping who held the seat), never a bare OFFICIAL beside the leader's name. The flow and every rule are drawn in [`docs/diagrams/10-elections.md`](docs/diagrams/10-elections.md#election-night-the-live-count).

**Which election the site is about.** The site no longer flips to the next cycle the night polls close. `election_phase.active_election()` keeps it on the election just held while any count is still moving and for 14 days after the last change (`RESULTS_GRACE_DAYS`), never past January 3, when the new Congress is sworn in; only then does the campaign page for the next election return. The pipeline's cycle, the API's `phase` and the election-season jobs all read that one function.

**Where the numbers come from.** Every five minutes from election day (hourly once no count has moved for a day), `live_results/sync.py` reads each covered state's own election-night feed — the same four results vendors the confirmed-candidate adapters already read (Clarity, Tally ENR, TotalVote, Enhanced Voting), one reader per vendor, configured per state in `state_candidate_sources.json`. A state is covered when its entry names a feed one of those readers can read (`live_results_states()`); the results endpoint lists them as `liveStates`, and the about pages render their count and names from that list rather than typing it (`frontend/src/lib/liveStates.ts`). Every other state is drawn as having no live count here and links to its election office, never as a state where nothing has happened. All states are read at once and stored separately, so one slow or broken feed doesn't hold up the rest.

**A wrong number on election night is worse than none.** Nothing is read for a state before its last polls close (`poll_close_times.json`). Test, preview, demo or wrong-date data is refused and stores nothing; so is a feed that goes backwards in time or version, and an impossible count is dropped. A count whose total fell is stored but announces nothing. Civitas never calls a race: a count is "leading" — "not final" until the state itself says official, and still "leading" after, never "wins", since an official count's leader can still face a runoff (Georgia requires a majority in the general), a recount or a court. A seat is reported as changing party only once half its reporting units are in — and where a state reports by county or town, which "reports" on its first batch, once every one is in and six hours have passed since the first votes (the first votes ever: a momentary zero read doesn't restart the clock), or the state calls its count official. That bar only decides when a change of party is first *said*; once said, it stands until the lead itself goes back to the seat's party or ties (`lead_is_back`) — a count that dips below the bar with the same challenger ahead (reporting figures missing for a poll, units added, an official flag switched off) is not a reversal, and says nothing. The results page's map and race lines mark the same announced state (`RaceResult.flip_announced`, set when the sync raises the flip or its reversal), never the bar re-read on each request — the same state the Action Center issue and the Live updates feed show, so the three never disagree. A poll whose total fell changes none of them, even when the holder leads in its figures: it announces nothing, so until the next poll reverts all three together the page says both things that are true — the change of party was announced, and the latest count shows the holder's party ahead (or a tie): FLIP ANNOUNCED · HOLDER'S PARTY LEADS on the race, "change of party announced earlier, holder's party ahead in the latest count" in the map's names, and a separate line under the national SEATS CHANGING PARTY counter rather than a place in it. No surface says "changing party" or FLIP · LEADING beside the holder's lead (`flipShown` / `flipNotShownText` in `frontend/src/lib/results.ts`). A held poll with no votes in it, or whose leader the feed gives no party for, says that ("no votes in the latest count", "leader's party not given") — never "no votes yet" or "another party" — since every surface checks the announced change before its "no votes" and "tied" wording. Feed rows are matched to the race's confirmed November nominees where it has any, never to a primary loser, and a row whose feed names no party borrows a matched candidate's party only when that candidate is the one nominee with the surname and the given name fits. A data reset during the results window keeps the held election's ballot and count (`RESET_KEEPS_WHILE_RESULTS`), and the statewide, judicial and ballot-basis coverage markers its pages read (`HELD_BALLOT_MARKER_TIERS`): once election day has passed nothing can rebuild them, because the roster and ballot syncs stand down. How each state's last read went is kept (`LiveResultRead`), so a page can say "the count hasn't started" and "the state's feed couldn't be read" apart. A state whose feed Civitas has stopped reading — its last check more than a sync pass plus ten minutes old (15 minutes while counts move, 70 once hourly), or no read record at all that long after its polls closed — is marked **STALE**: its row says when its feed was last checked ("hasn't been checked since …") and when the count shown was read, the maps keep the last leader's colour under an amber-and-dark stripe, and when every closed state is stale or failing the `/elections` masthead says STALE (and which of the two) instead of LIVE. The pages judge this, and whether a state's polls have closed, by the **server's clock** — the `Date` header of their latest good response, run on by at most one poll interval (`resultsNow` in `frontend/src/lib/results.ts`) — never the visitor's, which can be off by minutes or hours. The results endpoint is fetched with `cache: "no-cache"`, so the browser's own HTTP cache never answers a refetch with an old copy's `Date` (nginx still answers from its cache, whose responses carry a fresh `Date`, so a stale body's old read times still read as stale). One clock per page (`useResultsNow`), handed to every part of it, that never runs backwards. While the page's own refreshes fail its clock stops where it stood when the refresh failed — never back at the last answer, which re-opened polls that had just closed on screen — and REFRESH FAILED, with when it failed (on the server's clock, like the read times beside it) and when the counts on screen were read, is the only thing it says: no state is marked LIVE (the national list says NOT REFRESHED), no state's feed is blamed, and every count on the maps — the results section's district map and the House research drawer's alike — carries the not-live stripe. The polite live regions hold only the state (live, stale, refresh failed, polls closed), not the times beside it, so a screen reader hears each change once.

**Developing issues and posts.** When a covered seat's count shows it changing party, the Action Center opens a DEVELOPING issue for it, ranked below confirmed stories and promoted if the press confirms it — only by a story that names that race, the state and the seat together in one phrase ("Georgia's 2nd District", "GA-02", "the U.S. Senate race in Georgia" — never a state and a number from different races in a roundup, a dateline, "West Virginia" for Virginia, or a state Senate seat, hyphenated or not — nor "Ohio Senate" followed by a seat number, a district or a place inside the state ("Ohio Senate seat 5", "Florida Senate seat in Tampa"), since that is also the name of the legislature's upper chamber, which elects the same night: "seat" and "election" count only with "U.S.", "special" or a possessive ("Ohio's Senate seat"); a postal code inside a link, bare domain or not, doesn't count; spelled-out ordinals do, but only before a district, seat or race word ("Georgia's second district", never "It was Georgia's first."); "Alaska's lone House seat" names an at-large seat. Regular and special are told apart only where a state holds both this cycle — "regular" names the regular one, Florida's and Ohio's one 2026 race is a special, and "the Ohio Senate race" names it. A district or seat number after the phrase, whatever punctuation stands between ("Ohio Senate race, District 5"), or a district, legislature or possessive after a state named last ("the Senate race in Ohio District 5"), makes it a legislature's. The phrase is only half: the story must also name one of that race's own candidates by full name — the leader or runner-up in the stored count, or before a count the certified nominees (failing those, the race's candidates on file) — so a legislature or commission story, which names other people, never promotes it whatever its wording. A full name is a given name the records state (FEC's first given name or a nickname it quotes, the count's or ballot's printed first name, or its initial with a period: "W. Smith"), matched exactly — "Donna Lee" is not Don Lee — then optionally a middle name or initial that doesn't contradict the record ("Pat Q. Smith"), then the whole surname: multi-word or hyphenated as a unit ("Ana Ruiz Ortega", "Jo Hart-Keller"), accents and curly apostrophes folded ("Kip O’Dwyer"), a suffix allowed after. A results feed's printed name is read against the person's FEC filing (the ballot's printing is the state's own and stands, nickname and all): its given name starts at the first word that is a name they filed or a short or long form of their first filed name or nickname — the filed name beginning the word ("Christopher" for CHRIS), or a word no longer than it sharing its first two letters ("Steve" for STEPHEN) — so a feed's "Congressman Steve Rowe" (filed ROWE, STEPHEN ALLEN) is Steve and "Congressman Rowe" states no given name; a word that fits only as a short form and is followed by a full word that fits nothing filed means the printing states none ("Representative Bob Jones" for JONES, ROBERT E), and a parenthesised word counts as a nickname only if it fits too ("(Incumbent)" is not). A count row linked to no candidate is read against the race's filings of that surname the same way ("Congressman Smith" beside SMITH, CHARLES states no given name). A printed middle name not on file drops the printing's given name the same way ("Steve Allen Rowe" beside a filing of STEPHEN alone). So a printed given name counts only if it is a filed name, a short form sharing the filed name's first two letters, or a word beginning with / extending the filed name: a nickname that changes the initial or the second letter — Bob/ROBERT, Bill/WILLIAM, Liz/ELIZABETH, Jim/JAMES, Tom/THOMAS, Jack/JOHN, Peggy/MARGARET — is not read from a feed's printing, a miss, never a wrong promotion (the filed given name and the ballot's own printing still work). The exact limits: a title no longer than the filed given name and sharing its first two letters still reads as a short form ("Sheriff" for SHERIDAN, "Judge" for JUDITH, "Chair" for CHARLES), a filing stating only an initial lets any word with that initial through, and a printing with no filing of that surname anywhere in the race stands as printed. Initials count only together — a quoted "A.J." matches "A.J.", "A. J." or "AJ", and a record of initials alone ("DOE, J. D.") is named by both ("J.D. Doe") — never as one bare letter, and a single quoted letter like "(I)" is a ballot annotation, not a name. The name must be written as one: every word before the surname, and the surname's last word, capitalised ("DALE CARVER" counts; "voters will mark brown ribbons" is not Mark Brown). Two people sharing a full name are not told apart: a namesake of a candidate, by full name, in a story that also names the candidate's seat promotes it — an accepted limit, as no record distinguishes them. A surname alone never counts ("Smith", "Rep. Bishop", "Sen. Jones"): eight review rounds graded surname evidence, and each found a namesake, a title ("The King Center", "Captain Smith"), a later bare mention of a namesake, or a common word at a sentence's start ("Green energy", "Case counts") that it let through. A district phrase followed by another body's name — capitalised ("Georgia's District 2 Public Service Commission seat", "California's 2nd District Court of Appeal"), a lower-case compound running into a seat word ("2nd District county commission seat"), or a seat "on" something ("2nd seat on the Public Service Commission") — is that body's, unless what follows names the race's candidate, the House or Congress; this is read from the sentence's structure, not a list of bodies. In a state with two Senate races an unqualified phrase is told apart by whose candidates it names — the race whose candidate is named by full name, and none of the other's by any mention (a story naming both races' candidates names neither, and the other race's candidate by capitalised surname alone is enough for that, since holding a story back can only cost a miss) — and with no candidate on record the phrase decides alone: `action_center._results_race_named`. A story naming the race but none of its candidates by full name leaves the issue developing — a miss the next story can fix, where a wrong promotion can't be undone), since the template shares a state and "District" with every other seat's, and never once retired. Until then it follows the count, and if the lead reverts it leaves the Action Center and is rewritten to say the count no longer shows a change of party (the homepage record and the issue's own page still show it). Once promoted it is the news story's: the count no longer rewrites it, and it matches later coverage like any other issue. An issue the Action Center's two-week cleanup has since deleted is not redrafted as news on the next poll; only a new flip, after a reversal, opens a new one. Each race's flip is its own story: the homepage never hides one as a "duplicate" of another race's. The same flips, official counts (a House count's only while it still shows the seat changing party) and big Senate moves are published like every other post — to the Elections feed (`/feed/elections.xml`, and each state's feed) and then Bluesky — louder than usual but budgeted (below). Every sentence in the feed, the issue and the posts is a fixed template around the state's own figures — no model writes any of it.

---

## Publishing: feeds and Bluesky

Every post goes through one function, `app/broadcast.publish`, which stores it as a `BroadcastPost` row and only then delivers it to Bluesky (when `BSKY_HANDLE`/`BSKY_APP_PASSWORD` are set). The rows are the Atom feeds, listed on `/feeds`:

| Feed | Holds |
|------|-------|
| `/feed.xml` | Everything |
| `/feed/issues.xml`, `/feed/congress.xml`, `/feed/members.xml`, `/feed/elections.xml` | One topic (`broadcast.FEEDS`) |
| `/feed/states/<ST>.xml` | One state's race posts, election-night counts and member spotlights |

Anyone can follow a feed with a reader, a Discord or Slack bot, or their own program, and Civitas keeps no list of who does (AGENTS.md §8). The feeds are served by `app/api/feed.py` (50 newest entries, ETag/304) and cached by nginx for 5 minutes. Each entry carries what the Bluesky post's link card shows: the linked page's picture and description, read once from its Open Graph tags (`bluesky_utils.og_card`, the same reading the Bluesky card uses) and kept on the row (`card_image`, `card_image_alt`, `card_description`). The entry's content is HTML (the picture, the post, a "Read on Civitas" link, and the source article when the post restates one), with the description as its summary, the picture as an enclosure and a Media RSS thumbnail for readers and chat bots, and the source as a `related` link. The card is read right after the post is stored, so an unreadable page never holds a post back; the hourly pass (`broadcast.fill_missing_cards`) fills in any it missed from the last week. The feed itself never fetches anything. The feed's `<icon>` is `app/apple-icon.png`, the favicon (`app/icon.svg`) rendered at exactly 6x, since Discord and most readers don't draw SVG; `icons.test.ts` fails if the two drift. The same file is the site's `apple-touch-icon`. Each page a post links to draws its own card at `/api/og` (`frontend/src/app/api/og/route.tsx`): an issue, a member, a state's ballot, and a Congress day or week, whose tiles give each chamber's record votes or say why there are none (not in session, no record yet), never a zero for a day with no record. A post is in the feed whatever Bluesky does with it. A send Bluesky refuses is retried hourly (`broadcast.deliver_pending`), for at most three tries in all, only on the Eastern day it was written, since a post can say "Yesterday: …", and stopping after two refusals in a row, since each try is a login; election-night result posts are never resent (below). A send interrupted by a crash is never retried, so nothing is posted twice. The table survives an admin data reset (`RESET_KEEPS`): it is what the posting modules check (by each post's `subject`, e.g. `race:2026-SEN-GA`) before publishing again, so a reset neither empties the feeds nor re-posts the last few days.

The posting modules below decide what to publish and when. The Civitas Bluesky account (`@civitas-research.org`) carries the same posts as the feeds, except any Bluesky still hadn't taken by the end of that day (an election-night result it refused, at once) and a correction of a flip it never showed:

| Post type | Trigger | Content |
|-----------|---------|---------|
| **Issue post** | New topic enters action center, or existing topic gets articles with a newer date | The issue's lede — a claim the model located and `post_composer` verified verbatim; on a repost, the first fact the last post didn't carry (the new information that released it) — or the top article's real headline when the lede is too long to post whole (a claim is never cut: truncation can drop the qualifier that makes it true). No word is model-written. If the event predates today, code prefixes "Yesterday: …" or "On [Month day]: …" (`bluesky_poster._compose_new_post`) |
| **Member spotlight** | Once per day (random pick of a senator or representative not yet spotlighted, cycling through all before repeating) | A fixed template of the member's Representation Score, rank in their chamber and three dimension scores (`bluesky_spotlight.compose_spotlight`). No word is model-written |
| **Congress day** | Once per session day, when the Daily Digest has made its record final (usually the next evening) and the day's page shows it (the link card is read from that page, which can still be its earlier render); only days from the last three, so the Digest back-fill never posts history; at most once per day (`congress_bluesky.py`) | The day report's own sentence ("The Senate passed 3 bills, agreed to 4 resolutions and took 3 record votes. …") and the passed bills' numbers, never their titles (an official short title can read as advocacy), linking to that day's `/congress` page. No word is model-written |
| **Congress week** | Once per week, for the week (Monday to Sunday) just ended, once every day either chamber met is final; only that week, so nothing older is ever posted (`congress_bluesky.post_weekly_congress`) | The week report's own sentence ("The Senate met 3 days and took 12 record votes. …") and the numbers of bills that became law, linking to `/congress/week/{monday}`. No word is model-written; it replaced a weekly recap the model wrote from the timeline |
| **Race update** | A news item matched to a race by a candidate's full name and judged to be about that race; at most 4 a rolling day, one per race every 48 hours, never the same article twice, and never a sentence already published (two articles sharing a description once gave the same post twice) (`election_bluesky.post_race_coverage_updates`) | Who did what: an actor and a predicate the model located, each copied word for word from the news item and checked by `post_composer` to be asserted of each other, linking to the race on its state's ballot page. A sentence too long to post whole with its link is not posted (a cut could end on "Sens." or before the object its verb needs). No word is model-written |
| **Election-night result** | Every 5 min while any count is moving (`live_results/bluesky.py`): a seat changing party, a count the state calls official (Senate, or a flip), a Senate lead change with most of the count in, every unit reporting in a Senate race | A fixed template around the state's own figures that says "leads" — with "Not final." while the state hasn't listed its count as official, and still "leads" once it has; figures are dropped to fit, never the qualifier. At most 6 an hour and 40 an election, one per race per 20 minutes; a post held back by the budget or a race's cooldown waits for a later pass, but is dropped once two hours old or when a later post about the same race supersedes it, and every post is worded from the count as it stands when it goes out — so an official or every-unit-reporting post waits (up to the same two hours) while the count no longer says so (the flag switched off, units added). Each is published through `broadcast.publish` (kind `result`, in the Elections feed). A send Bluesky refuses is not resent later (`broadcast.NO_RETRY_KINDS`): word for word an hour on, it could describe a count that has since moved or reverted, so the next event's post says where the count stands; a correction goes to Bluesky only if the flip it corrects did. What has been said, the budget and the cooldown are read from the stored posts, so a data reset doesn't post the night again, and a posted flip whose announced count went back to the seat's party while the reset had wiped the count's events still gets its correction (never from a held poll, and only once the seat's holder is known — a holder the reset left unknown is looked up again on each read). A House seat in a state whose congressional map was redrawn for the cycle (`app/data/redrawn_congressional_maps.json`: AL, CA, FL, LA, NC, OH, TN, TX, UT for 2026) has no known holder, so no flip is announced for it. A posted flip that reverts gets a correction outside every cap. The race-coverage posts stand down while a count is moving. No word is model-written |
| **Repost + like** | Outlet post matches an active issue (cosine sim ≥ 0.78) | Reposts + likes posts from AP News, NPR, and PBS NewsHour (`NEWS_OUTLET_HANDLES` — a narrower list than the RSS feed set, since it needs a Bluesky presence); posts under 24h old; max 3 per hourly run |

**Withdrawn issues are listed, not quietly deleted.** `backend/app/data/retractions.json` is the public record of issues Civitas published and withdrew, and why. An entry's issues are removed by the Alembic data migration it names, the API answers 410 with the reason, and the issue page shows that reason (marked noindex) instead of breaking. The page itself is a 200: Next's App Router has no way to give a page a 410. The first entry (2026-09-27) withdrew two issues built from unrelated stories by the clustering bug fixed in #659.

The spotlight pick is deliberately *not* the highest or lowest scorer. Always picking an extreme, combined with framing it as praise or criticism, produced a real incident: a "praise" post about a senator's score read as badly out of touch after negative news broke about him the same day. A random pick stated as plain numbers can't fail that way. The text was model-written until 2026-09 and still judged the numbers under a list of banned words ("placing him in the average range", "All individual metrics fall within typical expectations"); it is now a template.

The spotlight reads member scores, not the news, so it runs on every refresh — including the two paths that abort early because no articles arrived or none were policy-relevant. That makes a missing spotlight a usable signal in its own right: if it hasn't posted, the pipeline isn't completing, and the quiet isn't a slow news day.

Each issue links back to its permanent Civitas permalink (`/issue/<id>`). The permalink is stable — issue IDs never change even as content is updated, and any issue that has ever been published is retained indefinitely. The 14-day cleanup of old issues keys on `bsky_last_post_text` (only ever written on a successful publish) rather than `bsky_posted_at`, which the repost path clears and so does not mean "never published".

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
- **Constituent Alignment** (keyed `constituentAlignment`; it was `independentVoting` until 2026-09 — the dimension was rebuilt in v4.2): how a member's voting compares to what their *seat* elected them to do. Party-line voting in a safe seat that elected that platform scores as representation, not as a failure of independence — the delegate model of representation (Miller & Stokes 1963), not independence as an intrinsic virtue. A House seat's lean is the Cook PVI of the district the member was *elected on* — the sitting Congress's lines, pinned per Congress in `app/data/district_pvi_sources.json` (`fetch/district_pvi.py`), never the map the next election uses; members switch to a new Congress's lines — together with the scored windows, which read the same setting — at the first House pipeline run in a job that starts after noon ET on January 3, when that Congress takes office (the setting follows the clock, advanced at the start of each pipeline job, no restart — so leave `CURRENT_CONGRESS` unset: a value in `.env` pins the windows and the lines; a Congress with no pinned entry stays on the newest pinned lines and raises an ops alert). Each stored House score records the Congress whose lines it used, and its "show the math" breakdown is recomputed on those same lines. The elections pages show the lines of the Congress each election seats — for a cycle nobody has pinned yet, the newest pinned lines. The member's break rate is scored against the break rate same-party members show at the same seat lean, measured from the chamber every run (`compute_constituent_reference`); the member's Nokken-Poole roll-call position (Voteview) is scored against a seat-conditional per-party expectation (Canes-Wrone, Brady & Cogan 2002's district-relative extremity). Both apply the same way in safe and competitive seats, and position is scored symmetrically — v6.13 decided each of those choices by testing them against House re-election results. Since v6.27 a position counts at the strength its roll calls support, min(1, w(n) / w(200)) with w(n) = n / (n + n0): a full record (200 votes or more, a convention) counts in full, and n0 is measured on Voteview's own thin and full records with drift per transition (`scripts/calibrate_position_confidence.py`, `app/data/position_confidence.json`), one curve for both chambers over every Congress since 1989, chosen by predicting the next Congress with each transition's n0 fitted on the earlier ones only (its drift, shared by every structure, from its own full pairs) and keeping the simplest structure whose error is within one standard error of the best's (the one-standard-error rule, a convention, adopted in review after four other rules had been tried, two leaving one member out, then two forward): the latest era's curve (transitions since the 110th Congress, a split by convention) has lower error than one curve by about two standard errors of the paired difference (just under two), about one over the last three transitions alone (computed from `forward_test` and its `last_three`), and a window of the last six transitions about as much (just over two), but both are well within the best's own standard error, a curve per chamber predicts no better, and with the split or the width chosen from earlier Congresses alone each has lower error than one curve only within the noise; nothing in it follows the sitting Congress); the saturation scale comes from full records, and a member with no position in a current section sits at 50 (until a new Congress's positions pass the gates the part is left out); the same study kept the 50-at-the-norm shape and the scale pooled across both parties (the pooled scale a stated choice: the evidence decides neither way) (`docs/research/constituent-alignment.md`, section 14). The vote score is highest when a member breaks with their party about as often as same-party members of similarly-leaning seats, and falls both ways (v6.16): the gap is measured in standard deviations per vote (`seat_residual`, the Pearson residual against a fractional-logit expectation), so a few extra points count for more where members of that seat rarely break, and it reaches 0 at 1.5 of the party's 90th-percentile gaps above the expectation or 3 below, so loyalty costs half as much as breaking too often. Each party is read on its own scale, so a party that is more unified in a given Congress isn't scored higher for it. The member's own party's primary voters reward this shape; the general electorate rewards breaking more than the norm — the score follows the first, as a stated choice (see `docs/research/constituent-alignment.md`, sections 8–12). Ideal points are ingested automatically every pipeline run (`app/pipeline/fetch/voteview.py`, ingestion-gated) — no manual step.
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

Additional member metrics, both chambers (informational, not scored):

| Metric | What It Measures | Technique |
|--------|------------------|-----------|
| **Leadership Score** | Legislative influence — how many peers cosponsor this member's bills | PageRank on cosponsorship graph (Brin & Page 1998) |
| **Ideology Score** | Behavioral ideological position derived from cosponsorship patterns | SVD on cosponsorship matrix (Tauberer 2012) |
| **Partisan Depth** | How deeply aligned with their party across policy areas | Yea/Nay ratio on D- vs R-leaning bills per area, with SVD ideology as a prior while votes are few; the label is the member's tercile within their own party |

### Supreme Court Justice Scores

Each justice is scored on one measure (`JUSTICE_SCORE_WEIGHTS`): independence from the appointing president, from the Supreme Court Database's votes in cases the federal government argued, shown with its standard error — see Phase 5 above, and `docs/research/justice-scores.md` for why it replaced consistency and independence from the appointing party's bloc (both ranked justices by distance from the Court's median, Spearman −0.82 and −0.75).

### Presidential Scores

Presidents are scored on three dimensions — Public Mandate (25%), Effectiveness (25%) and Historical Legacy (50%), each president's shown as the share it actually carries when a dimension is missing — using a mix of live API data (BLS employment, BEA/FRED GDP) and historical records (C-SPAN Historians Survey, UCSB American Presidency Project approval and election margins, MeasuringWorth GDP). Independence, Follow-Through, and Competence were removed in 2026-07 rather than left as hand-set values with no live formula behind them; a president with no data source for a dimension shows N/A and the overall score renormalizes over whichever dimensions actually apply. See the [scoring changelog](/changelog) for the full account.

A member's dimension falls back to a neutral 50 when its data is missing or too thin to trust (a failed fetch, a new member), and is shrunk toward 50 as its evidence thins; a president's dimension with no data source shows N/A instead. A record that is present but empty is not missing: a member with no substantive bills after half a year in office scores Legislative Effectiveness on a credit of 0. No LLM input is used in score calculation — formulas are deterministic and auditable.

---

## Hybrid Search (Explore)

The Explore feature searches primary-source government documents. It is not a
single similarity score: general web search has never been one, and neither is
this. Four independent rankers are combined — two retrieval channels and two
query-independent priors.

**What is indexed:** Senate and House floor speeches, presidential actions
(executive orders, proclamations, memoranda), Supreme Court opinions, and
Federal Register rulemaking documents — five source types, not bill text.
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
| `vec_explore` | `/data/vectors.db` (sqlite-vec) | 384-dim embeddings of each document's whole text, in windows of the encoder's context length led by title and summary, with doc type / chamber / politician as filterable metadata |
| `explore_fts` | app DB (SQLite FTS5) | A BM25F inverted index over title, summary and body. External-content, so the text is not duplicated; triggers keep it live between runs |
| `authority` / `cited_by_count` | `explore_documents` columns | PageRank over the citation graph between these documents |

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
semantic, keyword and hybrid separately, using known-item retrieval over four
query styles (title, paraphrase, identifier, rare-term) with relevance
judgments derived from the corpus rather than hand-labelled. Change a weight,
re-run it.

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

## Score Data Transparency

A senator's or representative's profile is the scorecard at a glance (`components/scorecard/MemberScorecard.tsx`): a header with who they are, how to reach them, the Representation Score, the rank the leaderboard gives it (served as `chamberRank` on `GET /api/politicians/{id}`) and its trend; then the three scored dimensions side by side, each showing what drives its number with nothing to expand; then their financial-disclosure holdings as a pie beside the list; then tiles for what is on record but not scored (stock trades, donor-vote links, positions by policy area). Every full list — every vote, every donor, every bill — opens in a drawer over the page; each vote there is one line (what was voted on, linked to the bill's page, the question, the date, the member's vote, and whether it went against the party). On a phone the columns stack.

Each column's sentence and parts come from `GET /api/{senators|representatives}/{id}/score-breakdown`, which the profile page fetches with the profile. It recomputes each dimension from the member's stored records with the scorer's own functions (`score_calculator.explain_scores`) and returns every component (value, weight, and the scorer's own sentence on how it came about) plus `facts`: the numbers the scorecard's sentences state — PAC and small-donor shares and the comparison they are measured against, party-line votes and breaks and the seat's expected break rate, bills and joint resolutions by the furthest stage each reached and the simple and concurrent resolutions beside them (counted apart, as the component's own sentence counts bills, so a resolution the chamber agreed to never reads as a bill that passed). The page formats them and computes none. The header's score trend states the change `GET /api/{senators|representatives|presidents}/{id}/history` serves as `change`: since the earliest snapshot on the latest one's scoring method (and, for a member, Congress), never across a methodology change. The full voting record's party-line figures (`votingRecord.votedWithPartyCount`, `votedAgainstPartyCount`, `partyLoyaltyPct`) are the counts Constituent Alignment scores (`score_calculator.party_line_tally`), so they agree with the column. Nothing in it is written by a model, and because it is the same code the pipeline scores with, the scorecard can't drift from its numbers. A president's profile is laid out the same way (`components/scorecard/PresidentScorecard.tsx`): the Presidential Score, with the rank the president leaderboard gives it (the header's comparison link opens `/compare/presidents` with this president already chosen: a server-rendered page whose two native selects submit as a plain GET form, setting both Presidential Scores, the three dimensions and the same `facts` side by side; `/compare` pairs members of Congress) (a sitting president is not ranked until the term ends), then Public Mandate, Effectiveness and Historical Legacy side by side, each stating its figures beside the all-president averages it is scored against (`facts` on `GET /api/presidents/{id}/score-breakdown`: approval and its trend, jobs per year and GDP growth, the historians' points, and the same person's other rated presidency), then stock trades and executive orders, on record and not scored. The president leaderboard opens with a summary of the sitting president's scorecard (`PresidentSummary`: the Presidential Score and its three parts, with a link to the full scorecard), shown above the ranked table rather than in it. A column says a dimension is unscored only when its score is null, never because its figures failed to load. A justice's profile follows (`components/scorecard/JusticeScorecard.tsx`): the Judicial Score and its rank, then the loyalty estimate with its standard error and the rates under the appointing president and under others, Martin-Quinn positions and the voting record (not scored), then agreement with each sitting justice (served named and ordered, `agreement`: `[{id, name, share}]`), all read off `GET /api/justices/{id}`.

The profile also lists the records the dimensions read:

| Score | Records shown |
|-------|---------------|
| Funding Independence | Top donors by amount with type and industry, PAC share, small-donor share, industry breakdown (`IndustryDonation`) |
| Constituent Alignment | Every counted break this Congress with the chamber's own per-party tally (served in the breakdown's `facts.breakVotes`), breaks from the party's flank listed apart (`facts.flankBreakVotes`), and the seat's expected break rate |
| Legislative Effectiveness | Sponsored bills and joint resolutions by the furthest stage each reached (simple and concurrent resolutions counted apart), with commemorative bills marked |

The layer surfaces the underlying `KeyVote`, `Donor`, `IndustryDonation` and `SponsoredBill` records rather than LLM-written explanations — the numbers are auditable against the source data.

---

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
GET /api/public/v1/representatives               Serving representatives, ranked by score
GET /api/public/v1/representatives/{id}          One representative's full record
GET /api/public/v1/representatives/{id}/history  A representative's scores over time
GET /api/public/v1/search                        Floor speeches, presidential actions,
                                                 Supreme Court opinions and federal rules
GET /api/public/v1/states                        States and their member counts
GET /api/public/v1/                              Index: every endpoint, weights, links
```

Conventions: lists are pages (`entries`, `total`, `page`, `perPage`,
`totalPages`); a member's `rank` is their place in the whole chamber whatever
the filters; an unknown id is a 404 (history included); every record has a
`siteUrl`; filter values (party, chamber, document type) are enumerated in
the spec and a value outside them is a 422.

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
| Party alignment from the roll call's actual split only | "Broke with party" is defined by how the parties voted; content feeds only the per-area depth breakdown | Poole & Rosenthal 1985; Laver et al. 2003 |
| Learning store as experience replay | Past classifications bootstrap future accuracy | Lin 1992; Yarowsky 1995 |
| Inverse HHI for funding diversity | Standard concentration metric from IO economics | Rhoades 1993 |
| Linear shrinkage toward 50 on thin evidence (fixed rate `min(n/k, 1)`, not empirical Bayes; Constituent Alignment's position part instead uses a measured reliability weight relative to a full record, v6.27) | Keeps a few votes or cases from producing an extreme score | Stein-type shrinkage idea: Efron & Morris 1975 |
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

# 3. Edit .env — at minimum set your API key and admin token
#    DATA_GOV_API_KEY=your-key-from-api-data-gov
#    ADMIN_TOKEN=your-secret-admin-token
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

# Fast tests only (skips the tests that load the sentence-transformer model)
docker compose run --rm --no-deps backend python -m pytest tests/ -v -m "not slow"

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
- Papke, L. & Wooldridge, J. (1996). Econometric Methods for Fractional Response Variables. *Journal of Applied Econometrics*, 11(6), 619-632.
- Poole, K. & Rosenthal, H. (1985). A Spatial Model for Legislative Roll Call Analysis. *AJPS*, 29(2), 357-384.
- Reimers, N. & Gurevych, I. (2019). Sentence-BERT. *EMNLP 2019*, 3982-3992.
- Snell, J. et al. (2017). Prototypical Networks for Few-Shot Learning. *NeurIPS 2017*, 4077-4087.
- Stratmann, T. (2005). Some Talk: Money in Politics. *Public Choice*, 124(1-2), 135-156.
- Tauberer, J. (2012). *Open Government Data*. GovTrack.us ideology/leadership methodology.
- Volden, C. & Wiseman, A. (2014). *Legislative Effectiveness in the United States Congress: The Lawmakers*. Cambridge UP.

## License

Licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).
Unlike a permissive license (MIT, Apache), AGPL requires that anyone who runs
a modified version of Civitas as a network service — not just anyone who
redistributes it — must also publish their modifications. That closes the
loophole a permissive license leaves open: someone could otherwise fork the
scoring algorithm, quietly bias it, and host it without ever disclosing what
changed. If you can't see the source, you can't trust the score — AGPL keeps
that true for every fork, not just this repository.
