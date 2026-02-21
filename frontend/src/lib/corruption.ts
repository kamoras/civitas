import { Senator } from "@/types/senator";

const DEFAULT_WEIGHTS: Record<string, number> = {
  constituentFunding: 0.25,
  promiseFulfillment: 0.20,
  independenceIndex: 0.25,
  donorDiversity: 0.10,
  accountability: 0.20,
};

export function calculateOverallScore(
  breakdown: Senator["representationScore"],
  weights?: Record<string, number>,
): number {
  const w = weights ?? DEFAULT_WEIGHTS;
  return Math.round(
    breakdown.constituentFunding * (w.constituentFunding ?? 0.25) +
      breakdown.promiseFulfillment * (w.promiseFulfillment ?? 0.20) +
      breakdown.independenceIndex * (w.independenceIndex ?? 0.25) +
      breakdown.donorDiversity * (w.donorDiversity ?? 0.10) +
      breakdown.accountability * (w.accountability ?? 0.20)
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
