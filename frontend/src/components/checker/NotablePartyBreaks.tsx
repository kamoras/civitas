"use client";

import { useState } from "react";
import { fetchSenatorVotes, fetchRepVotes } from "@/lib/api";
import type { KeyVote } from "@/types/senator";

interface NotablePartyBreaksProps {
  entityId: string;
  entityType: "senate" | "house";
  votedAgainstPartyCount: number;
}

export default function NotablePartyBreaks({
  entityId,
  entityType,
  votedAgainstPartyCount,
}: NotablePartyBreaksProps) {
  const [open, setOpen] = useState(false);
  const [votes, setVotes] = useState<KeyVote[] | null>(null);
  const [loading, setLoading] = useState(false);

  if (votedAgainstPartyCount === 0) return null;

  async function handleToggle() {
    if (!open && votes === null) {
      setLoading(true);
      try {
        const fn = entityType === "house" ? fetchRepVotes : fetchSenatorVotes;
        const result = await fn(entityId, { category: "key", filter: "against-party", perPage: 5 });
        setVotes(result.votes);
      } catch {
        setVotes([]);
      } finally {
        setLoading(false);
      }
    }
    setOpen((v) => !v);
  }

  return (
    <div className="mt-2 border-t border-matrix-green/10 pt-2">
      <button
        onClick={handleToggle}
        className="font-pixel text-[10px] text-neon-cyan/60 hover:text-neon-cyan transition-colors flex items-center gap-1"
        aria-expanded={open}
      >
        <span aria-hidden="true">{open ? "▼" : "▶"}</span>
        NOTABLE PARTY BREAKS ({votedAgainstPartyCount})
      </button>

      {open && (
        <div className="mt-2 space-y-2" role="list" aria-label="Party break votes">
          {loading && (
            <div className="text-[10px] text-matrix-green/40 font-pixel animate-pulse">
              LOADING VOTES...
            </div>
          )}
          {votes && votes.length === 0 && (
            <div className="text-[10px] text-matrix-green/40 font-pixel italic">
              No key party-break votes found.
            </div>
          )}
          {votes && votes.map((vote) => (
            <div key={`${vote.billId}-${vote.date}`} role="listitem" className="flex flex-col gap-0.5 py-1 border-b border-matrix-green/10">
              <div className="flex items-start justify-between gap-2">
                <a
                  href={`https://www.congress.gov/search?q=${encodeURIComponent(vote.billName)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[11px] text-matrix-green/80 hover:text-neon-cyan transition-colors leading-snug flex-1 min-w-0"
                >
                  {vote.billName}
                </a>
                <div className="flex items-center gap-1.5 shrink-0">
                  <span
                    className={`font-pixel text-[10px] px-1 py-0.5 border ${
                      vote.vote === "Yea"
                        ? "text-matrix-green border-matrix-green/40 bg-matrix-green/10"
                        : "text-red-400 border-red-400/40 bg-red-400/10"
                    }`}
                  >
                    {vote.vote.toUpperCase()}
                  </span>
                  <span className="text-[9px] text-matrix-green/30 font-mono">{vote.date}</span>
                </div>
              </div>
              {vote.policyArea && vote.policyArea !== "PROCEDURAL" && (
                <span className="text-[9px] text-neon-cyan/40 font-pixel">
                  {vote.policyArea.replace(/_/g, " ")}
                </span>
              )}
              {vote.description && (
                <p className="text-[10px] text-matrix-green/50 leading-snug">
                  {vote.description.slice(0, 100)}{vote.description.length > 100 ? "…" : ""}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
