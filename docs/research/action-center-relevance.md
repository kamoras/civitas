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

## Limits

One labeller, so no agreement figure; the ambiguous 12% is where a second
reader would differ. kNN learns the labeller's rubric, which no prototype
set was given, so part of its advantage is that it was taught the target
definition. The sample covers ten weeks of one news cycle (a war with Iran,
a Himalayan flood, a midterm campaign); the temporal splits test how far
that carries, up to six weeks out, not a different year.
