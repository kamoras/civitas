import { Senator } from "@/types/senator";
import { formatCurrency } from "@/lib/formatting";
import CorruptionScore from "./CorruptionScore";
import IndustryBreakdown from "./IndustryBreakdown";
import VotingRecord from "./VotingRecord";
import LobbyingMatches from "./LobbyingMatches";
import PlatformTracker from "./PlatformTracker";
import PunkCommentary from "./PunkCommentary";

interface SenatorCardProps {
  senator: Senator;
}

const PARTY_COLORS = {
  D: "text-dem-blue",
  R: "text-rep-red",
  I: "text-ind-purple",
};

const PARTY_BORDER = {
  D: "border-dem-blue/30",
  R: "border-rep-red/30",
  I: "border-ind-purple/30",
};

const PARTY_LABELS = {
  D: "DEMOCRAT",
  R: "REPUBLICAN",
  I: "INDEPENDENT",
};

export default function SenatorCard({ senator }: SenatorCardProps) {
  const pacPercentRaw = (senator.funding.totalFromPACs / senator.funding.totalRaised) * 100;
  const pacPercent =
    pacPercentRaw > 0 && pacPercentRaw < 1 ? "<1" : Math.round(pacPercentRaw).toString();

  return (
    <div className={`terminal-window border-t-2 ${PARTY_BORDER[senator.party]}`}>
      <div className="terminal-titlebar">
        <div className="terminal-dot red" />
        <div className="terminal-dot yellow" />
        <div className="terminal-dot green" />
        <span className="text-xs text-matrix-green/40 ml-2 font-mono">{senator.id}.dat</span>
      </div>

      <div className="p-4 sm:p-6 space-y-8">
        {/* Header */}
        <div className="flex items-start gap-4">
          <div
            className={`w-16 h-16 border-2 ${PARTY_BORDER[senator.party]} flex items-center justify-center font-pixel text-lg ${PARTY_COLORS[senator.party]}`}
          >
            {senator.initials}
          </div>
          <div>
            <h2
              className={`text-lg sm:text-2xl md:text-3xl font-pixel ${PARTY_COLORS[senator.party]} break-words`}
            >
              {senator.name}
            </h2>
            <div className="flex flex-wrap items-center gap-2 mt-1 text-sm">
              <span className={`font-pixel text-xs ${PARTY_COLORS[senator.party]}`}>
                [{senator.party}]
              </span>
              <span className="text-matrix-green/40">{PARTY_LABELS[senator.party]}</span>
              <span className="text-matrix-green/20">|</span>
              <span className="text-matrix-green/40">{senator.yearsInOffice} YRS IN OFFICE</span>
            </div>
          </div>
        </div>

        {/* Quick Stats */}
        <div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
            <div className="bg-matrix-dark-green/20 border border-matrix-green/10 p-2">
              <div className="text-lg font-pixel text-neon-cyan">
                {formatCurrency(senator.funding.totalRaised)}
              </div>
              <div className="text-[10px] text-matrix-green/40">TOTAL RAISED</div>
            </div>
            <div className="bg-matrix-dark-green/20 border border-matrix-green/10 p-2">
              <div className="text-lg font-pixel text-neon-pink">
                {formatCurrency(senator.funding.totalFromPACs)}
              </div>
              <div className="text-[10px] text-matrix-green/40">FROM PACs</div>
            </div>
            <div className="bg-matrix-dark-green/20 border border-matrix-green/10 p-2">
              <div className="text-lg font-pixel text-red-500">{pacPercent}%</div>
              <div className="text-[10px] text-matrix-green/40">PAC FUNDED</div>
            </div>
            <div className="bg-matrix-dark-green/20 border border-matrix-green/10 p-2">
              <div className="text-lg font-pixel text-matrix-green">
                {senator.funding.smallDonorPercentage}%
              </div>
              <div className="text-[10px] text-matrix-green/40">SMALL DONORS</div>
            </div>
          </div>
          <div className="text-[10px] text-matrix-green/25 mt-2 text-right">
            Source: fec.gov/data &amp; opensecrets.org/members-of-congress
          </div>
        </div>

        {/* Corruption Score */}
        <CorruptionScore breakdown={senator.corruptionScore} />

        {/* Top Donors */}
        <div>
          <div className="flex items-baseline justify-between mb-3">
            <h3 className="text-lg text-neon-cyan neon-cyan">
              {">"} TOP CORPORATE &amp; PAC DONORS
            </h3>
            <span className="text-[10px] text-matrix-green/25">
              Source: fec.gov/data &amp; opensecrets.org
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-matrix-green/40 text-xs border-b border-matrix-green/20">
                  <th className="text-left py-2 pr-4">RANK</th>
                  <th className="text-left py-2 pr-4">DONOR</th>
                  <th className="text-right py-2 pr-4">AMOUNT</th>
                  <th className="text-right py-2">TYPE</th>
                </tr>
              </thead>
              <tbody>
                {senator.funding.topDonors.map((donor, i) => (
                  <tr
                    key={donor.name}
                    className={`border-b border-matrix-green/5 ${
                      i % 2 === 0 ? "bg-matrix-dark-green/10" : ""
                    }`}
                  >
                    <td className="py-2 pr-4 text-matrix-green/40">#{i + 1}</td>
                    <td className="py-2 pr-4">
                      <div className="text-matrix-green/80">{donor.name}</div>
                      {donor.pacSponsor && (
                        <div className="text-[10px] text-neon-pink/60 mt-0.5">
                          BEHIND THE PAC: {donor.pacSponsor}
                        </div>
                      )}
                      {donor.pacAnalysis && (
                        <div className="text-[10px] text-matrix-green/40 mt-0.5">
                          {donor.pacAnalysis}
                        </div>
                      )}
                    </td>
                    <td className="py-2 pr-4 text-right text-neon-cyan">
                      {formatCurrency(donor.total)}
                    </td>
                    <td className="py-2 text-right">
                      <div className="text-matrix-green/40 text-xs">{donor.type}</div>
                      {donor.pacIndustry && donor.pacIndustry !== "OTHER" && (
                        <div className="text-[10px] text-neon-cyan/50 mt-0.5">
                          {donor.pacIndustry.replace("_", " ")}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Industry Breakdown */}
        <IndustryBreakdown industries={senator.funding.industryBreakdown} />

        {/* Voting Record */}
        <VotingRecord
          totalVotes={senator.votingRecord.totalVotes}
          proCorporateVotes={senator.votingRecord.proCorporateVotes}
          proConsumerVotes={senator.votingRecord.proConsumerVotes}
          votedWithPartyCount={senator.votingRecord.votedWithPartyCount}
          votedAgainstPartyCount={senator.votingRecord.votedAgainstPartyCount}
          partyLoyaltyPct={senator.votingRecord.partyLoyaltyPct}
          votingSummary={senator.votingRecord.votingSummary}
          recentVotes={senator.votingRecord.recentVotes}
          keyVotes={senator.votingRecord.keyVotes}
        />

        {/* Campaign Promises vs. Votes */}
        <PlatformTracker
          promises={senator.campaignPromises || []}
          platformSummary={senator.platformSummary || ""}
        />

        {/* Lobbying Matches */}
        <LobbyingMatches matches={senator.lobbyingMatches} />

        {/* Commentary */}
        <PunkCommentary senator={senator} />
      </div>
    </div>
  );
}
