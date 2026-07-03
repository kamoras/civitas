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
