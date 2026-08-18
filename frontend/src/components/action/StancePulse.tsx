"use client";

import { useCallback, useState, useSyncExternalStore } from "react";
import { submitPulseVote } from "@/lib/api";

const STORAGE_KEY = "civitas_pulse_votes";
const PULSE_EVENT = "civitas:pulse-votes";

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
  window.dispatchEvent(new Event(PULSE_EVENT));
}

function subscribeVoted(callback: () => void): () => void {
  window.addEventListener(PULSE_EVENT, callback);
  window.addEventListener("storage", callback);
  return () => {
    window.removeEventListener(PULSE_EVENT, callback);
    window.removeEventListener("storage", callback);
  };
}

// Whether the user has already voted on this issue (localStorage-backed).
// useSyncExternalStore returns a boolean snapshot — stable across renders, and
// updated when markVoted dispatches PULSE_EVENT — so no read-into-state effect.
function useHasVoted(issueId: number): boolean {
  return useSyncExternalStore(
    subscribeVoted,
    () => getVotedIssues().has(issueId),
    () => false
  );
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
  const [submitting, setSubmitting] = useState(false);
  const hasVoted = useHasVoted(issueId);

  const vote = useCallback(
    async (stance: "concerned" | "not_priority") => {
      if (hasVoted || submitting) return;
      setSubmitting(true);
      try {
        const result = await submitPulseVote(issueId, stance);
        setConcerned(result.concernedCount);
        setNotPriority(result.notPriorityCount);
        markVoted(issueId); // dispatches PULSE_EVENT → useHasVoted re-reads true
      } catch {
        /* fail silently — non-critical feature */
      } finally {
        setSubmitting(false);
      }
    },
    [issueId, hasVoted, submitting]
  );

  const total = concerned + notPriority;
  const pctConcerned = total > 0 ? Math.round((concerned / total) * 100) : 0;
  const pctNotPriority = total > 0 ? 100 - pctConcerned : 0;

  return (
    <div className="mt-4 pt-4 border-t border-white/[0.07]">
      <fieldset>
        <legend className="font-mono text-xs tracking-widest text-ink-min mb-2">
          COMMUNITY PULSE
        </legend>

        {!hasVoted ? (
          <div
            className="flex gap-2"
            role="radiogroup"
            aria-label="How important is this issue to you?"
          >
            <button
              onClick={() => vote("concerned")}
              disabled={submitting}
              className="flex-1 py-2.5 px-3 border border-white/15 text-signal-cyan font-mono text-xs tracking-widest hover:bg-signal-cyan/10 hover:border-signal-cyan/40 transition-colors
                         disabled:opacity-40 disabled:cursor-not-allowed"
              role="radio"
              aria-checked="false"
            >
              THIS CONCERNS ME
            </button>
            <button
              onClick={() => vote("not_priority")}
              disabled={submitting}
              className="flex-1 py-2.5 px-3 border border-white/[0.07] text-ink-lo font-mono text-xs tracking-widest hover:bg-white/[0.03] hover:border-white/15 transition-colors
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
              className="flex h-3 overflow-hidden border border-white/[0.07]"
              role="meter"
              aria-valuenow={pctConcerned}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`${pctConcerned}% of respondents say this concerns them`}
            >
              <div
                className="bg-signal-cyan transition-all duration-500"
                style={{ width: `${pctConcerned}%` }}
              />
              <div
                className="bg-white/[0.03] transition-all duration-500"
                style={{ width: `${pctNotPriority}%` }}
              />
            </div>
            <div className="flex justify-between mt-1.5 font-mono text-xs tracking-wide">
              <span className="text-signal-cyan">CONCERNED {pctConcerned}%</span>
              <span className="text-ink-min">
                {total} response{total !== 1 ? "s" : ""}
              </span>
              <span className="text-ink-min">NOT PRIORITY {pctNotPriority}%</span>
            </div>
          </div>
        )}
      </fieldset>
    </div>
  );
}
