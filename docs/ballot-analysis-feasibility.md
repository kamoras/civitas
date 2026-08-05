# Ballot analysis feature — feasibility research

**Status:** research only. No code changes proposed in this PR beyond this
document. **Date:** 2026-08-05. **Cycle in scope:** 2026 midterms
(election day 2026-11-03).

---

## 1. The question

Can Civitas show a visitor "what's on my ballot" for their state — a
non-partisan, plain-language digest of each decision and what it means —
with a link into per-race detail (funding etc.) and an external link to
the official ballot we sourced it from? And can the election landing page
stop dumping ~470 race cards on the visitor and instead route through a
per-state view?

Two separable asks, with very different risk profiles:

| Ask | Verdict |
|---|---|
| **A. Route the election feature through a per-state view** instead of a flat race directory | **Feasible now.** No new data sources, no new external dependency. Mostly a routing/IA change over data already in the database. |
| **B. Show the state's ballot with neutral summaries of each decision** | **Feasible in a deliberately narrowed form** — the *statewide* portion of the ballot (U.S. Senate seat, statewide ballot measures), sourced from official state materials, extractive rather than model-written. **Not feasible** as "your ballot" in the literal sense, at any budget, without an address lookup that conflicts with this project's no-exfiltration rule. |

Recommendation: **build A first as its own change** (it delivers most of
the perceived value and carries near-zero risk), then B in the phases in
§8, with the naming discipline in §5 applied from day one.

---

## 2. What already exists (so we don't rebuild it)

Roughly half of ask A is already shipped. Reading the current feature:

| Surface | File | What it does today |
|---|---|---|
| Action Center elections tab | `frontend/src/components/action/ElectionsTab.tsx` | Countdown, seats-up totals, clickable US map, per-state panel listing sitting senators + House district count, link out to `/elections?state=XX` |
| Election directory | `frontend/src/app/elections/page.tsx` | Map colored by PVI + **every race in the cycle as a card**, grouped by state header (the "million seats" problem — the grouping at `page.tsx:71-79` was already an attempt to tame it) |
| Race detail | `frontend/src/app/elections/[raceId]/RaceDetailClient.tsx` | Candidates, FEC fundraising (`RaceFinancials`), verbatim news/Bluesky coverage feed, PVI with provenance note |
| API | `backend/app/api/elections.py` | `/elections/races`, `/elections/races/{id}`, `/elections/candidates/{id}`, `/elections/pvi` |
| Ingestion | `backend/app/pipeline/election_pipeline.py` | FEC roster sync → prioritized financial refresh → coverage ingestion → Bluesky → snapshot |
| Calendar facts | `backend/app/election_calendar.py` | Statutory election day (2 U.S.C. §7), Senate class rotation, `seats_up_for_year()` |

So "click into a Senate seat that's up for grabs and see funding data"
**already works** — it is `/elections/2026-SEN-GA`. What's missing is the
intermediate state view, and the ballot content itself.

Equally important, the existing feature already encodes the editorial
posture this proposal needs. `RaceCoverageItem` (`backend/app/models.py:733`)
stores source text **verbatim, never model-generated, so the table carries
zero hallucination surface**. `_candidate_summary` returns `null` rather
than a fabricated `$0` for un-synced financials. `_pvi_for_race`
(`api/elections.py:26`) flags when a House race is showing a *statewide*
lean instead of silently blending it. A ballot feature that generates
prose about what a measure "means" would be the first surface in this
feature to break that pattern — which is the central design tension in §5.

---

## 3. The structural finding: there is no such thing as "your state's ballot"

This is the finding that should shape the product decision, so it goes
before the source survey.

