import { Senator } from "@/types/senator";
import { calculateOverallScore } from "@/lib/corruption";
import { formatCurrency } from "@/lib/formatting";

export function generateCommentary(senator: Senator): string[] {
  const comments: string[] = [];
  const score = calculateOverallScore(senator.representationScore);
  const { funding, votingRecord, lobbyingMatches } = senator;

  // Funding concentration
  if (funding.totalFromPACs > 0 && funding.totalRaised > 0) {
    const pacPct = Math.round((funding.totalFromPACs / funding.totalRaised) * 100);
    if (pacPct >= 50) {
      comments.push(
        `${pacPct}% of ${senator.name}'s ${formatCurrency(funding.totalRaised)} in campaign fundraising came from PACs — more than half from political organizations rather than individual donors.`
      );
    } else if (pacPct >= 25) {
      comments.push(
        `PACs account for ${pacPct}% of ${senator.name}'s total fundraising (${formatCurrency(funding.totalFromPACs)} out of ${formatCurrency(funding.totalRaised)}).`
      );
    }
  }

  // Small donor percentage
  if (funding.smallDonorPercentage > 0) {
    if (funding.smallDonorPercentage >= 60) {
      comments.push(
        `${Math.round(funding.smallDonorPercentage)}% of contributions came from small donors — a relatively grassroots funding base compared to Senate peers.`
      );
    } else if (funding.smallDonorPercentage < 20) {
      comments.push(
        `Only ${Math.round(funding.smallDonorPercentage)}% of contributions came from small donors — the overwhelming majority of funding flows from large donors and PACs.`
      );
    }
  }

  // Top donor callout
  if (funding.topDonors.length > 0) {
    const top = funding.topDonors[0];
    comments.push(
      `Top donor: ${top.name} contributed ${formatCurrency(top.total)}${top.pacIndustry ? ` (${top.pacIndustry} sector)` : ""}.`
    );
  }

  // Party loyalty
  if (votingRecord.partyLoyaltyPct > 0) {
    if (votingRecord.partyLoyaltyPct >= 95) {
      comments.push(
        `${senator.name} votes with their party ${Math.round(votingRecord.partyLoyaltyPct)}% of the time — near-perfect party-line discipline.`
      );
    } else if (votingRecord.partyLoyaltyPct < 70) {
      comments.push(
        `Party loyalty sits at ${Math.round(votingRecord.partyLoyaltyPct)}% — ${senator.name} crosses party lines more often than most.`
      );
    }
  }

  // Lobbying matches
  if (lobbyingMatches.length > 0) {
    const aligned = lobbyingMatches.filter((m) => m.senatorVoteAligned);
    const withResult = lobbyingMatches.filter((m) => m.senatorVoteAligned !== null && m.senatorVoteAligned !== undefined);
    if (aligned.length > 0 && withResult.length > 0) {
      comments.push(
        `In ${aligned.length} of ${withResult.length} evaluable donor-vote overlaps, ${senator.name} voted in the same direction as the donor's industry interests. This is a correlation — not evidence of influence — but worth noting.`
      );
    }
  }

  // Overall score
  if (score >= 70) {
    comments.push(
      `Representation score: ${score}/100. The data suggests strong constituent alignment — this senator scores well across funding independence, donor diversity, and platform adherence.`
    );
  } else if (score <= 30) {
    comments.push(
      `Representation score: ${score}/100 — one of the lower scores in our dataset, indicating limited independence from PAC funding and industry-aligned voting patterns.`
    );
  }

  // Fallback
  if (comments.length === 0) {
    comments.push(
      `Pipeline data for ${senator.name} is still being collected. Run the pipeline to generate a full analysis.`
    );
  }

  return comments;
}
