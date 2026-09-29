"use client";

import { useAsyncData } from "@/hooks/useAsyncData";
import { fetchSignalOverlap } from "@/lib/api";
import type { SignalOverlapPairKey } from "@/types/scoreBreakdown";

const CHAMBERS = [
  ["senate", "Senate"],
  ["house", "House"],
] as const;

const BAND_TEXT = {
  ok: "distinct",
  watch: "worth watching",
  action: "overlapping — flagged for a fix",
  none: "not measurable",
} as const;

/** The last pipeline run's correlation between two related score
 * components, per chamber (GET /api/signal-overlap). The only live element
 * in the About chapters (/about/scores): the rest is prerendered. */
export default function SignalOverlapReading({ pair }: { pair: SignalOverlapPairKey }) {
  const { data, error } = useAsyncData("signal-overlap", fetchSignalOverlap);

  if (error) return <>The latest reading could not be loaded.</>;
  if (!data) return <>Loading the latest reading…</>;

  const readings = CHAMBERS.flatMap(([key, name]) => {
    const chamber = data.chambers[key];
    const p = chamber?.pairs[pair];
    if (!p) return [];
    const r =
      p.r === null ? "no reading" : `r = ${p.r >= 0 ? "+" : "−"}${Math.abs(p.r).toFixed(3)}`;
    const when = chamber?.computedAt ? `, run of ${chamber.computedAt.slice(0, 10)}` : "";
    return [`${name} ${r} across ${p.n} members (${BAND_TEXT[p.band]}${when})`];
  });
  if (readings.length === 0) return <>Not measured yet: the first pipeline run records it.</>;

  return (
    <>
      Latest: {readings.join("; ")}. An operator alert fires at |r| ≥ {data.actionR.toFixed(2)}.
    </>
  );
}
