# Trend posts ("On the record"): gates, matching and the image trial

When a U.S. policy topic trends on Bluesky, Civitas posts the official
record behind it: a recorded vote, a bill, the day's Congressional Record,
an agency's document, or an Action Center issue. The post is
`Trending on Bluesky: "<name>". On the record: <record>`. Every word is
template text, a stored record field, or Bluesky's trend name quoted and
attributed. No model writes any of it. Code: `analyze/trend_poster.py`,
`analyze/trend_evidence.py`, `analyze/trend_relevance.py`,
`services/record_cards.py`.

## Why the matches are pinned

A post about a trend could be wrong in two ways: about the record, or
about what connects the record to the trend. The template rules out the
first: it only restates stored fields. The second needs a connection a
reader can check by eye, so a trend posts only on one of four matches:

| Match | Rule |
|---|---|
| issue | an Action Center issue (headline and lede) at cosine >= 0.5 to the trend, that names a distinctive entity from the trend's own name (an entity in few stored issues, the national monitors' test), for a trend Bluesky describes. The post shows the official record the issue already links (a vote on its bill, a Federal Register document, the bill) when it has a recent one |
| act | the trend names a statute's full title: the whole capitalised run before "Act" is a record's title (a trailing "of <year>" aside), or the trend also gives the record's bill number |
| nominee | a nominee a roll call names, named in full, by a trend the appointment vote finds is about the nomination itself |
| member | a member named in the trend (in full, or by a surname under the Action Center's collision safeguards), and a recent bill that member sponsored at cosine >= 0.5, for a described trend |

Every record has to be dated no later than two days after the trend began,
and fall within the sitting Congress. A record more than 30 days older
than the trend counts only when the trend's own name names it (an act's
title or bill number). A vote with no measure title and no bill is never a
record. A person named with no record of their own on the topic is not a
match.

### What the first version got wrong, and the rules that fixed it

The first backtest posted 11 trends; read by hand, 1 to 3 attached the
right record (labelled by the reviewer). Each rule above answers one
failure, and none of them is a threshold change:

| First-version failure (count of 11 posts) | What went wrong | Rule |
|---|---|---|
| a short act name matched a longer title of another act (1) | a short name inside a longer title is another act | full-title equality, or the bill number |
| a foreign law's name matched a U.S. act of the same short name (1) | a jurisdiction in front of the title names another law | the whole capitalised run, never a tail of it |
| trends about an official's actions anchored on that official's confirmation vote (5) | a person named in a story anchored their confirmation; several were 2025 votes attached to 2026 stories | nominee only when the trend is about the appointment (appointment kNN), and 30-day recency unless the trend's name names the record |
| a slogan trend matched an untitled "On Passage" vote (1) | a vote with nothing to show what was voted on | no record without a measure title or a bill; a hidden title is replaced by the bill number, never left blank |
| a polls-across-races trend matched one race's story (1) | cosine matched a story to its theme | issue matches need a description and a distinctive entity from the trend's name |
| (intermediate run) a trend about agency data matched the 2025 passage vote of the act it mentioned | the act was the story's object, named in passing | an old record counts only when the trend's name names it |

## The gates and their calibration

Four kNN votes over hand-labelled Bluesky trend topics, written to
`app/data/trend_gates_calibration.json` by `scripts/calibrate_trend_gates.py`.
The labelled sets are trend topics from the feed generators of Bluesky's
trending-topics account. Each set's `_source` and `_rubric` say how it was
sampled, and that an AI agent labelled it. The method is leave-one-out,
with near-duplicates (cosine > 0.95) excluded and 95% bootstrap intervals.

| Gate | Examples (positive) | k | Threshold | LOO AUC | Precision | Recall |
|---|---|---|---|---|---|---|
| relevance (U.S. policy story), removed 2026-10-16 | 350 (93) | 15 | 0.5 (design decision) | 0.91 [0.88, 0.94] | 0.74 [0.64, 0.83] | 0.61 [0.51, 0.70] |
| tragedy | 501 (143) | 25 | 0.5 (90% recall) | 0.97 [0.96, 0.99] | 0.84 | 0.90 |
| political violence (among tragedies), removed 2026-10-16 | 143 (45) | 5 | 0.8 (90% precision) | 0.91 [0.85, 0.96] | 0.93 [0.80, 1.00] | 0.56 [0.40, 0.70] |
| loaded language (trend name) | 759 (145) | 25 | 0.3 (best F2) | 0.89 [0.86, 0.92] | 0.54 [0.47, 0.60] | 0.79 [0.72, 0.86] |
| appointment (nominee anchor) | 549 (31) | 15 | 0.55 (90% precision) | 0.99 [0.98, 0.99] | 0.91 [0.77, 1.00] | 0.61 [0.44, 0.79] |

On its own the relevance vote only kept out off-topic trends. The pinned
match is what makes a post. The loaded-language vote reads topic as much as
framing. A recall target of 90% flagged a third of plainly named topics
(precision 0.34 at threshold 0.2), so its threshold is the F2 point instead:
a false flag only skips a trend. Adding the 350 relevance topics' names as
mostly neutral examples was needed too. Without them, a plainly named topic
on a subject with loaded neighbours (an agency, a lawsuit) was voted loaded
on topic alone.

