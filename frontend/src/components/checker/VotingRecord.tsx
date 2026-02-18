"use client";

import { useState } from "react";
import { KeyVote } from "@/types/senator";
import { formatCurrency } from "@/lib/formatting";

interface VotingRecordProps {
  totalVotes: number;
  proCorporateVotes: number;
  proConsumerVotes: number;
  votedWithPartyCount: number;
  votedAgainstPartyCount: number;
  partyLoyaltyPct: number;
  votingSummary: string;
  recentVotes: KeyVote[];
  keyVotes: KeyVote[];
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

  const content = (
    <div className="flex items-center gap-2 flex-wrap">
      <VoteBadge vote={vote.vote} />
      <PartyBadge leaning={vote.partyLeaning} />
      {vote.vote !== "Not Voting" && (
        <>
          <PartyAlignmentBadge votedWithParty={vote.votedWithParty} />
          {vote.proBusinessVote && (
            <span
              className={`text-[10px] px-1.5 py-0.5 border ${
                vote.vote === vote.proBusinessVote
                  ? "text-neon-pink/60 border-neon-pink/20 bg-neon-pink/5"
                  : "text-neon-cyan/60 border-neon-cyan/20 bg-neon-cyan/5"
              }`}
            >
              {vote.vote === vote.proBusinessVote ? "VOTED FOR INDUSTRY" : "VOTED FOR CONSUMERS"}
            </span>
          )}
        </>
      )}
      <span className="text-matrix-green/80 text-sm">{vote.billName}</span>
    </div>
  );

  if (!expandable) {
    return (
      <div className="terminal-window p-3">
        {content}
        {vote.date && (
          <div className="text-[10px] text-matrix-green/30 mt-1">{vote.date}</div>
        )}
      </div>
    );
  }

  return (
    <div className="terminal-window">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left p-3 flex items-center justify-between"
      >
        {content}
        <span className="text-matrix-green/40 ml-2 shrink-0">
          {expanded ? "[-]" : "[+]"}
        </span>
      </button>

      {expanded && (
        <div className="px-3 pb-3 border-t border-matrix-green/10 pt-3 space-y-2 text-sm">
          <div className="text-matrix-green/50">
            {vote.billId} &mdash; {vote.date}
          </div>
          {vote.keyVoteReasoning && (
            <div className="bg-matrix-green/5 border border-matrix-green/20 p-2">
              <div className="text-xs text-matrix-green/60 mb-1">WHY THIS VOTE MATTERS</div>
              <div className="text-xs text-matrix-green/80">{vote.keyVoteReasoning}</div>
            </div>
          )}
          <p className="text-matrix-green/70">{vote.description}</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
            <div className="bg-red-500/5 border border-red-500/20 p-2">
              <div className="text-xs text-red-500/60 mb-1">CORPORATE INTEREST</div>
              <div className="text-xs text-red-400">{vote.corporateInterest}</div>
            </div>
            <div className="bg-neon-cyan/5 border border-neon-cyan/20 p-2">
              <div className="text-xs text-neon-cyan/60 mb-1">PUBLIC IMPACT</div>
              <div className="text-xs text-neon-cyan/80">{vote.publicImpact}</div>
            </div>
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

export default function VotingRecord({
  totalVotes,
  proCorporateVotes,
  partyLoyaltyPct,
  votingSummary,
  recentVotes,
  keyVotes,
}: VotingRecordProps) {
  const corpPercent = totalVotes > 0 ? Math.round((proCorporateVotes / totalVotes) * 100) : 0;

  return (
    <div className="space-y-8">
      {/* Summary Stats */}
      <div>
        <div className="flex items-baseline justify-between mb-3">
          <h3 className="text-lg text-neon-cyan neon-cyan">{">"} VOTING RECORD</h3>
          <span className="text-[10px] text-matrix-green/25">
            Source: congress.gov &amp; senate.gov roll calls
          </span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-2 text-center text-sm">
          <div className="terminal-window p-3">
            <div className="text-xl font-pixel text-matrix-green">
              {totalVotes.toLocaleString()}
            </div>
            <div className="text-matrix-green/40 text-xs">TOTAL VOTES</div>
          </div>
          <div className="terminal-window p-3">
            <div className="text-xl font-pixel text-neon-cyan">{Math.round(partyLoyaltyPct)}%</div>
            <div className="text-matrix-green/40 text-xs">PARTY LOYALTY</div>
          </div>
          <div className="terminal-window p-3">
            <div className="text-xl font-pixel text-neon-pink">{corpPercent}%</div>
            <div className="text-matrix-green/40 text-xs">VOTED FOR INDUSTRY</div>
          </div>
          <div className="terminal-window p-3">
            <div className="text-xl font-pixel text-matrix-green">{100 - corpPercent}%</div>
            <div className="text-matrix-green/40 text-xs">VOTED FOR CONSUMERS</div>
          </div>
        </div>
        <p className="text-[10px] text-matrix-green/30 mb-4 italic">
          &ldquo;Party loyalty&rdquo; = percentage of tracked votes where senator voted with their
          party&apos;s expected position. &ldquo;Voted for industry&rdquo; = the senator&apos;s
          vote (Yea or Nay) went in the direction that serves corporate interests on that bill.
          Neither implies the vote was wrong or motivated by external factors.
        </p>
      </div>

      {/* Key Votes — Long-Term Summary */}
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

      {/* Recent Votes */}
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
