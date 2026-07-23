# Action Center Quality Audit — 2026-07-22

**Scope:** issue generation, story writing, and Bluesky posting. Method: full read of the
`action_center.py` pipeline (3,664 lines), `bluesky_poster.py`, `grounding.py`, and the
prompts; then a line-by-line quality review of the last 40 real production issues
(ids 372–411), their full stories, and all 17 Bluesky posts with stored text, plus
production log/DB forensics on the Pi.

**Context that frames everything below:** production generates all of this with
**LFM2.5-1.2B**, a 1.2-billion-parameter local model. The pipeline's architecture —
mechanical grounding validators, fail-closed retries, length scaled to fact count —
is a genuinely well-engineered compensation for a model this size, and the worst
hallucination classes (invented statistics, invented named officials, invented
elections) are demonstrably being caught. What gets through is subtler: placeholder
tokens, cross-topic bleed, vacuous filler, and structural identity problems that no
per-text validator can see.

---

## Findings, ranked

### H1. Bluesky posts drift out of sync with the pages they link to — HIGH

4 of the 17 stored posts describe **different content than the issue page their
permalink points to**. Live examples:

- **id=405** page: "House approves Pentagon funding framework … $95B … 216-212 vote."
  Its post: "House advanced temporary funding bill to avoid shutdown; Senators plan
  response…" — a different (earlier) story.
- **id=406** page: Politico poll, 37%/50% MAGA Iran-war support. Its post: "Several
  lawmakers discussed Iran strategy and funding; Sen. Peters noted planning gaps…" —
  none of that is on the page.
- **id=394** page: defense policy bill 216-212. Its post: "GOP leaders focusing on
  securing support… committee assignments being adjusted."

**Root cause:** topic-rematching (`TOPIC_CHANGE_THRESHOLD = 0.82` raw title cosine)
re-uses an existing row for a "same topic" cluster and **replaces the row's entire
content in place**. The published post text is the row's history; the permalink
`/issue/{id}` shows whatever the row says *now*. The code already computes exactly
the right signal — `_full_story_should_invalidate()` fires when title/facts changed
enough that the cached story describes the wrong event — but it only nulls
`full_story`. The same signal should mean "this is a different story than what we
posted": either mint a new row instead of overwriting, or at minimum never treat the
old `bsky_last_post_text` as the row's post.

### H2. Duplicate rows (and posts) for the same real-world story — HIGH

The same story routinely becomes multiple rows because 0.82 raw-title cosine is too
strict a bar for "same topic" when the LLM re-titles it differently each run:

- **id=394 + id=405**, both current on 07-22: "Defense policy bill passage and budget
  debates" vs "House approves Pentagon funding framework" — same $95B bill, same
  216-212 vote, two rows, two posts.
- **id=396 + id=401**, adjacent days: "FDA investigation continues over Taylor Farms
  lettuce" vs "Cyclosporiasis outbreak investigation updates" — same outbreak, same
  7,000 cases, same false-positive sample.

Across 07-20→07-22, ~18 rows cover ~13 distinct stories (~28% duplicate rate). Each
duplicate row is a fresh Bluesky post candidate; the word-set Jaccard near-dup guard
(0.65) misses them because the model rephrases freely. Combined effect: **12 posts on
07-22 alone**, several re-covering the same stories — feed-spam territory for a
curated civic account.

Note H1 and H2 are the *same* weak joint failing in both directions: raw title
cosine at 0.82 both accepts false rematches (content replacement, H1) and misses true
ones (duplicate rows, H2). Matching on facts overlap (entities/numbers) or embedding
the summary rather than the volatile LLM title would fix both ends.

### H3. A World Cup story published as a civic issue — via a surname collision — HIGH

**id=395 "Spanish and Argentine reactions to World Cup final"** is a pure sports
story (sources: BBC World, PBS NewsHour) published as an actionable civic issue,
with a full story and Bluesky-post eligibility. The action-surface gate requires a
bill, official, or civic document — it passed because soccer player **Ferran
Torres** surname-matched **Rep. Ritchie Torres and Rep. Norma J. Torres** through the
last-name-only disambiguation path ("referenced in coverage"). One bad name match
produced two harms at once: two House members falsely tagged on a World Cup story,
and the false tag *was itself the action surface* that let the story publish.

**Fix shape:** the action-surface gate should not accept last-name-only
("referenced in coverage") matches as sufficient — require a full-name match, a
resolved bill, or an explore-doc hit. Separately, the disambiguation prompt
compares against "Representative X from NY" prototypes but nothing checks that the
*surrounding text is about politics at all*; a topicality guard (the policy-relevance
prototypes already exist) on the disambiguation would kill this class.

### H4. Literal `[date]` placeholders published to the site and Bluesky — HIGH

**id=397**: fact "Thune announced the tribute details on **[date]**." and "The
National Cathedral hosted the ceremony on **[date]**." — published verbatim, and the
Bluesky post went out reading "Thune shared details on **[date]**…". The full story
then paraphrased the placeholders into "on a recent date" and "on a specific date."

Every grounding check is digit-based; a bracketed placeholder has no digits, so
nothing fired. **Fix: trivially mechanical** — reject any generated text matching
`\[[a-z ]{2,20}\]` (placeholder tokens) in facts, summaries, stories, and posts.
This is the single cheapest high-impact fix in the audit.

### M5. Meta-facts and vacuous facts still leak through — MEDIUM

`_validate_facts`' meta-phrase list covers "in the article(s)", "the coverage ",
"the reporting " — but not **"the articles"** as sentence *subject*:

- id=404: "The articles focused on internal party dynamics rather than public policy outcomes."
- id=403: "The articles referenced specific names and dates related to the discussion."

