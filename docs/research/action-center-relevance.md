# How good is the Action Center's relevance gate?

The Action Center's first gate (`action_center._filter_policy_relevant`)
decides which feed articles are about U.S. policy at all. Until 2026-10 it
kept an article whose embedding scored at least 0.20 against any of 24
hand-written policy prototypes, after a 0.82 penalty for articles that
matched no U.S.-civic prototype. The only evidence for it was a comment
quoting a handful of hand-picked headlines; an earlier comparison logged
how often a learned prototype set agreed with it (68%), which says nothing
about which one was right. This study labelled real feed articles and
measured. Done 2026-10-10.

**Result.** The prototype gate kept 62% relevant articles and found 80% of
the relevant ones. A similarity-weighted kNN vote against the labelled
articles keeps 89–97% relevant articles while finding 76–87%, on articles
published one to six weeks after its reference set. That replaced the
prototypes for articles. The digest filter in front of it was also dropping
single stories (18 of the 19 its body check dropped), and was fixed.

## The labelled sample

**Articles.** Every article the configured national feeds
(`news_feeds.NEWS_FEEDS`) carried, read through the pipeline's own parser
(`fetch_news_articles`, with its opinion filter and URL dedup): the live
feeds on 2026-10-10, plus Wayback Machine snapshots of the same feed URLs
from 2026-08-03 to 2026-10-01 (AP, NPR, PBS, BBC and Roll Call are archived;
The Hill and Politico are not, so they appear only live). 627 articles.
Production keeps no dropped articles, so this is the only way to see both
sides. A second sample, 200 drawn at random from the 545 articles
production sent to issue extraction between 2026-08-27 and 2026-09-24 (the
filled prompts in `llm_generation_samples`), is used only as extra
reference examples and a precision check, never as test data, since
production had already kept them (595 such articles, 545 of them not also
in the feed sample).

The live AP feed (feedx.net) returned no article dated within 48 hours: its
newest item is from 2026-09-09. The pipeline logs that as "Fetched 0
articles" at INFO, nothing louder.

**Rubric** (written before labelling, stored with the examples). Relevant =
a U.S. policy, legislation, government or civic story a reader of this site
would want: the U.S. government acting (Congress, the President, agencies,
state governments on policy, budgets); U.S. courts on law, policy or
government; U.S. elections and campaigns; U.S. policy issues framed as
policy; U.S. foreign policy and military action with the U.S. government as
an actor; civic action aimed at U.S. policy. Not relevant: foreign politics
or conflict with no U.S. government actor; disasters without a
government-response angle; crime and trials of private people; business
without a regulatory angle; sports, entertainment, science and health
human-interest. One labeller. 75 of the 627 (12%) are marked ambiguous: a
disaster story that mentions an agency in passing, a foreign story with the
U.S. as a minor party, a feature on a political figure.

Because the old prototypes deliberately included international politics,
every result is also given under a **broad** label that counts foreign
politics and foreign conflict as relevant.

The labelled set is `backend/app/data/policy_relevance_examples.json`
(827 articles, the gate's exact input text: title plus the first 200
characters of the description, with politicians' names replaced by
`[name]`).

## The prototype gate

Over the 602 feed articles the digest filter let through, with 95%
bootstrap intervals:

| Label | Kept | Precision | Recall | F1 |
|---|---|---|---|---|
| strict | 348 | 0.615 [0.563, 0.665] | 0.799 [0.750, 0.847] | 0.695 |
| strict, unambiguous only (529) | 305 | 0.646 [0.591, 0.701] | 0.811 [0.760, 0.857] | 0.719 |
| broad | 348 | 0.773 [0.730, 0.816] | 0.715 [0.669, 0.760] | 0.743 |

Counting the 25 digest drops as drops (627 articles), strict recall is
0.740. Of the 200 production-kept articles, 87% were relevant.

