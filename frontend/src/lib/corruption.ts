import { Senator } from "@/types/senator";

const WEIGHTS = {
  corporateFunding: 0.3,
  lobbyistAlignment: 0.25,
  industryConcentration: 0.2,
  flipFlopIndex: 0.15,
  revolvingDoor: 0.1,
};

export function calculateOverallScore(breakdown: Senator["corruptionScore"]): number {
  return Math.round(
    breakdown.corporateFunding * WEIGHTS.corporateFunding +
      breakdown.lobbyistAlignment * WEIGHTS.lobbyistAlignment +
      breakdown.industryConcentration * WEIGHTS.industryConcentration +
      breakdown.flipFlopIndex * WEIGHTS.flipFlopIndex +
      breakdown.revolvingDoor * WEIGHTS.revolvingDoor
  );
}

export function getScoreLabel(score: number): string {
  if (score >= 81) return "VERY HIGH";
  if (score >= 61) return "HIGH";
  if (score >= 41) return "MODERATE";
  if (score >= 21) return "LOW";
  return "VERY LOW";
}

export function getScoreColor(score: number): string {
  if (score >= 81) return "text-red-500";
  if (score >= 61) return "text-orange-500";
  if (score >= 41) return "text-yellow-500";
  if (score >= 21) return "text-neon-cyan";
  return "text-matrix-green";
}
