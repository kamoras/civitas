"use client";

import { useState } from "react";
import { KeyVote, VotingRecord as VotingRecordType } from "@/types/senator";
import { formatCurrency } from "@/lib/formatting";
import { voteSourceUrl } from "@/lib/sources";

interface VotingRecordProps {
  votingRecord: VotingRecordType;
}

const PARTY_BADGE: Record<string, { label: string; className: string }> = {
  R: { label: "R", className: "text-rep-red border-rep-red/30 bg-rep-red/10" },
  D: { label: "D", className: "text-dem-blue border-dem-blue/30 bg-dem-blue/10" },
  bipartisan: { label: "BI", className: "text-ind-purple border-ind-purple/30 bg-ind-purple/10" },
};

function PartyBadge({ leaning }: { leaning: string | null }) {
  if (!leaning) return null;
  const badge = PARTY_BADGE[leaning];
  if (!badge) return null;
  return (
    <span className={`text-[10px] px-1 py-0.5 border font-pixel ${badge.className}`}>
      {badge.label}
    </span>
  );
}

function PartyAlignmentBadge({ votedWithParty }: { votedWithParty: boolean | null }) {
  if (votedWithParty === null) return null;
  return votedWithParty ? (
    <span className="text-[10px] px-1.5 py-0.5 border text-matrix-green/50 border-matrix-green/20 bg-matrix-green/5">
      WITH PARTY
    </span>
  ) : (
    <span className="text-[10px] px-1.5 py-0.5 border text-neon-pink border-neon-pink/40 bg-neon-pink/10 font-bold">
      AGAINST PARTY
    </span>
  );
}

function VoteBadge({ vote }: { vote: string }) {
  const styles =
    vote === "Yea"
      ? "text-matrix-green bg-matrix-green/10 border-matrix-green/30"
      : vote === "Nay"
        ? "text-red-500 bg-red-500/10 border-red-500/30"
        : "text-yellow-500 bg-yellow-500/10 border-yellow-500/30";
  return (
    <span className={`font-pixel text-xs px-2 py-1 border ${styles}`}>
      {vote.toUpperCase()}
    </span>
  );
}

