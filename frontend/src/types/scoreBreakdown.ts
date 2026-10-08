import type { VoteRollCall } from "./senator";

// Shapes returned by the /{entityType}/{id}/score-breakdown endpoints —
// the "click a score, see the math" panel's data source. Senator,
// representative, and president dimensions share the same shape. A
// justice's scorecard reads its loyalty figures off the justice itself.

export interface ScoreBreakdownComponent {
  label: string;
  weight?: number;
  /** null when the scorer couldn't measure it; the dimension is then
   *  weighed over its other components. */
  score?: number | null;
  detail: string;
}

export interface ScoreBreakdownDimension {
  score: number;
  components: ScoreBreakdownComponent[];
  note?: string;
  /** The numbers a scorecard sentence states, computed by the scorer that
   *  produced the score (score_calculator's *_core functions). Absent when
   *  the dimension had no data to score. */
  facts?: Record<string, unknown>;
}

/** Senator/representative: fundingIndependence, constituentAlignment, fundingDiversity, legislativeEffectiveness. */
export type RepresentationScoreBreakdown = Record<string, ScoreBreakdownDimension>;

/** fundingIndependence.facts */
export interface FundingFacts {
  contributions: number;
  pacShare: number;
  /** Null: the campaign itemizes every gift, so the filings can't say. */
  smallDonorShare: number | null;
  smallDonorExpectedShare: number | null;
  smallDonorComparison: "house-median" | "state-size";
}

/** A vote against the party, as the chamber recorded the roll call. */
export interface BreakVote {
  vote: string;
  rollCall: VoteRollCall | null;
}

/** constituentAlignment.facts */
export interface AlignmentFacts {
  party: string;
  partyVotes: number;
  breaks: number | null;
  breakRate: number | null;
  expectedBreakRate: number | null;
  /** Votes against the party from its flank: listed, not counted. Absent
   *  (with the two lists) until the member's whole-Congress record is
   *  measured. */
  flankBreaks?: number | null;
  breakVotes?: BreakVote[];
  flankBreakVotes?: BreakVote[];
}

/** legislativeEffectiveness.facts: bills whose furthest stage is each of
 *  introduced, committee action, beyond committee, passed a chamber, law. */
export interface EffectivenessFacts {
  billsByStage: number[];
}

/** President: the three dimensions, each with `facts` (the figures below).
 *  A dimension that doesn't apply to a president has no components and a
 *  null score. */
export interface PresidentScoreBreakdown {
  publicMandate: ScoreBreakdownDimension;
  effectiveness: ScoreBreakdownDimension;
  historicalLegacy: ScoreBreakdownDimension;
}

/** GET /api/signal-overlap: how closely two related score components moved
 * together over the last run's members (analyze/signal_overlap.py). */
export interface SignalOverlapPair {
  /** Pearson r, or null when there was nothing to measure. */
  r: number | null;
  n: number;
  band: "ok" | "watch" | "action" | "none";
  labels: [string, string];
}

export type SignalOverlapPairKey = "constituent" | "effectiveness";

export interface SignalOverlap {
  actionR: number;
  watchR: number;
  /** null for a chamber not yet measured. */
  chambers: Record<
    "senate" | "house",
    {
      pairs: Partial<Record<SignalOverlapPairKey, SignalOverlapPair>>;
      computedAt: string | null;
    } | null
  >;
}

/** President: publicMandate.facts. Approval where polling exists (Truman
 *  onward), else the average election margin; means are all presidents'. */
export interface PublicMandateFacts {
  approval: number | null;
  approvalMean: number | null;
  approvalTrend: number | null;
  trendMean: number | null;
  /** First-quartile average approval: where the term started. */
  approvalStart?: number | null;
  /** The change presidents starting at approvalStart went on to make. */
  trendExpected?: number | null;
  /** Set for the sitting president: the comparison is predecessors over
   *  their first this-many days, not their full terms. */
  comparedOverDays?: number | null;
  electionMargin: number | null;
  marginMean: number | null;
  /** Average approval over the last 90 days (not scored). */
  recentApproval: number | null;
  /** Approval in the president's own party, the other party and among
   *  independents, and what presidents typically got from each under the
   *  same polarization in Congress (president v9). */
  approvalGroups?: { own: number; opp: number; ind: number } | null;
  approvalExpected?: { own: number; opp: number; ind: number } | null;
  /** Points above (+) or below (-) the era's expectation, averaged over the
   *  three groups: what average approval is scored on; and the typical
   *  president's figure. */
  approvalVsEra?: number | null;
  approvalVsEraMean?: number | null;
}

/** President: effectiveness.facts. Jobs per attributed year (the first year
 *  set aside) against presidencies since 1939. GDP: since 1947, growth per
 *  person against 13 peer economies' median over the same years, catch-up
 *  set aside (gdpRelative, scored against gdpRelativeMean); before, total growth
 *  against other prewar presidencies (gdpGrowth vs gdpMean). gdpSince:
 *  since 1947, else before. */
export interface PresidentEffectivenessFacts {
  jobsMillions: number | null;
  jobsPerYear: number | null;
  jobsMean: number | null;
  gdpGrowth: number | null;
  gdpMean: number | null;
  gdpSince: boolean | null;
  gdpPerPerson: number | null;
  gdpPeers: number | null;
  /** Points of gdpPerPerson - gdpPeers the peers' catch-up accounts for. */
  gdpCatchUp: number | null;
  gdpRelative: number | null;
  gdpRelativeMean: number | null;
  /** Since 1947 (president v10): the unemployment rate the year the term
   *  began, its change over the credited years, and the change presidents
   *  starting at that rate saw; inflation the year the term began, its
   *  average over the credited years, and what presidents starting there
   *  averaged. */
  unemploymentStart?: number | null;
  unemploymentChange?: number | null;
  unemploymentExpected?: number | null;
  inflationStart?: number | null;
  inflationAverage?: number | null;
  inflationExpected?: number | null;
  economyYears?: number | null;
}

/** President: historicalLegacy.facts. C-SPAN 2021 survey points; otherTerms
 *  are the same person's other presidencies the survey rated. */
export interface HistoricalLegacyFacts {
  points: number | null;
  pointsMean: number | null;
  otherTerms: { id: string; number: number; points: number; score: number | null }[];
}
