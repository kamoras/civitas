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

/** constituentAlignment.facts */
export interface AlignmentFacts {
  party: string;
  partyVotes: number;
  breaks: number | null;
  breakRate: number | null;
  expectedBreakRate: number | null;
}

/** legislativeEffectiveness.facts: bills whose furthest stage is each of
 *  introduced, committee action, beyond committee, passed a chamber, law. */
export interface EffectivenessFacts {
  billsByStage: number[];
}

/** President dimensions that are pure editorial estimates, not a live formula. */
export interface SeedOnlyDimension {
  score: number;
  seedOnly: true;
}

export interface PresidentScoreBreakdown {
  publicMandate: SeedOnlyDimension;
  effectiveness: ScoreBreakdownDimension | SeedOnlyDimension;
  agencyAlignment: ScoreBreakdownDimension | SeedOnlyDimension;
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
