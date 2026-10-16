"use client";

import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";
import type { BillRecordVote, VoteDetail } from "@/types/congress";
import { CHAMBER_NAME, resultTone, rollCallHref, shortDate, voteResult } from "@/lib/congress";
import { TallyText, VoteCount } from "./ReportParts";
import VoteMembers, { PartyBar } from "./VoteMembers";

const TONE = { yes: "text-phos-mid", no: "text-signal-red", neutral: "text-ink-lo" } as const;

function voteKey(v: { chamber: string; session: number; number: number }) {
  return `vote-${v.chamber}-${v.session}-${v.number}`;
}

/** A bill's recorded votes, and for the selected one each party's split
 * and every member's position, collapsed behind a state pick or "Show all"
 * (the roll call's own page lists them all). */
export default function VotePanel({
  votes,
  congress,
}: {
  votes: BillRecordVote[];
  congress: number;
}) {
  const [chosen, setChosen] = useState<string | null>(null);
  // Each vote's members, fetched once: a vote never changes after it is taken.
  const [loaded, setLoaded] = useState<Record<string, VoteDetail | "failed">>({});

  // A link from a day report ("#vote-senate-2-243") opens that vote. Read
  // after hydration, through the hashchange subscription, so the server
  // render and the first client render agree.
  const hash = useSyncExternalStore(
    (onChange) => {
      window.addEventListener("hashchange", onChange);
      return () => window.removeEventListener("hashchange", onChange);
    },
    () => window.location.hash.slice(1),
    () => ""
  );
  const fromHash = votes.some((v) => voteKey(v) === hash) ? hash : null;
  const selected = chosen ?? fromHash ?? (votes[0] ? voteKey(votes[0]) : "");
  const setSelected = setChosen;

  const vote = votes.find((v) => voteKey(v) === selected);
  const entry = vote ? loaded[selected] : undefined;
  const detail = entry && entry !== "failed" ? entry : null;
  const failed = entry === "failed";

  useEffect(() => {
    if (!vote || loaded[selected] !== undefined) return;
    let cancelled = false;
    fetch(`/api/congress/votes/${vote.chamber}/${congress}/${vote.session}/${vote.number}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d: VoteDetail) => !cancelled && setLoaded((m) => ({ ...m, [selected]: d })))
      .catch(() => !cancelled && setLoaded((m) => ({ ...m, [selected]: "failed" })));
    return () => {
      cancelled = true;
    };
  }, [vote, congress, selected, loaded]);

  if (!votes.length) {
    return (
      <p className="text-[15px] text-ink-lo">No recorded votes on this bill in either chamber.</p>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col">
        {votes.map((v) => {
          const key = voteKey(v);
          const on = key === selected;
          return (
            <li key={key} id={key} className="border-b border-white/[0.09]">
              <button
                type="button"
                aria-pressed={on}
                onClick={() => setSelected(key)}
                className={`grid w-full grid-cols-[6rem_minmax(0,1fr)] gap-x-4 gap-y-2 py-3 text-left transition-colors sm:grid-cols-[6rem_minmax(0,1fr)_9.5rem] ${
                  on ? "bg-white/[0.04]" : "hover:bg-white/[0.02]"
                }`}
              >
                <span className="flex flex-col gap-1 pl-2 font-mono text-xs uppercase tracking-[0.1em] text-ink-lo">
                  <span>
                    {CHAMBER_NAME[v.chamber]} <span className="sr-only">roll call </span>
                    {v.number}
                  </span>
                  <span className="text-ink-min">{shortDate(v.date)}</span>
                </span>
                <span className="text-[15px] leading-snug text-ink">{v.question}</span>
                <span className="col-start-2 flex flex-col items-start gap-1 pr-2 sm:col-start-auto sm:items-end">
                  <span
                    className={`font-mono text-xs uppercase tracking-[0.1em] ${TONE[resultTone(v.rejected)]}`}
                  >
                    {voteResult(v)}
                  </span>
                  <VoteCount vote={v} />
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      {vote && (
        <div className="flex flex-col gap-4 border border-white/15 bg-surface p-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-lo">
                {CHAMBER_NAME[vote.chamber]} vote {vote.number} · {shortDate(vote.date)}
              </p>
              <p className="mt-1 text-lg font-bold text-ink-hi">
                {vote.question}: {voteResult(vote).toLowerCase()}, <TallyText vote={vote} />
              </p>
            </div>
            <Link
              href={rollCallHref({ ...vote, congress })}
              className="font-mono text-xs uppercase tracking-[0.12em] text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-phos"
            >
              Open the roll call &rarr;
            </Link>
          </div>
          <div className="flex flex-col gap-2">
            {vote.parties.map((p) => (
              <PartyBar key={p.party} split={p} />
            ))}
          </div>
          {failed && (
            <p className="text-sm text-signal-amber">
              Each member&apos;s vote could not be loaded. Try again shortly.
            </p>
          )}
          {!detail && !failed && (
            <p className="text-sm text-ink-lo">Loading each member&apos;s vote…</p>
          )}
          {detail && (
            <div className="border-t border-white/[0.09] pt-4">
              <VoteMembers members={detail.members} collapsible />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