The neutrality gate on the trend's name is the loaded vote plus
`grounding`'s source-independent checks: electioneering, editorializing,
and an event stated as done that the trend's own description only urges.
A failing trend is skipped. A loaded bill title is left out of the post
and the image, and the bill number stands in.

### Tragedies and violence

These are not excluded outright. A trend the tragedy vote flags is eligible
only when its record is an official one: a vote, a bill, the day's record
or an agency's document, not an Action Center issue. (Until 2026-10-16 its
political-violence vote also had to be at least 0.8; see below.) The post states only what that record says.
The template has no slot for victims or perpetrators. No record, no post.

### 2026-10-16: the political-violence vote is removed

Political violence is in scope: an agent's shooting, a death in immigration
detention, an attack on the Capitol. The vote meant to let those through
refused 44% of the political tragedies it was labelled on (leave-one-out
recall 0.56), and the official record it was paired with already says
there is a government angle. A tragedy now needs only the official record.

Replayed over the backtest with the vote removed: the same 3 posts. Of the
62 trends the tragedy vote flags, 55 have no pinned record and 7 are
anchored only on an Action Center issue, all 7 about immigration
enforcement (detainee listings, detentions, hiring) — skipped under both
rules because an issue is not an official record. No tragedy in the window
had an official record, so no decision changed. The tests post an agent's
shooting, a detention death and a Capitol attack anniversary, each with a
passed Act as its record (`test_political_violence_with_an_official_record_posts`).

What kept those stories off Bluesky was the official-record rule, not the
vote: the immigration-enforcement trends above match only news issues. So
a tragedy may now also post a **confirmed Action Center issue**: a news
issue (not developing, not a duplicate) reported by two or more outlets,
whose lede is a claim compose() verified in its source
(`tragedy_issue_refusal`). The post quotes that lede, credited to its outlet
("Civitas Action Center, <date>, quoting <outlet>: <lede>"), never the
headline, and the lede must pass the neutrality gate and the loaded-language
vote. Political reading is what the trend and record already carry:
Bluesky filed the trend under politics, and the record passed the Action
Center's U.S.-policy gate.

Replayed: the 7 immigration-enforcement trends all anchor on one issue from
2026-09-24, from before ledes were verified claims (its lede, "ICE has
wasted tens of millions of dollars with 'uninformed' detention plans", is a
characterisation no check verified), so they are still refused, rightly.
The backtest posts the same 3. Of every issue in the database that could
serve a tragedy (14 confirmed, multi-outlet, verified-lede issues since
2026-10-02), the drafts are verbatim attributed claims; the loaded-language
vote refuses 3 of them ("... alleging Trump's taxpayer-funded ads are
illegal propaganda", and two plain ones at 0.30 and 0.31), and 2 are too
long for a post.

## Selection, caps and the party audit

- Selection is party-blind. A trend's score is its rank weight (1 for the
  first listed, falling linearly) times the match strength (1 for an exact
  act or nominee match, the cosine otherwise). Nothing else is read.
  `test_trend_selection_is_party_blind` swaps every party label and checks
  the choice doesn't change.
- Caps: one post a run, two an Eastern day, at least four hours apart. The
  same record goes at most once in 14 days, and never a record an issue post
  already carried.
- Timing: posts go only inside the measured posting window (10:00 to 01:00
  Eastern, `bluesky_post_windows.json`). An eligible trend outside it is
  held. At the next run Bluesky's list is read again, and a held trend that
  has left the list is dropped.
- Every evaluated trend gets one `TrendCandidate` audit row: its scores,
  match, record, the parties of the members it names and of the record's
  sponsors, and the decision and its reason. The admin Trends tab compares
  the posted trends' party lean (majority of those parties, D vs R) with
  the eligible pool's, using an exact two-sided binomial test.
  `check_party_balance` raises an ops alert at p < 0.01 once 10 leaning
  trends have posted.

## 2026-10-16: Bluesky's category replaces the relevance vote

The relevance vote was the one gate whose labels Bluesky already publishes:
getTrends files every trend under a category (politics, entertainment,
sports, science-tech, culture, business, other). The vote is gone, and only
a trend filed under "politics" goes to record matching
(`trend_relevance.POLITICS_CATEGORY`). The pinned record stays the
precision guarantee.

- **The backtest's 3,084 trends** come from the trending account's topic
  history, which carries no category, so the replay measures the vote's
  removal (every trend treated as politics, the most a category gate could
  let through), on today's records and the same caps: 4 posts against 3.
  The fourth is an "Insurrection Act" trend, matched to the bill of that
  title, that the vote had scored 0.28. Of the 2,725 trends that reach
  matching without the vote, 2,686 find no pinned record.
