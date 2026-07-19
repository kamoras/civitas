import { Senator } from "@/types/senator";
import type { PresidentialScore } from "@/types/president";
import type { JusticeScore } from "@/types/justice";

// Matches backend/app/config_definitions.py's SCORE_WEIGHTS — only used as
// a fallback before /api/config's live weights load (or if that fetch
// fails), so it must stay in sync by hand. promisePersistence removed
// entirely (2026-07, ALGORITHM_VERSION v6.0) — see that file's docstring
// for the empirical finding (0 of 100 senators reached even "medium"
// promise-evaluation confidence).
export const DEFAULT_WEIGHTS: Record<string, number> = {
  fundingIndependence: 0.33,
  independentVoting: 0.33,
  legislativeEffectiveness: 0.34,
};

export const DEFAULT_PRESIDENT_WEIGHTS: Record<string, number> = {
  independence: 0.15,
  followThrough: 0.20,
  publicMandate: 0.15,
  effectiveness: 0.20,
  competence: 0.15,
  agencyAlignment: 0.15,
};

const DEFAULT_JUSTICE_WEIGHTS: Record<string, number> = {
  consistency: 0.35,
  independence: 0.30,
  bipartisanAgreement: 0.15,
  judicialRestraint: 0.20,
};

// Shared by calculateOverallScore/calculatePresidentScore/calculateJusticeScore
// below — each was its own copy-pasted "round(sum of field * (weight ?? default))"
// with only the field names and default weights differing.
function weightedScore(
  // `object`, not Record<string, number>: none of the three score-breakdown
  // interfaces declare an index signature (so TS won't structurally accept
  // them as a Record), and some carry non-numeric fields alongside the
  // score_* ones (e.g. representationScore.confidence) that this never reads.
  breakdown: object,
  defaultWeights: Record<string, number>,
  weights?: Record<string, number>,
): number {
  const b = breakdown as Record<string, unknown>;
  const w = weights ?? defaultWeights;
  let sum = 0;
  for (const key of Object.keys(defaultWeights)) {
    const value = b[key];
    sum += (typeof value === "number" ? value : 0) * (w[key] ?? defaultWeights[key]);
  }
  return Math.round(sum);
}

export function calculateOverallScore(
  breakdown: Senator["representationScore"] | undefined | null,
  weights?: Record<string, number>,
): number {
  if (!breakdown) return 0;
  return weightedScore(breakdown, DEFAULT_WEIGHTS, weights);
}

export function calculatePresidentScore(
  s: PresidentialScore,
  weights?: Record<string, number>,
): number {
  return weightedScore(s, DEFAULT_PRESIDENT_WEIGHTS, weights);
}

export function calculateJusticeScore(
  s: JusticeScore,
  weights?: Record<string, number>,
): number {
  return weightedScore(s, DEFAULT_JUSTICE_WEIGHTS, weights);
}

export function getJusticeLabel(score: number): string {
  if (score >= 75) return "HIGHLY CONSISTENT";
  if (score >= 55) return "MODERATELY CONSISTENT";
  if (score >= 35) return "IDEOLOGICALLY PREDICTABLE";
  return "DEEPLY PARTISAN";
}

// Labels describe how well a member represents their constituents, not
// general virtue — "GOOD" previously read as a moral judgment on the
// politician rather than what the score actually measures (see the north
// star note in score_calculator.py: scores measure representation, not
// intrinsic goodness).
export function getScoreLabel(score: number): string {
  if (score >= 81) return "STRONGLY REPRESENTATIVE";
  if (score >= 61) return "REPRESENTATIVE";
  if (score >= 41) return "MIXED REPRESENTATION";
  if (score >= 21) return "WEAKLY REPRESENTATIVE";
  return "DEEPLY CAPTURED";
}

export function getScoreColor(score: number): string {
  if (score >= 81) return "text-matrix-green";
  if (score >= 61) return "text-neon-cyan";
  if (score >= 41) return "text-yellow-500";
  if (score >= 21) return "text-orange-500";
  return "text-red-500";
}

// bg-* counterpart of getScoreColor, for progress-bar fills — same
// thresholds/colors so a score renders identically whether it's shown as
// text or a bar, on any page. Several per-metric bars (senator/president/
// justice detail pages, the leaderboard) previously reimplemented this
// with drifting cutoffs (70/40, 70/50/35, 75/55/35/15...) and drifting
// colors (bg-cyan-400 instead of the site's actual neon-cyan), so the
// same score could render a different tier depending on which page you
// were on.
export function getScoreBgColor(score: number): string {
  if (score >= 81) return "bg-matrix-green";
  if (score >= 61) return "bg-neon-cyan";
  if (score >= 41) return "bg-yellow-500";
  if (score >= 21) return "bg-orange-500";
  return "bg-red-500";
}
