import { Senator } from "@/types/senator";
import { formatCurrency } from "@/lib/formatting";

type CommentaryGenerator = (senator: Senator) => string | null;

const generators: CommentaryGenerator[] = [
  // High pharma donations + relevant voting
  (s) => {
    const pharma = s.funding.industryBreakdown.find((d) => d.industry === "PHARMA");
    if (pharma && pharma.percentage > 8)
      return `${s.name} received ${formatCurrency(pharma.total)} from pharmaceutical interests (${pharma.percentage}% of total funding). Cross-reference this with their voting record on drug pricing legislation above.`;
    return null;
  },

  // High oil & gas
  (s) => {
    const oil = s.funding.industryBreakdown.find((d) => d.industry === "OIL_GAS");
    if (oil && oil.percentage > 8)
      return `Oil & Gas is a top funding source for ${s.name} at ${formatCurrency(oil.total)} (${oil.percentage}% of total). See how this lines up with their energy and climate votes above.`;
    return null;
  },

  // Low small donor percentage
  (s) => {
    if (s.funding.smallDonorPercentage < 15)
      return `${s.funding.smallDonorPercentage}% of ${s.name}'s funding comes from small individual donors (under $200). The remaining ${100 - s.funding.smallDonorPercentage}% comes from PACs, large donors, and corporate interests.`;
    return null;
  },

  // High PAC money
  (s) => {
    const pacPercent = Math.round((s.funding.totalFromPACs / s.funding.totalRaised) * 100);
    if (pacPercent > 40)
      return `PAC contributions make up ${pacPercent}% of ${s.name}'s total fundraising (${formatCurrency(s.funding.totalFromPACs)} out of ${formatCurrency(s.funding.totalRaised)}).`;
    return null;
  },

  // High pro-corporate voting record
  (s) => {
    const corpPercent = Math.round(
      (s.votingRecord.proCorporateVotes / s.votingRecord.totalVotes) * 100
    );
    if (corpPercent > 70)
      return `${s.name} voted in line with industry lobby positions on ${corpPercent}% of tracked votes (${s.votingRecord.proCorporateVotes.toLocaleString()} out of ${s.votingRecord.totalVotes.toLocaleString()} total). Vote alignment categorized per GovTrack.us and MapLight methodology.`;
    return null;
  },

  // High finance/wall street
  (s) => {
    const finance = s.funding.industryBreakdown.find((d) => d.industry === "FINANCE");
    if (finance && finance.percentage > 10)
      return `Securities and investment firms contributed ${formatCurrency(finance.total)} to ${s.name} (${finance.percentage}% of total funding). Review their votes on financial regulation above.`;
    return null;
  },

  // Defense spending
  (s) => {
    const defense = s.funding.industryBreakdown.find((d) => d.industry === "DEFENSE");
    if (defense && defense.percentage > 7)
      return `Defense and aerospace contractors contributed ${formatCurrency(defense.total)} to ${s.name} (${defense.percentage}% of total). See their defense authorization votes above.`;
    return null;
  },

  // Lobbying match rate
  (s) => {
    const aligned = s.lobbyingMatches.filter((m) => m.senatorVoteAligned);
    if (s.lobbyingMatches.length > 0) {
      const rate = Math.round((aligned.length / s.lobbyingMatches.length) * 100);
      if (rate >= 70)
        return `${s.name}'s votes aligned with lobby donor positions on ${rate}% of tracked bills (${aligned.length} out of ${s.lobbyingMatches.length}). Note: alignment may reflect shared policy views rather than direct influence.`;
    }
    return null;
  },

  // Years in office + total raised
  (s) => {
    if (s.yearsInOffice >= 20)
      return `${s.name} has served ${s.yearsInOffice} years in the Senate and raised ${formatCurrency(s.funding.totalRaised)} over their career. All figures sourced from public FEC and Senate LDA filings.`;
    return null;
  },
];

export function generateCommentary(senator: Senator): string[] {
  const results: string[] = [];
  for (const gen of generators) {
    const comment = gen(senator);
    if (comment) results.push(comment);
    if (results.length >= 3) break;
  }

  if (results.length === 0) {
    results.push(
      `${senator.name} has served ${senator.yearsInOffice} years in office and raised ${formatCurrency(senator.funding.totalRaised)} in campaign funds. ${senator.funding.smallDonorPercentage}% of that came from small donors. All data sourced from public FEC filings.`
    );
  }

  return results;
}
