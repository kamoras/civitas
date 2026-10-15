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
| issue | an Action Center issue headline at cosine >= 0.5 to the trend (all-MiniLM-L6-v2); the post shows the official record the issue already links (a vote on its bill, a Federal Register document, the bill) when it has one |
| act | a statute's short title in the trend ("... Act"), found in a record's own title |
| nominee | a nominee a roll call names, named in full in the trend |
| member | a member named in the trend (in full, or by a surname under the Action Center's collision safeguards), and a bill that member sponsored at cosine >= 0.5 to the trend |

A named member with no record of their own on the topic is not a match:
a post about a person is not a post about a record. Every record has to be
dated no later than two days after the trend began, and fall within the
sitting Congress.

## The gates and their calibration

Four kNN votes over hand-labelled Bluesky trend topics, written to
`app/data/trend_gates_calibration.json` by `scripts/calibrate_trend_gates.py`.
The labelled sets are trend topics from the feed generators of Bluesky's
trending-topics account. Each set's `_source` and `_rubric` say how it was
sampled, and that an AI agent labelled it. The method is leave-one-out,
with near-duplicates (cosine > 0.95) excluded and 95% bootstrap intervals.

| Gate | Examples (positive) | k | Threshold | LOO AUC | Precision | Recall |
|---|---|---|---|---|---|---|
| relevance (U.S. policy story) | 350 (93) | 15 | 0.5 (design decision) | 0.91 [0.88, 0.94] | 0.74 [0.64, 0.83] | 0.61 [0.51, 0.70] |
| tragedy | 501 (143) | 25 | 0.5 (90% recall) | 0.97 [0.96, 0.99] | 0.84 | 0.90 |
| political violence (among tragedies) | 143 (45) | 5 | 0.8 (90% precision) | 0.91 [0.85, 0.96] | 0.93 [0.80, 1.00] | 0.56 [0.40, 0.70] |
| loaded language (trend name) | 759 (145) | 25 | 0.3 (best F2) | 0.89 [0.86, 0.92] | 0.54 [0.47, 0.60] | 0.79 [0.72, 0.86] |

On its own the relevance vote only keeps out off-topic trends. The pinned
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
only when its political-violence vote is at least 0.8 **and** its record is
an official one: a vote, a bill, the day's record or an agency's document,
not an Action Center issue. The post states only what that record says.
The template has no slot for victims or perpetrators. No record, no post.

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

Matching those to records, then applying the caps and the party audit,
needs the records database. That part is reported in the pull request that
added this, not here.

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
