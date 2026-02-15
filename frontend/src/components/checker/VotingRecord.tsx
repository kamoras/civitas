"use client";

import { useState } from "react";
import { KeyVote } from "@/types/senator";
import { formatCurrency } from "@/lib/formatting";

interface VotingRecordProps {
  totalVotes: number;
  proCorporateVotes: number;
  proConsumerVotes: number;
  keyVotes: KeyVote[];
}

export default function VotingRecord({
  totalVotes,
  proCorporateVotes,
  keyVotes,
}: VotingRecordProps) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const corpPercent = Math.round((proCorporateVotes / totalVotes) * 100);

  return (
    <div>
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-lg text-neon-cyan neon-cyan">{">"} VOTING RECORD</h3>
        <span className="text-[10px] text-matrix-green/25">
          Source: congress.gov roll call votes
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2 mb-2 text-center text-sm">
        <div className="terminal-window p-3">
          <div className="text-xl font-pixel text-matrix-green">{totalVotes.toLocaleString()}</div>
          <div className="text-matrix-green/40 text-xs">TOTAL VOTES</div>
        </div>
        <div className="terminal-window p-3">
          <div className="text-xl font-pixel text-neon-cyan">{corpPercent}%</div>
          <div className="text-matrix-green/40 text-xs">INDUSTRY-ALIGNED</div>
        </div>
        <div className="terminal-window p-3">
          <div className="text-xl font-pixel text-matrix-green">{100 - corpPercent}%</div>
          <div className="text-matrix-green/40 text-xs">CONSUMER-ALIGNED</div>
        </div>
      </div>
      <p className="text-[10px] text-matrix-green/30 mb-4 italic">
        &ldquo;Industry-aligned&rdquo; = voted in the direction that serves corporate/business
        interests on that bill, based on bill classification from Congress.gov data. This does not
        imply the vote was wrong or motivated by donations.
      </p>

      <div className="text-xs text-matrix-green/40 mb-2">KEY VOTES:</div>
      <div className="space-y-2">
        {keyVotes.map((vote) => (
          <div key={vote.billId} className="terminal-window">
            <button
              onClick={() => setExpanded(expanded === vote.billId ? null : vote.billId)}
              className="w-full text-left p-3 flex items-center justify-between"
            >
              <div className="flex items-center gap-3 flex-wrap">
                <span
                  className={`font-pixel text-xs px-2 py-1 ${
                    vote.vote === "Yea"
                      ? "text-matrix-green bg-matrix-green/10 border border-matrix-green/30"
                      : vote.vote === "Nay"
                        ? "text-red-500 bg-red-500/10 border border-red-500/30"
                        : "text-yellow-500 bg-yellow-500/10 border border-yellow-500/30"
                  }`}
                >
                  {vote.vote.toUpperCase()}
                </span>
                {vote.proBusinessVote && vote.vote !== "Not Voting" && (
                  <span
                    className={`text-[10px] px-1.5 py-0.5 border ${
                      vote.vote === vote.proBusinessVote
                        ? "text-neon-pink/70 border-neon-pink/30 bg-neon-pink/5"
                        : "text-neon-cyan/70 border-neon-cyan/30 bg-neon-cyan/5"
                    }`}
                  >
                    {vote.vote === vote.proBusinessVote ? "INDUSTRY-ALIGNED" : "CONSUMER-ALIGNED"}
                  </span>
                )}
                <span className="text-matrix-green/80 text-sm">{vote.billName}</span>
              </div>
              <span className="text-matrix-green/40">
                {expanded === vote.billId ? "[-]" : "[+]"}
              </span>
            </button>

            {expanded === vote.billId && (
              <div className="px-3 pb-3 border-t border-matrix-green/10 pt-3 space-y-2 text-sm">
                <div className="text-matrix-green/50">
                  {vote.billId} &mdash; {vote.date}
                </div>
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
        ))}
      </div>
    </div>
  );
}
