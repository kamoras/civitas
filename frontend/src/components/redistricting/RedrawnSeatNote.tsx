"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { RedistrictedSeat } from "@/types/redistricting";
import { fetchRedistrictedSeat } from "@/lib/api";
import { oldDistrictSentence, redistrictingHref } from "@/lib/redistricting";

/**
 * "This district changed": on the profile of a House member whose seat's
 * state votes on new lines, where the residents of the district the member
 * was elected in are now. Self-contained (it fetches its own figures and
 * renders nothing otherwise) so the profile page carries one line for it.
 *
 * Shown only while the members elected on the old lines sit
 * (fromCongressSitting): once the new Congress convenes, its members hold
 * the new districts and the note would describe someone else's seat.
 * Subjectless wording: the district is the subject, never the member.
 */
export default function RedrawnSeatNote({ state, district }: { state: string; district: number }) {
  const [data, setData] = useState<RedistrictedSeat | null>(null);
  useEffect(() => {
    let live = true;
    fetchRedistrictedSeat(state, district)
      .then((d) => live && setData(d))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [state, district]);

  const seat = data?.seat;
  if (!seat || !data?.fromCongressSitting) return null;
  return (
    <p className="mt-4 border-l border-signal-cyan/60 pl-3 font-sans text-[13px] leading-relaxed text-ink-lo">
      <span className="font-mono text-[11px] text-signal-cyan">
        District redrawn for {data.cycle}
      </span>{" "}
      {oldDistrictSentence(seat)}{" "}
      <Link
        href={redistrictingHref(state, `old-${district}`)}
        className="font-mono text-xs text-signal-cyan underline decoration-signal-cyan/40 underline-offset-4 hover:text-phos"
      >
        [ What changed → ]
      </Link>
    </p>
  );
}
