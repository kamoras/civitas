# Search

Moved from the README. AGENTS.md states the rules; this page keeps the longer description and the measurements behind them.

## Site search

One box in the header searches everything with a page: members (and their
former ids and surnames), presidents, justices, bills (every spelling of a
number: "HR 1", "H.R.1", "hr1"), roll calls, agencies (name and acronym),
committees, states, districts ("TX-23", "Texas 23rd"), races, ballot
measures, Action Center issues and ongoing stories, nominations, federal
documents (by FR document number, FR citation, executive order,
proclamation or RIN, by title through Explore's own keyword index, and on
the results page by their words and meaning through Explore's hybrid
ranker), and the site's own pages (`backend/app/data/site_pages.json`, held equal to the
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
over an old one). No model, no LLM: this is all the autocomplete runs.

**One list on the results page.** Enter (or the form without JavaScript)
opens `/search?q=`, which ranks the matches above together with federal
documents from Explore's hybrid ranker (semantic, BM25F keyword, recency,
citation PageRank; see Hybrid Search below): `GET /api/search` runs
`hybrid_search` for the words and fuses its first 30 documents with the
matches by reciprocal rank (`site_search.fuse`), since a name-match score
and a fused RRF score are on different scales. Each result keeps its kind
("Members of Congress", "Bills", "Documents"), and filter chips narrow the
list to one kind. The Documents chip (`/search?tab=documents`) is what
Explore's search page was: the full document list with its filters (branch,
type, agency, newest first, open for comment, one member's record), the
coverage note and the not-held notes, rendered on the server from
`GET /api/explore` and working without JavaScript (a GET form). `/explore`
answers a 308 there with its query string (`q`, `agency`, `politician_id`,
`comment`, `type`, `sort`); every document keeps its page at
`/explore/{key}`, and `/api/explore/*`, the sitemaps and the public
`search_documents` endpoint and MCP tool are unchanged. The documents' half
costs a query encode (about a second uncached on the Pi), so it runs only on
the results page and never for a direct jump; nginx limits `/search`
requests per address with Explore search's own limit (the api-misses hop).
Its weights are fitted (`scripts/calibrate_search_fusion.py`, written to
`app/data/search_fusion_calibration.json`) on site search's known-item
probes and `evaluate_explore_search.py`'s document probes together, half
the targets fit and half held out, with no site-search probe type allowed
to lose more than a standard error. The fit keeps a gate: a match below
0.5 adds nothing, so a weak name match sits below the documents, and
strong matches stay above them (k 0.5, document weight 0.1). Held out, on a
copy of the production database (2026-10-20, 23,966 documents):

