"use client";

import { useAsyncData } from "@/hooks/useAsyncData";
import { fetchChamberMeasures } from "@/lib/api";
import { axisMax, Dot, fmt } from "@/components/scorecard/WhereTheyStand";
import type { ChamberMeasure } from "@/types/peers";
import StripPoints, { partySeat, StripAxis, type StripPoint } from "@/components/shared/Strip";

type Side = { id: string; name: string };

const JITTER = [10, 4, 16, 7, 13, 1, 19, 10];

/** One measure, the whole chamber as faint dots and the two members as a
 *  filled and an outlined marker, each named in the caption. */
function PairStrip({
  m,
  chamber,
  left,
  right,
}: {
  m: ChamberMeasure;
  chamber: "senate" | "house";
  left: Side;
  right: Side;
}) {
  const max = axisMax({
    unit: m.unit,
    distribution: m.points.map((p) => p.value),
    value: null,
    expected: m.median,
  });
  const at = (v: number) => Math.min(100, (v / max) * 100);
  const find = (id: string) => m.points.find((p) => p.id === id);
  const l = find(left.id);
  const r = find(right.id);
  const points: StripPoint[] = m.points.map((p, i) => ({
    id: p.id,
    name: p.name,
    detail: partySeat(p, chamber === "house"),
    shown: p.shown,
    rank: p.rank,
    href: `/politicians/${p.id}`,
    x: at(p.value),
    y: p === l || p === r ? 22 : 12 + JITTER[i % JITTER.length] / 2 + 2.5,
  }));
  return (
    <figure className="border-t border-white/25 px-4 py-4 first:border-t-0">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-[13px] font-semibold text-ink-lo">{m.label}</span>
        <span className="text-sm text-ink">
          {left.name}: <strong className="font-semibold">{l?.shown ?? "no record"}</strong> ·{" "}
          {right.name}: <strong className="font-semibold">{r?.shown ?? "no record"}</strong>
          {m.median != null && (
            <span className="text-ink-lo"> · chamber median {fmt(m, m.median)}</span>
          )}
        </span>
      </figcaption>
      <StripPoints
        label={`${m.label}, every ${chamber === "house" ? "representative" : "senator"}`}
        points={points}
        startId={left.id}
        className="mt-2 h-10"
      >
        <div className="absolute inset-x-0 top-[22px] h-px bg-white/40" />
        {m.points.map((p, i) => (
          <Dot
            key={p.id}
            size={5}
            fill="var(--chart-muted)"
            className="absolute -translate-x-1/2"
            style={{ left: `${at(p.value)}%`, top: `${12 + JITTER[i % JITTER.length] / 2}px` }}
          />
        ))}
        {m.median != null && (
          <span
            className="absolute top-1 h-8 -translate-x-1/2 border-l-2 border-ink-hi"
            style={{ left: `${at(m.median)}%` }}
          />
        )}
        {l && (
          <Dot
            size={14}
            fill="var(--ink-hi)"
            ring="var(--phos)"
            className="absolute top-[15px] -translate-x-1/2"
            style={{ left: `${at(l.value)}%` }}
          />
        )}
        {r && (
          <Dot
            size={14}
            fill="var(--surface)"
            ring="var(--ink-hi)"
            className="absolute top-[15px] -translate-x-1/2"
            style={{ left: `${at(r.value)}%` }}
          />
        )}
      </StripPoints>
      <StripAxis start={fmt(m, 0)} end={fmt(m, max)} />
    </figure>
  );
}

/**
 * Two members of one chamber on the four measures the member pages use,
 * among every colleague (backend app/peer_comparison.chamber_measures).
 * No winner marks: the strips show where each sits, not who is better.
 */
export default function PairStrips({
  chamber,
  left,
  right,
}: {
  chamber: "senate" | "house";
  left: Side;
  right: Side;
}) {
  const { data, error } = useAsyncData(chamber, () => fetchChamberMeasures(chamber));
  if (error && !data)
    return <p className="px-4 py-3 text-sm text-ink-lo">The measures could not be loaded.</p>;
  if (!data) return <p className="px-4 py-3 text-sm text-ink-lo">Loading the measures…</p>;
  return (
    <div>
      <p
        className="flex flex-wrap gap-x-4 gap-y-1 px-4 pt-3 text-xs text-ink-lo"
        aria-hidden="true"
      >
        <span>
          <Dot
            size={12}
            fill="var(--ink-hi)"
            ring="var(--phos)"
            className="mr-1 inline-block align-middle"
          />
          {left.name}
        </span>
        <span>
          <Dot
            size={12}
            fill="var(--surface)"
            ring="var(--ink-hi)"
            className="mr-1 inline-block align-middle"
          />
          {right.name}
        </span>
        <span>│ chamber median</span>
      </p>
      {data.measures.map((m) => (
        <PairStrip key={m.key} m={m} chamber={chamber} left={left} right={right} />
      ))}
    </div>
  );
}