function VoteCard({
  vote,
  expandable = false,
}: {
  vote: KeyVote;
  expandable?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  const getVoteBorder = (v: string) => {
    if (v === "Yea") return "border-l-4 border-l-matrix-green/40";
    if (v === "Nay") return "border-l-4 border-l-red-500/40";
    return "border-l-4 border-l-yellow-500/30";
  };

  const voteColor =
    vote.vote === "Yea"
      ? "text-matrix-green"
      : vote.vote === "Nay"
        ? "text-red-500"
        : "text-yellow-500";

  const borderClass = getVoteBorder(vote.vote);
  const sourceLink = voteSourceUrl(vote.billId);

  const detailBadges = (
    <div className="flex items-center gap-2 flex-wrap">
      <VoteBadge vote={vote.vote} />
      <PartyBadge leaning={vote.partyLeaning} />
      {vote.vote !== "Not Voting" && (
        <>
          <PartyAlignmentBadge votedWithParty={vote.votedWithParty} />
          {vote.stanceVote && vote.policyArea !== "PROCEDURAL" && (
            <span
              className={`text-[10px] px-1.5 py-0.5 border ${
                vote.vote === vote.stanceVote
                  ? "text-neon-yellow/70 border-neon-yellow/30 bg-neon-yellow/5"
                  : "text-matrix-green/50 border-matrix-green/30 bg-matrix-green/5"
              }`}
              title={vote.vote === vote.stanceVote ? `Voted for ${vote.stance}` : `Voted against ${vote.stance}`}
            >
              {vote.policyArea}
            </span>
          )}
        </>
      )}
    </div>
  );

  if (!expandable) {
    return (
      <div className={`terminal-window p-3 ${borderClass}`}>
        <div className="flex items-center gap-2 flex-wrap">
          <VoteBadge vote={vote.vote} />
          <span className="text-matrix-green/80 text-sm">{vote.billName}</span>
        </div>
        <div className="flex items-center gap-2 mt-1">
          {vote.date && (
            <span className="text-[10px] text-matrix-green/30">{vote.date}</span>
          )}
          {sourceLink && (
            <a
              href={sourceLink}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[10px] text-neon-cyan/40 hover:text-neon-cyan transition-colors"
            >
              [SOURCE]
            </a>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={`terminal-window ${borderClass}`}>
      <button
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        aria-label={`${vote.billName}: ${vote.vote}. ${expanded ? "Collapse" : "Expand"} details`}
        className="w-full text-left p-3 flex items-center justify-between gap-2"
      >
        <div className="flex-1 min-w-0">
          <span className="text-matrix-green/80 text-sm">{vote.billName}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className={`font-pixel text-xs ${voteColor}`}>
            {vote.vote.toUpperCase()}
          </span>
          <span className="text-matrix-green/40" aria-hidden="true">
            {expanded ? "[-]" : "[+]"}
          </span>
        </div>
      </button>

      {expanded && (
        <div className="px-3 pb-3 border-t border-matrix-green/10 pt-3 space-y-2 text-sm">
          {detailBadges}

          <div className="flex items-center gap-2 flex-wrap text-matrix-green/50">
            <span>{vote.billId} &mdash; {vote.date}</span>
            {sourceLink && (
              <a
                href={sourceLink}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[10px] text-neon-cyan/50 hover:text-neon-cyan transition-colors border border-neon-cyan/20 px-1.5 py-0.5"
              >
                VIEW ON CONGRESS.GOV
              </a>
            )}
          </div>

          {vote.description && vote.description !== vote.billName && (
            <p className="text-matrix-green/70">{vote.description}</p>
          )}

          {vote.keyVoteReasoning && (
            <div className="bg-matrix-green/5 border border-matrix-green/20 p-2">
              <div className="text-xs text-matrix-green/60 mb-1">WHY THIS VOTE MATTERS</div>
              <div className="text-xs text-matrix-green/80">{vote.keyVoteReasoning}</div>
            </div>
          )}

          {vote.policyArea && vote.policyArea !== "PROCEDURAL" && (
            <div className="bg-neon-yellow/5 border border-neon-yellow/20 p-2">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs text-neon-yellow/60">
                  POLICY: {vote.policyArea}
                </span>
                {vote.stance && (
                  <span className="text-[10px] text-neon-yellow/40">
                    STANCE: {vote.stance}
                  </span>
                )}
              </div>
              {vote.impactedGroups && vote.impactedGroups.length > 0 && (
                <div className="text-xs text-matrix-green/70">
                  Impacted: {vote.impactedGroups.join(", ")}
                </div>
              )}
              {vote.affectedIndustries && vote.affectedIndustries.length > 0 && (
                <div className="text-xs text-neon-pink/60 mt-1">
                  Industries: {vote.affectedIndustries.join(", ").replace(/_/g, " ")}
                </div>
              )}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
            {vote.corporateInterest && (
              <div className="bg-red-500/5 border border-red-500/20 p-2">
                <div className="text-xs text-red-500/60 mb-1">INDUSTRY INTEREST</div>
                <div className="text-xs text-red-400">{vote.corporateInterest}</div>
              </div>
            )}
            {vote.publicImpact && (
              <div className="bg-neon-cyan/5 border border-neon-cyan/20 p-2">
                <div className="text-xs text-neon-cyan/60 mb-1">PUBLIC IMPACT</div>
                <div className="text-xs text-neon-cyan/80">{vote.publicImpact}</div>
              </div>
            )}
          </div>
          {vote.relevantDonors.length > 0 && (
            <div className="bg-neon-pink/5 border border-neon-pink/20 p-2">
              <div className="text-xs text-neon-pink/60 mb-1">
                RELATED DONORS: {vote.relevantDonors.join(", ")}
              </div>
              <div className="text-xs text-neon-pink">
                TOTAL FROM THESE DONORS: {formatCurrency(vote.relevantDonorTotal)}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PolicyBreakdownChart({ votingRecord }: { votingRecord: VotingRecordType }) {
  const breakdown = votingRecord.policyBreakdown || [];
  if (breakdown.length === 0) return null;

  const maxVotes = Math.max(...breakdown.map((b) => b.totalVotes), 1);

  return (
    <div className="terminal-window p-3">
      <div className="text-xs text-neon-cyan/60 mb-2 font-pixel">VOTES BY POLICY AREA</div>
      <div className="space-y-1.5">
        {breakdown.map((area) => {
          const withPct = area.totalVotes > 0 ? Math.round((area.withStance / area.totalVotes) * 100) : 0;
          return (
            <div key={area.policyArea} className="text-xs">
              <div className="flex justify-between mb-0.5">
                <span className="text-matrix-green/70">{area.policyArea}</span>
                <span className="text-matrix-green/40">
                  {area.totalVotes} votes
                </span>
              </div>
              <div className="h-2 bg-matrix-dark-green/30 border border-matrix-green/10 flex overflow-hidden">
                <div
                  className="h-full bg-neon-yellow/60"
                  style={{ width: `${(area.withStance / maxVotes) * 100}%` }}
                  title={`${area.withStance} with stance (${withPct}%)`}
                />
                <div
                  className="h-full bg-neon-cyan/40"
                  style={{ width: `${(area.againstStance / maxVotes) * 100}%` }}
                  title={`${area.againstStance} against stance`}
                />
              </div>
            </div>
          );
        })}
      </div>
      <div className="flex gap-4 mt-2 text-[10px] text-matrix-green/50">
        <span className="flex items-center gap-1">
          <span className="inline-block w-2 h-2 bg-neon-yellow/60" /> with stance
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-2 h-2 bg-neon-cyan/40" /> against stance
        </span>
      </div>
    </div>
  );
}

export default function VotingRecord({ votingRecord }: VotingRecordProps) {
  const {
    totalVotes,
    scoreableVotes,
    donorAlignedVotes,
    partyLoyaltyPct,
    votingSummary,
    recentVotes,
    keyVotes,
  } = votingRecord;

  const donorAlignedPct = scoreableVotes > 0 ? Math.round((donorAlignedVotes / scoreableVotes) * 100) : 0;

  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-baseline justify-between mb-3">
          <h3 className="text-lg text-neon-cyan neon-cyan">{">"} VOTING RECORD</h3>
          <span className="text-[10px] text-matrix-green/50">
            Source: congress.gov &amp; senate.gov roll calls
          </span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-2 text-center text-sm">
          <div className="terminal-window p-3">
            <div className="text-xl font-pixel text-matrix-green">
              {totalVotes.toLocaleString()}
            </div>
            <div className="text-matrix-green/40 text-xs">TOTAL TRACKED</div>
          </div>
          <div className="terminal-window p-3">
            <div className="text-xl font-pixel text-neon-cyan">{Math.round(partyLoyaltyPct)}%</div>
            <div className="text-matrix-green/40 text-xs">PARTY LOYALTY</div>
            <div className="text-[10px] text-matrix-green/50">votes with party line</div>
          </div>
          <div className="terminal-window p-3">
            <div className="text-xl font-pixel text-neon-yellow">{donorAlignedPct}%</div>
            <div className="text-matrix-green/40 text-xs">DONOR-ALIGNED</div>
            <div className="text-[10px] text-matrix-green/50">
              {donorAlignedVotes} of {scoreableVotes} scoreable
            </div>
          </div>
          <div className="terminal-window p-3">
            <div className="text-xl font-pixel text-matrix-green">{100 - donorAlignedPct}%</div>
            <div className="text-matrix-green/40 text-xs">INDEPENDENT</div>
            <div className="text-[10px] text-matrix-green/50">voted against donor interests</div>
          </div>
        </div>
      </div>

      <PolicyBreakdownChart votingRecord={votingRecord} />

      {keyVotes.length > 0 && (
        <div>
          <div className="text-xs text-neon-cyan/60 mb-2 font-pixel">
            {">"} KEY VOTES — LONG-TERM SUMMARY
          </div>
          {votingSummary && (
            <div className="terminal-window p-3 mb-3">
              <p className="text-sm text-matrix-green/80 leading-relaxed">{votingSummary}</p>
            </div>
          )}
          <div className="space-y-2">
            {keyVotes.map((vote) => (
              <VoteCard key={`key-${vote.billId}`} vote={vote} expandable />
            ))}
          </div>
        </div>
      )}

      {recentVotes.length > 0 && (
        <div>
          <div className="text-xs text-neon-cyan/60 mb-2 font-pixel">
            {">"} RECENT VOTES
          </div>
          <div className="space-y-2">
            {recentVotes.map((vote) => (
              <VoteCard key={`recent-${vote.billId}`} vote={vote} expandable />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
