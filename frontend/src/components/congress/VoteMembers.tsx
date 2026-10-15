"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { PartySplit, VoteMember } from "@/types/congress";
import { PARTY_COLORS } from "@/lib/partyStyles";
import { houseSeatLabel } from "@/lib/elections";

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

/** One party's split on a roll call, as the chamber recorded it. The bar is
 * display scaling of those counts, not a new figure. */
export function PartyBar({ split }: { split: PartySplit }) {
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

/** Every member's position on one roll call, grouped as the chamber
 * recorded them, each name linked to the member's page where the site has
 * one. Filterable to a state: a drill-down over the list, never an address. */
export default function VoteMembers({ members }: { members: VoteMember[] }) {
  const [state, setState] = useState("");
  const states = useMemo(() => [...new Set(members.map((m) => m.state))].sort(), [members]);
  // One state's House members in district order, so a reader who knows
  // their district finds their member without reading every name.
  const shown = members
    .filter((m) => !state || m.state === state)
    .sort((a, b) => (state ? (a.district ?? 999) - (b.district ?? 999) : 0));

  return (
    <div className="flex flex-col gap-4">
      {states.length > 1 && (
        <label className="flex w-fit flex-col gap-1 font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
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
      <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {BUCKETS.map(({ key, label, tone }) => {
          const group = shown.filter((m) => m.bucket === key);
          if (!group.length) return null;
          return (
            <div key={key} className="flex flex-col gap-2">
              <span className={`font-mono text-xs uppercase tracking-[0.12em] ${tone}`}>
                {label} · {group.length}
              </span>
              <ul className="flex flex-col gap-1 text-sm">
                {group.map((m) => {
                  const name =
                    m.name ?? (m.firstName ? `${m.firstName} ${m.lastName}` : m.lastName);
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
                        {m.party}-
                        {m.district != null ? houseSeatLabel(m.state, m.district) : m.state}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </div>
    </div>
  );
}
