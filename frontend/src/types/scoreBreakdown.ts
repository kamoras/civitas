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
}

/** Senator/representative: fundingIndependence, independentVoting, fundingDiversity, legislativeEffectiveness. */
export type RepresentationScoreBreakdown = Record<string, ScoreBreakdownDimension>;

/** President dimensions that are pure editorial estimates, not a live formula. */
export interface SeedOnlyDimension {
  score: number;
  seedOnly: true;
}

export interface PresidentScoreBreakdown {
  independence: SeedOnlyDimension;
  followThrough: SeedOnlyDimension;
  publicMandate: SeedOnlyDimension;
  competence: ScoreBreakdownDimension | SeedOnlyDimension;
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
    bipartisanAgreement: JusticeDimensionBreakdown;
    judicialRestraint: JusticeDimensionBreakdown;
  };
  [key: string]: unknown;
}

export function isSeedOnly(d: ScoreBreakdownDimension | SeedOnlyDimension): d is SeedOnlyDimension {
  return "seedOnly" in d && d.seedOnly === true;
}