| Query type | Probes | MRR before | MRR after | In the top 10, before | after |
|---|---|---|---|---|---|
| Name, surname, acronym, identifier, typo, page, section words | 527 | 1.000 | 1.000 | 100% | 100% |
| Document title (site search's probe) | 74 | 0.919 | 0.919 | 91.9% | 91.9% |
| Document title words (Explore's probe) | 61 | 0.526 | 0.524 | 68.9% | 68.9% |
| Document's own FR number / citation | 35 | 0.938 | 0.938 | 100% | 100% |
| Paraphrase (the body's own frequent words) | 66 | 0 | 0.663 | 0% | 90.9% |
| Rare terms | 66 | 0 | 0.437 | 0% | 95.5% |
| Identifier cited in the text | 10 | 0.200 | 0.533 | 20% | 60% |
| Record citation / docket | 46 | 0 | 0.425 | 0% | 80.4% |

Before is the page as it was (the matches alone, documents by title only).
On the six real paraphrased queries for open proposed rules that started
this work ("fees for foreign students working after graduation", "pap
smear proficiency testing remote", ...), the results page found 0 before
and all 6 after (ranks 1, 1, 1, 2, 3 and 10); the three exact titles and
the FR document number still go straight to their documents.

**Each result is a box (2026-10).** The results page lists each match as a
compact record box (`BoxGrid` / `RecordBox`, one column on a phone and two
from 40rem): its kind as the kicker on the merged list, its name linked, and
the index's context line (type, issuer, date). A document's own words around
the query sit behind its "More" (`snippet` on each document item: Explore's
keyword excerpt with the matches marked, else its summary, verbatim). Once
something is asked the page's standing line folds behind "More about this
page" on a phone, and the filter chips are one row that scrolls sideways.
Measured on an iPhone SE (375 px) with the density audit's method, main
against this change on a local `next build` over the same database copy:
the first result moved up from 1.12 to 0.91 screens ("tariffs"), 1.16 to
0.94 ("medicaid") and 1.52 to 1.34 ("warren", under a close call), and the
first screen's prose fell from 34% to 21%. The pages are longer (6.4 to 8.3
screens for "tariffs"): each box is a line taller than the old row, and the
documents' "More" is new.

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
half; a jump is wrong when it lands anywhere but the probe's own page (an ambiguous query's other pages have probes of their own, so it still never jumps on one). The pipeline refits it itself (§3a's acceptance rule, AGENTS.md §3b). On a copy of the production index (the refit of 2026-10-23 in the calibration file), held out from the fit:

| Probe type | Probes | Right page first | Straight there | Wrong jump |
|---|---|---|---|---|
| Name | 403 | 100% | 98.5% | 0 |
| Surname | 309 | 100% | 81.2% | 1 |
| Acronym | 214 | 100% | 94.9% | 0 |
| Identifier | 417 | 100% | 100% | 0 |
| Typo | 408 | 99.5% | 96.6% | 0 |
| Page | 96 | 100% | 97.9% | 0 |
| Section words | 410 | 100% | 100% | 0 |
| Document title | 397 | 95.5% | 71.3% | 0 |
| Prefix (in the first 8) | 413 | 100% | | |

All 2,654: 92.3% straight to the right page with one Enter, one wrong jump (a surname); p50 9 ms, p95 42 ms per query in that run (the whole read path, Explore's title search included). "Straight there" counts every probe, an ambiguous one included (it lists, never jumps: two members of one surname, a document title other documents share). Against the calibration it replaced, on the same held-out probes (per probe, the reciprocal rank of its page plus 1 for a right jump), the refit gained 0.059 (standard error 0.005), most on typos (+0.25) and site pages (+0.10), with no type worse: it jumps at a margin of 0.02 where the old one needed 0.3. It became possible when the harness stopped counting a jump to the probe's own page as wrong: until then Record speeches titled "Illinois" or "ICE" made those names ambiguous and the full refit of 2026-10-22 turned jumping off (#1248).

**Privacy.** Nothing a reader types is kept. Answers are cached only in the
reader's browser (`Cache-Control: private`), never in nginx's cache on disk;
nginx's access log drops every query string and referrer, uvicorn's drops
query strings, and the `/search` page asks the backend with `no-store`.

## Hybrid search (Explore)

Explore's ranker searches primary-source government documents. Its page is
now site search's Documents filter (`/search?tab=documents`, see Site
search), and the results page fuses its ranking into the one list. It is not a
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

**Members and bills, beside the results** (removed 2026-10). Explore's typed groups (`services/explore_matches.py`, `GET /api/explore/matches`, `MatchGroups.tsx`) duplicated site search's own matching, and went when Explore's search page became site search's Documents filter: `/search` ranks members, bills, agencies and documents in one list (see Site search).

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

**The page leads with one box (2026-10).** Above the source text, which is
unchanged, a document's page opens with a record box (`BoxGrid` /
`RecordBox`): the title as the page's heading, a line the backend fills from
the record's fields ("Proposed Rule from the State Department, August 28,
2026 · 91 FR 55505", `api/explore.py` `document_headline`), the comment
period as a countdown while it is open (`commentWindow`, the same
`comment_periods.comment_window` the Action Center uses), "Submit your
comment", and chips for the agency, the speaker's record, the bills a
speech's Record heading names, the executive orders the text cites that have
a page here (`cited_orders`, read with the citation graph's own
`extract_citations`; the document's own number left out, six at most), the
official copy and "Cite". The box's grid is `#comment`, where every
"Comment" link lands, at the height of the panel it replaced. Measured on
an iPhone SE (375 px) with the density audit's method: an open rule went
from 5.7 to 5.3 screens, with `#comment` at 0.26 screens either way and
first-screen prose down from 20% to 16%. A rule's text moved up from 2.03
to 1.58 screens and a floor speech's from 1.83 to 1.32.

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