Both published. Alongside them, a class of content-free "facts" no validator
addresses: "Two entities were cited in the exchange: the Smithsonian and the White
House" (id=403), "No official decision was made…" (id=404), "No single entity has
been named…" (id=401), "The committee is preparing to address upcoming budget
considerations" (id=411). An issue like **id=407 "Congressional scheduling
adjustments"** consists *entirely* of this filler — no names, no numbers, no bill —
yet published and posted. Fix: extend `_META_PHRASES` (cheap), and add a minimum-
specificity gate — an issue whose facts contain no proper noun + no number is not
publishable.

### M6. Cross-topic facts bleed into issues — MEDIUM

Facts validated against the *whole cluster's* text pass even when they belong to a
different story that shared the cluster:

- id=408 (Netanyahu/NYC arrest): "**President Zelenskyy removed his army chief** amid
  protests and appointed a new leader."
- id=410 (Arizona primaries): "**Over 27 senior officials have left** their positions
  in the Trump administration since the start of his first term."
- id=396 (cyclospora outbreak): "**PhRMA** has noted the situation as a key point of
  discussion in industry discussions."

The coherence filter (centered-sim ≥ 0.25) reduces but doesn't eliminate mixed
clusters, and the 1.2B model then blends whatever it sees. A per-fact topical check
(each fact's embedding vs the issue title/centroid) would catch these — the
infrastructure for it already exists in the file.

### M7. Full stories: padded filler and prompt-banned patterns — MEDIUM

The stories are grounded (no invented numbers/names observed — the validators work)
but read as **fact-list padding**, and several *explicitly prompt-banned* patterns
appear because the mechanical hedge check only knows specific phrases:

- Generic wrap-ups the prompt forbids verbatim: "This development reflects the
  broader challenges facing congressional leadership…" (id=411), "These developments
  point to a more nuanced landscape…" (id=406).
- Speculation: "This shift **may influence** how political leaders frame their
  messaging" (id=406).
- Motive claims: "The timing of her announcement **was influenced by** President
  Trump's public statements, which **shaped the tone and focus** of the race" (id=411).
- Coverage-as-subject: "The coverage from BBC World and PBS NewsHour captured these
  developments" (id=395).

Each adds no information and some add unsupported causal claims. Realistic take: a
1.2B model asked for 120-750 flowing words will pad; the countermeasure that works
is shrinking the ask (the `_story_word_target` scaling was the right move — consider
lowering the floor further) plus adding the wrap-up/speculation patterns to the
mechanical checker since the prompt alone demonstrably doesn't hold.

### M8. Unverifiable relational and analytic claims published as facts — MEDIUM

- id=411: "Senator Darline Graham announced her candidacy for the seat left by **her
  brother, Lindsey Graham**." A family relationship stated as fact. The
  electoral-claims guard exists precisely because the model once invented a
  Graham-vs-Collins race; **family relationships are the same fabrication class with
  no guard**. (Whether or not this instance is true, nothing checked it.)
- id=411: "President Donald Trump **influenced the timing and focus** of the new race
  announcement" — an analytic causal claim, not an event.

### M9. Quality telemetry is unobservable — MEDIUM

All validator activity (dropped facts, grounding rejections, skipped issues,
suppressed duplicates) exists only as container-log lines — and the container
restarted at 23:01 UTC today (deploys are frequent), so history is gone. It is
currently **impossible to answer** "how often does the fact validator fire?" or "how
many issues were skipped fail-closed last week?" — the exact numbers this audit
wanted. The refresh-state dict already tracks counters in memory; persisting
per-run validator counts (a small JSON column or table) would make quality
measurable and regressions visible.

---

## What is working well

Credit where due, because the architecture is sound:

- **Number/name/electoral grounding with fail-closed retries** — no invented
  statistics or invented officials found in 40 issues; the documented pre-fix
  failure modes (fabricated "1.5°C target", invented Schumer quote, invented
  Graham-Collins race) did not recur.
- **Story length scaled to fact count** removed the pad-to-350-words fabrication
  incentive.
- **Self-calibrating cluster merge** (size-capped threshold scan) and the
  two-topic cluster splitter are thoughtful fixes to real observed failures.
- **The post near-dup guard and post-first ordering** (posting before slow story
  generation) both trace to real incidents and work as designed.
- The audit-trail comment discipline in this file is exceptional — nearly every
  constant cites the production incident that set it.

## Prioritized recommendations

1. **Placeholder-token rejection** (H4) — one regex, applies to all four text
   surfaces. Cheapest fix, publicly-visible defect class.
2. **Action-surface gate: stop counting last-name-only official matches** (H3) —
   also stops the false officials-tagging that misled the gate.
3. **Fix the identity model** (H1+H2): match topics on content overlap (entities/
   facts), not LLM-title cosine; when `_full_story_should_invalidate` says the
   story changed, mint a new row instead of overwriting the posted one.
4. **Extend `_META_PHRASES` + minimum-specificity gate** (M5) — kills the vacuous
   issue class entirely.
5. **Per-fact topical grounding** (M6).
6. **Persist validator counters per run** (M9) — makes every other fix's effect
   measurable.
7. **Mechanical wrap-up/speculation patterns + family-relationship guard**
   (M7, M8).
8. Longer-term: the 1.2B model is the ceiling on issue/story quality. The
   validators mean it fails *safe*, but a meaningful share of published text is
   filler, and fail-closed skips discard usable stories at an unmeasured rate
   (see M9). Worth evaluating a 3-8B model on the Pi's budget, or reserving a
   larger model for the two public-facing surfaces (posts, stories).
