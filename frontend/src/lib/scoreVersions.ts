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
