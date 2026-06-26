"use client";

import { useCallback, useEffect, useState } from "react";
import { submitPulseVote } from "@/lib/api";

const STORAGE_KEY = "civitas_pulse_votes";

function getVotedIssues(): Set<number> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return new Set();
    return new Set(JSON.parse(raw));
  } catch {
    return new Set();
  }
}

function markVoted(issueId: number): void {
  const voted = getVotedIssues();
  voted.add(issueId);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(voted)));
}

export default function StancePulse({
  issueId,
  initialConcerned,
  initialNotPriority,
}: {
  issueId: number;
  initialConcerned: number;
  initialNotPriority: number;
}) {
  const [concerned, setConcerned] = useState(initialConcerned);
  const [notPriority, setNotPriority] = useState(initialNotPriority);
  const [hasVoted, setHasVoted] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    setHasVoted(getVotedIssues().has(issueId));
  }, [issueId]);

  const vote = useCallback(
    async (stance: "concerned" | "not_priority") => {
      if (hasVoted || submitting) return;
      setSubmitting(true);
      try {
        const result = await submitPulseVote(issueId, stance);
        setConcerned(result.concernedCount);
        setNotPriority(result.notPriorityCount);
        markVoted(issueId);
        setHasVoted(true);
      } catch {
        /* fail silently — non-critical feature */
      } finally {
        setSubmitting(false);
      }
    },
    [issueId, hasVoted, submitting],
  );

  const total = concerned + notPriority;
  const pctConcerned = total > 0 ? Math.round((concerned / total) * 100) : 0;
  const pctNotPriority = total > 0 ? 100 - pctConcerned : 0;

  return (
    <div className="mt-4 pt-4 border-t border-matrix-green/10">
      <fieldset>
        <legend className="font-mono text-[10px] tracking-widest text-matrix-green/40 mb-2">
          COMMUNITY PULSE
        </legend>

        {!hasVoted ? (
          <div className="flex gap-2" role="radiogroup" aria-label="How important is this issue to you?">
            <button
              onClick={() => vote("concerned")}
              disabled={submitting}
              className="flex-1 py-2.5 px-3 border border-neon-cyan/30 text-neon-cyan/80 font-mono text-xs tracking-widest
                         hover:bg-neon-cyan/10 hover:border-neon-cyan/50 transition-colors
                         disabled:opacity-40 disabled:cursor-not-allowed"
              role="radio"
              aria-checked="false"
            >
              THIS CONCERNS ME
            </button>
            <button
              onClick={() => vote("not_priority")}
              disabled={submitting}
              className="flex-1 py-2.5 px-3 border border-matrix-green/20 text-matrix-green/50 font-mono text-xs tracking-widest
                         hover:bg-matrix-green/5 hover:border-matrix-green/30 transition-colors
                         disabled:opacity-40 disabled:cursor-not-allowed"
              role="radio"
              aria-checked="false"
            >
              NOT A PRIORITY
            </button>
          </div>
        ) : (
          <div>
            <div
              className="flex h-3 overflow-hidden border border-matrix-green/20"
              role="meter"
              aria-valuenow={pctConcerned}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`${pctConcerned}% of respondents say this concerns them`}
            >
              <div
                className="bg-neon-cyan/40 transition-all duration-500"
                style={{ width: `${pctConcerned}%` }}
              />
              <div
                className="bg-matrix-green/10 transition-all duration-500"
                style={{ width: `${pctNotPriority}%` }}
              />
            </div>
            <div className="flex justify-between mt-1.5 font-pixel text-[10px]">
              <span className="text-neon-cyan/70">
                CONCERNED {pctConcerned}%
              </span>
              <span className="text-matrix-green/40">
                {total} response{total !== 1 ? "s" : ""}
              </span>
              <span className="text-matrix-green/40">
                NOT PRIORITY {pctNotPriority}%
              </span>
            </div>
          </div>
        )}
      </fieldset>
    </div>
  );
}