**What it wrongly kept** (134 of 348): foreign politics 30, disasters 29,
foreign conflict 25, crime 20, markets and business 10, human interest 10,
science and health 6, sports and entertainment 3. Disasters came from the
"Extreme weather, climate disaster" prototype: "Storms and possible
tornadoes pummel the Northeast US, flooding roads and damaging beaches"
scored 0.47, higher than most congressional stories, and thirteen articles
on one Himalayan flood passed. Others: "Switzerland rejects stricter
interpretation of its neutrality" (0.40), "Saudi Arabia, Turkey and Pakistan
sign defence pact" (0.42), "Six smugglers jailed for manslaughter over worst
Channel small boats disaster" (0.34), "U.S. stocks edge further from their
records as oil prices keep swinging" (0.31).

**What it wrongly dropped** (54): U.S. campaign coverage 27, U.S.
government 16, U.S. foreign policy 5, policy issues 5, courts 1. Campaign
stories score low against every prototype: "Democrats drop $5M into Kansas
Senate race" (0.18), "Battle for the Lehigh Valley: Can GOP hold its
razor-thin margin?" (0.18), "Carpetbagger allegation, misplaced valor make
Nevada's safe red House seat competitive" (0.16), "Minnesota AG sues Texas
governor to extradite ICE agent for trial" (0.19).

**No threshold fixes it.** The two score distributions overlap: median
0.27 for relevant articles and 0.17 for the rest, interquartile ranges
0.21–0.34 and 0.11–0.25. Average precision is 0.705 against a base rate of
0.445.

| Threshold | Kept | Precision | Recall | F0.5 |
|---|---|---|---|---|
| 0.16 | 435 | 0.575 | 0.933 | 0.623 |
| 0.18 | 393 | 0.593 | 0.869 | 0.633 |
| **0.20 (was in use)** | 348 | 0.615 | 0.799 | 0.645 |
| 0.22 | 301 | 0.641 | 0.720 | 0.656 |
| 0.24 | 259 | 0.668 | 0.646 | 0.663 |
| 0.28 | 175 | 0.703 | 0.459 | 0.635 |
| 0.32 | 116 | 0.733 | 0.317 | 0.581 |

Precision passes 0.70 only once recall is under 0.46. 0.20 sat close to the
F0.5 optimum (0.24) of a curve with no good point on it.

## Candidates, on held-out articles

Each candidate was tuned on one part and scored on another. Two kinds of
split: random halves stratified by label, and **temporal** splits (tune on
articles before a cut, test on articles a week or more after it), which
matter for kNN: several outlets cover one event, and a random split puts
the same story on both sides. F0.5 weighs precision over recall, for the
Action Center's fewer-but-better aim. Thresholds were tuned to maximise it.
McNemar is exact, two-sided, on per-article correctness against the
prototype gate.

- **(a)** the same prototypes with a re-tuned threshold;
- **(b)** the titles (or titles and summaries) of the 18 published issues
  with `summary_source` set as prototypes, with and without the U.S.
  penalty;
- **(c)** kNN: the similarity-weighted share of relevant examples among the
  k nearest labelled articles, with k chosen by leave-one-out on the tuning
  part, with and without the 200 production-kept examples;
