"use client";

import { useMemo } from "react";
import ChartReadout from "./ChartReadout";

/** One person's mark on a distribution strip, with what its label prints.
 *  Every word and figure is the API's; the strip only places them. */
export interface StripPoint {
  id: string;
  name: string;
  /** Party and seat ("D-CA-5") or a presidency's years; none for a justice. */
  detail?: string | null;
  shown: string;
  /** The place on the row, from the API ("3rd highest of 15"). */
  rank: string;
  href: string;
  /** Across the strip, 0-100. */
  x: number;
  /** The mark's centre, in px from the strip's top. */
  y: number;
}

/** A member's party and seat as the site prints them: "D-CA", "R-TX-5", "R-WY-AL". */
export function partySeat(
  p: { party: string; state: string; district: number | null },
  house: boolean
): string {
  return `${p.party}-${p.state}${house ? `-${p.district || "AL"}` : ""}`;
}

/**
 * The marks of a distribution strip, made readable one by one: the shared
 * chart readout (ChartReadout), picked along the axis, the arrow keys
 * moving by value. The drawing (`children`) stays hidden from assistive
 * technology: each strip's table is its full text form.
 */
export default function StripPoints({
  label,
  points,
  startId,
  className = "",
  children,
}: {
  /** The strip's accessible name: the measure and who is on it. */
  label: string;
  points: StripPoint[];
  /** Where the arrow keys start: the page's own person. */
  startId?: string;
  /** The strip's height (h-10, h-14). */
  className?: string;
  children: React.ReactNode;
}) {
  // By position, ties in the API's (value) order.
  const marks = useMemo(
    () =>
      [...points].sort((a, b) => a.x - b.x).map((p) => ({ ...p, label: p.name, context: p.rank })),
    [points]
  );
  return (
    <ChartReadout label={label} marks={marks} startId={startId} className={className}>
      <div className="absolute inset-0">{children}</div>
    </ChartReadout>
  );
}

/**
 * Under a strip: the tick's label on its own line, at the tick (slid along
 * so it never runs off either end), then the axis's two ends. On separate
 * lines, the tick's label never sits over an end label, and never reads as
 * centred under the strip when the tick isn't.
 */
export function StripAxis({
  start,
  end,
  tick,
}: {
  start: string;
  end: string;
  tick?: { at: number; text: string } | null;
}) {
  return (
    <div aria-hidden="true" className="text-xs text-ink-min">
      {tick && (
        <span
          className="relative block w-max max-w-full font-semibold text-ink-lo"
          style={{ left: `${tick.at}%`, transform: `translateX(-${tick.at}%)` }}
        >
          {tick.text}
        </span>
      )}
      <div className="flex justify-between gap-x-3">
        <span className="min-w-0">{start}</span>
        <span className="min-w-0 text-right">{end}</span>
      </div>
    </div>
  );
}

/** A row's big figure: the number large, and any words after it ("million a
 *  year", "of 493") on a smaller line under it, so a long unit never wraps
 *  the number mid-figure. */
export function BigFigure({ shown, className }: { shown: string | null; className: string }) {
  const parts = shown == null ? null : /^([+\-−]?[\d.,]+%?)\s+(.+)$/.exec(shown);
  return (
    <p className={className}>
      {parts ? (
        <>
          {parts[1]}{" "}
          <span className="block text-sm font-semibold leading-snug text-ink-lo">{parts[2]}</span>
        </>
      ) : (
        (shown ?? "No record")
      )}
    </p>
  );
}