A U.S. ballot is defined per **ballot style** — the unique combination of
contests a given voter is eligible to vote on. Multiple precincts may
share a style, and a single precinct may contain multiple styles wherever
it is split by a district boundary
([NIST Election Glossary](https://pages.nist.gov/ElectionGlossary/)).
Styles are produced county by county: [Ellis County, TX](http://www.elliscountytx.gov/1206/Individual-Sample-Ballots-by-Style)
published 36 distinct sample ballots for a single joint election. Across
~3,100 counties, the national count is in the tens of thousands.

What is genuinely **uniform across a whole state** in a midterm:

- The U.S. Senate contest, when that state has one (regular class seat or
  a special) — already modeled as `Race`.
- **Statewide ballot measures** — 85 certified in 35 states for 2026 as of
  April 1 per [Ballotpedia's 2026 scorecard](https://ballotpedia.org/Ballot_Measure_Scorecard,_2026).
- Statewide executive offices, where held — 36 governorships, 31 lieutenant
  governors, 30 attorneys general, 26 secretaries of state in 2026
  ([Ballotpedia](https://ballotpedia.org/State_executive_official_elections,_2026)).
  **Civitas has no data on any of these** (no state-office model, no state
  candidate source). A "state ballot" view that omits the governor's race
  in 36 states is conspicuously incomplete.

What is **not** uniform, and therefore cannot appear on a state page
without lying to somebody: U.S. House district, state senate/house
district, county offices, municipal offices, judicial retention questions,
school board, local measures, and any local option question.

The honest framing, then, is a **statewide ballot digest**, not "your
ballot." Everything else routes to the state's own sample-ballot lookup
(§7.4). Ballotpedia's own address tool exists precisely because this
problem cannot be solved at the state level
([Sample ballot lookup tools](https://ballotpedia.org/Sample_ballot_lookup_tools)).

---

## 4. Data source survey

Assessed against four constraints this project actually has: free or
near-free; machine-readable or scriptable; license compatible with an
AGPL-3.0 open-source deployment; and **no user PII leaving the box**
(README, "Why Local Inference": *"Senator donor records, promise
evaluations, and issue analyses never leave the local network"*).

| Source | Gives us | Cost / license | Verdict |
|---|---|---|---|
| **FEC API** (integrated: `pipeline/fetch/fec.py`) | Federal candidates + money | Free, US-gov public domain | ✅ Already the backbone for Senate/House contests |
| **Google Civic Information API** — `elections.voterInfoQuery` ([docs](https://developers.google.com/civic-information/docs/v2/elections/voterInfoQuery)) | Per-address ballot: contests, candidates, **referendums** (with `referendumBallotResponses`), polling places | Free; 25,000 queries/day, 2,500/100s ([Using the API](https://developers.google.com/civic-information/docs/using_api)) | ❌ **as a primary source.** Three independent disqualifiers: (1) it is keyed on **the voter's registered address** — sending visitor addresses to Google is the exact thing this platform's architecture exists to avoid; (2) platform risk — Google already [turned down the Representatives endpoint in April 2025](https://groups.google.com/g/google-civicinfo-api/c/9fwFn-dhktA), and developers report the elections endpoint [returning polling locations with no contests or candidates](https://groups.google.com/g/google-civicinfo-api/c/N04wrZKPekc); (3) VIP-backed contest data typically materializes only close to election day and coverage varies by state |
| **CTCL Ballot Information Project** ([page](https://www.techandciviclife.org/our-work/civic-information/our-data/ballot-information/)) | The dataset behind much of Civic Info's ballot content; JSON/XML/TSV; sliding-scale access for nonpartisan nonprofits | Free–low for mission-aligned orgs | ❌ CTCL's own site indicates **BIP is no longer being updated as of January 2026** (only the officeholder "Governance Project" continues). Confirm before discarding, but do not design on it |
| **Ballotpedia** — API v3 / Sample Ballot Lookup Tool | Best single-vendor coverage: measures, official titles, summaries, support/oppose, local coverage | API in the **thousands of dollars/month**; cheapest option a **$600 one-time CSV**; text is **GFDL**; ToU **prohibits automated extraction** without a written license ([Buy Political Data](https://ballotpedia.org/Ballotpedia:Buy_Political_Data), [Copyrights](https://ballotpedia.org/Ballotpedia:Copyrights)) | ❌ Cost and license both incompatible. GFDL text pulled into an AGPL codebase creates a licensing tangle, and scraping is explicitly barred. Usable only as a **human-checked reference during development**, and as an outbound link |
| **BallotReady / Cicero** | Commercial officeholder + candidate + ballot data | Paid | ❌ Same reason |
| **Official state materials** — Secretary of State ballot-measure pages and voter guides | **The authoritative artifact**, and in many states an *official impartial analysis*: California's [LAO ballot analyses](https://lao.ca.gov/ballotanalysis) (mandated by Prop 9, 1974), Colorado's Blue Book, Washington's [proposed measure page](https://www.sos.wa.gov/elections/voters/proposed-ballot-measure-information) with ballot title, full text, explanatory statement and fiscal impact | Free. Ballot titles, measure text and official explanatory statements are legislative/official material — the government edicts doctrine bars copyright in laws and official legal texts ([*Georgia v. Public.Resource.Org*, 2020](https://www.supremecourt.gov/opinions/19pdf/18-1150_7m58.pdf)); state works are otherwise *not* automatically public domain the way federal works are, so per-state confirmation is required | ✅ **The right source for the summary text itself.** Cost: ~50 bespoke adapters, mostly HTML and PDF, no common schema |
| **Wikipedia** — [2026 United States ballot measures](https://en.wikipedia.org/wiki/2026_United_States_ballot_measures) | Curated table of certified statewide measures: state, number, origin (initiative vs. legislative referral), subject, status | CC BY-SA 4.0 | ✅ **Viable index/seed.** Direct precedent in this repo: `backend/scripts/fetch_district_pvi.py` already scrapes Wikipedia infoboxes for district PVI |
| **NCSL** [Statewide Ballot Measures Database](https://www.ncsl.org/elections-and-campaigns/statewide-ballot-measures-database) | Every statewide measure, 50 states + DC, by year/state/topic | Free; PowerBI embed, no documented public API | ✅ As a **cross-check** for the index (the same adversarial two-source gating `fetch_state_pvi.py` applies to PVI) |
| **Open States / Plural** [API v3](https://docs.openstates.org/api-v3/) | State bills incl. legislatively-referred measures, full text search | Free with API key | ⚠️ Partial — covers referrals, not citizen initiatives; useful for the *legislative history* of a referred measure |
| **FollowTheMoney / OpenSecrets** ballot-measure committee finance | Money for/against measures (state-level committees, not FEC) | Free-ish | ⚠️ Out of phase-1 scope; the obvious "funding data" analog to candidate financials for measures |

**Net:** there is no free, comprehensive, machine-readable, license-clean
ballot API. There *is* a workable composition: **Wikipedia + NCSL for the
index of what's on the ballot, official state pages for the authoritative
text and impartial analysis, FEC for federal candidate money** — all
already-precedented source types for this codebase.

---

## 5. Neutrality: the hard part, and why it is mostly not an LLM problem

"Non-biased summary of what's on your ballot" reads like a summarization
task. It should not be built as one.

**The evidence against generating it.** The AI Democracy Projects (Proof
News + IAS) had election officials and experts grade five frontier models
on voter questions: **more than half of responses were rated inaccurate**,
and a follow-up round found [27% of 2024 election questions answered
wrong](https://www.nbcnews.com/tech/tech-news/ai-chatbots-got-questions-2024-election-wrong-27-time-study-finds-rcna155640).
Those were frontier models. Civitas runs **LFM2.5-1.2B** with a 2,048-token
working budget (README, "Why Embedding-First, LLM-Sparingly"). The
existing `pipeline/analyze/grounding.py` exists because this exact model
already invented a fictional date and attributed a fabricated quote to a
senator who appeared nowhere in the source material. Ballot content is a
strictly higher-stakes surface than a Bluesky post.

**The evidence for doing *something*.** Raw ballot language is genuinely
inaccessible. Ballotpedia's readability project puts the average
Flesch-Kincaid grade level of ballot titles at **19 in 2022 and 16 in
2024** — graduate and bachelor's level respectively — ranging from 8
(Alaska) to 42 (Connecticut)
([2022](https://ballotpedia.org/Ballot_measure_readability_scores,_2022),
[2024](https://ballotpedia.org/Ballot_measure_readability_scores,_2024)).
Reilly & Richey found voters **skip** measures whose titles read harder.
Reprinting the ballot title alone would technically be neutral and
practically useless.

**The resolution — a tiered ladder, each rung shipped and evaluated
before the next:**

1. **Verbatim official text (zero hallucination surface).** Ballot number,
   official ballot title/question, official summary, and fiscal impact
   statement, quoted and attributed, with a link to the state's own page.
   This is exactly the `RaceCoverageItem` precedent applied to measures.
2. **Structured extraction, still verbatim.** "A YES vote means… / A NO
   vote means…" — most official guides publish this framing themselves;
   lift it, don't write it. Where a state doesn't publish it, render
   nothing rather than infer.
3. **Plain-language layer (optional, gated, last).** The local model used
   *only* to compress official text that already exists — never to
   characterize, never to evaluate, never to fill a gap. Enforced by:
   - existing checks — `ungrounded_numbers`, `ungrounded_titled_names`,
     `ungrounded_statistics` — run against the official text as source;
   - a **new** no-new-claims check in the same deterministic spirit:
     reject output containing a content word absent from the source
     (a tightened analog to the existing surname/number checks);
   - reject-and-fall-back-to-verbatim on any failure, never retry-until-
     it-passes;
   - readability measured before/after (FKGL), so the layer has to *earn*
     its risk with a measurable drop;
   - the output cached in `AnalysisCache` with a prompt version, so a
     prompt change invalidates every summary rather than leaving a mix.

**Arguments for and against a measure**: only if quoted from the state's
own official voter guide (where the arguments are statutorily solicited and
published), always in pairs, always length-capped symmetrically, always
labeled as proponents'/opponents' claims rather than fact. **Never**
ingested from advocacy sites or campaign material. If a state publishes
only one side, publish neither.

**Framing discipline**, borrowed from how PVI is already labeled
(`PviMethodologyNote.tsx`, `get_pvi_meta`): every measure carries a
source, an as-of date, and a "this is the statewide portion of the ballot;
your actual ballot includes local contests not shown here" note. The page
must never be titled "your ballot."

---

## 6. Data volume and hardware fit

Trivial, unusually for this codebase. ~85–110 statewide measures per
cycle nationwide, versus ~6,900 FEC candidate records the election
pipeline already handles with a rate-limited 500/run batch. Even fetching
and re-parsing every state's measure page nightly is a rounding error
against the existing FETCH phase. The optional plain-language layer adds
at most ~100 LLM calls **per cycle** (cached), against the 100–400 per
nightly run the platform already does.

**Volatility, however, is high, and that is the real cost.** Measures
qualify, get renumbered, and get pulled continuously: courts disqualified
16 certified measures over the past decade
([Ballotpedia](https://ballotpedia.org/List_of_certified_state_ballot_measures_removed_from_the_ballot_by_courts)),
and in **July 2026 alone** four measures were newly certified in
California while seven were removed in California and North Dakota
([Ballotpedia News, 2026-07-08](https://news.ballotpedia.org/2026/07/08/over-the-past-two-weeks-four-new-ballot-measures-were-certified-in-california-and-seven-measures-were-removed-in-california-and-north-dakota/)).
A stale ballot digest telling someone to vote on a measure a court struck
is worse than no digest. Implications: measures need a `status` field
(certified / removed / withdrawn) rendered explicitly, an as-of date on
every card, a refresh cadence that tightens inside
`is_election_season()`'s window (the same switch `scheduler.py` already
makes for coverage), and removed measures shown as removed rather than
silently deleted.

---

## 7. Proposed shape

### 7.1 Information architecture

```
/elections                     → STATE INDEX (map + 50-state grid). No race cards.
  └ /elections/state/[ST]      → STATE BALLOT DIGEST  ← new
      ├ Federal section        → Senate contest (if any) + House districts list
      │   └ /elections/[raceId]      → existing race detail: candidates, FEC money, coverage
      ├ Statewide measures     → per-measure card: number, official title, plain summary,
      │   │                       yes/no meaning, fiscal note, status, as-of date
      │   └ /elections/measure/[id]  → full official text, fiscal analysis, official link
      └ "Not shown here"       → link to the state's OFFICIAL sample-ballot lookup
```

The landing page stops rendering ~470 cards; the per-state page renders
the 1–2 Senate contests, a collapsed district list, and that state's
measures. This is the user-visible fix to "a million seats up for grabs."

### 7.2 Backend

New model, following existing conventions (readable composite id like
`Race`, source's own identifiers preserved, `NULL` never standing in for
zero):

```python
class BallotMeasure(Base):          # id e.g. "2026-CA-PROP-50"
    cycle_year, state, number, title            # official ballot number + title, verbatim
    measure_type, origin                        # constitutional amendment / statute; initiative / referral
    status                                      # certified | removed | withdrawn  (see §6)
    official_title, official_summary            # verbatim, source-attributed
    fiscal_impact                               # verbatim where published
    yes_means, no_means                         # verbatim/extracted; NULL when the state publishes none
    plain_summary, plain_summary_version        # phase 3 only; NULL until it passes grounding
    source_url, source_name, as_of              # provenance, always rendered
```

Endpoints, mirroring `api/elections.py`'s existing camelCase-dict style
and `cached_json` TTLs:

- `GET /elections/states/{state}/ballot` — the digest: races + measures +
  official-lookup link + as-of.
- `GET /elections/measures/{id}` — full detail.

Ingestion: a new phase in `election_pipeline.py` (or a sibling module
`pipeline/fetch/ballot_measures.py` + `analyze/ballot_measures.py`),
gated so a failure isolates like every other phase there does.

### 7.3 The index, generated like PVI is

`fetch_state_pvi.py` is the template to copy, and its gating discipline is
the point: two independent sources, cross-agreement thresholds, continuity
checks against the previously committed file, and `exit 1` rather than
trusting a bad parse. Applied here: build the measure index from Wikipedia
**and** NCSL, require the state/number sets to agree, and fail loudly on
divergence rather than publishing a half-parsed ballot. Ship it as
`backend/app/data/ballot_measures_2026.json` with `_source` / `_method` /
`_as_of` keys, read through an accessor with a `get_*_meta()` companion —
the same provenance path `get_pvi_meta()` already established.

### 7.4 The official-ballot link (explicitly requested)

A 50-entry `backend/app/data/state_ballot_lookup.json`: state code → the
Secretary of State's own sample-ballot / voter-information lookup URL,
plus label and as-of. Small, hand-curated, and the single highest
value-per-line item in this whole proposal — it is what makes the
"statewide only" scoping honest. It should be rendered on the state page
whether or not that state has any measures, and phase 0 can ship it alone.

---

## 8. Phasing and effort

| Phase | Scope | Rough effort | Risk |
|---|---|---|---|
| **0 — IA restructure** | `/elections` becomes a state index; new `/elections/state/[ST]` assembling data **already in the DB**; official-lookup link table; Action Center tab links through it | ~1 focused PR, frontend-heavy + one endpoint + one data file | Low. No new external dependency. Watch the App Router query-string traps documented in AGENTS.md (`router.replace` on prerendered routes) |
| **1 — Measure index** | `BallotMeasure` model, Wikipedia+NCSL generator with fidelity gates, verbatim official title + status + source link on the state page | ~1 PR + a generator script with real fidelity gating | Medium. Parser brittleness; volatility handling (§6) |
| **2 — Official summaries** | Per-state adapters for states publishing structured impartial analyses (CA, CO, WA, OR, MO, OH, … — a minority of states carrying a majority of measures), fiscal impact, yes/no meaning | Incremental, one state at a time; each adapter is small and independently shippable | Medium. Ongoing maintenance as state sites change |
| **3 — Plain-language layer** | Grounded local-LLM compression of official text, new no-new-claims check, FKGL before/after eval, `AnalysisCache`-backed | Small code, **large** review burden | **High.** Only rung that can produce a wrong statement about a ballot decision. Ship only if phase 2's text is demonstrably too hard to read and the eval shows a real readability gain with zero grounding failures |
| **4 — optional** | Measure committee finance (FollowTheMoney), state executive contests | Not scoped here | — |

Phases 0 and 1 are independently valuable and independently shippable.
Phase 3 is genuinely optional and should be treated as reversible.

---

## 9. What I'd recommend against

- **Any address-based lookup.** It is the only route to a true personal
  ballot, and it requires shipping visitor addresses to a third party.
  That trades away the platform's defining property for a feature that
  can be approximated honestly at the state level.
- **Licensing Ballotpedia data.** Right data, wrong terms and wrong
  budget for an AGPL project. Link to them instead.
- **Letting the model write "what this measure means" from the title
  alone.** That is the exact thin-fact-set condition under which this
  model has already fabricated content (`grounding.py`'s own comments).
- **Calling the page "your ballot."** §3. Call it what it is: the
  statewide portion, with a link to the real thing.

---

## 10. Open questions for the maintainer

1. **State executive contests** — a 2026 state page with no governor's
   race in 36 states is a visible hole. Scope them in (new data source,
   real work) or scope them out explicitly in the page's own copy?
2. **How far down the ladder in §5 do we want to go?** Phases 0–2 keep
   the platform's zero-hallucination-surface property intact for this
   feature. Phase 3 knowingly spends some of it for readability.
3. **Coverage floor for launch** — ship the state view for all 50 states
   with measures only where we have clean data (uneven but honest), or
   hold until N states are covered?
4. **Off-cycle behavior** — what does `/elections/state/XX` show in an odd
   year, when a handful of states still hold measure elections?

---

## 11. How this was researched, and what to re-verify

Codebase claims here were read directly from the files cited. External
claims come from web search result summaries: **direct page fetches were
blocked by this environment's network policy (HTTP 403 via the agent
proxy on every outbound host)**, so no source page below was retrieved
and read in full. Before any of this becomes engineering commitment,
re-verify against the primary sources — specifically:

- CTCL's Ballot Information Project end-of-updates status (Jan 2026).
- Whether Google Civic Info `voterInfoQuery` remains supported for 2026,
  and its actual per-state contest coverage.
- Ballotpedia's current API pricing and the exact terms on the $600 CSV.
- Which states publish an official impartial analysis in a scrapable
  form, and each one's reuse terms — the government edicts doctrine
  covers the ballot text and official legal material, but state-published
  *commentary* is not uniformly free to redistribute.

## Sources

- [Google Civic Information API — voterInfoQuery](https://developers.google.com/civic-information/docs/v2/elections/voterInfoQuery) · [Using the API (quotas)](https://developers.google.com/civic-information/docs/using_api) · [Notice of Turndown of the Representatives API](https://groups.google.com/g/google-civicinfo-api/c/9fwFn-dhktA) · [Contests/candidates missing report](https://groups.google.com/g/google-civicinfo-api/c/N04wrZKPekc)
- [CTCL — Ballot Information Project](https://www.techandciviclife.org/our-work/civic-information/our-data/ballot-information/)
- [Voting Information Project](https://www.votinginfoproject.org/about) · [Democracy Works](https://www.democracy.works/voting-info-project)
- [Ballotpedia — Buy Political Data](https://ballotpedia.org/Ballotpedia:Buy_Political_Data) · [Copyrights](https://ballotpedia.org/Ballotpedia:Copyrights) · [Reusing content](https://ballotpedia.org/Ballotpedia:Reusing_Ballotpedia_content) · [2026 Ballot Measure Scorecard](https://ballotpedia.org/Ballot_Measure_Scorecard,_2026) · [State executive elections 2026](https://ballotpedia.org/State_executive_official_elections,_2026) · [Measures removed by courts](https://ballotpedia.org/List_of_certified_state_ballot_measures_removed_from_the_ballot_by_courts) · [2026-07-08 certification/removal roundup](https://news.ballotpedia.org/2026/07/08/over-the-past-two-weeks-four-new-ballot-measures-were-certified-in-california-and-seven-measures-were-removed-in-california-and-north-dakota/) · [Readability 2022](https://ballotpedia.org/Ballot_measure_readability_scores,_2022) · [Readability 2024](https://ballotpedia.org/Ballot_measure_readability_scores,_2024) · [Sample ballot lookup tools](https://ballotpedia.org/Sample_ballot_lookup_tools) · [Features of official voter guides by state](https://ballotpedia.org/Features_of_official_voter_guides,_compared_by_state)
- [NCSL Statewide Ballot Measures Database](https://www.ncsl.org/elections-and-campaigns/statewide-ballot-measures-database)
- [Wikipedia — 2026 United States ballot measures](https://en.wikipedia.org/wiki/2026_United_States_ballot_measures) · [2026 gubernatorial elections](https://en.wikipedia.org/wiki/2026_United_States_gubernatorial_elections) · [Government edicts doctrine](https://en.wikipedia.org/wiki/Government_edicts_doctrine)
- [California LAO — ballot analyses](https://lao.ca.gov/ballotanalysis) · [California SoS — ballot measures](https://www.sos.ca.gov/elections/ballot-measures) · [Washington SoS — proposed ballot measure information](https://www.sos.wa.gov/elections/voters/proposed-ballot-measure-information)
- [Open States API v3](https://docs.openstates.org/api-v3/)
- [NIST Election Glossary — ballot style / precinct split](https://pages.nist.gov/ElectionGlossary/) · [Ellis County, TX — sample ballots by style](http://www.elliscountytx.gov/1206/Individual-Sample-Ballots-by-Style)
- [*Georgia v. Public.Resource.Org* (2020)](https://www.supremecourt.gov/opinions/19pdf/18-1150_7m58.pdf)
- [IAS — AI chatbots found inaccurate answering voter queries](https://www.ias.edu/news/ai-chatbots-found-inaccurate-answering-voter-queries) · [NBC News — chatbots wrong 27% of the time](https://www.nbcnews.com/tech/tech-news/ai-chatbots-got-questions-2024-election-wrong-27-time-study-finds-rcna155640) · [CNBC — officials warn chatbots unreliable for voting info](https://www.cnbc.com/2024/11/01/ai-chatbots-arent-reliable-for-voting-questions-government-officials.html)
