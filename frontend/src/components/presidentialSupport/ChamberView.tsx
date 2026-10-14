"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import SeatChart from "@/components/presidentialSupport/SeatChart";
import RateTrack from "@/components/presidentialSupport/RateTrack";
import VoteList from "@/components/presidentialSupport/VoteList";
import MethodNotes from "@/components/presidentialSupport/MethodNotes";
import { STATE_NAMES } from "@/lib/stateCodes";
import { BOXED_CONTROL } from "@/lib/controlStyles";
import { PARTY_COLORS } from "@/lib/partyStyles";
import {
  chamberSentence,
  expectationSentence,
  furthest,
  leanLabel,
  matchesMember,
  pct,
  points,
  recordSentence,
  seatLabel,
} from "@/lib/presidentialSupport";
import type { ChamberSupport, ChamberSupportMember } from "@/types/presidentialSupport";

type SortKey = "name" | "difference" | "rate";

/**
 * The browsable chamber: a sentence for the whole chamber, every member on
 * the seat-lean chart, a finder that lights a member up and reads their
 * record out, the members furthest from their seat's norm either way, and,
 * one step down, the full list, every counted vote and the method.
 */
export default function ChamberView({ view }: { view: ChamberSupport }) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("difference");

  const matches = useMemo(
    () => view.members.filter((m) => matchesMember(m, query, STATE_NAMES[m.state])),
    [view.members, query]
  );
  const highlighted = useMemo(() => new Set(matches.map((m) => m.id)), [matches]);
  const member = view.members.find((m) => m.id === selected) ?? null;
  const summary = chamberSentence(view);
  const chamberWord = view.chamber === "senate" ? "Senate" : "House";
  const sorted = useMemo(() => {
    const rows = [...view.members];
    if (sort === "name") return rows;
    const key = (m: ChamberSupportMember) => (sort === "rate" ? m.rate : m.difference) ?? -Infinity;
    return rows.sort((a, b) => key(b) - key(a));
  }, [view.members, sort]);
  const jsonHref = `/api/public/v1/presidential-support/${view.chamber}`;

  const memberButton = (m: ChamberSupportMember, detail: string) => (
    <button
      type="button"
      onClick={() => setSelected(m.id)}
      aria-pressed={selected === m.id}
      className="flex min-h-11 w-full items-baseline justify-between gap-3 border-b border-white/[0.07] py-2 text-left hover:text-phos"
    >
      <span className="font-sans text-sm text-ink">
        {m.name}{" "}
        <span className={`font-mono text-xs ${PARTY_COLORS[m.party] ?? "text-ink-lo"}`}>
          {m.party}-{seatLabel(m)}
        </span>
      </span>
      <span className="shrink-0 font-mono text-xs text-ink-lo">{detail}</span>
    </button>
  );

  return (
    <div className="space-y-8">
      <p className="max-w-3xl font-display text-lg leading-relaxed text-ink">{summary}</p>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="min-w-0">
          <SeatChart
            view={view}
            selected={selected}
            highlighted={highlighted}
            onSelect={setSelected}
            summary={summary}
          />
        </div>

        <div className="min-w-0 space-y-4">
          <div>
            <label
              htmlFor="ps-find"
              className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min"
            >
              Find a member: name, state or district (TX-7)
            </label>
            <input
              id="ps-find"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoComplete="off"
              className="mt-1 block min-h-11 w-full border border-white/15 bg-surface-base px-3 font-sans text-base text-ink-hi placeholder:text-ink-min focus:border-ink-lo focus:outline-none"
              placeholder="e.g. Ohio"
            />
            {query.trim() && (
              <div aria-live="polite" className="mt-2">
                <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                  {matches.length} match{matches.length === 1 ? "" : "es"}
                </p>
                <ul className="mt-1 max-h-64 overflow-y-auto">
                  {matches.slice(0, 12).map((m) => (
                    <li key={m.id}>{memberButton(m, pct(m.rate))}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          {member ? (
            <div className="border-t border-white/[0.09] pt-3" aria-live="polite">
              <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
                {member.party}-{seatLabel(member)} · seat {leanLabel(member.seatLean)}
              </p>
              <p className="mt-1 font-sans text-base leading-relaxed text-ink">
                {recordSentence(member.name, member)} {expectationSentence(member)}
              </p>
              <div className="mt-3">
                <RateTrack s={member} label={member.name} />
              </div>
              <Link
                href={`/politicians/${member.id}#ps-heading`}
                className="mt-3 inline-block font-mono text-xs uppercase tracking-[0.12em] text-signal-cyan hover:text-phos"
              >
                [Every vote behind this, on {member.name}&apos;s page] →
              </Link>
            </div>
          ) : (
            <p className="border-t border-white/[0.09] pt-3 font-sans text-sm text-ink-lo">
              Pick a square on the chart, or find a member, to read their record.
            </p>
          )}
        </div>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        {(["more", "less"] as const).map((side) => {
          const rows = furthest(view, side);
          return (
            <div key={side}>
              <TerminalTitlebar
                title={side === "more" ? "More often than expected" : "Less often than expected"}
              />
              <div className="border border-t-0 border-white/[0.07] px-4 pb-2">
                <h2 className="sr-only">
                  {side === "more" ? "Furthest above expected" : "Furthest below expected"}
                </h2>
                {rows.length ? (
                  <ul>
                    {rows.map((m) => (
                      <li key={m.id}>
                        {memberButton(m, `${points(m.difference)} · n ${m.votes}`)}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="py-3 font-sans text-sm text-ink-lo">
                    No member is outside the expected range this way.
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <details className="border-t border-white/[0.09] pt-3">
        <summary className="cursor-pointer font-mono text-xs uppercase tracking-[0.12em] text-ink-lo hover:text-phos">
          [All {view.members.length} members, the {view.rollCalls.length} counted votes, the method
          and the data]
        </summary>
        <div className="mt-4 space-y-8">
          <section aria-labelledby="ps-method">
            <h2
              id="ps-method"
              className="mb-2 font-mono text-xs uppercase tracking-[0.14em] text-ink-min"
            >
              Method
            </h2>
            <MethodNotes method={view.method} jsonHref={jsonHref} />
          </section>

          <section aria-labelledby="ps-all">
            <h2 id="ps-all" className="font-mono text-xs uppercase tracking-[0.14em] text-ink-min">
              Every member · {chamberWord}, {view.congress}th Congress
            </h2>
            <div role="group" aria-label="Sort members" className="mt-2 flex flex-wrap gap-2">
              {(
                [
                  ["difference", "Gap from expected"],
                  ["rate", "Rate"],
                  ["name", "Name"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  aria-pressed={sort === key}
                  onClick={() => setSort(key)}
                  className={`min-h-11 border px-3 font-mono text-xs uppercase tracking-[0.12em] ${
                    sort === key ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full border-collapse font-mono text-xs text-ink-lo">
                <thead>
                  <tr className="border-b border-white/[0.09] text-left uppercase tracking-[0.12em] text-ink-min">
                    <th className="py-2 pr-3 font-normal">Member</th>
                    <th className="py-2 pr-3 font-normal">Seat</th>
                    <th className="py-2 pr-3 text-right font-normal">n</th>
                    <th className="py-2 pr-3 text-right font-normal">Rate (95% range)</th>
                    <th className="py-2 pr-3 text-right font-normal">Expected</th>
                    <th className="py-2 text-right font-normal">Gap (range)</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((m) => (
                    <tr key={m.id} className="border-b border-white/[0.07]">
                      <td className="py-1.5 pr-3">
                        <Link
                          href={`/politicians/${m.id}`}
                          className="font-sans text-sm text-ink hover:text-phos"
                        >
                          {m.name}
                        </Link>{" "}
                        <span className={PARTY_COLORS[m.party] ?? ""}>{m.party}</span>
                      </td>
                      <td className="py-1.5 pr-3 whitespace-nowrap">
                        {seatLabel(m)} · {leanLabel(m.seatLean)}
                      </td>
                      <td className="py-1.5 pr-3 text-right">{m.votes}</td>
                      <td className="py-1.5 pr-3 text-right whitespace-nowrap">
                        {pct(m.rate)} ({pct(m.rateLow)} to {pct(m.rateHigh)})
                      </td>
                      <td className="py-1.5 pr-3 text-right">{pct(m.expected)}</td>
                      <td className="py-1.5 text-right whitespace-nowrap">
                        {points(m.difference)}
                        {m.differenceLow != null &&
                          ` (${points(m.differenceLow)} to ${points(m.differenceHigh)})`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section aria-labelledby="ps-votes">
            <h2
              id="ps-votes"
              className="mb-2 font-mono text-xs uppercase tracking-[0.14em] text-ink-min"
            >
              The {view.rollCalls.length} counted votes, newest first
            </h2>
            <VoteList votes={view.rollCalls} />
          </section>
        </div>
      </details>
    </div>
  );
}
