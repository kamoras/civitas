import { log } from "../config.mjs";

/**
 * Calculate the five corruption sub-scores from real data.
 * These feed into the weighted formula in src/lib/corruption.ts.
 *
 * @param {Object} senator - Assembled senator data (funding, voting, lobbying)
 * @param {Object} flipFlopResult - LLM flip-flop analysis result
 * @returns {Object} corruptionScore sub-fields
 */
export function calculateScores(senator, flipFlopResult) {
  return {
    corporateFunding: calcCorporateFunding(senator.funding),
    lobbyistAlignment: calcLobbyistAlignment(senator.votingRecord, senator.lobbyingMatches),
    industryConcentration: calcIndustryConcentration(senator.funding.industryBreakdown),
    flipFlopIndex: flipFlopResult?.flipFlopScore ?? 25,
    revolvingDoor: calcRevolvingDoor(senator),
  };
}

/**
 * Corporate Funding Score (0-100)
 * Based on PAC funding ratio and total from corporate sources.
 */
function calcCorporateFunding(funding) {
  if (!funding.totalRaised || funding.totalRaised === 0) return 0;

  const pacRatio = funding.totalFromPACs / funding.totalRaised;
  const smallDonorInverse = 1 - funding.smallDonorPercentage / 100;

  // Weighted: 60% PAC ratio, 40% inverse small donor percentage
  const raw = pacRatio * 0.6 + smallDonorInverse * 0.4;

  // Scale to 0-100
  return clamp(Math.round(raw * 100));
}

/**
 * Lobbyist Alignment Score (0-100)
 * Percentage of lobbying matches where senator voted with the lobby position.
 */
function calcLobbyistAlignment(votingRecord, lobbyingMatches) {
  if (!lobbyingMatches || lobbyingMatches.length === 0) return 25; // Default moderate-low

  const aligned = lobbyingMatches.filter((m) => m.senatorVoteAligned).length;
  const rate = aligned / lobbyingMatches.length;

  return clamp(Math.round(rate * 100));
}

/**
 * Industry Concentration Score (0-100)
 * Uses Herfindahl-Hirschman Index of industry donation shares.
 * High concentration = funding dominated by few industries = higher score.
 */
function calcIndustryConcentration(industryBreakdown) {
  if (!industryBreakdown || industryBreakdown.length === 0) return 0;

  // Calculate HHI from percentage shares
  const hhi = industryBreakdown.reduce((sum, ind) => {
    const share = ind.percentage / 100;
    return sum + share * share;
  }, 0);

  // HHI ranges from ~0.05 (diverse) to 1.0 (monopoly)
  // Scale: 0.05 -> 0, 0.5 -> 100
  const normalized = Math.min((hhi - 0.05) / 0.45, 1.0);

  return clamp(Math.round(normalized * 100));
}

/**
 * Revolving Door Score (0-100)
 * Based on years in office and industry ties.
 * Long-serving senators with concentrated industry funding score higher.
 */
function calcRevolvingDoor(senator) {
  // This is a simplified heuristic. In v2, the LLM would analyze
  // hearing transcripts for mentions of prior/future industry employment.
  const yearsFactor = Math.min(senator.yearsInOffice / 30, 1.0);
  const topIndustryPct = (senator.funding.industryBreakdown?.[0]?.percentage || 0) / 100;
  const pacFactor =
    senator.funding.totalRaised > 0
      ? senator.funding.totalFromPACs / senator.funding.totalRaised
      : 0;

  const raw = yearsFactor * 0.3 + topIndustryPct * 0.4 + pacFactor * 0.3;
  return clamp(Math.round(raw * 100));
}

function clamp(value, min = 0, max = 100) {
  return Math.max(min, Math.min(max, value));
}
