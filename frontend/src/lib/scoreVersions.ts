/**
 * Scoring algorithm version history — the public methodology changelog.
 *
 * Keep in sync with ALGORITHM_VERSION in
 * backend/app/pipeline/analyze/score_calculator.py. Dates are the first
 * pipeline run that produced scores under each version; trend charts use
 * them to mark methodology changes so a score shift from an algorithm
 * update isn't read as a behavior change.
 */

export interface ScoreVersion {
  version: string;
  date: string; // YYYY-MM-DD of first pipeline run under this version
  title: string;
  changes: string[];
}

export const SCORE_VERSIONS: ScoreVersion[] = [
  {
    version: "v5.7",
    date: "2026-07-12",
    title: "Removed noise-driven secondary policy-area tags",
    changes: [
      "Bills, votes, and Action Center issues could previously be tagged with up to 3-4 policy areas each, on the theory that real legislation and news often span multiple domains. A follow-up to the classification audit measured the actual gap, across 60 real Action Center issues, between the top-scoring category and the runner-up: a median of 0.018 (90th percentile: 0.053) out of a 0-1 similarity scale. Every category clusters within a few hundredths of each other for almost any input text, so the \"secondary area\" filter was effectively noise — a story about a murder trial was tagged Technology; a story about an accounting firm's collapse was tagged Technology and Energy without ever confidently noting it was about financial matters at all. Classification is single-area only until a genuinely discriminating signal for secondary relevance exists.",
    ],
  },
  {
    version: "v5.6",
    date: "2026-07-12",
    title: "Fairness audit — House scoring bugs, bill/vote classification imbalance",
    changes: [
      "A fairness pass comparing scores across party, seat safety, seniority, and chamber found the House-vs-Senate gap in Legislative Effectiveness was mostly a bug, not real: the sponsorship-volume ceiling had been calibrated only against Senate data (435 House members structurally introduce far fewer bills per congress than 100 senators, splitting similar institutional bandwidth), and a separate pipeline bug was silently discarding any House member's sponsored-bill data past the 50th bill — 39% of the House hit that cap exactly, undercounting both the volume and advancement components. Both are fixed; House Legislative Effectiveness's population average moved from well below the neutral midpoint to in line with the Senate's.",
      "Verified as real, not bugs: Democrats' higher average Funding Independence traces to a genuine, measurable difference in how the two parties' campaigns are financed (PAC-reliance roughly double for Republicans in current data), and House members' lower Funding Independence traces to House races being structurally cheaper and more PAC-dependent than Senate races. Both are long-documented patterns in campaign-finance research, not something the formula treats parties or chambers differently to produce.",
      "Fixed a structural weakness in the adaptive bill/vote classifier: its self-training reference corpus is heavily imbalanced (some policy categories had zero examples), and a confident vote from that corpus was allowed to override an otherwise-correct match against the hand-written category descriptions — even when the corpus literally couldn't have produced the right answer because it had never seen an example of that category. A confident classifier vote is now only trusted over the category-description match when the corpus has meaningful representation of the alternative; otherwise the category-description match wins. Measured accuracy on a held-out policy-area test set: 78.6% before, 100% after.",
    ],
  },
  {
    version: "v5.5",
    date: "2026-07-12",
    title: "Funding Diversity data bug, Legislative Effectiveness recalibration",
    changes: [
      "Fixed a data bug in Funding Diversity's industry-concentration signal: UNCLASSIFIED (donations the classifier couldn't attribute to any industry) was being treated as a legitimate industry rather than excluded like OTHER/POLITICAL, and the concentration math used each industry's rounded display percentage instead of its actual dollar total — an industry under roughly 0.5% of a senator's total raised rounds to 0% and vanished from the calculation entirely. Together these made 95 of 100 senators look almost totally concentrated in a single 'industry' (usually UNCLASSIFIED) regardless of their real donor spread, dragging the population average well below every other dimension's neutral calibration point. Concentration is now computed from real dollar totals.",
      "Legislative Effectiveness's sponsorship-volume ceiling (bills introduced per congress served) is recalibrated: it was set in 2026-06 against a full-credit ceiling just above that period's 90th percentile, but the live distribution has since grown enough that over a fifth of senators were fully saturating the old ceiling, scoring identically regardless of how far past it they were. Reset to restore the same top-decile headroom the ceiling originally provided.",
      "Documented (not changed): the donor-independence component of Constituent Alignment always runs on its reduced-weight fallback, because no lobbying-disclosure source this platform ingests discloses which way a donor's industry wanted a given bill to go — only aggregate spend. Filling that in would require hand-authoring an industry-to-position assumption, which this platform's scores are built never to contain.",
    ],
  },
  {
    version: "v5.4",
    date: "2026-07-12",
    title: "Promise-evidence gray-zone gate",
    changes: [
      "v5.3's Promise Persistence shrinkage-prior resize didn't fix the underlying collapse: a self-check after the 2026-07-11 run measured population stdev at 3.72, still below the 8.0 floor that check exists to enforce. The real cause was upstream — the 0.80/0.82 relevance threshold was calibrated against promises that quote a bill by name (true matches score 0.77-1.0), but most real campaign promises are generic platform language whose best genuinely-related vote typically scores only 0.65-0.75, never clearing the bar regardless of the shrinkage prior's size. Only 31 of 100 senators had any evaluable promise at all.",
      "Below the existing high threshold, a gray zone now goes to an LLM that reads the actual promise and candidate vote/bill text and judges genuine relatedness, instead of being dropped outright. At or above the threshold nothing changes — no new LLM calls, no regression risk for bill-quoting promises — and the check fails closed on any LLM error. The House pipeline is unaffected and keeps its original deterministic cutoff.",
    ],
  },
  {
    version: "v5.3",
    date: "2026-07-11",
    title: "Promise Persistence recalibration and confidence-badge fix",
    changes: [
      "Promise Persistence's shrinkage prior is recalibrated for v5.1's stricter evidence thresholds. That recalibration (0.80/0.82 relevance) fixed a real false-positive problem but also roughly halved how many promises are evaluable per member — with the shrinkage prior left at its old size, it came to dominate almost every score, collapsing Promise Persistence toward a narrow neutral band regardless of a member's actual record (stdev fell from ~7 to ~3 in testing). The prior is resized to restore the original balance between evidence and shrinkage for the new, smaller-but-more-accurate evidence pool.",
      "Fixed a bug where every senator's data-sufficiency confidence badges (the 'low data' marker shown next to sparse scores) were silently dropped before saving, since 2026-07-04 — representatives were unaffected. Confidence badges now display correctly for senators too.",
      "Added an automatic check after each pipeline run that flags if any score dimension's spread collapses across the full senator population, so a recalibration mistake like the Promise Persistence one above gets caught immediately instead of by manual audit.",
      "Action Center's full-length issue articles now scale their target length to how much reporting actually exists for that issue, instead of a fixed word count — thin coverage was previously padded out with invented specifics (fabricated dates, statistics, and international-agreement details not present in any source) to hit the old floor. Generated articles are also checked for and rejected if they repeat the same sentence verbatim, and mechanical fact-checking now catches fabricated years and physical-magnitude figures (e.g. degrees) in addition to money and percentages.",
      "Bluesky senator spotlight posts no longer describe a middling score as a 'standout' — the post now highlights a specific dimension only when it's genuinely far from the population's neutral point; when none of a senator's five scores are unusual, the post states the overall ranking plainly instead of praising or criticizing an unremarkable number.",
    ],
  },
  {
    version: "v5.2",
    date: "2026-07-11",
    title: "District-level seat expectations and donor-record integrity",
    changes: [
      "House Constituent Alignment now measures each representative against their DISTRICT's partisan lean (Cook PVI, ingested per-district) instead of their state's. State lean was structurally unfair in split states: a member elected by a D+19 urban district in a red state was scored as holding an 'opposed seat' and expected to vote against their party ~20% of the time — when their district elected exactly the platform they vote for. Senators are unchanged (their constituency is the state).",
      "Donor records now include only actual contributions (FEC Schedule A line 11). The receipt itemization also contains joint-fundraising transfers, campaign loans, vendor refunds, and bank interest — all of which were being listed as 'donors': one senator's #1 donor was a media-buying vendor's refunds, and banks that merely lent to campaigns appeared as top donors. This corrects top-donor concentration (part of Funding Independence), Funding Diversity, profile donor lists, and donor-vote matching.",
      "The candidate's own contributions (line 11D) are identified by FEC line number rather than name matching, making self-funding exclusion deterministic.",
      "All public LLM-generated content (issue facts, full stories, Bluesky posts) now passes mechanical grounding checks: numbers and titled-official references must appear in the source material the text was generated from, or the content is rejected. Spotlight posts previously also computed their headline 'overall score' as an unweighted average — they now show the same weighted composite as the leaderboard.",
    ],
  },
  {
    version: "v5.1",
    date: "2026-07-10",
    title: "Funding-window and promise-evidence data corrections",
    changes: [
      "Fixed a funding-window bug that counted the same election twice for roughly a third of members: the FEC totals endpoint returns both an election-full aggregate row and partial per-cycle rows for the same race, and the 'two most recent elections' window could pick two rows from the same election — double-counting a partial window and dropping the member's previous (often much larger) race entirely. Funding totals now take one row per election. Funding Independence changes materially for affected members in both directions (recently-elected members whose real race had fallen out of the window recover; members whose small partial window was double-counted lose inflated scores).",
      "Promise-vote relevance thresholds recalibrated against the embedding model's measured noise floor. Any two pieces of formal legislative text score ~0.55-0.87 cosine similarity from shared register alone; the old thresholds (0.28/0.40) sat entirely inside that noise, so the alignment engine cited whatever ranked highest among unrelated votes as 'evidence' for a promise. New thresholds (0.80/0.82) plus a promise-category / vote-policy-area compatibility gate cut cross-domain false positives to ~1-2% while keeping ~90% of true matches. Expect fewer but far more trustworthy kept/broken verdicts, with more promises honestly marked unclear.",
      "Promises that name a specific bill are now scored by whether the member voted the way they said they would, instead of by the bill's generic pro/anti policy direction — a Yea on 'Ending X Act' no longer counts as breaking a promise to support ending X.",
      "Floor-speech advocacy classification fixed: at the old similarity threshold every sampled speech matched all 14 policy categories (a school-recognition speech 'advocated' on guns, taxes, and immigration), feeding a constant near-perfect advocacy signal into Promise Persistence for anyone with any floor remarks. Remarks now match at most one category at a calibrated threshold; ~29% of real remarks classify, matching the observed ceremonial/substantive split.",
    ],
  },
  {
    version: "v5",
    date: "2026-07-05",
    title: "Representing all constituents — coalition breadth and status-fair effectiveness",
    changes: [
      "Constituent Alignment gains a coalition-breadth component (20%): the rate at which a member attracts cosponsors from the other party and cosponsors the other party's bills, normalized to the chamber median (Lugar Center Bipartisan Index method). Voting congruence asks whether you vote the way your seat elected you to; breadth asks whether you also legislate for the constituents who didn't vote for you. Cohort-median crossing scores 50; the normalization is recomputed from observed behavior every run, so it is party- and majority-symmetric with no fixed constants.",
      "Legislative Effectiveness advancement is now benchmarked against the sponsor's majority/minority status in each congress. Measured from our own bill corpus: senate majority sponsors advance 3.6% of substantive bills vs 2.4% for the minority; house 6.4% vs 2.4% (consistent with Volden & Wiseman 2014). The old absolute 5% threshold silently penalized whichever party was out of power; matching your status baseline now scores 50 for everyone.",
      "Senators with sparse platform data get their Promise Persistence positions augmented from their own sponsored legislation (the deterministic House path from v4.3), deduplicated against stated platform promises. This closes most of the v4.3 cross-chamber shrinkage offset by giving both chambers comparable evaluable-position counts.",
      "Action center ranking now leads with civic actionability (40%) over coverage breadth (35%) and trending (25%): a story where citizens can directly act outranks a better-covered story with no US action surface.",
    ],
  },
  {
    version: "v4.3",
    date: "2026-07-05",
    title: "Real House promise data",
    changes: [
      "House members now have evaluable promise data. Representatives' positions are derived from their sponsored legislation (bills they introduce) rather than campaign platform text, which is scarce for House members. Each representative's positions are extracted from bill topics and evaluated deterministically against their floor votes using the same embedding rules as the Senate path.",
      "Position source integrity: sponsored bills are excluded from a position's evidence entirely — positions are evaluated against floor votes only — so a representative can never 'keep a promise' simply by introducing the bill that defines it. Sponsoring is effort, not outcome.",
      "Near-duplicate topic deduplication uses a similarity threshold (0.88) derived empirically from the distribution of same-member bill-title similarities, rather than hand-picked. This prevents a representative with many related bills on the same topic from appearing to have independent evidence across redundant positions.",
      "Published confidence markers (high/medium/low) now reflect real evaluable-promise counts for House representatives. Previously all 431 representatives had zero promises, collapsing their Promise Persistence scores to neutral. Now the dimension is data-driven for both chambers.",
      "Known cross-chamber offset, disclosed rather than tuned away: House Promise Persistence runs a few points above the Senate's (shadow-validated means 64 vs 58) because representatives average ~8 evaluable positions to senators' ~2, so House scores are shrunk less toward the neutral prior. The underlying kept-rates are similar (79% vs 74%). Forcing the means equal would require a hand-fed chamber constant, which the methodology forbids.",
    ],
  },
  {
    version: "v4.2",
    date: "2026-07-05",
    title: "Constituent Alignment — representation, not defection",
    changes: [
      "Mission clarified: scores measure how well members represent their constituents — not independence as an intrinsic virtue. Party-line voting in a seat that elected that platform is representation.",
      "Independent Voting renamed Constituent Alignment and rebuilt: each member's contested-vote break rate is now scored against a seat-specific expectation derived from state partisan lean (Cook PVI). Matching the seat's expectation scores ~50 (\"typical partisan for this seat\"); hyper-loyalty in a swing or opposed seat drifts below neutral instead of to a failure grade.",
      "Crossing party lines is not rewarded for its own sake: surplus crossing earns credit only where it plausibly moves toward the state's median voter (opposed and swing seats). In deep aligned seats it sits near neutral — break direction relative to state opinion is unobservable, so safe-seat defection is treated as neither virtue nor defiance.",
      "Removed the hard floor that pinned any break rate under 3% at a score of ~20 — it placed 73 of 100 senators in an indistinguishable 26-38 band and labeled typical representation a failure.",
      "Removed the exemption for party-line votes on policy areas related to a member's top donor industries. Donor industries are not a proxy for state interests; that exemption shielded exactly the votes most suspect for donor influence.",
    ],
  },
  {
    version: "v4.1",
    date: "2026-07-03",
    title: "Adversarial review fixes",
    changes: [
      "Candidates' own money (self-loans) is deterministically excluded from top-donor concentration and donor-vote matching — it was previously mistyped as employer money and 19 senators appeared as their own biggest donor.",
      "PAC dependency now also weighs absolute PAC dollars, so very large campaigns can't dilute millions in PAC money to a near-zero share.",
      "Donor-vote overlap descriptions were reworded: totals are employer-aggregated contributions, not single org-level donations, and overlap does not imply influence. Real registered lobbying totals (Senate LDA filings) now accompany matches where they exist.",
      "Promise evaluations are cleaned before scoring, so the Promise Persistence score is computed from exactly the promises shown.",
      "A funding-window ordering bug was fixed that gave one senator his 1984 and 2014 campaign totals instead of his most recent race.",
    ],
  },
  {
    version: "v4",
    date: "2026-07-03",
    title: "Score audit overhaul",
    changes: [
      "Funding Independence rebuilt: PAC dependency (50%), small-donor share (25%), relative top-donor concentration (25%). The previous concentration metric was structurally near-zero for $50M+ campaigns, so missing PAC data scored as independence.",
      "PAC totals now come from authoritative FEC cycle totals instead of classifier-typed donor sums; outside spending uses complete per-cycle Schedule E totals (previously truncated to the 50 largest expenditures).",
      "Independent Voting: donor component reduced to 25% while alignment data is unavailable, and no longer a constant; the party-break curve gained a 3% base rate and a capped safe-state discount.",
      "Legislative Effectiveness: advancement now counts substantive bills only (no commemorative resolutions, no double-counted laws, no calendar placements); volume is per-congress. The previous version saturated at a mean of 82.",
      "Promise alignment: sponsoring a bill on a promised topic now counts as effort (partial at most) — only advanced legislation counts as kept; topically-related votes with unknown direction no longer count as kept.",
      "House party-loyalty labels now defer to actual roll-call splits instead of content classification, fixing House Independent Voting scores that were pinned at ~87 for all 431 representatives.",
    ],
  },
  {
    version: "v3",
    date: "2026-05-15",
    title: "Data quality audit",
    changes: [
      "Funding Independence reweighted toward donor concentration; Schedule E outside spending added.",
      "Promise Persistence decoupled from voting data (removed circular fallback).",
      "Independent Voting donor default scaled by fundraising total.",
      "Legislative Effectiveness advancement threshold recalibrated to actual Senate passage rates.",
    ],
  },
];

/** Map version -> date for chart annotation lookups. */
export const VERSION_DATES: Record<string, string> = Object.fromEntries(
  SCORE_VERSIONS.map((v) => [v.version, v.date]),
);
