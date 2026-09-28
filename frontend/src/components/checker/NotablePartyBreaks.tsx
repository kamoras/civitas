"use client";

import { useState } from "react";
import { fetchSenatorVotes, fetchRepVotes } from "@/lib/api";
import type { KeyVote } from "@/types/senator";
import RollCallSummary from "./RollCallSummary";

interface NotablePartyBreaksProps {
  entityId: string;
  entityType: "senate" | "house";
  votedAgainstPartyCount: number;
}

/** Every vote this member cast against their party. A vote counts as one
 *  only when the parties split on it (most of one voted Yea, most of the
 *  other Nay) and the member voted with the other side; each shows the
 *  roll call's own party tallies so the reader can see it. It listed only
 *  "key" votes before, so a member whose breaks were all recent roll calls
 *  showed a count and then an empty list. */
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
        const result = await fn(entityId, { category: "all", filter: "against-party", perPage: 100 });
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
        VOTES AGAINST THEIR PARTY ({votedAgainstPartyCount})
      </button>

      {open && (
        <div className="mt-2 space-y-2">
          <p className="text-xs text-ink-min">
            Roll calls where most of the member&apos;s party voted one way, most of the other party the
            other way, and the member voted with the other party.
          </p>
          {loading && (
            <div className="text-xs text-ink-min font-mono animate-pulse">LOADING VOTES...</div>
          )}
          {votes && votes.length === 0 && (
            <div className="text-xs text-ink-min font-mono">Could not load the votes.</div>
          )}
          {votes && votes.length > 0 && (
            <ul className="space-y-2" aria-label="Votes against their party">
              {votes.map((vote, i) => (
                <li key={`${vote.billId}-${vote.rollCall?.number ?? i}`} className="py-1.5 border-b border-white/[0.07] space-y-1">
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-xs text-ink leading-snug flex-1 min-w-0">
                      {vote.rollCall?.title || vote.billName}
                    </span>
                    <span
                      className={`shrink-0 font-mono text-xs px-1 py-0.5 border ${
                        vote.vote === "Yea"
                          ? "text-ink-hi border-white/15 bg-white/[0.03]"
                          : "text-signal-red border-signal-red/40 bg-signal-red/10"
                      }`}
                    >
                      VOTED {vote.vote.toUpperCase()}
                    </span>
                  </div>
                  {vote.rollCall ? (
                    <RollCallSummary rollCall={vote.rollCall} />
                  ) : (
                    vote.date && <p className="text-xs text-ink-min font-mono">{vote.date}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
