# Action Center: ballot links and thin issues

Measured 2026-10-17 on a copy of the production database (548 confirmed news
issues, 2026-06-26 to 2026-10-16; 1,146 confirmed general-election
candidates in 2026 races).

## Ballot links

An issue now records which states and races its own words name
(`ActionIssue.ballot_links`, `action_center.ballot_links`), and the issue page
shows "On the ballot: Georgia · Senate race (Roe) · <next deadline> ·
Register · Check your registration" from the voter calendar and the state's
official lookup (`app/ballot_links.py`). The rules, most specific first:

| Rule | What it reads | Links | Judged correct |
|---|---|---|---|
| Nominee by full name | a confirmed nominee named in the headline, summary or facts (the results gate's matcher); a full name nominees of two races share counts only beside its state | 58 | 58 (the named person is that race's nominee) |
| Surname beside its state | the state's only nominee of that surname, never written after another capitalised word | 1 | 1 |
| Seat phrase | "Michigan Senate race", "Georgia's 2nd District" (the results gate's grammar, no candidate required) | 5 | 5 |
| State in an election story (dropped) | a state in the headline of a story whose class vote is `us_election` | 22 | 16 on topic |

64 race links on 55 of the 548 issues. One judge (the author), reading each
issue's text; no threshold was fitted to these judgements.

- The first three rules are entity matches against stored rows and made no
  wrong link: every person named is that race's confirmed nominee. Two cases
  the rules exist for: one full name shared by nominees in two states' races
  links neither unless the story names one of the states; and a story about
  a different person of a nominee's surname (the surname written after
  another given name) names no one.
- A nominee named in a story that is not about the election (a senator on a
  budget hearing) still links that race. The link is true, and says whose race
  it is.
- A fourth rule was measured and dropped: a state named in the headline of a
  story the class vote calls an election, linked alone with its voter
  information. Its six off-topic links (an airport renamed, a state
  school-board vote, a primary calendar two cycles out) came from the class
  vote reading some state stories as elections; 16 of 22 (73%, exact lower
  95% bound 0.50) is under the site's 0.80 bar, and the vote's share doesn't
  separate them (an off-topic link at 0.80, an on-topic one at 0.67), so no
  cut could tighten it. A state is linked only through one of its races.
- Of the eight election stories in the last 30 days, three now link, each a
  Senate race by a nominee named (one by surname beside the state). The
  other five name a state but no nominee or seat phrase the rules read, and
  stay unlinked rather than risk the dropped rule's noise.

## Single-fact issues

- Of the last 72 confirmed issues, 37 carry one fact (51%). Since the claim
  layer (2026-09-24), 37 of 41 do.
- An issue publishes only with two verified claims (a lede and at least one
  fact). Each article gives at most two: one located in the headline and one
  in its summary. Since complete linkage, most clusters are one or two
  articles (single-fact issues: 14 with one article, 21 with two), and
  restatements are dropped. So the two-claim floor is also, most of the time,
  the ceiling.
- The verification gate: of the 356 recorded claim answers since 2026-10-11,
  162 compose under the current gate. The rest fail for these reasons:
  - 67: a span is not verbatim (the model paraphrased: "injects" for
    "inject");
  - 59: the two spans aren't asserted together (an agency + "sent out", where
    the source says payments are sent out "according to" the agency);
  - the rest: clause-boundary, short-predicate and acting-party checks.
- Read one by one, the rejections are right, with one exception: an
  auxiliary in the source between the spans ("The administration
  **is** seeking to stop the counting of some votes in California") composed
  "is seeking" but was refused as a bare participle. That is fixed
  (`post_composer._BE_FORMS`). Replayed on the same 356 answers:
  - 162 to 168 verified claims, all six true sentences, one contiguous span of
    the source each;
  - the issue on the federal mail-ballot suit gains its second
    fact, which passes the restatement check.
- Not done here: locating a claim in each sentence of an article's summary
  rather than one per summary. That is the structural change that would add
  facts from the same sources, but it needs new model answers to measure. No
  language model runs on the development machine, and running the
  production one for a replay would load the Pi beside the hourly refresh.
  It is the next measurement.
