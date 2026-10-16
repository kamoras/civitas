"use client";

import Link from "next/link";
import { useId, useMemo, useState } from "react";
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
  const candidates = Object.entries(split.candidates ?? {}).sort((a, b) => b[1] - a[1]);
  // A Speaker election (votes name candidates) or a quorum call (members
  // answer present) has no yea/nay split to draw.
  if (candidates.length || (!split.yea && !split.nay && split.present)) {
    const parts = [
      ...candidates.map(([name, n]) => `${n} ${name}`),
      ...(split.present ? [`${split.present} present`] : []),
      ...(split.notVoting ? [`${split.notVoting} NV`] : []),
    ];
    return (
      <div className="grid grid-cols-[7rem_minmax(0,1fr)] items-baseline gap-x-3 text-sm">
        <span className="text-ink">{PARTY_NAME[split.party] ?? split.party}</span>
        <span className="font-mono text-xs tabular-nums text-ink-lo">{parts.join(" · ")}</span>
      </div>
    );
  }
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
 * one. Filterable to a state: a drill-down over the list, never an address.
 *
 * `collapsible` (a bill page, where the roll call is one section of many):
 * the names wait behind a state pick or "Show all", with each group's count
 * still shown. S. 2403's page listed all 433 House members inline, 16
 * screens and 519 tab stops before its history; the roll call's own page
 * keeps the full list. */
export default function VoteMembers({
  members,
  collapsible = false,
}: {
  members: VoteMember[];
  collapsible?: boolean;
}) {
  const [state, setState] = useState("");
  const [showAll, setShowAll] = useState(!collapsible);
  const listId = useId();
  const named = showAll || state !== "";
  const states = useMemo(() => [...new Set(members.map((m) => m.state))].sort(), [members]);
  // One state's House members in district order, so a reader who knows
  // their district finds their member without reading every name.
  const shown = members
    .filter((m) => !state || m.state === state)
    .sort((a, b) => (state ? (a.district ?? 999) - (b.district ?? 999) : 0));
  // An election of the Speaker: one group per candidate, most votes first,
  // ahead of present and not voting.
  const groups = useMemo(() => {
    const votes = new Map<string, number>();
    for (const m of members)
      if (m.candidate) votes.set(m.candidate, (votes.get(m.candidate) ?? 0) + 1);
    const candidates = [...votes.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([name]) => ({
        key: `candidate:${name}`,
        label: name,
        tone: "text-ink",
        has: (m: VoteMember) => m.candidate === name,
      }));
    return [
      ...candidates,
      ...BUCKETS.map((b) => ({
        ...b,
        key: b.key as string,
        has: (m: VoteMember) => m.bucket === b.key,
      })),
    ];
  }, [members]);

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
      {collapsible && !state && (
        <button
          type="button"
          aria-expanded={showAll}
          aria-controls={listId}
          onClick={() => setShowAll((v) => !v)}
          className="min-h-11 w-fit font-mono text-xs uppercase tracking-[0.12em] text-ink-lo underline decoration-white/30 underline-offset-4 hover:text-phos"
        >
          {showAll ? "Hide the names" : `Show all ${members.length} members' votes`}
        </button>
      )}
      <div id={listId} className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {groups.map(({ key, label, tone, has }) => {
          const group = shown.filter(has);
          if (!group.length) return null;
          return (
            <div key={key} className="flex flex-col gap-2">
              <span className={`font-mono text-xs uppercase tracking-[0.12em] ${tone}`}>
                {label} · {group.length}
              </span>
              {named && (
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
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
