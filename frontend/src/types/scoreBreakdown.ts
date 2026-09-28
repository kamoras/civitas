import type { VoteRollCall } from "./senator";

// Shapes returned by the /{entityType}/{id}/score-breakdown endpoints —
// the "click a score, see the math" panel's data source. Senator,
// representative, and president dimensions share the same shape;
// justice dimensions carry different fields entirely (see
// JusticeScoreBreakdown below), since analyze_justice_votes' math
// doesn't decompose into weighted components the same way.

export interface ScoreBreakdownComponent {
  label: string;
  weight?: number;
  score?: number;
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
  smallDonorShare: number;
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

/** President: the four dimensions, each with `facts` (the figures below).
 *  A dimension that doesn't apply to a president has no components and a
 *  null score. */
export interface PresidentScoreBreakdown {
  publicMandate: ScoreBreakdownDimension;
  effectiveness: ScoreBreakdownDimension;
  agencyAlignment: ScoreBreakdownDimension;
  historicalLegacy: ScoreBreakdownDimension;
}

export interface JusticeDimensionBreakdown {
  detail: string;
  [key: string]: unknown;
}

export interface JusticeScoreBreakdown {
  breakdown: {
    consistency: JusticeDimensionBreakdown;
    independence: JusticeDimensionBreakdown;
  };
  [key: string]: unknown;
}

/** President: publicMandate.facts. Approval where polling exists (Truman
 *  onward), else the average election margin; means are all presidents'. */
export interface PublicMandateFacts {
  approval: number | null;
  approvalMean: number | null;
  approvalTrend: number | null;
  trendMean: number | null;
  electionMargin: number | null;
  marginMean: number | null;
  /** Average approval over the last 90 days (not scored). */
  recentApproval: number | null;
}

/** President: effectiveness.facts. Jobs per attributed year (the first year
 *  set aside) against presidencies since 1939; GDP growth against those in
 *  the same data regime (gdpSince: since 1947, else before). */
export interface PresidentEffectivenessFacts {
  jobsMillions: number | null;
  jobsPerYear: number | null;
  jobsMean: number | null;
  gdpGrowth: number | null;
  gdpMean: number | null;
  gdpSince: boolean | null;
}

/** President: agencyAlignment.facts. */
export interface AgencyAlignmentFacts {
  finalizedPct: number | null;
  finalizedMean: number | null;
  rulemakings: number | null;
}

/** President: historicalLegacy.facts. C-SPAN 2021 survey points; otherTerms
 *  are the same person's other presidencies the survey rated. */
export interface HistoricalLegacyFacts {
  points: number | null;
  pointsMean: number | null;
  otherTerms: { id: string; number: number; points: number; score: number | null }[];
}
