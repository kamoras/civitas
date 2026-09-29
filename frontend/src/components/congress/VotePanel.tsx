"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import type { BillRecordVote, PartySplit, VoteDetail, VoteMember } from "@/types/congress";
import { CHAMBER_NAME, resultTone, shortDate } from "@/lib/congress";
import { PARTY_COLORS } from "@/lib/partyStyles";

const PARTY_NAME: Record<string, string> = {
  R: "Republicans",
  D: "Democrats",
  I: "Independents",
  ID: "Independents",
};
const BUCKETS: { key: VoteMember["bucket"]; label: string; tone: string }[] = [
  { key: "yea", label: "Yea", tone: "text-phos-mid" },
  { key: "nay", label: "Nay", tone: "text-signal-red" },
  { key: "present", label: "Present", tone: "text-ink-lo" },
  { key: "notVoting", label: "Not voting", tone: "text-ink-lo" },
];
const TONE = { yes: "text-phos-mid", no: "text-signal-red", neutral: "text-ink-lo" } as const;

function voteKey(v: { chamber: string; session: number; number: number }) {
  return `vote-${v.chamber}-${v.session}-${v.number}`;
}

function PartyBar({ split }: { split: PartySplit }) {
  const total = split.yea + split.nay + split.present + split.notVoting || 1;
  return (
    <div className="grid grid-cols-[7rem_minmax(0,1fr)] items-center gap-x-3 gap-y-1 text-sm sm:grid-cols-[7rem_minmax(0,1fr)_10rem]">
      <span className="text-ink">{PARTY_NAME[split.party] ?? split.party}</span>
      <div aria-hidden="true" className="flex h-2.5 bg-white/[0.08]">
        <div className="bg-ink-lo" style={{ width: `${(split.yea / total) * 100}%` }} />
        <div className="bg-white/30" style={{ width: `${(split.nay / total) * 100}%` }} />
      </div>
      <span className="col-start-2 font-mono text-xs tabular-nums text-ink-lo sm:col-start-auto">
        {split.yea} yea · {split.nay} nay{split.notVoting ? ` · ${split.notVoting} NV` : ""}
      </span>
    </div>
  );
}

/** A bill's recorded votes, and for the selected one every member's
 * position, filterable to a state (a drill-down, never an address). */
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
  const [state, setState] = useState("");

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

  const states = useMemo(
    () => (detail ? [...new Set(detail.members.map((m) => m.state))].sort() : []),
    [detail]
  );
  const members = detail ? detail.members.filter((m) => !state || m.state === state) : [];

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
                    {CHAMBER_NAME[v.chamber]} {v.number}
                  </span>
                  <span className="text-ink-min">{shortDate(v.date)}</span>
                </span>
                <span className="text-[15px] leading-snug text-ink">{v.question}</span>
                <span className="col-start-2 flex flex-col items-start gap-1 pr-2 sm:col-start-auto sm:items-end">
                  <span
                    className={`font-mono text-xs uppercase tracking-[0.1em] ${TONE[resultTone(v.rejected)]}`}
                  >
                    {v.result}
                  </span>
                  <span className="font-mono text-lg tabular-nums text-ink-hi">
                    {v.yeas}–{v.nays}
                  </span>
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
                {vote.question}: {vote.result.toLowerCase()}, {vote.yeas}–{vote.nays}
              </p>
            </div>
            {states.length > 1 && (
              <label className="flex flex-col gap-1 font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                State
                <select
                  value={state}
                  onChange={(e) => setState(e.target.value)}
                  className="min-h-11 min-w-40 border border-white/20 bg-surface-base px-2 font-sans text-sm normal-case tracking-normal text-ink"
                >
                  <option value="">All states</option>
                  {states.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </label>
            )}
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
            <div className="grid gap-5 border-t border-white/[0.09] pt-4 sm:grid-cols-2 lg:grid-cols-4">
              {BUCKETS.map(({ key, label, tone }) => {
                const group = members.filter((m) => m.bucket === key);
                if (!group.length) return null;
                return (
                  <div key={key} className="flex flex-col gap-2">
                    <span className={`font-mono text-xs uppercase tracking-[0.12em] ${tone}`}>
                      {label} · {group.length}
                    </span>
                    <ul className="flex flex-col gap-1 text-sm">
                      {group.map((m) => {
                        const name = m.firstName ? `${m.firstName} ${m.lastName}` : m.lastName;
                        return (
                          <li
                            key={`${m.lastName}-${m.state}-${m.firstName}`}
                            className="flex items-baseline gap-2"
                          >
                            {m.page ? (
                              <Link href={m.page} className="text-ink hover:text-phos">
                                {name}
                              </Link>
                            ) : (
                              <span className="text-ink">{name}</span>
                            )}
                            <span
                              className={`whitespace-nowrap font-mono text-xs ${PARTY_COLORS[m.party] ?? "text-ink-lo"}`}
                            >
                              {m.party}-{m.state}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
