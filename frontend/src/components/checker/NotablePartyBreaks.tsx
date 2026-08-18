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
    <div className="mt-2 border-t border-white/[0.07] pt-2">
      <button
        onClick={handleToggle}
        className="font-mono text-xs text-ink-lo hover:text-phos transition-colors flex items-center gap-1"
        aria-expanded={open}
      >
        <span aria-hidden="true">{open ? "▼" : "▶"}</span>
        NOTABLE PARTY BREAKS ({votedAgainstPartyCount})
      </button>

      {open && (
        <div className="mt-2 space-y-2" role="list" aria-label="Party break votes">
          {loading && (
            <div className="text-xs text-ink-min font-mono animate-pulse">LOADING VOTES...</div>
          )}
          {votes && votes.length === 0 && (
            <div className="text-xs text-ink-min font-mono italic">
              No key party-break votes found.
            </div>
          )}
          {votes &&
            votes.map((vote) => (
              <div
                key={`${vote.billId}-${vote.date}`}
                role="listitem"
                className="flex flex-col gap-0.5 py-1 border-b border-white/[0.07]"
              >
                <div className="flex items-start justify-between gap-2">
                  <a
                    href={`https://www.congress.gov/search?q=${encodeURIComponent(vote.billName)}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-ink hover:text-phos transition-colors leading-snug flex-1 min-w-0"
                  >
                    {vote.billName}
                  </a>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <span
                      className={`font-mono text-xs px-1 py-0.5 border ${
                        vote.vote === "Yea"
                          ? "text-ink-hi border-white/15 bg-white/[0.03]"
                          : "text-signal-red border-red-400/40 bg-red-400/10"
                      }`}
                    >
                      {vote.vote.toUpperCase()}
                    </span>
                    <span className="text-xs text-ink-min font-mono">{vote.date}</span>
                  </div>
                </div>
                {vote.policyArea && vote.policyArea !== "PROCEDURAL" && (
                  <span className="text-xs text-ink-lo font-mono">
                    {vote.policyArea.replace(/_/g, " ")}
                  </span>
                )}
                {vote.description && (
                  <p className="text-xs text-ink-lo leading-snug">
                    {vote.description.slice(0, 100)}
                    {vote.description.length > 100 ? "…" : ""}
                  </p>
                )}
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
