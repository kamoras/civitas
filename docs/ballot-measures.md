# Statewide ballot measures — design record

Why the ballot feature is shaped the way it is. Code in
`api/elections.py`, `pipeline/fetch/ballot_measures.py`, `models.py` and
`components/elections/BallotMeasureCard.tsx` cites the sections here for
decisions that would otherwise look arbitrary — particularly §6.4, which
is the reason there is no plain-language summary field anywhere in the
schema.

Research date 2026-08-05. External claims come from web-search summaries;
direct page fetches were blocked in the authoring environment, so §7 flags
what still needs confirming before each state adapter ships.

---

## 1. What shipped

- `/elections` is a **state index** — a PVI-colored map plus a
  keyboard-reachable 51-state grid. It no longer renders ~470 race cards.
- `/elections/states/[ST]` is the **state ballot page**: the U.S. Senate
  contest if there is one, statewide ballot measures, the state's House
  districts, and an explicit account of everything a state page cannot
  show.
- `/elections/[raceId]` (candidates, FEC money, coverage) is unchanged and
  is now reached through the state page.
- Measures are ingested nightly by a new `ballot_measures` phase in
  `election_pipeline.py` from the Vote Smart API, gated on
  `VOTESMART_API_KEY`. With no key the phase is skipped and every state
  reports "not yet covered" — never "no measures".

---

## 2. There is no such thing as "your state's ballot"

