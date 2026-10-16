"use client";

import Link from "next/link";
import { useAsyncData } from "@/hooks/useAsyncData";
import { fetchMapSeats } from "@/lib/api";
import { redistrictingHref } from "@/lib/redistricting";
import SeatOnMap, { PollRangeSource } from "@/components/elections/SeatOnMap";

/**
 * The state at a glance, above the ballot in both layouts: each Senate
 * seat's usual vote and poll range, linking the seat map, and for a state
 * on new House lines, what changed. On a phone the new-map link used to
 * appear only after opening the House contest and picking a district, and
 * nothing from the seat map showed until a race drawer was opened.
 *
 * The seat lines are the map's own (GET /api/elections/map/seats); out of
 * the campaign the map isn't shown and neither are they. `redrawn` is the
 * ballot's own per-race redistricting record.
 */
export default function StateAtAGlance({
  state,
  stateName,
  redrawn,
}: {
  state: string;
  stateName: string;
  redrawn: boolean;
}) {
  const { data } = useAsyncData(`map-seats:${state}`, () => fetchMapSeats({ state }));
  const senate = (data?.seats ?? []).filter((s) => s.chamber === "senate");
  if (!senate.length && !redrawn) return null;
  return (
    <section
      aria-label={`${stateName} at a glance`}
      className="mb-5 border border-white/[0.14] px-3 py-2 font-mono text-xs tracking-[0.08em]"
    >
      <h2 className="mb-1 text-ink-min tracking-[0.16em]">AT A GLANCE</h2>
      <ul className="flex flex-col gap-1">
        {senate.map((s) => (
          <li key={s.id}>
            <SeatOnMap seat={s} label={s.special ? "SENATE (SPECIAL)" : "SENATE"} />
          </li>
        ))}
        {redrawn && (
          <li>
            <Link
              href={redistrictingHref(state)}
              className="inline-flex min-h-6 items-center text-signal-cyan hover:text-phos"
            >
              NEW HOUSE MAP: WHAT CHANGED →
            </Link>
          </li>
        )}
      </ul>
      {data && senate.some((s) => s.range80Label) && <PollRangeSource source={data.pollSource} />}
    </section>
  );
}
