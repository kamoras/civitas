import { Senator } from "@/types/senator";
import type { PresidentialScore } from "@/types/president";

const DEFAULT_WEIGHTS: Record<string, number> = {
  fundingIndependence: 0.30,
  promisePersistence: 0.25,
  independentVoting: 0.25,
  fundingDiversity: 0.20,
};

export function calculateOverallScore(
  breakdown: Senator["representationScore"] | undefined | null,
  weights?: Record<string, number>,
): number {
  if (!breakdown) return 0;
  const w = weights ?? DEFAULT_WEIGHTS;
  return Math.round(
    (breakdown.fundingIndependence ?? 0) * (w.fundingIndependence ?? 0.30) +
      (breakdown.promisePersistence ?? 0) * (w.promisePersistence ?? 0.25) +
      (breakdown.independentVoting ?? 0) * (w.independentVoting ?? 0.25) +
      (breakdown.fundingDiversity ?? 0) * (w.fundingDiversity ?? 0.20)
  );
}

const DEFAULT_PRESIDENT_WEIGHTS: Record<string, number> = {
  independence: 0.15,
  followThrough: 0.20,
  publicMandate: 0.15,
  effectiveness: 0.20,
  competence: 0.15,
  agencyAlignment: 0.15,
};

export function calculatePresidentScore(
  s: PresidentialScore,
  weights?: Record<string, number>,
): number {
  const w = weights ?? DEFAULT_PRESIDENT_WEIGHTS;
  return Math.round(
    s.independence * (w.independence ?? 0.15) +
      s.followThrough * (w.followThrough ?? 0.20) +
      s.publicMandate * (w.publicMandate ?? 0.15) +
      s.effectiveness * (w.effectiveness ?? 0.20) +
      s.competence * (w.competence ?? 0.15) +
      s.agencyAlignment * (w.agencyAlignment ?? 0.15),
  );
}

export function getScoreLabel(score: number): string {
  if (score >= 81) return "EXCELLENT";
  if (score >= 61) return "GOOD";
  if (score >= 41) return "MODERATE";
  if (score >= 21) return "POOR";
  return "DEEPLY CAPTURED";
}

export function getScoreColor(score: number): string {
  if (score >= 81) return "text-matrix-green";
  if (score >= 61) return "text-neon-cyan";
  if (score >= 41) return "text-yellow-500";
  if (score >= 21) return "text-orange-500";
  return "text-red-500";
}
