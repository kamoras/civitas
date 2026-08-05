# Ballot analysis feature — feasibility research

**Status:** research only. No behavior change in this PR.
**Date:** 2026-08-05. **Revision 2** — rewritten after five adversarial
reviews and a round of live testing; see §13 for what changed and §12 for
what was actually run. **Cycle in scope:** 2026 midterms.

---

## 1. The question, and the verdicts

Can Civitas show a visitor a non-partisan digest of what's on their
ballot, with per-race detail (funding) and a link out to the official
ballot? And can the election landing page stop dumping ~470 race cards and
route through a per-state view instead?

| Ask | Verdict |
|---|---|
| **A. Route the election feature through a per-state view** | **Feasible.** But the delta is smaller than it looks — `/elections?state=XX` already filters — and it is gated on two pre-existing frontend defects in the files being restructured (§2.1). Two PRs, not one. |
| **B. Statewide ballot digest with neutral summaries** | **Feasible with a hard scope change.** Not "your ballot" (§3), not "the statewide ballot" either (§3.3), and not for an unspecified election — every surface must name *which* election it describes (§4). Rungs 1–2 of §6 only. |
| **C. LLM-written plain-language summaries** | **Recommended against.** Revision 1 had this as an optional last phase. The review found a class of error — polarity inversion — that no check in `grounding.py` can detect *by construction*, and found that the safety gate proposed for it selects against its own goal (§6.4). |

**Every verdict below is provisional on §11.** External claims here come
from web-search summaries; direct page fetches are blocked in the
authoring environment. Revision 1 presented these as settled and got
several wrong — see §13.

---

## 2. What already exists

| Surface | File | What it does |
|---|---|---|
| Action Center elections tab | `frontend/src/components/action/ElectionsTab.tsx` | Countdown, seats-up totals, clickable US map, per-state panel, links to `/elections?state=XX` |
| Election directory | `frontend/src/app/elections/page.tsx` | PVI-colored map + every race as a card, grouped by state; **already filters to one state via `?state=XX`** |
| Race detail | `frontend/src/app/elections/[raceId]/RaceDetailClient.tsx` | Candidates, FEC fundraising, verbatim coverage feed, PVI note |
| API | `backend/app/api/elections.py` | `/api/elections/races`, `/races/{id}`, `/candidates/{id}`, `/pvi` |
| **Per-state assembler (already exists)** | `backend/app/api/action.py:769` `get_election_info` | Returns per-state `hasSenateRace` / `hasHouseRace` / `houseDistricts` / `senators`, plus `nextElection` and seats-up totals |
| Ingestion | `backend/app/pipeline/election_pipeline.py` | FEC roster → prioritized financial refresh → coverage → Bluesky → snapshot |

Two consequences revision 1 missed:

- **"Click into a Senate seat and see funding data" already works** —
  `/elections/2026-SEN-GA`, verified live in §12.
- **A third per-state assembler would be one too many.** `action.py:790-796`
  already merges special-election states specifically so the teaser and
  `/api/elections` "don't disagree about which states have a Senate race
  (2026-07 review F16)." Adding `/elections/states/{ST}/ballot` as a third
  view of the same facts makes that a three-way agreement problem. Extend
  one of the existing two.

### 2.1 Two pre-existing defects inside the blast radius

Both are in files ask A rewrites, so under AGENTS.md's working agreement
("fix the class, not the instance") they are phase-0 scope, not risks to
"watch."

- **`frontend/src/app/elections/page.tsx:36-40` uses `router.replace()` for
  a search-param-only update on a statically prerendered route** — the trap
  AGENTS.md:722-731 documents, and `action/page.tsx:696-707` deliberately
  avoids ("Deliberately NOT router.replace()"). **Reproduced live against a
  production build** (§12.3): load `/elections?state=GA`, click California
  on the map, and the URL stays `?state=GA` while the heading still reads
  "GA — R+1". The map goes dead. The trigger condition — arriving with a
  query string — is exactly what `ElectionsTab.tsx:138` links to.
- **`frontend/src/app/compare/page.tsx:443` has the same call shape.** Same
  class, different page.

Not confirmed: a claim that `Navbar.tsx`'s bare `href="/elections"` suffers
the stale-search restore (AGENTS.md trap 3). Tested with full soft
navigation and it behaved correctly — all races, clean URL (§12.3). Left
alone.

---

## 3. There is no such thing as "your state's ballot"

### 3.1 Ballots are per ballot style, not per state