A ballot is defined per **ballot style** — the unique combination of
contests a voter is eligible for. Precincts split by district boundaries
carry several ([NIST Election
Glossary](https://pages.nist.gov/ElectionGlossary/)); [Ellis County,
TX](http://www.elliscountytx.gov/1206/Individual-Sample-Ballots-by-Style)
published 36 for one joint election. Nationally, tens of thousands.

Genuinely uniform statewide: the Senate contest; statewide ballot measures
(**149 certified in 39 states for 2026 as of 2026-07-22**, still climbing
— [Ballotpedia](https://news.ballotpedia.org/2026/07/23/over-the-past-two-weeks-10-new-ballot-measures-were-certified-in-three-states-idaho-massachusetts-and-washington/));
statewide executive offices (36 governorships, 31 lt. governors, 30 AGs,
26 SoS in 2026 — [Ballotpedia](https://ballotpedia.org/State_executive_official_elections,_2026)),
for which this codebase has **no data at all**; and statewide judicial
retention questions.

So the page is titled **"federal contests & statewide measures"**, and the
omissions are enumerated by the backend (`omits`) and rendered **above**
the content, not in a footnote. A partial digest with the caveat at the
bottom gets read as a complete ballot.

The one address-keyed source that could give a real per-voter ballot —
Google Civic's `voterInfoQuery` — is rejected on exactly the grounds the
platform's architecture exists to defend: any address a visitor types
would leave the box. The House-district list says the same thing out loud
("you vote in exactly one of these; Civitas does not ask for your
address") and links house.gov rather than guessing.

---

## 3. Naming the election is load-bearing

`election_calendar.next_election_day()` computes the **federal general**
only (2 U.S.C. §7). Primaries are party-specific, run on ~50 different
dates, and carry their own statewide measures — so a page that says "the
ballot" without a date is wrong for most of the year, and this was written
90 days before the general, in primary season.

Hence `election_date` and `election_type` are non-null on every payload and
render in the page heading, `BallotMeasure` is **keyed on election date
rather than cycle year** (Ohio can run an "Issue 1" in a May primary and a
different "Issue 1" in November), and "Primary and runoff ballots" is a
permanent entry in `omits`. Contest-method differences that would break a
ballot metaphor outright — Louisiana's jungle primary on general election
day, Georgia's December runoffs, RCV in Maine and Alaska, top-two in
California and Washington — are why no visual ballot facsimile is
rendered at all.

---

## 4. Sources

| Source | Verdict |
|---|---|
| **[Vote Smart API](https://api.votesmart.org/docs/Measure.html)** | **In use.** Free key, nonpartisan, dedicated measure class with official title/summary and the state's own yes/no framing |
| Official state materials ([CA LAO](https://lao.ca.gov/ballotanalysis), CO Blue Book, [WA SoS](https://www.sos.wa.gov/elections/voters/proposed-ballot-measure-information)) | The authoritative upgrade path, and always the linked authority. ~50 bespoke adapters, so not first. Note [CA runs an SoS API portal](https://calicodev.sos.ca.gov/) and CO's Legislative Council keeps a structured database — the adapter estimate is softer than it looks |
| Google Civic `voterInfoQuery` | ❌ Address-keyed (§2). Only the *Representatives* endpoint was retired (April 2025); elections endpoints are live |
| CTCL Ballot Information Project | ❌ Not updated since Jan 2026, and it was a *candidate* dataset |
| [Ballotpedia](https://ballotpedia.org/Ballotpedia:Copyrights) | Not used. Its anti-scraping clause is scoped to *commercial* purposes and GFDL is a free copyleft license, so a non-commercial civic deployment may well be permitted — but that needs confirming in writing, and Vote Smart needs no such argument |
| [Democracy Works](https://data.democracy.works/ballot-info), [BallotAPI](https://github.com/open-austin/ballotapi), [BISC](https://ballot.org/the-hot-sheet/), NCSL, Wikipedia | Unevaluated or cross-check only. NCSL is a PowerBI embed with no documented API |

Worth knowing: the free ballot-content layer didn't degrade in three
places independently — it consolidated. CTCL's BIP fed Google Civic, and
Democracy Works now states its ballot content is *provided by
Ballotpedia*. That is the argument for sourcing from states directly over
time.

---

## 5. Volatility is the cost, not volume

~150–180 measures per cycle against the ~6,900 FEC candidate records the
pipeline already handles — trivial for the hardware. But measures churn
continuously: courts have overturned **2.3% of state ballot measures since
1995**; in one two-week July 2026 window four were certified in California
while seven were removed in California and North Dakota; two Arizona
measures were blocked on 2026-07-29.

What that bought in the code:

- **Removed measures are rendered as removed, not deleted.** A voter who
  saw a measure last week needs to be told a court struck it, and an
  absent card cannot say that. Deletion happens only after a 45-day grace
  window (`MEASURE_REMOVAL_GRACE_DAYS`).
- **An implausible shrink is treated as a bad response**, not as news
  (`MEASURE_SHRINK_FLOOR`). One truncated upstream reply must not blank a
  state's ballot.
- **Short cache TTLs** — the state ballot uses the 2-minute client tier,
  not the 1-hour "reference data" tier its shape suggests. A browser
  holding a struck measure for an hour after the backend corrected it is
  the specific failure this feature cannot have.
- **Ingest failures alert** (`send_ops_alert`, deduped). A silently broken
  adapter and a quiet week look identical from outside, and this is the
  one dataset where that ambiguity costs a vote.

---

## 6. Neutrality

### 6.1 Verbatim is not the same as neutral

Ballot titles are among the most litigated documents in election law
*because* they are contested — Utah's Amendment D (2024) was voided by a
court that found the legislature's own ballot language counterfactual;
Ohio Issue 1 (2023), Missouri Amendment 3 (2024) and Florida Amendment 4's
state-authored fiscal statement (2024) were all litigated over wording.

Reprinting a legislature-drafted title unattributed under a non-partisan
masthead launders its author's framing. So `title_authority` and
`fiscal_authority` render beside the quote ("Drafted by the Georgia
General Assembly"). Naming the drafter is *more* neutral than the bare
quote, because who wrote it is what tells a reader how to weigh it.

### 6.2 What is deliberately absent

**Arguments for and against.** The state prints them; proponent and
opponent committees write them, and in California the slots are
purchased. Rendering them in symmetric pairs manufactures false balance
between a funded campaign and a lone rebuttal, and the obvious rule for
one-sided cases ("publish neither") is trivially gamed by declining to
file. Not shipped rather than shipped badly; measure-committee finance is
the better neutral signal and is the natural next addition.

**Coverage status.** `MeasureCoverage` exists so an empty section can say
*which* kind of empty it is. "This state has none" and "we haven't
ingested this state" render as visibly different blocks, because a Texan
looking at a blank section under their state's name concludes there is
nothing to research.

### 6.3 What the source publishes, or nothing

`yes_means` / `no_means` are lifted from the state's own framing or left
null. Never derived from the title — the obvious derivation (yes = enact)
is exactly inverted on a veto referendum, where "approved" *retains* the
law under challenge.

### 6.4 Why there is no plain-language rewrite

Ballot language is genuinely inaccessible: average Flesch-Kincaid grade
level 19 in 2022, 16 in 2024, and **21 in 2025 — the highest since
Ballotpedia began tracking in 2017**. Reilly & Richey (2011) found voters
skip measures whose titles read harder. So the case for a plain-language
layer is real. It was still rejected:

- **The failure that matters cannot be detected.** A model that emits "a
  YES vote would repeal the tax" when the official text says *approved
  retains* passes all ten checks in `grounding.py`, because every token is
  present in the source. That module is a token-presence checker with no
  representation of predicate direction, and cannot be extended to catch
  it. `ungrounded_electoral_claims` is worse than merely unhelpful here —
  its context regex requires "ballot"/"voters" to be *absent* from the
  source to fire, and every ballot-measure source contains both, so it
  returns `[]` on 100% of these inputs.
- **The obvious gate selects against its own goal.** "Reject output
  containing a content word absent from the source" is a subset test: it
  cannot detect a dropped exemption, sunset, threshold, or negation
  (deletion only shrinks the output set), while lowering FKGL *requires*
  substituting shorter words not in the source. It rejects what helps and
  passes what harms.
- **FKGL as a ship gate rewards deletion**, and deletion is where the
  exemptions live.
- **The context budget forces truncation.** A CA LAO analysis runs
  1,500–4,000 words against a 2,048-token budget, and grounding is
  relative to the string passed as source — so truncating silently narrows
  the ground truth and the checks narrow with it.
- **The fallback would be visibly partisan.** "Reject and fall back to
  verbatim" leaves the long, contentious measure rendered as grade-19
  legalese beside three readable ones — a screenshot captioned "Civitas
  explains the measures it likes" that is accurate about what the page
  shows.

Independent evidence on the general risk, correctly attributed: the **AI
Democracy Projects** (Proof News + IAS, Jan 2024, expert-rated) found
*half* of five frontier models' answers to voter questions inaccurate; a
separate **GroundTruthAI** study (June 2024, automated, different models)
put a different figure at 27%. Two studies, not one and its follow-up —
and both tested models far larger than the 1.2B running here.

---

## 7. Legal

*Georgia v. Public.Resource.Org* (2020) is a two-element test: works
created by **judges and legislators** in the course of their **judicial
and legislative duties**. Extension to the executive branch is an open
question. This matters because **California's ballot titles and summaries
are written by the Attorney General** (Cal. Elec. Code §§9050-9053) and
Washington's explanatory statement is AG-prepared, while Colorado's Blue
Book fiscal statements (Legislative Council Staff) and California's LAO
analyses are legislative — which makes those the safest first state
adapters on legal as well as technical grounds. Arguments for and against
carry no edicts protection at all (§6.2).

Confirm before each state's adapter ships: that state's reuse terms;
sample-ballot/facsimile statutes (which is why nothing here reproduces
ballot layout, the state seal, or checkbox glyphs); and ballot-measure
committee disclosure exemptions.

---

## 8. Not built, deliberately

- **Plain-language / LLM summaries** — §6.4.
- **A visual ballot facsimile** — mock form controls are announced to
  screen readers as real controls, a two-column ballot cannot reflow at
  320px, and it could not represent RCV or top-four contests anyway.
- **Any address lookup** — §2.
- **Arguments for/against** — §6.2.
- **State executive contests** — no data source in this codebase yet.
  Named in `omits` so their absence is stated rather than implied.
- **Per-state official ballot links** — the mechanism ships
  (`state_ballot_lookup.json` + a liveness check that gates rendering on a
  URL actually resolving), but no per-state URL is populated, because none
  could be verified from the authoring environment. Every state falls back
  to the USAGov national directory until a network-enabled run promotes
  entries. A dead link on "see your real ballot", in election week, from a
  URL Civitas vouched for, is the worst failure this feature has.

---

## 9. Sources

[Vote Smart API](https://www.votesmart.org/votesmart-api) ·
[Measure class](https://api.votesmart.org/docs/Measure.html) ·
[Google Civic voterInfoQuery](https://developers.google.com/civic-information/docs/v2/elections/voterInfoQuery) ·
[Representatives turndown](https://groups.google.com/g/google-civicinfo-api/c/9fwFn-dhktA) ·
[CTCL BIP](https://www.techandciviclife.org/our-work/research-department/our-data/ballot-information/) ·
[Ballotpedia copyrights](https://ballotpedia.org/Ballotpedia:Copyrights) ·
[2026 certifications, July 23](https://news.ballotpedia.org/2026/07/23/over-the-past-two-weeks-10-new-ballot-measures-were-certified-in-three-states-idaho-massachusetts-and-washington/) ·
[July 8 removals](https://news.ballotpedia.org/2026/07/08/over-the-past-two-weeks-four-new-ballot-measures-were-certified-in-california-and-seven-measures-were-removed-in-california-and-north-dakota/) ·
[July 29 Arizona](https://news.ballotpedia.org/2026/07/29/two-referred-ballot-measures-were-ruled-unconstitutional-and-blocked-from-the-arizona-ballot-by-maricopa-county-judges/) ·
[2.3% overturned since 1995](https://news.ballotpedia.org/2026/05/18/state-and-federal-courts-have-overturned-2-3-of-state-ballot-measures-since-1995/) ·
[readability 2025](https://news.ballotpedia.org/2025/10/20/2025-statewide-ballot-measures-written-at-the-highest-reading-level-equivalent-to-a-doctorate-degree-since-ballotpedia-started-tracking-in-2017/) ·
[state executive elections 2026](https://ballotpedia.org/State_executive_official_elections,_2026) ·
[NIST glossary](https://pages.nist.gov/ElectionGlossary/) ·
[Georgia v. PRO](https://www.supremecourt.gov/opinions/19pdf/18-1150_7m58.pdf) ·
[IAS / AI Democracy Projects](https://www.ias.edu/news/ai-chatbots-found-inaccurate-answering-voter-queries) ·
[GroundTruthAI via NBC](https://www.nbcnews.com/tech/tech-news/ai-chatbots-got-questions-2024-election-wrong-27-time-study-finds-rcna155640)
