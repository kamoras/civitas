"use client";

import type { ChamberSupport, ChamberSupportMember } from "@/types/presidentialSupport";
import { leanLabel, pct } from "@/lib/presidentialSupport";

// Seat lean shown, in PVI points either side of even: the expectation is
// flat beyond ±15 (the alignment scale saturates there), and a seat past 30
// is drawn at the edge.
const LEAN = 30;
const W = 480;
const H = 320;
// No room is left for labels: they are HTML around the drawing, so they stay
// at the page's type size however wide the chart is drawn.
const PAD = { l: 4, r: 4, t: 4, b: 4 };
const Y_TICKS = [0, 0.25, 0.5, 0.75, 1];
const X_TICKS = [-30, -15, 0, 15, 30];
const SQ = 7;

const x = (lean: number) =>
  PAD.l + ((Math.max(-LEAN, Math.min(LEAN, lean)) + LEAN) / (2 * LEAN)) * (W - PAD.l - PAD.r);
const y = (rate: number) => PAD.t + (1 - rate) * (H - PAD.t - PAD.b);

const PARTY_STROKE: Record<string, string> = {
  D: "stroke-dem-blue",
  R: "stroke-rep-red",
  I: "stroke-ind-purple",
};
const PARTY_FILL: Record<string, string> = {
  D: "fill-dem-blue",
  R: "fill-rep-red",
  I: "fill-ind-purple",
};

/**
 * Every member as a square: across, how the seat leans (Cook PVI, from
 * Democratic on the left to Republican on the right); up, the share of
 * counted votes on the president's side. Each party's line is what this
 * Congress's members of that party, in a seat of that lean, do on average.
 * A filled square is a member whose 95% range misses that line.
 *
 * The squares are not focusable (a tab stop per member would be hundreds):
 * the finder beside the chart and the full list below reach every member
 * by keyboard, and the chart's label summarises it.
 */
export default function SeatChart({
  view,
  selected,
  highlighted,
  onSelect,
  summary,
}: {
  view: ChamberSupport;
  selected: string | null;
  highlighted: Set<string>;
  onSelect: (id: string) => void;
  summary: string;
}) {
  const plotted = view.members.filter((m) => m.rate != null && m.seatLean != null);
  const sel = plotted.find((m) => m.id === selected) ?? null;
  const square = (m: ChamberSupportMember, size = SQ) => {
    const outside = m.comparedWithExpected === "more" || m.comparedWithExpected === "less";
    const dim = highlighted.size > 0 && !highlighted.has(m.id);
    return (
      <rect
        key={m.id}
        x={x(m.seatLean!) - size / 2}
        y={y(m.rate!) - size / 2}
        width={size}
        height={size}
        className={`${PARTY_STROKE[m.party] ?? "stroke-ink-lo"} ${
          outside ? (PARTY_FILL[m.party] ?? "fill-ink-lo") : "fill-surface-base"
        } cursor-pointer`}
        strokeWidth={1}
        opacity={dim ? 0.25 : 1}
        onClick={() => onSelect(m.id)}
      >
        <title>{`${m.name} (${m.party}-${m.state}), seat ${leanLabel(m.seatLean)}: ${pct(m.rate)} with the president, expected ${pct(m.expected)}`}</title>
      </rect>
    );
  };

  return (
    <figure className="m-0">
      <div className="pl-9">
        <div className="relative min-w-0">
          {/* The y labels live in a box exactly the drawing's height. */}
          <div
            className="absolute inset-y-0 -left-9 w-9 font-mono text-xs text-ink-min"
            aria-hidden="true"
          >
            {Y_TICKS.map((t) => (
              <span
                key={t}
                className="absolute right-1 -translate-y-1/2"
                style={{ top: `${(y(t) / H) * 100}%` }}
              >
                {t * 100}%
              </span>
            ))}
          </div>
          <svg
            viewBox={`0 0 ${W} ${H}`}
            role="img"
            aria-label={summary}
            className="block h-auto w-full"
            shapeRendering="crispEdges"
          >
            {Y_TICKS.map((t) => (
              <line
                key={t}
                x1={PAD.l}
                x2={W - PAD.r}
                y1={y(t)}
                y2={y(t)}
                className="stroke-white/[0.07]"
                strokeWidth={1}
              />
            ))}
            {X_TICKS.map((l) => (
              <line
                key={l}
                x1={x(l)}
                x2={x(l)}
                y1={PAD.t}
                y2={H - PAD.b}
                className="stroke-white/[0.07]"
                strokeWidth={1}
              />
            ))}
            {Object.entries(view.method.curves).map(([party, points]) => (
              <polyline
                key={party}
                points={points.map(([l, r]) => `${x(l)},${y(r)}`).join(" ")}
                fill="none"
                className={PARTY_STROKE[party] ?? "stroke-ink-lo"}
                strokeWidth={1.5}
                strokeDasharray="4 3"
                shapeRendering="geometricPrecision"
              />
            ))}
            {plotted.filter((m) => m.id !== selected).map((m) => square(m))}
            {sel && (
              <g>
                <line
                  x1={x(sel.seatLean!)}
                  x2={x(sel.seatLean!)}
                  y1={PAD.t}
                  y2={H - PAD.b}
                  className="stroke-ink-lo"
                  strokeWidth={1}
                />
                <line
                  x1={PAD.l}
                  x2={W - PAD.r}
                  y1={y(sel.rate!)}
                  y2={y(sel.rate!)}
                  className="stroke-ink-lo"
                  strokeWidth={1}
                />
                {square(sel, 10)}
              </g>
            )}
          </svg>
        </div>
        <div>
          <div className="relative h-5 font-mono text-xs text-ink-min" aria-hidden="true">
            {X_TICKS.map((l) => (
              <span
                key={l}
                className={`absolute top-1 ${l === -LEAN ? "" : l === LEAN ? "-translate-x-full" : "-translate-x-1/2"}`}
                style={{ left: `${(x(l) / W) * 100}%` }}
              >
                {leanLabel(l)}
              </span>
            ))}
          </div>
          <p className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
            Seat lean (Cook PVI) · Up: share of counted votes on the president&apos;s side
          </p>
        </div>
      </div>
      <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
        <span>
          <svg width="8" height="8" className="mr-1 inline align-middle" aria-hidden="true">
            <rect x="0.5" y="0.5" width="7" height="7" className="fill-ink-lo stroke-ink-lo" />
          </svg>
          Outside the expected range
        </span>
        <span>
          <svg width="8" height="8" className="mr-1 inline align-middle" aria-hidden="true">
            <rect
              x="0.5"
              y="0.5"
              width="7"
              height="7"
              className="fill-surface-base stroke-ink-lo"
            />
          </svg>
          Within it
        </span>
        <span>Dashed: expected, by party</span>
        <span className="text-dem-blue">D</span>
        <span className="text-rep-red">R</span>
      </figcaption>
    </figure>
  );
}
