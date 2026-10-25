# Action Center

Moved from the README. AGENTS.md states the rules; this page keeps the longer description and the measurements behind them.

## The hourly Action Center pipeline

The Action Center is intentionally separate from the nightly pipeline because it operates on different timescales and data characteristics:

```
Every hour at :15
       │
       ▼
  1. FETCH ───── RSS (NPR, PBS, BBC, The Hill, Politico, Roll Call —
       │         7 feeds across 6 newsrooms; NPR's two desks count as one).
       │         AP's mirror went stale on 2026-09-09 and apnews.com walls
       │         off automated readers, so AP was dropped (2026-10).
       │         A feed or trending source that fails, or has nothing new
       │         for a day longer than its longest gap in 60 days (and the
       │         48h window), raises an ops alert (check_source_freshness).
       │         Under a week of history, the longest gap among feeds that
       │         have a week stands in, so a feed first seen midweek isn't
       │         stale on its first weekend.
       │         + Google Trends + Bluesky trending
       │         48-hour article window; direct URLs only (no redirect wrappers)
       │         Reddit was a third trending source until 2026-09: it now
       │         requires OAuth for datacenter traffic and 403s everything
       │         else, so it was retired rather than left silently empty.
       │         A source that FAILS is reported as failed, never merged in
       │         as a source that simply had nothing to say.
       ▼
  2. FILTER ──── Drop multi-story digests (fetch/news_digest.py, shared
       │         with race coverage: a newsletter section, a briefing's
       │         title, a body listing unrelated stories, or a headline and body telling
       │         the same unrelated stories one item each — "<A>. And, <B>":
       │         5 of 1,473 stored articles, all briefings), then a kNN
       │         vote among the k nearest feed articles already judged
       │         (feed_articles), each labelled by its publisher's filing
       │         (politics feed or desk = yes, world feed or desk = no)
       │         or by naming a sitting official or federal agency (yes);
       │         k and the vote threshold refit daily, nothing labelled by
       │         hand (app/policy_relevance.py). Precision 0.88-0.96
       │         against the hand-label vote it replaced (0.89-0.98), and
       │         one more issue than it over 2026-10-02..15
       │         (docs/research/action-center-relevance.md)
       ▼
  3. CLUSTER ─── Complete linkage on headline + first 200 characters of
       │         the description (retrieval model, day-centred), cut at
       │         0.71: EVERY pair in a cluster must clear it, so a chain
       │         of look-alikes (floods ~ storm ~ epidemic) can't form one
       │         story. Single linkage plus a centroid merge from 0.20 built
       │         2026-09-27's chimeras: a Bangkok-floods title over a Hawaii
       │         hurricane lede and facts about a nor'easter and Fiji's HIV
       │         epidemic. Two articles whose own text dates the event
       │         two or more days apart are never joined, and the cut is
       │         refit daily: the lowest no stored pair about one actor on
       │         such different days reaches, plus a stated margin of 0.10
       │         for same-day different acts no fact labels
       │         (action_center.refit_cuts; 0.60 + 0.10 on 2026-09-28..
       │         10-15, where 530 hand-judged pairs had put it at 0.71). The same-run
       │         duplicate check drops a look-alike cluster rather than
       │         appending it (appending folded Hurricane Nolo into the
       │         nor'easter on that feed).
       │         Titles are compared with the day's mean headline vector
       │         removed. The coherence filter used to remove the CLUSTER's
       │         own mean instead, which erases
       │         the topic the articles share: live runs kept 1 of 5
       │         same-story articles, and on 2026-09-27's feed the filter
       │         kept 28 articles where the day's mean keeps 48.
       ▼
  4. RANK ────── lexicographic, no weights: names a tracked official,
       │         then outlets reporting, officials named, similarity to the
       │         civic-document corpus, trending (each key breaks only the
       │         ties left by the ones before; docs/research/action-center-ranking.md)
       │         Try ranked clusters in order (up to CANDIDATE_POOL = 6)
       │         until MAX_ISSUES = 2 publish — a cluster that fails a gate
       │         falls through to the next instead of ending the run. A
       │         cluster is skipped as a duplicate only of one that
       │         PUBLISHED this run: on 2026-10-08 a two-story live blog
       │         ranked first, both stories were dropped as its duplicates,
       │         and it then published nothing, two runs running
       │         Each published issue records its principal actor (the
       │         official named first in its headline, else a kNN class:
       │         story_actor.py); the admin charts their weekly Herfindahl
       │         index. Measured only: a one-per-person daily cap and
       │         demoting stories with no government actor were replayed
       │         over every run since 2026-09-24, held out by date, and
       │         found no replacement story to publish instead
       │         Each issue also records the states and races its own words
       │         name (action_center.ballot_links): a confirmed nominee by
       │         full name (a name two races' nominees share only beside its
       │         state), a state's only nominee of a surname beside the
       │         state's name, or a seat phrase ("Michigan Senate race"). The issue shows "On the ballot: Georgia · Senate
       │         race (Roe) · <next deadline> · Register · Check your
       │         registration" from the voter calendar and the state's own
       │         lookup, read when served (app/ballot_links.py). Measured on
       │         548 stored issues: 64 race links, every named person that
       │         race's nominee; a state alone is never linked (16 of 22
       │         such links were on topic, under the bar)
       │         (docs/research/action-center-ballot-links.md)
       ▼
  5. EXTRACT ─── The model LOCATES an assertion in one article; it never
       │         writes the sentence. post_composer.py checks both spans
       │         appear verbatim, that the source asserts one OF the other
       │         (adjacency — two true fragments can otherwise be assembled
       │         into one false sentence), that the span runs to the end of
       │         its clause, and only then renders the source's own words
       │         from actor through predicate — any words between them
       │         included, so "OpenAI agent made" never becomes "OpenAI
       │         made". Each article is read as headline + summary with
       │         the headline closed as its own sentence: joined by a bare
       │         newline, a claim ending at the headline's end read as cut
       │         off, and 2026-09-27's measured yield was 5 claims from 40
       │         articles (14 with the break marked), so most clusters
       │         missed the two-claim bar and nothing published for days.
       │         A predicate the model cut short ("sues", "grants review
       │         of") is read on in the source to the clause's punctuation
       │         — still verbatim, refused where it can't tell where the
       │         clause ends ("Sens." might be an abbreviation), and a
       │         period inside an open bracket ("(R-Ark.)") is not that
       │         end. A sentence with an unmatched bracket is refused (an
       │         actor named inside one composed "FDA) won't publish");
       │         one opening mid-sentence starts with a capital; a quote
       │         the source closes on a comma ends with a period. Each
       │         article's summary contributes its claim too, after the
       │         headlines: 2026-10-01, 78 of 300 clusters in three days
       │         were dropped as too few facts, 7 of 10 sampled failures
       │         were cut-short predicates. An -ing predicate is a clause
       │         when the source's own words between the spans end in a
       │         form of "be" ("The administration is seeking ..."):
       │         replayed on 356 recorded answers, 162 to 168 verified, all
       │         true. Since 2026-09-24, 37 of 41 issues carry one fact:
       │         each article gives at most two claims and most clusters are
       │         one or two articles (docs/research/action-center-ballot-links.md).
       │         The actor must name or describe a party that can act: a
       │         capitalised name passes, anything else ("a federal judge",
       │         "federal agents") its margin over actor/non-actor
       │         descriptions, threshold refit daily (see Classification
       │         Strategy). An actor inside a quotation, or
       │         one that only attributes a quotation ("…," X said in a
       │         statement), is refused: the first is the speaker's account
       │         published as fact. Two claims that restate one fact count
       │         once (claims.distinct_claims: similarity-model cosine >=
       │         a cut refit daily as the lowest no pair of lines from two
       │         issues published together reaches, plus 0.10: 0.79 on
       │         2026-10-16), so a headline and its own lede can't pass
       │         the two-claim gate as two facts.
       │         Title = the top article's real headline, never a
       │         livestream or video page's: an article whose headline
       │         kicker (the label before its first ": ") scores past the
       │         day's cut ("WATCH LIVE", "WATCH"; embedding margin over
       │         news-report and live-blog prototypes) gives no title,
       │         fact or source, and a cluster of nothing else waits for a
       │         report. The cut is fitted daily on publishers' own
       │         filing, no hand labels: stored feed articles under a
       │         video section (The Hill's /video-clips/, BBC's
       │         /news/videos/) against the same feeds' articles, max F1
       │         (0.120 on 2026-10-16: 18 of 18 filed-video kickers, 2 of
       │         126 articles, both Hill livestreams filed as news).
       │         Replayed on 1,441 stored headlines: 20 flagged, 10
       │         livestreams, 9 recorded clips, 1 Civitas vote-draft title
       │         (never filtered); 2 confirmed titles change (one to PBS's
       │         report, one withheld). Summary = the
       │         single best claim. Facts = the supporting claims, each
       │         carrying the outlet it came from, shown on the issue as
       │         "Media coverage".
       │         A cluster with no attributable assertion produces NO issue:
       │         MAX_ISSUES is a ceiling, not a quota. Publishing anyway is
       │         what produced "This coverage tracks the race and related
       │         developments." Trying only the top two made the ceiling a
       │         cap on ATTEMPTS: when both failed a run published nothing,
       │         and the next hour ranked the same two first again (five
       │         issues on 2026-09-23, none on 2026-09-26).
       │         Post-composition title deduplication (the near-identical cut, refit daily
       │         from data-labelled pairs: 0.89 on 2026-10-23)
       ▼
  6. PERSIST ─── Topic-keyed matching: each unique story maps to one permanent
       │         DB row regardless of rank changes or brief displacement.
       │         Same story → update content; repost only when a newer article
       │         also brings new information (a name, figure, or development).
       │         Otherwise the rank updates silently and nothing is posted.
       │         Brand new story → create new row.
       │         Full story (issue page): built here, while the cluster's
       │         articles exist. Each article is asked once more about its
       │         summary alone (with the headline in view the model always
       │         picks the headline); every verified claim, headline and
       │         body, is listed under the outlet that made it. Nothing is
       │         written. None when it would only repeat summary + facts.
       │         It replaced ~800 chars of model prose that published a
       │         relationship no source stated (issue 748), a wrong office
       │         (750) and filler (751); stored prose was cleared (0004).
       ▼
  7. ENRICH ──── sqlite-vec semantic search → link related bills/senators
       │         Related documents need title similarity ≥ 0.49 (min-error
       │         on 299 hand-read stored links; at the old 0.33, 189 were
       │         unrelated, e.g. Miami boating safety zones on a story about
       │         a Florida golf club)
       │         A bill number counts only when the words around it name
       │         that bill (lobbying_records.names_bill); a member's bare
       │         surname only when no other member shares it
       │         Actions (template text around stored data, no model):
       │         - the committee with jurisdiction, per chamber or none
       │           (app/committee_routing.py): House Rule X / Senate Rule
       │           XXV subjects (hubness-corrected similarity) blended with
       │           the votes of the nearest labelled texts the data supplies
       │           (each stored bill's Congress.gov committee referrals;
       │           feed articles filed under another desk naming no
       │           official, and race coverage, as no committee's), named
       │           only when both signals pick the same committee. Refit
       │           daily by data_refresh, installed under
       │           precision_rule.accept (2026-10-23: 15,933 bills and 2,707
       │           none texts; held out 0.817, bound 0.803, against the
       │           hand-labelled fit's 0.632 on the same decisions; on the
       │           136 newest hand-labelled issues 36 of 38 right against
       │           36 of 44). The row says "likely" only while the fit in
       │           use misses the bar (precision_rule_met). The chair and
       │           ranking member are read when the issue is served
       │           (scripts/calibrate_committee_routing.py)
       │         - "Comment by <date>": a proposed or final rule still open
       │           whose title clears the same 0.49; served only while open
       │         Replayed on the last 30 days of issues (2026-10-11), the
       │         share offering only "find your members" fell 80% -> 64%
       │         The Today tab's open comment periods (/api/action/
       │         open-comments) lead with any a recent issue links, then
       │         rules (the Register's own type) by readers (its page_views,
       │         read once a day into fr_page_views), then notices, then
       │         paperwork notices (app/comment_periods: the Register's
       │         table-of-contents subject for PRA collections, read with
       │         the page views into fr_toc_subject; 303 of 615 open on
       │         2026-10-17, precision 303/303, recall 303/304); by
       │         deadline, 6 of the top 10 were PRA renewals and a waiver
       │         petition (2026-10-11), by salience none were
       │         Every open comment period, in the same order:
       │         /search?tab=documents&comment=open (GET /api/explore?
       │         commentable=true, no q), the target of the homepage's and the Today
       │         tab's "Every open comment period" links; paperwork
       │         notices come apart under `paperwork` and are folded
       │         ("Paperwork notices (N)"), as on an agency page. Each
       │         card shows the abstract's first sentence verbatim and a
       │         days-left bar with the same fact in words
       │         Every issue but a count issue also offers the reader's
       │         own members on the page: a state picked from a list
       │         loads each office's phone (tel:) and contact form, how
       │         each voted on the issue's roll calls, and a fixed call
       │         script (GET /api/action/issues/{id}/members?state=; the
       │         state is kept nowhere). Before 2026-10-14 this was a
       │         link to the directory: 5 taps over 2 page loads to a
       │         phone number, now 2 on the page
       │         Related votes in Congress (app/roll_call_links.py): each
       │         issue and monitor update is matched to the roll calls
       │         taken 3 days before to 2 after, headline against the
       │         vote's title (an amendment's stated purpose), mean of the
       │         MiniLM and Arctic cosines >= the calibrated 0.64, where the
       │         exact lower 95% bound peaks (0.87 over 8,162 hand-labelled
       │         pairs, names masked; held out 53/57 right, bound 0.83,
       │         recall 0.22). Drift-watched; structural links are kept as
       │         labels for recalibration.
       │         Stored as refs at write time, the last week rechecked and
       │         200 unmatched rows backfilled per run; the members panel
       │         shows each member's vote on them and the call script
       │         cites it; a monitor's "On the record" charts them
       │         Who decides next: a linked bill's stored stage
       │         (bill_decider) or an open comment period's agency
       │         Members named by full name in the articles themselves
       │         are linked too ("named in the reporting")
       ▼
  8. MONITORS ── Detect cross-day recurring topics (title similarity ≥ 0.85)
       │         Create NationalMonitor records (matching issues on 5
       │         distinct days in 14, ≥3 unique sources); the LLM only
       │         names one, checked against its articles
       │         Add today's issues to monitors they match (title against
       │         the monitor's title and description ≥ 0.71, no LLM) and
       │         that share a distinctive entity with it (IDF > 2.0 over
       │         stored issues: "trump", in a third of them, never counts
       │         alone); stored updates re-checked each refresh
       │         Re-merge duplicate monitors (similarity-model title ≥ 0.75)
       │         No update for 7 days: watching; for 30 days: closed
       ▼
  9. TIMELINE ── Record daily TimelineEntry
       │         At week/month/year boundaries: LLM generates period summary
       ▼
 10. BLUESKY ─── Post the day's top two new/updated issues (at most two
       │         issue posts a day): the verified lede, verbatim (or the
       │         real headline if it doesn't fit), opening "Yesterday:" / "On
       │         <date>:" if the event predates today. Nothing model-written.
       │         Daily member spotlight: the member's most distinctive
       │         finding, with no score or rank.  (Also runs on the early-abort paths above — it
       │         doesn't depend on the news)
       │         Repost + like outlet posts linking an article an active issue cites.
```

