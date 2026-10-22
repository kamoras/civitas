# Action Center ranking: a fixed order instead of weights

Measured 2026-10-22. Code: `action_center._rank_clusters`.

## What it replaced

Until 2026-10 each hourly run ranked its story clusters by
0.40 × action surface + 0.35 × coverage + 0.25 × trending, each signal
divided by the run's maximum, the action surface itself 0.5 × officials named
(capped at 3) + 0.5 × civic-document similarity. None of the five numbers had
been fitted to anything.

## Why the weights were not fitted

Fitting weights needs a target the ranking should predict, measured on the
candidates it chose between. None of the candidates is defensible today:

| Candidate target | Why not |
|---|---|
| Issue views (`IssueView`, 7,063 views of 563 issues, 2026-08-20 to 10-21) | Counted only for issues the ranking already published, and a view depends on the slot the ranking gave the issue and on whether it was posted: the target is caused by the thing being fitted. |
| Action taps (`ActionTap`) | 4 taps in all (state picks), on 3 issues. |
| Becoming a national monitor | 2 monitors have ever opened. |
| Later coverage breadth | Free of that feedback (outlets don't read Civitas), but it measures newsworthiness, not whether a reader can act, and with six national feeds it is mostly the cluster's current outlet count again. Used below as a check, not as a target. |

The losing candidates were never stored either (their signals, or which
articles formed them): only the six top clusters of a run reach the log, and
the logs go with the container.

## The rule

Lexicographic, each key deciding only between clusters tied on every key
before it:

1. names at least one tracked official (the publication gate's own condition:
   an issue publishes only if it anchors to a bill or a named official, and
   the bill isn't known at rank time);
2. distinct outlets reporting it (outside editorial judgement, and stable from
   hour to hour);
3. officials named;
4. civic-document similarity;
5. trending similarity (most volatile, least complete).

The 0.5/0.5 split and the cap of 3 inside the action surface went with the
weights.

## Replay

Every feed article the relevance gate judged has been stored with its
publication time since 2026-10-16 (`feed_articles`), and every Bluesky trend
listing since 10-15 (`trend_observations`). Each hour from 2026-10-16 18:00
to 10-22 18:00 UTC (145 runs) was rebuilt from what had been seen by then:
the articles kept by the gate and published within 48 hours, clustered by
`_cluster_articles` at the bundled cut, ranked with the explore documents
dated by then and the current member tables. Google Trends is not stored, so
trending is Bluesky's alone (it counts last in the new rule; in the old one
it was a quarter of the score). A median of 81 clusters per run.

| | Old weights | Outlets first (no official key) | **Adopted** |
|---|---|---|---|
| Outlets reporting the top cluster (mean) | 1.35 | 2.17 | **2.07** |
| Top-6 candidates naming a tracked official | 0.93 | 0.74 | **0.99** |
| Outlets reporting a top-2 story 24 hours later (mean) | 1.10 (n=143) | 1.74 (n=168) | **1.51** (n=160) |
| Hours the top cluster changed from the hour before | 18 | 14 | **11** |

Against the old weights the adopted rule kept the same top cluster in 28 of
145 runs and shared 4.0 of the 6 candidates a run may try. Putting outlets
first without the official key filled a quarter of the candidate pool with
stories the publication gate would refuse; the official key restores that and
keeps most of the gain in coverage and stability.

The replay script was a one-off and is not in the repository: it rebuilt each
hour as described from production's `feed_articles`, `trend_observations`,
`explore_documents` and member tables, and called `_cluster_articles`,
`_compute_trending_boost` and `_action_link_signals` directly.

## When to revisit

A fitted ranking needs the candidates stored with their signals and an
outcome the ranking does not cause. Until then the order is a stated priority,
and the page says so (`/about/news`, "Ranking stories").