- **The 106 trends production has recorded with their category**
  (2026-10-15/16): the vote passed 20, and Bluesky filed 19 of those under
  politics (the 20th, business). Replayed, the category gate changes no
  post; one decision moves from ineligible to skipped (a foreign election
  whose issue record a later check refuses). The vote refused 58 of the 59
  trends Bluesky filed outside politics.

`trend_relevance_examples.json` is removed. Its 350 names still serve as
mostly neutral examples in the loaded-language set (see above).

## Backtest

Over the 3,076 English Bluesky trend topics from 2026-09-14 to 2026-10-15
(the trending account's topic history plus the first live getTrends samples):

| Stage | Trends |
|---|---|
| fails the relevance vote | 2,326 |
| skipped by the neutrality gate | 357 (72 of the 471 relevant ones) |
| tragedy without a political reading | 27 |
| political tragedy (needs an official record) | 35 |
| passes every gate, so goes to record matching | 331 |

Those that pass go to record matching, against a read-only snapshot of
the production database, with the caps applied, posting at the first
window hour after each trend began (the replay is a scratch script, not
committed).

### Precision

Rubric: a post (or eligible candidate) is **correct** when its record is
the measure, nomination, rule or event the trend is about: what a reader
who saw the trend would recognise as its record. A related record about
something else (the official's confirmation for a story about what they
did since; a law the story mentions in passing) is **incorrect**, as is a
record of another act or jurisdiction.

Final rules, 366 trends that passed the gates (2026-09-14 to 10-15):
3 eligible, 3 posted, on 3 days. Party lean of the posted: D 1, none 2.

| Trend (as Bluesky named it) | Match | Record | Label |
|---|---|---|---|
| an act's short title | act | the House passage vote on that act (2025), by its bill number | correct: the act the trend names. The trend's own news, a later Senate vote, wasn't in the snapshot; the post states the House vote and its date |
| a senator's resolution | member | that senator's resolution's Senate discharge vote (2026-09-29) | correct |
| a court case about agency records | issue | the Action Center issue on the Supreme Court's order in that case | correct |

Posted precision 3/3, eligible precision 3/3. The volume is under one a
week on this sample, which the standing rule (quality over quantity)
accepts; the history topics' names are cut at 24 characters and many
have no description, so live trends should match somewhat more often.
The first version, for comparison: 16 eligible, 11 posted, 1 to 3
correct. Of its 11, the new rules drop all 8 wrong ones and the one the
reviewer called plausible (it named its act only in its description, and
the act's bill was dated eight months earlier); they keep the act and
court-case trends (the act now matched to its own record, not another
act's) and add the resolution (the first version found it but couldn't
fit the vote's long title in a post; the bill number now stands in for
it). The trends and records by name are in the pull request.

## How long trends stay listed

`TrendObservation` keeps every hourly listing, and the admin tab reports
how long each topic stayed listed, measured from its `startedAt`. The first
hour of 10-minute samples (2026-10-15) saw five topics leave the list after
a median of 28 hours. That sample is far too small to set anything from.
The longest hold is the nine hours from 01:00 to 10:00 Eastern, so most
held trends should still be listed when the window opens. The admin figure
is the one to watch.

## The image, and the trial

Before this, Civitas had never posted a native image. Every post carried
the page's link card. In 3,351 posts across 35 Bluesky trend feeds
(observational; log(1 + likes) regressed on log(1 + followers) and embed
type, against text-only posts, with 95% bootstrap intervals):

| Embed | All trend feeds | Politics trends |
|---|---|---|
| image | x1.01 [0.85, 1.21] | x1.20 [0.95, 1.49] |
| link card with thumbnail (Civitas's format) | x0.51 [0.44, 0.59] | x0.61 [0.50, 0.76] |

Within the same authors, image posts did about 1.24 times as well as card
posts, with overlapping ranges. The comparison is observational, so trend
posts run a trial. For their first 28 days, Bluesky gets the image on even
Eastern days and the link card on odd ones (`BroadcastPost.embed_kind`). A
post about an issue always gets the card, since an issue has no record card.
`broadcast.refresh_engagement` reads each post's public like, repost and
reply counts for 14 days. After the trial, the image stays. The plumbing is
generic (`publish(image_url=, alt_text=, embed_kind=)`), so trying it on
other kinds is a one-line change once the trial has data.

The image is the record's card, rendered server-side by the frontend's
`/api/og` (`?vote=`, `?bill=`, `?congressEvent=`, `?explore=`) from
`GET /api/cards/<key>`. Each image carries its source line ("Source: ... ·
civitas-research.org/... · data as of ..."). Its alt text is built from the
same card object and states every figure the image shows. The pipeline
reads the PNG inside the stack (`FRONTEND_INTERNAL_URL`). A render failure,
a non-PNG or anything over Bluesky's 1,000,000-byte limit falls back to the
link card, and the post is never held for its picture.

The link card on odd days is drawn from the linked page's own `og:image`.
Until 2026-10-15 a roll call's, a bill's and an Explore document's page
named the generic site image there, so link-card days before then pitted a
generic thumbnail against the record's picture; since then each of those
pages names its record card, and the two arms differ only in format.