**Why cluster before ranking?** Articles about the same event arrive from multiple outlets within minutes. Without clustering, every "top issue" would be the same story from NPR, BBC, PBS and The Hill. Clustering first, then ranking by source breadth, surfaces the most distinct newsworthy topics.

**Why filter at 0.20 cosine similarity?** The policy prototype filter is deliberately permissive. False negatives (dropping a real policy story) are worse than false positives. Borderline cases are handled downstream by extraction rather than by asking a model to be neutral: a cluster that yields no verbatim, adjacently-asserted claim simply produces no issue.

**When does a story become a monitor?** When today's issue matches issues on four other days of the last fourteen (headline similarity ≥ 0.85 on the classification model) and those issues come from at least three outlets. An issue keeps one row while its story continues, so this counts separate stories on separate days, not days on the board; counting every day a matching issue was current let one story carried for a week open a monitor. The floor was measured on 2026-10-09 by replaying creation day by day over every stored issue since 2026-06-26, each assigned by hand to a story, against the seven that ran for weeks (Iran, the midterms, AI policy, federal funding, immigration enforcement, tariffs and trade, Russia-Ukraine). On the issues to August 31 the old 0.83 opened 5 monitors, 4 of them one of those stories, and back-filled 11 updates about other stories into them; 0.85 opened 3, all real, back-filling 4. On the issues after, 2 of 3 were real at both floors. With the existing Iran monitor in place, the replay over the whole period opens two: AI policy (July 25) and federal funding (July 30). The midterms, immigration, trade and Ukraine stories are missed: their issues are different events whose headlines rarely reach the floor, and nothing tried (other floors, the similarity model, headline and summary, more or fewer days) found them without also opening mixed monitors.