- **(d)** the current prototypes with the disaster prototype rewritten as
  government disaster response (d1), plus campaign and congressional
  leadership prototypes (d2), and with six negative prototypes (natural
  disaster, crime, sports and entertainment, business, foreign domestic
  politics, foreign war) subtracted (d3: d2's set; d4: the current set).
  These were written after seeing the error classes above, which came
  from the whole sample, so their held-out scores lean optimistic.

Strict labels, temporal split (tune before 2026-09-09, test from
2026-09-16, 240 articles):

| Candidate | Precision | Recall | F0.5 | McNemar p |
|---|---|---|---|---|
| prototype gate (0.20) | 0.609 [0.528, 0.688] | 0.743 [0.660, 0.822] | 0.632 | — |
| (a) re-tuned, 0.237 | 0.673 | 0.619 | 0.662 | 0.39 |
| (b) issue titles + penalty | 0.774 | 0.788 | 0.777 | 3e-4 |
| (b) issue titles and summaries + penalty | 0.781 | 0.788 | 0.782 | 6e-5 |
| (c) kNN, k=15 | 0.966 [0.921, 1.000] | 0.743 [0.659, 0.821] | 0.911 | 3e-8 |
| (c) kNN with production-kept, k=25 | 0.938 [0.885, 0.980] | 0.805 [0.727, 0.875] | 0.908 | 4e-10 |
| (d2) edited + added prototypes | 0.821 | 0.566 | 0.753 | 0.025 |
| (d3) d2 minus negative prototypes | 0.871 | 0.717 | 0.835 | 2e-5 |
| (d4) current minus negative prototypes | 0.851 | 0.655 | 0.803 | 6e-4 |

Mean held-out F0.5 over 30 random splits: prototype gate 0.644, (a) 0.640,
(b) 0.70–0.76, (c) 0.91–0.92, (d3) 0.82, (d4) 0.79. At the other two temporal
cuts (2026-09-01 and 2026-09-20) kNN is again first, (d3) second, and the
prototype gate and (a) last.

Under broad labels kNN (trained on strict labels) still has the best F0.5
(0.86–0.87 vs 0.77–0.79) and much better precision (0.94–0.98 vs 0.80–0.82)
but lower recall (0.58–0.66 vs 0.67–0.71), because it was taught that
foreign politics is not relevant. McNemar is not significant under broad
labels (p = 0.08–0.69).

## What shipped

kNN, as `relevance_votes` in `action_center.py`, with every labelled article
as the reference set. `backend/scripts/calibrate_policy_relevance.py` picks
k and the threshold over three temporal cuts and writes them, with its
held-out figures, to `app/data/policy_relevance_calibration.json`. F0.5 is
flat near its peak (0.924 at k=41, 0.85; one standard error 0.011), and at
0.85 every article of a story the reference set had not met yet could fall
under the bar: the first day of the U.S.-Russia diesel deal read like
Ukraine-war coverage, and 0 of its 6 articles passed at two of the cuts,
against 4–6 at 0.75. So the script takes the lowest threshold within one
standard error of the best: **k=41, vote ≥ 0.80**.

Production queries carry real names while the reference set has
`[name]`, so the shipped setting was re-measured that way, against the
prototype gate on the same articles:

| Cut (test 7+ days after) | Articles | kNN precision | Prototype precision | kNN recall | Prototype recall | McNemar p |
|---|---|---|---|---|---|---|
| 2026-08-30 | 338 | 0.888 [0.835, 0.935] | 0.622 | 0.865 [0.809, 0.916] | 0.769 | 1e-12 |
| 2026-09-09 | 240 | 0.930 [0.875, 0.978] | 0.609 | 0.823 [0.748, 0.891] | 0.743 | 1e-10 |
| 2026-09-22 | 163 | 0.971 [0.925, 1.000] | 0.674 | 0.761 [0.670, 0.846] | 0.727 | 3e-5 |

It keeps about a quarter fewer articles (321 against 426 over the three
test sets) and is better on precision and recall at every cut.

**What it gets wrong** (the 2026-09-09 cut, 27 of 240): it drops new U.S.
stories whose nearest examples are another kind of story — the diesel
deal's first articles read like Ukraine-war coverage, and five on a planned
public military execution read like crime coverage — plus ICE shooting
reports and a state banning child marriage. It keeps a few foreign
political stories that read like U.S. ones (a coalition crisis in Germany,
a resignation in Serbia, an Iranian court ruling) and features about the
press corps.

**Consequence to know.** Foreign political stories with no U.S. government
actor no longer pass. Two of the 18 issues published with the current
extraction were of that kind (an election in Brazil, school protests in
France). That follows the rubric and AGENTS.md's "filters articles for U.S.
policy relevance"; if the Action Center should carry them, relabel those
categories and rerun the calibration rather than restoring the prototypes.

The trending-topic mask (`_compute_trending_boost`) still uses the
prototypes and 0.20. Trending topics are short search phrases, which the
labelled articles don't represent; Bluesky's trending feed needs
credentials this measurement didn't have, and Google Trends returned ten
topics, all correctly masked out. It is unmeasured.

## 2026-10-16: the reference labels itself

The labelled set above was drawn by one labeller, once, and could only go
stale. The gate now votes over the feed articles it has already judged
(`feed_articles`), each labelled by what the publisher and Civitas's own
records state, never by hand (`app/policy_relevance.py`):

- **relevant**: filed under U.S. politics by its publisher (NPR's politics
  feed, Politico's Congress feed, Roll Call, or a politics desk's URL
  section: PBS `/newshour/politics/`, The Hill `/homenews/`, `/regulation/`,
  `/policy/`; `news_feeds.NEWS_FEEDS`), or naming a sitting member of
  Congress or justice (full name), the sitting president (full name or
  surname) or a federal agency (its Federal Register name, or its
  all-capitals short name);
- **not relevant**: filed under another desk (NPR's world feed, BBC World,
  PBS's world and arts desks) and naming none of those;
- **no label**: anything else (PBS's nation, health and science desks, The
  Hill's video clips).

k and the threshold are refit on the first run of each UTC day, leave one
out, by F0.5, ties to the lower threshold. Officials' names are masked in
what is embedded, in the reference and in what is judged alike; until now
the reference was masked and the judged text was not. Until the store
holds 150 articles of each label, the seed (the texts of
`policy_relevance_examples.json`, labelled from their outlet and masked
names, their hand labels removed) fills in.

**Agreement with the labels it replaced.** On the 675 seed rows these
labels cover (of 827), 91% agree with the hand labels (relevant labels
0.90 precise, not-relevant labels 0.93). A kNN vote fitted on these labels
alone, on the three temporal splits above, scored against the hand labels:

| Split | Hand-label vote: precision, recall | Publisher-label vote: precision, recall |
|---|---|---|
| 2026-08-30 / 09-06 | 0.885, 0.880 | 0.883 [0.83, 0.93], 0.819 |
| 2026-09-09 / 09-16 | 0.927, 0.850 | 0.938 [0.89, 0.98], 0.758 |
| 2026-09-22 / 09-29 | 0.975, 0.839 | 0.963 [0.91, 1.00], 0.828 |

Precision is the same within its intervals; recall is up to nine points
lower on the seed alone (without the URL-section labels, which the seed
has no URLs for).

**Replay against what published, 2026-10-02 to 10-15.** The 27 issues
published in the window, their 47 source articles (title and description
from each page's own metadata; for 17 pages behind a bot wall, the lines
the issue quotes from them), judged by the old gate and the new; an issue
survives when every article its lines are quoted from passes. The new
gate's reference is the seed plus the PBS and BBC items archived by the
Wayback Machine before 2026-10-02 (94 labelled), standing in for the
store's first day:

| Date | Published | Old gate | New gate |
|---|---|---|---|
| 10-02 | 1 | 1 | 1 |
| 10-03 | 1 | 0 | 0 |
| 10-04 | 1 | 1 | 1 |
| 10-05 | 5 | 5 | 4 |
| 10-06 | 1 | 0 | 0 |
| 10-07 | 4 | 3 | 3 |
| 10-08 | 1 | 1 | 1 |
| 10-09 | 3 | 2 | 2 |
| 10-10 | 1 | 0 | 1 |
| 10-11 | 2 | 1 | 2 |
| 10-12 | 2 | 2 | 2 |
| 10-13 | 3 | 3 | 3 |
| 10-15 | 2 | 2 | 2 |
| **Total** | **27** | **21** | **22** |

(The old gate, as calibrated on 2026-10-10, no longer passes six issues
that published before or during its first days.) The one issue the new gate
loses is a foreign election with no U.S. actor, which the rubric above
already excluded; it gains two days of the Russian diesel-deal story,
whose first articles the old gate read as Ukraine-war coverage.

On the whole archived PBS and BBC stream for the window (226 items), the old
gate keeps 86 and the new 110: it drops 1 the old kept (the same foreign
election) and adds 25, among them the jobs report, an FBI internal report,
student-loan deadlines, deportation cases and the diesel deal, and also
three Nobel-prize live streams, two features on GLP-1 drugs and an AI
feature, which the vote takes for politics-desk video. On the seed alone
(a fresh database's first hours) it drops 12 the old kept, among them an
ICE policy memo and a new DHS fee; the store's first day of URL-section and
agency labels brought those back, which is why the seed steps aside only
once each label has 150 stored articles.

## 2026-10-16: the clustering and restatement cuts refit themselves

Both cuts were fitted once to pairs one labeller judged (564 article pairs,
204 claim pairs). Each is now refit once a day from the stored data
(`action_center.refit_cuts`), as the lowest cut no pair the data itself
says is two stories (or two facts) reaches, plus a stated margin
(`CUT_MARGIN`, 0.10, a choice reported beside every fit):

- **clustering**: pairs of stored feed articles that share a distinctive
  entity and whose own text dates the event two or more days apart (a
  weekday read against the publication date, or a month and day). On the
  301 articles of 2026-09-28..10-15 (257 such pairs) the fit is 0.60, the
  cut 0.70. Clustering also never joins two such articles.
- **restatement**: pairs of lines from two issues published within a day
  of each other as separate stories, sharing a distinctive entity, leaving
  out any pair that quotes one sentence (five words in a row). On the
  issues of the last three weeks (76 lines, 380 pairs) the fit is 0.69, the
  cut 0.79. Lines of issues days apart were tried first and rejected: an
  ongoing story's later issue restates the earlier one's facts, so that fit
  read 0.94.

The margin covers what no stated fact labels: one actor's different acts
on the same day, and two facts of one event. The fits alone reach those
pairs: at 0.60, on the same 301 articles, 50 pairs merge, 13 of them pairs
the hand judgments called different events.

| Cut | Article pairs over the cut (48 h) | [diag] hand: same / different |
|---|---|---|
| hand-judged 0.71 | 19 | 18 / 1 |
| refit 0.60 + 0.10, with the day veto | 22 | 20 / 2 |
| refit 0.60 alone, with the day veto | 50 | 36 / 13 |

| Restatement cut | Claim pairs flagged (of 204) | [diag] hand precision / recall |
|---|---|---|
| hand-fitted 0.75 | 27 | 0.96 / 0.42 |
| refit 0.69 + 0.10 = 0.79 | 18 | 0.94 / 0.27 |

The restatement cut moves up, so fewer second claims are dropped as
restatements: a missed restatement counts one fact twice toward the
two-claim gate, a false one drops a real fact and often the issue with it.

## The digest filter

`_digest_reason`'s body check drops an article whose description splits
into three or more items naming disjoint entities that the headline doesn't
account for. On this sample it dropped 19 articles that way: one was a
curated preview of several stories; 18 were single stories (14 relevant).
Sixteen of those were pushed to three items by one of two things: an abbreviation's
period read as a sentence break ("after U.S. | Immigration and Customs
Enforcement agents", "calls for Rep. | [name] to drop out"), or attribution
read as an item ("(Image credit: …)", "(AP Photo/…)", "… reports.", "…
discussed it with …"). `_split_body_items` now joins across abbreviations
and drops credits and a closing sign-off. On the same 627 articles the body
check drops 2 (both still single stories: a correction notice ahead of the
story, and an interview that names its subject by acronym in one sentence
and in full in another). The curated preview is no longer caught. The
title patterns are unchanged.

A two-story briefing escapes that check by construction: its headline names
both stories ("Government spent millions on campaign ads. And, federal
agents raid a lawmaker's home") and its body has two items, under the three-item
floor. `_parallel_stories` (2026-10-15) catches the shape both halves share:
the headline splits into items naming disjoint entities, the body's items are
disjoint too, and each body item names exactly one headline item's entities,
every headline item named. Measured on 1,473 stored articles with a headline
and a body (race coverage plus the Action Center's claim sources,
2026-10-11..15):

| headlines with 2+ disjoint items | 28 |
|---|---|
| dropped by `_parallel_stories` | 5, all two-story briefings (three NPR "X. And, Y" items, two "Live updates: A; B") |
| left: multi-story live blogs | 11 (the body names both stories in one item, or adds an item naming neither) |
| left: single stories | 12 (photo columns "Capitol Lens \| …", two-sentence headlines about one story, a debate between two candidates) |

Disjoint headline items alone would have dropped all 12 single stories with
the 16 briefings, so the body half is what makes it precise. Its known miss
is the live blog, left to the coherence and on-topic checks downstream.

## Limits

One labeller, so no agreement figure; the ambiguous 12% is where a second
reader would differ. kNN learns the labeller's rubric, which no prototype
set was given, so part of its advantage is that it was taught the target
definition. The sample covers ten weeks of one news cycle (a war with Iran,
a Himalayan flood, a midterm campaign); the temporal splits test how far
that carries, up to six weeks out, not a different year.
