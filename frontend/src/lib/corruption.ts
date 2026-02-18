import { Senator } from "@/types/senator";

// Weights for the Representation Scorecard (higher = better representative)
const WEIGHTS = {
  constituentFunding: 0.3,    // 30% — small donors vs PAC money
  promiseFulfillment: 0.3,    // 30% — votes vs stated platform
  independenceIndex: 0.2,     // 20% — independence from lobbyists
  donorDiversity: 0.1,        // 10% — breadth of funding sources
  accountability: 0.1,        // 10% — institutional accountability
};

export function calculateOverallScore(breakdown: Senator["representationScore"]): number {
  return Math.round(
    breakdown.constituentFunding * WEIGHTS.constituentFunding +
      breakdown.promiseFulfillment * WEIGHTS.promiseFulfillment +
      breakdown.independenceIndex * WEIGHTS.independenceIndex +
      breakdown.donorDiversity * WEIGHTS.donorDiversity +
      breakdown.accountability * WEIGHTS.accountability
  );
}

export function getScoreLabel(score: number): string {
  if (score >= 81) return "EXCELLENT";
  if (score >= 61) return "GOOD";
  if (score >= 41) return "MODERATE";
  if (score >= 21) return "POOR";
  return "DEEPLY CAPTURED";
}

// Higher score = better representation → green is good, red is bad
export function getScoreColor(score: number): string {
  if (score >= 81) return "text-matrix-green";
  if (score >= 61) return "text-neon-cyan";
  if (score >= 41) return "text-yellow-500";
  if (score >= 21) return "text-orange-500";
  return "text-red-500";
}