**Why was there only one monitor?** Creation worked, but each new monitor vanished the same hour. The model that names a monitor was given "U.S.-Iran Conflict" as an example, and replayed on the production model it gave that name to 18 of 23 topics, AI policy and beef prices among them. Nothing checked the title against the articles, so the new monitor was created under the existing monitor's title and the duplicate check merged it into that monitor, back-filled updates and all. That, and before 2026-10-08 a merge floor that joined any two topics, is how the one monitor came to hold the Canada, Korea, Ukraine and Gaza stories. The prompt no longer has an example, a title word the articles never use is refused (on the replay that turned back 22 of the 23 old titles and kept the one real Iran title), and the model's "is it significant" verdict, which said yes to all 23 including the 13 that were not a long-running story, and its category, FOREIGN_POLICY for 16, are gone: the category is the issues' own policy area. A refused name falls back to the headline nearest the centroid of the issues that opened the monitor, and a monitor still named after one of its own headlines is asked about again on later runs. Until 2026-10-14 the fallback was whichever issue crossed the day floor, so a monitor on a bipartisan senators' letter opened under one interview's headline, and pages printed its slug back out as its name.

**How does a story reach the votes it is about? (2026-10)** Bills were matched only by a number or short title printed in the articles, and news prints neither: 0 of 543 confirmed issues had ever linked a bill, so the members panel never showed a vote, and the Iran monitor (303 updates) showed none of Congress's war-powers votes, the House's 212-212 tie among them. Now `app/roll_call_links.py` links a story to every roll call taken from three days before its date to two after whose title is close to its headline: the mean of the all-MiniLM-L6-v2 and Arctic-XS cosines at or above the calibrated threshold (`app/data/roll_call_link_calibration.json`, fitted by `scripts/calibrate_roll_call_links.py` on `app/data/roll_call_link_examples.json`). Structural signals were looked for first and did not exist: the articles named a bill one of the candidate votes was on in 0 stories, and a vote's record was an issue's primary source in 2. So the 9,868 candidate pairs of 642 stored stories (issues, the Iran monitor's updates, timeline entries; 2026-04-27 to 10-02) were read and marked by hand, under a criterion written into the examples file. The threshold is where the precision's exact lower 95% bound peaks, which must be at least 0.80: 0.64, where 52 of 54 links made on the 8,162 linkable pairs are right (bound 0.87), recall 0.21. Chosen the same way on three blocks of dates and scored on the fourth, 53 of 57 links were right (bound 0.83). Choosing instead the lowest threshold whose bound reached 0.80 linked more, but held out under the bar. On issues alone, 14 of 16 (bound 0.62, too few to clear the bar alone). Hand labels were used only because no data-supplied label exists; every structural link found from now on (a bill the articles name, a vote record as an issue's primary source) is kept as one (`RollCallLinkLabel`) for the next recalibration. Person names are masked as `[name]` in the examples and, by `NameMask` (every name the site holds a record of), in live text, and the mask is left out of what is compared: kept as a token it made every story naming someone close to every nomination vote (best bound 0.79). On the same stories masked as live text is, 51 of 51 links are right (bound 0.93). The gate's pass rate is watched by the calibration-drift check. What is compared was measured: the headline alone against the vote's title alone; the verified lede, an issue's facts and the vote's question each lowered the best bound. An amendment vote's title names only its sponsor, so it is compared by its stated purpose (`RollCall.amendment_purpose`, #1072) and left out without one; the replay's Senate amendment votes carry theirs (read from senate.gov), the House's were not stored yet, so House amendment purposes are linked by the same threshold without having been in the replay. Links are computed in the hourly refresh (the last week every run, 200 unmatched rows per run) and only read by the API. An issue lists them as "Related votes in Congress", the members panel shows each member's vote and the call script cites the newest; a monitor's "On the record" charts every linked vote's margin over the monitor's life, one row per chamber, with a table. On the labelled stories, masked as live text is, 8 issues link 14 votes, all of them right, and the Iran monitor's updates link 13 votes (the 212-212 tie included). The monitor's opened page is 6.6 phone screens long instead of 77.8: its updates show ten at a time, newest first.

**When does a monitor stop?** After 7 days without a matching issue it is marked watching and stays on the Ongoing tab; the next match makes it active again. After 30 it closes: it moves to the Ongoing tab's "closed in the last 60 days" list, and stays in the Archive and at its link (one that never gathered three updates is deleted). On the same history the long-running stories went up to 28 days between issues and came back, and none went longer.

**The Ongoing tab in boxes (2026-10).** Each monitor is a box: status, areas and when tracking began over its title, its updates and last date as the figure, and its newest update quoted with the outlet it is from, or, where that update's text isn't an outlet's own words, its headline and source (`latest` on `GET /api/action/monitors`: the text is quoted only where the claim-layer issue that wrote the update is found, credited to that issue's `summary_source`, since an update stores the issue's first source, not always the lede's outlet). The description, feed, related votes and every update are behind "More", read when first opened; the tab's note is one line with the rest behind "More". Measured on an iPhone SE (375 px) with the density audit's method on a local `next build` against the same database snapshot, main against this change: the tab went from 2.6 screens to 2.4, its longest block from 57 words to 19, the first screen's prose from 36% to 14%, and its first monitor moved up from 570 px to 411; on a monitor's own page the first update moved up from 1,726 px to 1,720 (863 to 720 on a shorter one).

**Why topic-keyed matching instead of rank-slot matching?** The original design keyed issues by `(date, rank)`. When the same story briefly fell off the top slots and returned, a new row was created with `bsky_posted_at=None`, triggering a duplicate Bluesky post. Topic-keyed matching (2-day lookback by cosine similarity) ensures the same story always maps to the same row. New articles advance `primary_article_date`; more outlets covering the same event do not.

**What makes a repost?** A newer article date is necessary but not sufficient — recap coverage rewords the same story under a fresher timestamp, which used to repost with nothing new to say. The new facts must also introduce either a named entity/figure or a story development (a veto, a court blocking an order, a failed override) that the facts *as of the last post* lacked. The baseline is `bsky_posted_facts`, not the live `facts` column: `facts` is rewritten on every hourly refresh whether or not anything was posted, so baselining on it let a development that surfaced between posts be absorbed and never read as new again. Rows that predate the column are backfilled from `facts` at startup — the poster is the only writer of `bsky_posted_facts` and it only ever sees issues the repost gate has already released, so a NULL baseline on an already-posted row could never resolve itself. The backfill is unconditional rather than fired once on the migration: the poster writes `bsky_posted_at` and `bsky_posted_facts` in the same commit, so a row with the first set and the second NULL can only predate the column, and a seeded row stops matching.

**Developing issues.** Before the news fetch, each refresh reads the latest final-passage roll calls and significant Federal Register rules (`early_signal.py`) and drafts a **Developing** issue, not posted anywhere, for one the news hasn't covered yet. A draft is a template filled from its record, with no model: a vote's measure, question, result, tally and date; a rule's agency, title, abstract (quoted whole in the facts, by whole sentences in the summary), publication date and document number. A rule's title leads with what the rule is, the Federal Register's own title ("Final rule: Transparency in Coverage"; for a rule with no title, its abstract's opening sentence), and the agencies open its summary: until 2026-10 the title led with them, which ran to five departments and put the rule's subject past a phone card's second line. A vote names its bill as the Record prints it ("H.R. 9497"), not the Clerk's file spelling ("H R 9497"). A current draft is re-worded from its record by the template each time the poll reads that record again, so a template change reaches live drafts (promoted and retired rows keep what was published). Only a vote from inside a draft's 48-hour confirmation window is drafted (cast on the run's day or the two before; an undated one never), and a current draft whose vote falls outside it is retired (`early_signal_gate_stale`, `early_signal_retired_stale`): the poll takes each chamber's latest votes whenever they were cast, and on 2026-10-15 the House's rolls of 2026-09-16 went up as that day's developing news, the House having voted on nothing since. The model's drafts had characterized the record ("a narrow 77-22" for S. 4668, 2026-09-28). A vote on a bill that a current reported issue already covers, by recording the bill or naming it by number or short title, isn't drafted, and a draft whose bill later reporting covers as its own issue is retired, at the start of a run and again once the run's issues are written. `mark_recent_duplicates` also marks such a draft a duplicate of the covering issue, decided afresh each run from the record's own rows, so it holds after the covering issue retires as well (the S. 4668 draft stayed listed beside the reporting on 2026-09-29 until this). Each pass records why it retired a draft (`ActionIssue.retired_reason`, migration 0052): `unconfirmed` (its vote aged past the window, its confirmation deadline passed) or `covered` (a reported issue covers its bill; the draft's page links it). A retired draft is not part of the record: the homepage's list of recent issues, which keeps retired confirmed stories, leaves it out (`NOT_RETIRED_DRAFT`; two month-old vote drafts led it on 2026-10-15), and so do the Congress week page's links from a rule to its issue and the open comment periods' "in the news" ordering; a covered draft's place there is the reporting's. That list also shows one issue per national monitor, the newest (`action._one_per_monitor`: the monitors an issue links, or whose updates quote its title), beside the monitor's own entry: two developments of one story are separate events, not duplicates (a statement by six senators against the Russian diesel deal, then their dozen's letter three days on, 0.82 apart by title), and on 18 of 46 days from 2026-09-01 one story held two or more of its first six entries; none does now. Its own page stays up until the 14-day cleanup and says why it left: an unconfirmed draft is flagged **Not confirmed**, and its summary's closing "news coverage … has not appeared yet" becomes "did not appear while it was on the Action Center" (`reword_retired_drafts`, each refresh); a covered draft is flagged **Superseded**, says a reported issue covered the same vote, and keeps its summary, since coverage did appear. A draft retired before reasons were kept is given one by the same pass, from whether a reported issue, current or not, covers its bill. News issues resolve their bills by short title as well as by number (`bill_service.short_title_index`: the current Congress's "... Act" titles of three words or more, a title two companion bills share naming neither), since outlets name a bill that way far more than by number. Every issue carries the refresh's own date: a Senate draft once carried the vote's raw date string, which sorted after every ISO date, so the Action Center (which lists the latest date's issues) showed that one draft alone (migration 0021 rewrote the stored dates). That date is an Eastern day, and so is every date the site shows from a stored timestamp (`time_utils.eastern_date`: an issue's "first surfaced", a monitor's opened, updated and closed dates): until 2026-10-15 those took the timestamp's UTC date, so an issue filed after 8 PM ET read as first surfaced the day after it was last updated (46 of 551 issues).

**How do you tell a quiet news day from an over-suppressing gate?** Both look the same from outside: nothing on the Action Center, nothing on Bluesky. Roughly ten independent checks in this pipeline fail closed — the right default when the platform publishes under its own name, but it means silence is the shared failure mode of all of them. Every refresh records what came in (`articles_fetched`, `articles_policy_relevant`, `clusters_considered`), what published (`issues_new_topic`, `issues_matched_existing`, `bsky_reposts_allowed`), and what each gate dropped, including on the two abort paths that publish nothing at all. `GET /api/admin/action-metrics` reads the window back with those three groups totalled: healthy intake against near-zero output is a suppression problem, near-zero intake is a quiet cycle or a broken feed. Runs are hourly, so a gap in the series is itself a signal — a refresh that crashed or was still holding the lock leaves no row. The Today tab's masthead reads the same rows: "Checked" is the newest run that fetched any articles (`checkedAt` on `GET /api/action/issues`), and "stories updated" the newest that published or re-matched an issue (`generatedAt`), so an hour that found nothing new doesn't look like a refresh that stopped, and a run with the feeds down doesn't count as a check.

**The clustering cut is refit from what the articles state, not from a label of shared names.** Since 2026-10-16 it is fitted once a day on the stored feed articles (`action_center.refit_cuts`): the lowest cut at which no pair sharing a distinctive entity, whose own text dates the event two or more days apart, would merge, plus a stated margin (`CUT_MARGIN`, 0.10) for the same-day different acts no stated fact labels; and clustering never joins two articles whose stated days differ that much. On the 2026-09-28..10-15 articles the fit is 0.60, the cut 0.70. What follows is how the hand-judged cut that preceded it came about. Until 2026-10-15 each run logged its article pairs labelled by whether their names and numbers overlap, and once a day the cut moved to where that label erred least. That label was wrong three times in four when read against issue titles (49 of 65 pairs, 2026-10-08), so the fit had no stable answer: total errors were flat from 0.50 to 0.75 and the stored cut moved 0.63, 0.78, 0.57 in two weeks. Now 553 pairs of feed articles (530 once the briefings the ingest filter drops are left out) published within 48 hours of each other (2026-09-28 to 10-15) are judged by hand as one event or two (`app/data/cluster_pair_judgments.json`, rubric inside), and `scripts/calibrate_cluster_pairs.py` picks, precision first, the text and cut that merge the most same-event pairs while merging no different-event pair: headline plus the first 200 characters of the description, at 0.71 (17 of 114 same-event pairs; headlines alone manage 6 at 0.84, and the 0.57 the old fit settled on merged 38 same-event pairs and 22 from different events). Requiring a shared rare name as well changes nothing at that cut. Replayed over 99 hourly runs rebuilt from the articles production stored (2026-10-11 to 10-15), the clusters that reach two claims cover 11 events instead of 12, and the 10 multi-event clusters the old cut let through to the gate (mostly a statement and a later letter about one deal merged into one story) are gone. Three other thresholds stopped being fitted on 2026-10-08 for the same reason. The same-story title shortcut was labelled by that entity overlap, which, read against the issues' titles, was right for 16 of 65 issue pairs; it keeps its hand-measured 0.92. The issue-to-monitor and monitor-merge floors were labelled by the LLM gate, which approved 81 of 88 matches, off-topic ones included, and whose 0.55 merge floor on the retrieval model merged every pair of different topics into one monitor. They are now constants measured on the monitor's 453 updates (`_MONITOR_ISSUE_SIM`, `_MONITOR_MERGE_TITLE_SIM`). For the same reason, deciding that two issues are one story now takes the same content, a near-identical title, or a shared source article with titles that agree: names and numbers in common no longer do, and a shared source alone no longer does either (one roundup article is cited by unrelated stories; on 94 judged pairs, a 0.62 title floor kept every different-story pair apart). A vote draft's source is its record, so a story citing it still matches the draft. The shortcut's 0.92 is bundled in `app/data/action_thresholds.json` with its provenance. The remaining Action Center tunables are still typed, and are marked as such where they are defined.

**Why does no model decide whether a story joins a monitor?** A story whose title is at least 0.71 similar to a monitor's title and description becomes one of its updates. Until 2026-10-09, a story between 0.71 and 0.80 also needed a yes from the LLM. That verdict was replayed offline on the production model, quantization, prompt and decoding against all 330 updates of the one monitor, each labelled on- or off-topic by hand. It said yes to 323. Of the 18 off-topic updates in that band it let 17 through, and of the 9 dated from 2026-07-15 on it rejected 1. Its own reasons often said the story was about something else, after a verdict field it had already filled with yes. Nothing tried in its place, tuned on the updates before 2026-07-15 and checked on the rest, rejected an off-topic update without rejecting on-topic ones too. The variants were: asking for the article's main subject, adding the monitor's description and recent updates, a bare constrained true/false, reason before verdict, a two-step subject check, multiple choice, a majority or unanimity of five samples, the true/false log-probability margin, and the model's extracted subject scored by embedding against the description and its nearest earlier updates. So the gate is the similarity floor, which is what it measurably was, with one model call fewer per borderline match.

**Why can't a deploy silence the refresh?** Two containers overlap during a deploy, so the refresh takes a cross-container lock: a row in `api_cache` whose primary key makes the second insert fail. The lock is a lease. Its holder rewrites the row's timestamp every minute from a heartbeat thread, a row ten minutes without a beat is taken over, and heartbeat and release touch only the row carrying the holder's own token. It used to be honored for a flat four hours from acquisition. A refresh runs in a thread, so a deploy's SIGTERM kills it without its `finally`, and the row it left made every run for the next four hours skip with "held by another container". On 2026-09-26 twenty-two deploys between 15:08 and 22:50 UTC kept that going all day, and no issue was published. A deploy still kills the refresh it lands on, and a refresh takes about twenty minutes of every hour, so on such a day most runs would die anyway: `check-and-deploy.sh` now defers a deploy while a refresh is running, but only for one that started within the last 40 minutes (twice the longest measured run), so an `is_running` flag wedged true cannot hold deploys the way it did on 2026-07-27. The first runs after the fix showed the heartbeat failing on every beat from minute three to the end of the run: the refresh flushed an LLM training-sample row into its own session and did not commit until every remaining cluster's model work was done, holding SQLite's one write transaction for about fifteen minutes. The heartbeat, the election coverage refresh and the LLM cache all waited out their 30-second busy timeout and failed, and the lease went stale under a live holder. Samples are now written on their own short session, and the monitor stage commits each write before its next model call. That rule covers every stage now that the refresh runs beside the nightly chain. On 2026-10-15, the first night it did, the monitor stage's orphan sweep opened a transaction even with nothing to delete and held it through the next model call. The nightly Senate run and the House sponsorship step each failed on "database is locked" inside that window. Each stage now commits before its next slow call, and a write held past ten seconds is logged with the line that opened it. The pipeline's learning-store and cache writes also retry a locked statement. On 2026-10-16 that log caught the Senate run itself holding the lock for about 20 seconds at a time: each learning-store upsert stayed open while the next bills and donors were classified. Those writes now commit at once.