A ballot is defined by **ballot style** — the unique combination of
contests a voter is eligible for. Precincts split across district
boundaries carry multiple styles ([NIST Election
Glossary](https://pages.nist.gov/ElectionGlossary/)); [Ellis County,
TX](http://www.elliscountytx.gov/1206/Individual-Sample-Ballots-by-Style)
published 36 for one joint election. Nationally: tens of thousands.

### 3.2 What is actually uniform statewide

- The U.S. Senate contest, when the state has one.
- **Statewide ballot measures** — **149 certified in 39 states for 2026 as
  of 2026-07-22**, still climbing
  ([Ballotpedia](https://news.ballotpedia.org/2026/07/23/over-the-past-two-weeks-10-new-ballot-measures-were-certified-in-three-states-idaho-massachusetts-and-washington/)).
  (Revision 1 used 85/35 from April 1 — a mid-certification snapshot read
  as a cycle total. 2022 finished at 140.)
- Statewide executive offices: 36 governorships, 31 lt. governors, 30 AGs,
  26 secretaries of state in 2026
  ([Ballotpedia](https://ballotpedia.org/State_executive_official_elections,_2026)).
  **Civitas has no data for any of them** — confirmed by grep: no model, no
  endpoint, no data file.
- Statewide judicial retention questions, in states that hold them.
  (Revision 1 filed these under "not uniform." Wrong.)

### 3.3 So the honest label is not "the statewide ballot" either

Revision 1's framing device — call it "the statewide portion" — is itself
an overclaim, because the page will omit governor, lt. governor, AG, and
SoS, all of which are statewide. The label has to be **enumerated, not
categorical**, and rendered above the content:

> Shows: U.S. Senate and House contests and statewide ballot measures for
> the November 3, 2026 general election. Not shown: governor and other
> state executive contests, state legislature, judicial contests, county
> and municipal offices, and local measures.

Retitle to **"Federal contests and statewide measures"** — checkable, and
it stops promising the thing §3.2 says is missing.

---

## 4. The election-identity problem (new in revision 2)

Revision 1 had no concept of *which* election the page describes. Every
date in it was 2026-11-03, from `election_calendar.next_election_day()`,
which computes the **federal general** only. That is wrong for most of the
year and structurally wrong in several states.

- **Primaries.** This doc is dated 2026-08-05 — peak primary season. A
  Missouri voter on 2026-08-03 has a *party primary* ballot, not the
  November one, and Missouri routinely puts constitutional amendments on
  the August primary ballot. ~14 states run **closed** primaries, so a
  unified cross-party candidate list is not a thing that voter can vote.
- **`is_election_season()` doesn't cover any of it.** It is 60 days before
  the *federal general* (`api/action.py:750-765`) — 2026-09-04 onward. Every
  primary, every odd-year measure election, and every Louisiana date falls
  outside it. Verified live: `/api/action/elections` returns
  `daysUntil: 90, isElectionSeason: false` today (§12.2). Revision 1
  proposed keying the measure refresh to this window; that would have left
  measures refreshing at the slowest cadence during the fastest churn.
- **Contest method varies and changes what a contest *means*.** Louisiana's
  jungle primary is held *on general election day* and may not fill the
  seat; Georgia federal contests go to a December runoff without a
  majority; Maine and Alaska use RCV (Alaska with a top-four primary);
  California and Washington use top-two, so two November candidates can
  share a party. `RaceCard.tsx:26-27` already has the right instinct
  ("`·` not `vs.` — pre-primary, top-2-by-cash is often not the
  general-election matchup"); a page titled as a ballot discards it.

**Required, not optional:** `election_date` and `election_type`
(`primary | runoff | general | special`) are first-class on every payload
and rendered in every heading; `contest_method`
(`plurality | majority_runoff | rcv | top_two | top_four | jungle`) renders
a one-line note per contest. Both come from a hand-curated
`state_election_dates_2026.json` in the same shape as §8.4's lookup table.
Measure refresh cadence derives from `min(election_date)` over
un-passed measures, **not** from `is_election_season()`.

---

## 5. Data sources — revised

Revision 1's net finding ("no free, comprehensive, machine-readable,
license-clean ballot API exists") was reached without surveying several
free sources, and rejected one source on a legal reading that doesn't hold.
Both errors pushed toward the most expensive plan (50 bespoke adapters).

| Source | Gives us | Terms | Verdict |
|---|---|---|---|
| **FEC** (integrated) | Federal candidates + money | Free, US-gov PD | ✅ In use |
| **[Vote Smart API](https://www.votesmart.org/votesmart-api)** | **A dedicated [Ballot Measures class](https://api.votesmart.org/docs/Measure.html)** — measureId, code, title, summary, election date, outcome, by state and year. Free API key. 2026 data is live on their site. | Nonprofit, nonpartisan since 1988 | ✅ **Evaluate first.** Directly contradicts revision 1's "no free structured source" finding. Unverified: current coverage depth and whether summaries are their prose (attribution/licensing) or the state's |
| **[Democracy Works Elections API](https://data.democracy.works/ballot-info)** | Explicitly includes statewide ballot measures; they run VIP | Nonprofit; terms unknown | ✅ Evaluate. Also the source of the key fact below |
| **[BallotAPI (Open Austin)](https://github.com/open-austin/ballotapi)** | REST API + DB of ballot info, **released into the public domain** | PD — cleanest possible | ⚠️ Verify liveness and coverage before counting on it |
| **Official state materials** (SoS pages, voter guides, [CA LAO](https://lao.ca.gov/ballotanalysis), CO Blue Book, [WA SoS](https://www.sos.wa.gov/elections/voters/proposed-ballot-measure-information)) | The authoritative text and official impartial analyses | Mixed — see §7 | ✅ Authoritative source for the text, but **not uniformly free to redistribute** and **not uniformly PDF-scraping**: [CA runs a public SoS API portal](https://calicodev.sos.ca.gov/), CO Legislative Council maintains a structured database. Revision 1's "~50 bespoke HTML/PDF adapters" was asserted, not checked |
| **[Ballotpedia](https://ballotpedia.org/Ballotpedia:Copyrights)** | Best coverage: measures, titles, summaries, support/oppose, local | GFDL (a **free copyleft** license permitting reuse with attribution); the ToU bars automated extraction **"for commercial purposes"** | ⚠️ **Reclassified from ❌.** Revision 1 dropped "for commercial purposes" from the scraping clause and treated GFDL *content* as incompatible with *AGPL code* — a category error, since the very next row accepts CC BY-SA Wikipedia content on identical logic. A non-commercial civic deployment may well be permitted. Get it in writing before relying on it. Pricing figures ("thousands/month", "$600 CSV") trace to third-party write-ups, **not** the cited Ballotpedia page |
| **[Wikipedia](https://en.wikipedia.org/wiki/2026_United_States_ballot_measures)** | Certified-measure tables | CC BY-SA 4.0 | ⚠️ Discovery hint only (§8.3) — and if used, limit the derivation to uncopyrightable facts (state, number, subject, status), lift no prose, and carry attribution in `_source` |
| **[NCSL](https://www.ncsl.org/elections-and-campaigns/statewide-ballot-measures-database)** | All statewide measures, historical | Free; **PowerBI embed, no documented API** | ⚠️ Probably not scrapable by `urllib` + regex. Revision 1 made it a *hard gate* (§8.3) |
| **[BISC Hot Sheet](https://ballot.org/the-hot-sheet/)**, **[MultiState](https://www.multistate.us/elections/ballot-measures-2026)** | Independent 2026 trackers; BISC had 213 tracked / 127 certified at a date Ballotpedia showed 139 | Free | ✅ Third cross-check — and the *disagreement* is the point |
| **Google Civic Info `voterInfoQuery`** | Per-address ballot incl. referendums; 25,000/day | Free + API key | ❌ **Any address the user types leaves the box** — that is the objection, and it stands. (Revision 1 said "registered address"; the API performs no registration check, and a maintainer who tested it would have found the claim false and discounted the sound privacy point with it.) Only the *Representatives* endpoint was retired (April 2025); elections endpoints are live |
| **CTCL Ballot Information Project** | — | — | ❌ Not updated since Jan 2026 — and it was a **candidate** dataset, not ballot measures |

**The causal link revision 1 missed:** BIP was the dataset feeding Google
Civic's ballot content, and Democracy Works now states its ballot content
is *provided by Ballotpedia*. The free ballot-content layer didn't
degrade independently in three places — it consolidated behind a
commercial vendor. That is the real argument for sourcing from states
directly, and it's stronger than "developers report missing contests."

**Revised net finding:** free structured sources plausibly exist (Vote
Smart, Democracy Works, BallotAPI) and **must be evaluated before** any
50-adapter estimate is credible. The 50-adapter plan is the fallback, not
the plan.

---

## 6. Neutrality — substantially revised

### 6.1 Verbatim official text is *not* the same as neutral

Revision 1's load-bearing premise was that quoting official text gives
neutrality for free. It gives *zero hallucination surface*, which is a
different property. Ballot titles are among the most litigated documents
in election law **precisely because they are contested**: Utah's Amendment
D (2024) was voided by a court that found the legislature's own ballot
language counterfactual; Ohio Issue 1 (2023), Missouri Amendment 3 (2024),
and Florida Amendment 4's state-authored financial impact statement (2024)
were all litigated over official wording.

Reprinting a legislature-drafted title under a masthead that says
"non-partisan, from official sources" launders one branch's framing
through Civitas's neutrality. **Disclosing authorship is more neutral than
the bare quote**, so the schema must carry it: `title_authority`,
`fiscal_authority`, `title_status`
(`as_printed | court_modified | under_challenge`), `challenge_note`,
`challenge_source_url`. And `status` needs `under_appeal` — a flat
`removed` during a pending appeal is itself a false statement.

### 6.2 Symmetric argument pairs manufacture false balance

Revision 1: arguments "always in pairs, length-capped symmetrically… If a
state publishes only one side, publish neither." Three problems:

- **Symmetry distorts.** A $40M campaign's argument and one retiree's
  rebuttal get equal weight, asserting a debate that may not exist.
- **"Publish neither" is exploitable** — decline to file and Civitas goes
  quiet on that measure, which drops exactly the most lopsided ones.
- **These are campaign documents.** Revision 1 said "never ingest advocacy
  or campaign material" while proposing to ingest arguments *written and
  submitted by proponent/opponent committees* — the state prints them, it
  does not author them. In California the argument slots are **purchased**,
  and the guide publishes four blocks (argument + rebuttal per side), so
  printing only the for/against pair favors whoever wrote more assertively.

**Replace symmetry with provenance and asymmetry disclosure:** name who
filed each argument and the registered committee; when only one side
filed, *say so* rather than deleting both; render all four blocks or none;
label them "written by the measure's proponents/opponents and printed by
the state — not by Civitas, and not fact-checked."

And move **measure committee finance** (FollowTheMoney) up from "phase 4,
out of scope." Who funds a measure is the most decision-relevant neutral
fact available, `RaceFinancials` already does exactly this for candidates,
and shipping arguments-without-money next to money-for-candidates is a
visible inconsistency against the project's own funding-independence
posture.

### 6.3 The ladder, revised

1. **Verbatim official text** — number, official title, official summary,
   fiscal statement, each with its authority (§6.1), quoted and linked.
2. **Structured extraction, still verbatim** — "A YES vote means…" lifted
   from the state's own framing, never inferred. Where the state publishes
   none, render nothing. Distinguish "state publishes none" from "our
   adapter hasn't run" with per-field sync watermarks — the same reason
   `Candidate.last_financials_sync` exists so the UI can say "awaiting FEC
   sync" instead of "$0" (verified rendering live, §12.2).
3. ~~Plain-language LLM layer~~ — **recommended against**, see §6.4.

### 6.4 Why the LLM layer is now a "no"

**The failure mode no check can catch.** Washington veto referenda use
"Approved retains the law / Rejected repeals it." A model asked to compress
that emits "A YES vote would repeal the tax" — exactly backwards. Walk
every check in `grounding.py` against it: `ungrounded_numbers`,
`ungrounded_statistics`, `ungrounded_titled_names`,
`ungrounded_party_claims`, `ungrounded_relationship_claims`,
`ungrounded_former_official_claims`, `hedge_language`,
`editorializing_language`, `placeholder_tokens`,
`vague_singular_office_references` — all clean, because every token is
present in the source. The module is a **token-presence** checker; it has
no representation of predicate direction. It cannot be extended to catch
this.

Worse: **`ungrounded_electoral_claims` is disarmed by construction on this
corpus.** Its context regex requires `\bballot\b` / `elect(ion|oral)` /
`voters?` to be *absent* from the source to fire — and every ballot-measure
source contains all three. It returns `[]` on 100% of these inputs.

**The proposed gate selects against its own goal.** "Reject output
containing a content word absent from the source" is a subset test. It
cannot detect deletion — dropping an exemption clause, a sunset, a
threshold, or a "not" strictly *shrinks* the output set and always passes.
Meanwhile lowering FKGL from 16–19 *requires* substituting shorter common
words for the source's legal vocabulary, i.e. producing words not in the
source. So it rejects the outputs that achieve the goal and passes the
deletions that are dangerous.

**FKGL as the ship gate rewards the same failure.** FKGL falls fastest when
subordinate clauses are deleted — which is where the exemptions live.
Combined with "zero grounding failures" (guaranteed, per above), the ship
criterion reduces to *maximize deletion, confirm the regexes are silent*.

**The context budget makes it a truncated-source generation.** A CA LAO
analysis runs 1,500–4,000 words against a 2,048-token budget. Truncation
narrows the string passed as `source`, and grounding is relative to *that
string* — so truncation silently narrows the ground truth and the checks
narrow with it. Revision 1 sized this phase by call count only.

**And the fallback creates visible editorial asymmetry.** "Reject and fall
back to verbatim" means the long, clause-dense, contentious measure renders
as grade-19 legalese beside three readable ones — a screenshot captioned
"Civitas explains the measures it likes" that is *factually accurate about
what the page shows*. Grounding failure is not random with respect to
subject matter. If any generation ever ships, the fallback must be
**page-atomic**: one failure blanks plain summaries for every measure on
that page.

**If it is ever revisited anyway**, the minimum bar is: hard-exclude every
veto referendum and retain/repeal question; forbid numerals in generated
text outright (deterministic and complete, and it removes the §12-class
number-collision problem where "340" matches "$1,340" somewhere in a
3,000-word fiscal analysis); feed only a short contiguous official span
that fits whole, never a truncation; gate on qualifier preservation and
negation parity rather than vocabulary containment; store the exact source
string alongside the output for post-hoc audit; require named human
sign-off per measure (~150/cycle is hours, not a program); and label it
inline and in the API payload as model-condensed. Note that if a human
grades all 150 anyway, the human grading *is* the plain-language layer.

---

## 7. Legal — corrected

Revision 1 asserted the government edicts doctrine covers ballot titles,
explanatory statements, and fiscal notes. That is **overstated**.
*Georgia v. Public.Resource.Org* (2020) is a two-element test: works
created by **judges and legislators** in the course of their **judicial
and legislative duties**. Extension to the executive branch is an open
question, not settled law.

This matters because the artifacts the plan ingests are largely executive:
**California's ballot titles and summaries are written by the Attorney
General** (Cal. Elec. Code §§9050-9053); Washington's explanatory
statement is AG-prepared. Clearly legislative — and therefore the safest
first adapter on *legal* grounds as well as technical — are **Colorado's
Blue Book fiscal statements (Legislative Council Staff)** and
**California's LAO analyses (legislative staff)**.

Separately: **the arguments for and against carry no edicts protection at
all** — they are private committees' work that the state merely prints.

Four legal surfaces revision 1 didn't touch, all needing a per-state read
before that state's adapter ships:

- **Sample-ballot / facsimile statutes.** Several states regulate
  documents resembling an official ballot. Never reproduce ballot layout,
  the state seal, or checkbox/oval glyphs; keep the digest visually
  distinct from a ballot.
- **Ballot-measure committee registration and disclosure.** Nonpartisan
  voter education is generally exempt, but the exemption turns on
  neutrality — which is exactly what a generated summary can breach
  silently. Another reason §6.4 lands on "no."
- **AI-disclosure / synthetic-election-media statutes**, several with
  60–90-day pre-election triggers. Any model-generated election content
  needs an inline label regardless of statute.
- **Provenance laundering.** Revision 1 allowed Ballotpedia "as a
  human-checked reference during development." In practice the developer
  shapes the parser around Ballotpedia's summary and the row ships with
  `source_name: "California Secretary of State"`. Forbid it in the
  adapter-authoring loop; verify against the SoS page only.

---

## 8. Proposed shape — revised

### 8.1 Information architecture

```
/elections                      → state index (map + accessible state grid)
  └ /elections/states/[ST]       ← new; plural, matching the API
      ├ heading: STATE — <election date> <election type>     (§4)
      ├ omission disclosure ABOVE the content                (§3.3)
      ├ official sample-ballot lookup CTA — above, not a footer
      ├ Federal: Senate contest + district list
      │   └ /elections/[raceId]  → existing detail (candidates, money, coverage)
      └ Statewide measures → per-measure card
          └ /elections/measures/[id]
```

- **`/elections/state` (singular) 404s today** — it matches the
  `[raceId]` dynamic segment and falls through to `notFound()` (verified,
  §12.3). Use the plural, matching the endpoint.
- **The district list needs a real design.** CA has 52 entries, TX 38;
  nobody knows their district number, and §10 forbids address collection.
  Label each district by **the sitting representative's name** (the
  `representatives` table has all 435) plus district PVI, and link out to
  house.gov's official find-your-representative lookup. Today's
  `ElectionsTab` `StatePanel` renders "52 congressional districts" as
  prose precisely because a bare number list is useless.
- **Preserve `?state=XX` with a redirect.** It's linked from the Action
  Center and exists in shared URLs.
- **DC must resolve.** `RaceMap.FIPS_TO_STATE` has **51** entries including
  DC, and `ElectionsTab` renders all of them as focusable buttons — so the
  map itself produces a link the new route would 404. (The PVI map also
  returns 51 states, verified §12.2.) DC votes on statewide initiatives.
  Render DC with its measures, the DCBOE lookup, and explicit copy about
  the Delegate seat. Make the lookup table 51+, and put the territory
  exclusion in page copy, not just a Python comment.
- **Accessibility is a constraint, not a detail.** `frontend/src/app/accessibility/page.tsx`
  publicly commits to WCAG 2.1 AA. That rules out a **visual ballot
  facsimile** outright: mock checkboxes are announced as real form
  controls (a blind user may believe they are voting), a two-column ballot
  can't reflow at 320px (1.4.10), and reproducing ballot text at the
  repo's `text-[9px]` + `/40` opacity idiom fails 1.4.4 and likely 1.4.3.
  Use headings and lists. Outbound links need `rel="noopener noreferrer"`
  and "(opens in new tab)" per the existing `Footer.tsx` pattern.

### 8.2 Data model

Key on **election date, not cycle year**: Ohio can run "Issue 1" in a May
primary *and* a November general, and `2026-OH-ISSUE-1` collides. Ballot
numbers also change, so the number must not be in the primary key —
`/elections/measures/2026-CA-PROP-50` would orphan on a renumber.

```python
class BallotMeasure(Base):
    id                          # synthetic/state filing id — NOT the ballot number
    election_date               # non-null; part of identity
    election_type               # primary | runoff | general | special
    state, number               # number is a mutable column
    previous_numbers            # drives 301s from stale URLs
    measure_type, origin
    status                      # certified | removed | withdrawn | under_appeal
    certified_at, removed_at, removal_source_url
    official_title, official_summary, fiscal_impact, yes_means, no_means
    title_authority, fiscal_authority, title_status, challenge_note   # §6.1
    source_url, source_name, as_of, last_seen_at
    official_text_synced_at     # per-field watermark, §6.3 rung 2
```

Plus a per-state, per-election **`MeasureCoverage`** row with an explicit
tri-state — `confirmed_none | covered | not_yet_covered | ingest_failed`
— and `checked_at`. Without it, "Texas has no measures" and "we haven't
ingested Texas" render identically, and a Texan seeing an empty section
concludes there's nothing to research. Default every state to
`not_yet_covered` so a new state is loud, not blank. This is the same
null-is-not-zero discipline the codebase already applies per field,
applied at the collection level.

**Migrations are hand-rolled.** There is no Alembic in use — column
additions live in `database.py:119`'s `additions` list, applied by
`_migrate_columns()`. Every phase that adds a column needs an entry with a
SQLite-legal default (`ALTER TABLE ADD COLUMN` rejects bare `NOT NULL`),
plus `_ensure_indexes`, plus registration in `reset_all_data()` — which
today already omits `Race`, `Candidate`, and `RaceCoverageItem`, a live
bug the same work should fix.

### 8.3 Ingestion — corrected pattern

Revision 1 proposed a git-committed `backend/app/data/ballot_measures_2026.json`
generated by a hand-run script, gated like `fetch_state_pvi.py`. Three
things are wrong with that:

- **`app/data/` is baked into the Docker image and is not writable at
  runtime.** Getting this wrong crash-looped a live pipeline run
  (2026-07-21, PermissionError, ~90 minutes in) — documented at
  `score_calculator.py:1151-1159`. The auto-refreshing path is
  `app/pipeline/fetch/district_pvi.py`, which writes `/data/` **and
  explicitly nulls the module-level cache after writing**; an accessor
  copied without that line serves a stale ballot until container restart.
- **The right store is the table, not a file.** `Race` deliberately has no
  PVI column so there is "exactly one source for that number, not a second
  copy that can drift." A JSON index carrying `status` alongside a
  `BallotMeasure.status` column is that same drift, on the field §9 says
  is actively harmful when stale.
- **The gate shape doesn't port, and hard-failing is the wrong response.**
  `fetch_state_pvi.py`'s gates key on quantities known in advance and
  stable for four years (exactly 51 jurisdictions, ±5 continuity). For
  measures the count *is* the unknown and changes weekly by design — the
  July 2026 CA/ND event (4 certified, 7 removed) would trip a continuity
  gate that is behaving correctly. And a build-time `exit 1` leaves the
  **site serving the struck measure**, more confidently the longer the
  gate stays red.

**Instead:** a year-round nightly phase in `election_pipeline.py` (a sixth
`ELECTION_PIPELINE_STEPS` entry with the same `progress.begin/complete/fail`
isolation), upserting rows like `_sync_roster` does; per-row reconciliation
with a grace period rather than set-equality (the `member_lifecycle.py`
contract — absent rows marked removed and *rendered* as removed, deleted
only after a window, reconciliation skipped entirely with an ops alert when
the fetched set is implausibly small); divergence between sources becomes a
`confirmed_by: ["votesmart","wikipedia"]` field and an alert, not an abort,
with single-source measures labeled unconfirmed; and a publish precondition
that the state's own URL resolved this run — the state page is the real
second source.

If Wikipedia is used at all, parse **by header name**, not column position
(`fetch_district_pvi.py`'s regex is name-addressed against a template
parameter across 435 separate articles — a structurally different and far
more robust situation than one actively-edited wikitable), and hard-fail on
an unrecognized header *with an alert*, since silence plus staleness is the
failure mode.

### 8.4 Official-ballot lookup links

A 51+ entry `state_ballot_lookup.json` (SoS sample-ballot lookup URL,
label, `as_of`). Two additions over revision 1: it doesn't have to be
hand-built from scratch — [Ballotpedia's state ballot measure websites
list](https://ballotpedia.org/State_ballot_measure_websites) and [U.S. Vote
Foundation](https://www.usvotefoundation.org/sample-ballot-lookup) are
existing curated indexes — and it needs a **liveness check**: a scheduled
HEAD sweep feeding `ops_alerts`, daily inside 60 days of any election
date. A 404 on "here's how to see your real ballot," in election week,
from a link Civitas vouched for, is the worst failure this feature has.

### 8.5 Operational requirements

- **Staleness is a serving-time property.** Blank `status`, `yes_means`,
  `no_means` and render "could not confirm since {date} — check {SoS
  link}" once `as_of` exceeds a ceiling (7 days in season, 30 out).
- **Fail loud.** Election-pipeline phases currently complete with
  `status: COMPLETED` after a caught failure and no `send_ops_alert` call
  exists anywhere in that pipeline. A measure-ingest failure must alert
  (deduped per state), mark that state `ingest_failed`, and set the run
  `FAILED` within 14 days of any election date. Add intake/output counters
  (`states_attempted`, `states_parsed`, `measures_found`,
  `measures_dropped`) mirroring the Action Center's `action-metrics`, so
  healthy intake with zero output is distinguishable from an empty ballot.
- **Correction latency.** `cached_json` sets `stale-while-revalidate`
  equal to `max-age`, roughly doubling effective staleness across browser
  and nginx. Within 14 days of an election date, measure endpoints need
  `max-age=0, must-revalidate` (~150 rows is nothing on a Pi), plus an
  admin kill switch that falls back to verbatim + SoS link without a
  deploy.
- **Disclosure must be fields, not template.** `/api/elections/*` is
  unauthenticated; a republisher gets the text without the as-of, the
  status, or the scope note. Every measure object carries `disclaimer`,
  `status`, `as_of`, `election_date`, and `official_lookup_url` as non-null.
- **nginx has no `location` for `/api/elections/`** — it falls through to
  the generic `/api/` block with rate limiting and **no proxy cache**,
  unlike `/api/action/elections` (1h). A new per-state endpoint needs one.
- **Never feed measure content to Bluesky.** State it as an invariant with
  a test. Related and independent: `election_bluesky.py:135` retries
  generation with the violated check named in the prompt — retry-until-it-
  passes, which steers toward evading the regex. That contradicts §6 and
  should be fixed on its own merits.
- `sitemap.ts` lists only `/elections`; 51 new routes need adding (race
  detail pages are already missing). Per-state `generateMetadata` is
  required or all 51 inherit the layout's title and a wrong canonical OG
  URL. And `elections/layout.tsx` fetches the full ~470-race payload for
  the whole subtree just to read `cycleYear` — move it.

---

## 9. Volume and volatility

~150–180 measures per cycle (revision 1 said 85–110, from a stale
snapshot) against ~6,900 FEC candidate records the pipeline already
handles. Still trivial for the hardware.

Volatility is the cost. Courts have overturned **2.3% of state ballot
measures since 1995**; 17 certified measures were removed by courts
2014–2025, eight in Arkansas alone. 2026 has already added more — a North
Dakota term-limits measure struck 2026-06-25, two Arizona referred
measures blocked 2026-07-29 — and in one two-week July window four
measures were certified in California while seven were removed in
California and North Dakota. Certification is *still running* as of this
writing.

---

## 10. What to recommend against

- **Any address-based lookup.** Unchanged, and it is the reason Google
  Civic is out despite being the only true per-ballot source.
- **LLM-written measure summaries** (§6.4). Changed from revision 1.
- **A visual ballot facsimile** (§8.1) — accessibility, plus it cannot
  represent RCV or top-four contests, plus §7's facsimile statutes.
- **Calling the page "your ballot," or "the statewide ballot"** (§3.3).
- **A third per-state assembler** (§2).

---

## 11. Provisional — verify before building

Direct page fetches were blocked in the authoring environment (403 via the
agent proxy on every host), so **no external source below was read in
full**; all external claims come from search summaries. Revision 1 carried
this as a closing caveat while §1 rendered settled verdicts; it belongs
here, attached to the verdicts.

Blocking checklist for phase 1:

1. **Vote Smart API** — current coverage, whether summaries are their
   prose or the state's, licensing, key terms. This one claim can collapse
   most of the estimated effort.
2. **Democracy Works Elections API** and **BallotAPI** — liveness, terms,
   coverage.
3. **Ballotpedia** — whether the non-commercial reading of the scraping
   clause holds, and actual pricing (the figures in circulation are
   third-party).
4. **Per-state reuse terms** for ballot titles, explanatory statements,
   and fiscal notes, given §7's executive-vs-legislative split.
5. **Sample-ballot/facsimile statutes**, **committee-disclosure exemption
   tests**, and **AI-disclosure statutes** for each launch state.
6. Whether CA's SoS API portal and CO's Legislative Council database
   actually serve measure text, which would undercut the adapter estimate.

---

## 12. Testing — what was actually run

PR #352 is documentation; there is no ballot feature to test. What was
tested is the **existing** election feature, because this doc's
recommendations rest on claims about it. Environment: Python 3.13 venv
(the repo needs 3.12+; the sandbox default 3.11 fails on an f-string
backslash in `fetch/fec.py`), Node 22, Chromium via Playwright.

### 12.1 Test suites

| Suite | Result |
|---|---|
| `pytest tests/test_elections_api.py test_election_calendar.py test_election_season.py` | **28 passed** |
| `pytest tests/test_election_pipeline.py test_election_coverage.py test_election_bluesky.py` | **56 passed** |
| `npm run lint` | **0 errors** (28 pre-existing warnings) |
| `npm test` (vitest) | **75 passed**, 6 files |
| `npm run build` (production) | **succeeded**; `/elections` prerendered static w/ 1h revalidate, `/elections/[raceId]` dynamic |

### 12.2 Live API, real routers, seeded SQLite

Booted `app.api.elections` + `app.api.action` over a temp DB seeded with a
regular Senate race, a special, and a House race:

- `/api/elections/races` — all three shapes; **`contributions`/`cashOnHand`
  null and `lastFinancialsSync` null** for the un-synced candidate (never
  a fabricated `0`); timestamps carry an explicit `Z`.
- `/api/elections/races/2026-SEN-GA` — candidates, coverage verbatim.
- `/api/elections/pvi` — **51 states**, 435 districts, full provenance
  (`source`, `method`, `window: 2020+2024`, `asOf: 2026-07-24`).
- `/api/action/elections` — `daysUntil: 90`, **`isElectionSeason: false`**,
  `senateSeatsUp: 34`. This is the measurement behind §4: the season
  window is shut today, 90 days out, in primary season.
- 404s correct on unknown race ids, including `/races/state`.

### 12.3 Browser, production build

- `/elections` renders the map, PVI provenance line, and race cards; the
  race count matched the seed.
- `/elections/2026-SEN-GA` renders `R+1`, `$12.3M` raised, and — for the
  un-synced candidate — **"AWAITING FEC SYNC"**, confirming the
  null-is-not-zero claim end to end.
- `/elections/2026-HOUSE-CA-12` renders `D+39` from the *district* map,
  distinct from the statewide fallback.
- **`/elections/state` → 404**, confirming the `[raceId]` collision.
- **The `router.replace` trap reproduced** (§2.1): from a cold load of
  `/elections?state=GA`, clicking California left the URL at `?state=GA`
  and the heading at "GA — R+1". From a cold load of *bare* `/elections`,
  the same clicks worked — matching AGENTS.md's description exactly.
- **Not reproduced:** the navbar stale-search restore. With full soft
  navigation, a bare `/elections` link returned all races and a clean URL.

**Not tested** (no data or no credentials in this environment): the FEC
roster sync, coverage ingestion, Bluesky posting, and the frontend against
a production-scale ~470-race payload.

---

## 13. What changed from revision 1

Errors found and corrected, so a reader of the earlier version knows what
not to trust:

| # | Revision 1 said | Corrected |
|---|---|---|
| 1 | 85 measures in 35 states (April 1) | 149 in 39 states as of July 22, still climbing; ~150–180 expected |
| 2 | "AI Democracy Projects… more than half inaccurate, and a follow-up round found 27%" | **Two unrelated studies.** AI Democracy Projects (Jan 2024, expert-rated): *half*. The 27% is **GroundTruthAI** (June 2024, automated, different models). Calling it a follow-up was fabricated provenance |
| 3 | Readability range "8 (Alaska) to 42 (Connecticut)" | Unverifiable and contradicted; **dropped**. Averages retained — and 2025 was the *worst* year on record (FKGL 21), which reverses the trend revision 1 implied |
| 4 | "Courts disqualified 16 measures over the past decade" | A Sept 2024 headline, not the current figure; 17 through 2025 plus 2026 additions. Better: 2.3% overturned since 1995 |
| 5 | Ballotpedia ❌ — "prohibits automated extraction"; GFDL/AGPL tangle | Clause is scoped to **commercial purposes**; GFDL is a free copyleft license; content-vs-code license conflation was a category error. Reclassified ⚠️ |
| 6 | Edicts doctrine covers ballot titles and explanatory statements | Two-element test (judges + legislators); **CA/WA titles are AG-authored**, executive extension unsettled; arguments are private works |
| 7 | Civic Info keyed on "the voter's **registered** address" | No registration check; any address string. Objection narrowed but stands |
| 8 | "No free, comprehensive, machine-readable source exists" | **Vote Smart has a free ballot-measures API**; Democracy Works and BallotAPI unevaluated; CA runs an SoS API portal |
| 9 | `fetch_state_pvi.py` / `app/data/` as the template | `app/data/` is **not runtime-writable** (prod crash 2026-07-21); the table is the store; the gate shape doesn't port and hard-failing serves stale |
| 10 | Per-state view is "new" | `?state=XX` already filters; the delta is the default view, SSR route, and measures |
| 11 | Three grounding checks named | Ten checks and two aggregates; `grounding_violations` **excludes** `ungrounded_statistics`; `ungrounded_electoral_claims` is **disarmed** on ballot text |
| 12 | LLM layer "optional, last" | **Recommended against** (§6.4) |
| 13 | No concept of primaries, runoffs, RCV, top-two | §4 |
| 14 | Verbatim = neutral | §6.1 |
| 15 | Symmetric argument pairs | §6.2 |
| 16 | Empty measures section acceptable | `MeasureCoverage` tri-state (§8.2) |
| 17 | Refresh keyed to `is_election_season()` | Federal-general-only window; measured `isElectionSeason: false` today (§12.2) |
| 18 | Endpoint paths without `/api` | Corrected |
| 19 | "PVI is already labeled with an as-of date" | `PviMethodologyNote` renders source and window, **not** `asOf`; the race page passes no meta at all |

---

## 14. Open questions

1. **State executive contests** — 36 governor's races missing is the
   biggest hole. Scope in, or disclose by name in page copy (§3.3)?
2. **Coverage floor for launch**, and the launch-state set. Choose it by a
   published criterion (measure count × feasibility) and target partisan
   balance explicitly — a first wave of CA/CO/WA/OR reads as a blue-state
   map next to the PVI coloring already on that page.
3. **Existing URLs** — what happens to `?state=XX` and `/elections/[raceId]`.
4. **Human sign-off** — does any measure title, fiscal note, or status
   change reach production on an automated nightly with no reviewer? §6
   and §8.5 assume not.
5. **Off-cycle years** — what `/elections/states/XX` renders when the next
   federal election is 18 months out.
6. **PVI adjacency** — partisan-lean coloring in the same viewport as
   measure content invites the "Civitas is campaigning" screenshot (§6.2).

## Sources

Google Civic: [voterInfoQuery](https://developers.google.com/civic-information/docs/v2/elections/voterInfoQuery) · [quotas](https://developers.google.com/civic-information/docs/using_api) · [Representatives turndown](https://groups.google.com/g/google-civicinfo-api/c/9fwFn-dhktA) · [divisionByAddress replacement](https://groups.google.com/g/google-civicinfo-api/c/eWoEc2tn8DA)
Ballot data: [Vote Smart API](https://www.votesmart.org/votesmart-api) · [Vote Smart Measure class](https://api.votesmart.org/docs/Measure.html) · [Democracy Works ballot info](https://data.democracy.works/ballot-info) · [BallotAPI](https://github.com/open-austin/ballotapi) · [CTCL BIP](https://www.techandciviclife.org/our-work/research-department/our-data/ballot-information/) · [VIP](https://www.votinginfoproject.org/about) · [NCSL database](https://www.ncsl.org/elections-and-campaigns/statewide-ballot-measures-database) · [BISC Hot Sheet](https://ballot.org/the-hot-sheet/) · [MultiState 2026](https://www.multistate.us/elections/ballot-measures-2026) · [Wikipedia 2026 measures](https://en.wikipedia.org/wiki/2026_United_States_ballot_measures)
Ballotpedia: [Copyrights](https://ballotpedia.org/Ballotpedia:Copyrights) · [Reusing content](https://ballotpedia.org/Ballotpedia:Reusing_Ballotpedia_content) · [Buy Political Data](https://ballotpedia.org/Ballotpedia:Buy_Political_Data) · [2026 scorecard](https://ballotpedia.org/Ballot_Measure_Scorecard,_2026) · [July 23 2026 certifications](https://news.ballotpedia.org/2026/07/23/over-the-past-two-weeks-10-new-ballot-measures-were-certified-in-three-states-idaho-massachusetts-and-washington/) · [July 8 2026 removals](https://news.ballotpedia.org/2026/07/08/over-the-past-two-weeks-four-new-ballot-measures-were-certified-in-california-and-seven-measures-were-removed-in-california-and-north-dakota/) · [July 29 2026 Arizona](https://news.ballotpedia.org/2026/07/29/two-referred-ballot-measures-were-ruled-unconstitutional-and-blocked-from-the-arizona-ballot-by-maricopa-county-judges/) · [2.3% overturned since 1995](https://news.ballotpedia.org/2026/05/18/state-and-federal-courts-have-overturned-2-3-of-state-ballot-measures-since-1995/) · [measures removed by courts](https://ballotpedia.org/List_of_certified_state_ballot_measures_removed_from_the_ballot_by_courts) · [readability 2022](https://ballotpedia.org/Ballot_measure_readability_scores,_2022) · [readability 2024](https://news.ballotpedia.org/2024/10/29/ballotpedia-releases-readability-analysis-of-2024-ballot-measures/) · [readability 2025 — highest on record](https://news.ballotpedia.org/2025/10/20/2025-statewide-ballot-measures-written-at-the-highest-reading-level-equivalent-to-a-doctorate-degree-since-ballotpedia-started-tracking-in-2017/) · [state executive elections 2026](https://ballotpedia.org/State_executive_official_elections,_2026) · [state ballot measure websites](https://ballotpedia.org/State_ballot_measure_websites) · [sample ballot lookup tools](https://ballotpedia.org/Sample_ballot_lookup_tools)
Official state: [CA LAO](https://lao.ca.gov/ballotanalysis) · [CA SoS API portal](https://calicodev.sos.ca.gov/) · [CA SoS ballot measures](https://www.sos.ca.gov/elections/ballot-measures) · [CO Legislative Council fiscal statements](https://leg.colorado.gov/agencies/legislative-council-staff/ballot-measure-fiscal-impact-statements) · [WA SoS](https://www.sos.wa.gov/elections/voters/proposed-ballot-measure-information) · [U.S. Vote Foundation](https://www.usvotefoundation.org/sample-ballot-lookup)
AI accuracy: [IAS / AI Democracy Projects](https://www.ias.edu/news/ai-chatbots-found-inaccurate-answering-voter-queries) · [Proof News](https://www.proofnews.org/seeking-election-information-dont-trust-ai/) · [GroundTruthAI via NBC](https://www.nbcnews.com/tech/tech-news/ai-chatbots-got-questions-2024-election-wrong-27-time-study-finds-rcna155640) · [officials warn](https://www.cnbc.com/2024/11/01/ai-chatbots-arent-reliable-for-voting-questions-government-officials.html)
Other: [NIST Election Glossary](https://pages.nist.gov/ElectionGlossary/) · [Ellis County TX ballot styles](http://www.elliscountytx.gov/1206/Individual-Sample-Ballots-by-Style) · [*Georgia v. Public.Resource.Org*](https://www.supremecourt.gov/opinions/19pdf/18-1150_7m58.pdf) · [Open States API v3](https://docs.openstates.org/api-v3/)
