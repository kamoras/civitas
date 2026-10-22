"use client";

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import ChartReadout, { type ChartMark } from "./ChartReadout";
import { overlay } from "@/lib/chartColors";
import type { StripPoint } from "./Strip";

/** One person on a whole-number measure: a strip point placed by its
 *  value, not by an x the caller worked out. */
export type CountPoint = Omit<StripPoint, "x" | "y"> & { value: number };

/** How a highlighted person's dot is drawn: the page's own member ringed,
 *  or each of compare's pair. */
export type CountMarked = Record<string, { fill: string; ring: string }>;

/** Columns past this, a phone's column is narrower than a dot: such a row
 *  keeps the strip. */
const MAX_COLUMNS = 50;

/**
 * Whether a measure is drawn as a dot histogram: its unit (the API's) is a
 * count and every figure on it is a whole number. Anything else, or a
 * response without `unit` (a backend older than the page), keeps the strip.
 */
export function wholeCounts(unit: string | undefined, values: number[]): boolean {
  if (unit !== "count" || values.length === 0 || !values.every(Number.isInteger)) return false;
  return Math.max(...values) - Math.min(0, ...values) + 1 <= MAX_COLUMNS;
}

/** The tallest the stacks may grow, in px; a taller column is capped and
 *  says how many more it holds. About a third of a phone's screen. */
const MAX_PLOT_PX = 200;
/** Dot diameters tried, largest first: the largest at which every column
 *  fits under MAX_PLOT_PX wins. 4px is the smallest still read as a dot. */
const DOT_SIZES = [10, 9, 8, 7, 6, 5, 4];
/** Space between dots, px. */
const GAP = 2;
/** Axis steps tried, smallest first, until labels stop crowding. */
const STEPS = [1, 2, 5, 10, 20, 25, 50, 100];
/** A label's font size and width per character, in rem (13px, a digit's
 *  advance in Fira Sans with tabular figures), for spacing labels. */
const LABEL_REM = 0.8125;
const CHAR_EM = 0.6;

const labelWidth = (text: string, rem: number) => (text.length * CHAR_EM + 0.6) * LABEL_REM * rem;

export interface HistDot {
  p: CountPoint;
  /** Centre, in px from the chart's left and top. */
  cx: number;
  cy: number;
}

export interface HistLayout {
  lo: number;
  hi: number;
  colW: number;
  d: number;
  /** Rows of overflow labels above the stacks, and their height (1rem). */
  labelRows: number;
  rowH: number;
  /** The whole chart's height: label rows, stacks and the baseline. */
  height: number;
  baseline: number;
  dots: HistDot[];
  /** A capped column's count of people not drawn, at its label's centre. */
  overflow: { value: number; n: number; cx: number; cy: number }[];
  /** Every person, in the order the arrow keys walk: by value, and within
   *  a column from the bottom up (the hidden ones last, at their label). */
  order: HistDot[];
  /** Axis labels: the whole numbers printed under the columns, each
   *  centred on its column but kept inside the chart. */
  ticks: { value: number; cx: number }[];
  x: (v: number) => number;
}

/**
 * Where every dot goes. One column per whole number from 0 (or the lowest
 * figure) to the highest; each person a dot stacked upward in their
 * column, the highlighted first so they sit at the foot of it and are
 * never capped away, the rest in the API's order. Pure layout: no figure
 * is computed, only where the API's figures are drawn.
 */
export function histLayout(
  points: CountPoint[],
  width: number,
  rem: number,
  first: string[] = []
): HistLayout {
  const lo = Math.min(0, ...points.map((p) => p.value));
  const hi = Math.max(lo, ...points.map((p) => p.value));
  const cols = hi - lo + 1;
  const colW = width / cols;
  const x = (v: number) => (v - lo + 0.5) * colW;

  const byCol = new Map<number, CountPoint[]>();
  for (const p of points) byCol.set(p.value, [...(byCol.get(p.value) ?? []), p]);
  for (const [v, ps] of byCol) {
    const lead = ps.filter((p) => first.includes(p.id));
    byCol.set(v, [...lead, ...ps.filter((p) => !first.includes(p.id))]);
  }
  const tallest = Math.max(1, ...[...byCol.values()].map((ps) => ps.length));

  // One file per column, the largest dot that fits; else several abreast,
  // leaving a dot's width between columns so they stay apart; else the
  // smallest dot several abreast, capped.
  const sizes = DOT_SIZES.filter((s) => s + GAP <= colW);
  if (!sizes.length) sizes.push(Math.max(2, Math.floor(colW) - GAP));
  const fit = (s: number, abreast: boolean) => {
    const across = abreast ? Math.max(1, Math.floor((colW - s - GAP) / (s + GAP))) : 1;
    return { d: s, across, rows: Math.ceil(tallest / across) };
  };
  const tries = [...sizes.map((s) => fit(s, false)), ...sizes.map((s) => fit(s, true))];
  const pick =
    tries.find((f) => f.rows * (f.d + GAP) <= MAX_PLOT_PX) ?? fit(sizes[sizes.length - 1], true);
  const { d, across } = pick;
  const pitch = d + GAP;
  const rows = Math.min(pick.rows, Math.floor(MAX_PLOT_PX / pitch));
  const capacity = rows * across;

  // Overflow labels, left to right, each on the first row it fits in.
  const rowH = rem;
  const rowEnds: number[] = [];
  const pending: { value: number; n: number; cx: number; row: number }[] = [];
  for (const [v, ps] of [...byCol].sort((a, b) => a[0] - b[0])) {
    if (ps.length <= capacity) continue;
    const n = ps.length - capacity;
    const w = labelWidth(`+${n}`, rem);
    const cx = Math.max(w / 2, Math.min(width - w / 2, x(v)));
    let row = rowEnds.findIndex((end) => end + 2 <= cx - w / 2);
    if (row < 0) row = rowEnds.length;
    rowEnds[row] = cx + w / 2;
    pending.push({ value: v, n, cx, row });
  }
  const top = rowEnds.length * rowH;
  const used = Math.min(rows, pick.rows);
  const baseline = top + used * pitch + 1;

  const dots: HistDot[] = [];
  const order: HistDot[] = [];
  const overflow = pending.map((o) => ({ ...o, cy: o.row * rowH + rowH / 2 }));
  for (const [v, ps] of [...byCol].sort((a, b) => a[0] - b[0])) {
    const shown = ps.slice(0, capacity);
    const wide = Math.min(across, shown.length);
    shown.forEach((p, i) => {
      const k = i % wide;
      const r = Math.floor(i / wide);
      const dot = {
        p,
        cx: x(v) + (k - (wide - 1) / 2) * pitch,
        cy: baseline - 1 - GAP / 2 - (r + 0.5) * pitch,
      };
      dots.push(dot);
      order.push(dot);
    });
    const label = overflow.find((o) => o.value === v);
    for (const p of ps.slice(capacity)) order.push({ p, cx: label!.cx, cy: label!.cy });
  }

  const digits = Math.max(String(lo).length, String(hi).length);
  const step = STEPS.find((s) => s * colW >= labelWidth("0".repeat(digits), rem) + 4) ?? 100;
  const ticks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    const w = labelWidth(String(v), rem);
    ticks.push({ value: v, cx: Math.max(w / 2, Math.min(width - w / 2, x(v))) });
  }

  return {
    lo,
    hi,
    colW,
    d,
    labelRows: rowEnds.length,
    rowH,
    height: baseline + 1,
    baseline,
    dots,
    overflow,
    order,
    ticks,
    x,
  };
}

/** A label slid along so it never runs off either end of the chart. */
const slid = (cx: number, width: number): React.CSSProperties => {
  const share = Math.max(0, Math.min(100, (cx / width) * 100));
  return { left: `${share}%`, transform: `translateX(-${share}%)` };
};

/** Before the chart is measured (the server's render): a phone's width. */
const DEFAULT_WIDTH = 320;

/**
 * A whole-number measure drawn as a stacked dot histogram: one column per
 * whole number, each person a dot stacked upward in their column (ties
 * side by side instead of piled on one point), the highlighted ringed, a
 * dashed line at the median or expected figure with its label, and the
 * whole numbers under it, thinned so they never crowd. Each dot reads out
 * through the shared ChartReadout: picked by the nearest dot, the arrow
 * keys walking every person in value order, up each column then on to the
 * next. The drawing is hidden from assistive technology: the caller keeps
 * the table that is its text form.
 */
export default function CountHistogram({
  label,
  points,
  marked = {},
  startId,
  tick,
  dot,
}: {
  label: string;
  points: CountPoint[];
  marked?: CountMarked;
  startId?: string;
  /** The dashed line: its value and its label as the API prints it. */
  tick?: { value: number; text: string } | null;
  /** Draws an unhighlighted dot (By the numbers draws party shapes). */
  dot?: (p: CountPoint, cx: number, cy: number, d: number) => React.ReactNode;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const remBox = useRef<HTMLSpanElement>(null);
  const [size, setSize] = useState({ width: 0, rem: 16 });
  useLayoutEffect(() => {
    const read = () =>
      setSize({
        width: wrap.current?.clientWidth || 0,
        rem: remBox.current?.getBoundingClientRect().width || 16,
      });
    read();
    if (typeof ResizeObserver === "undefined") return;
    // A 1rem-wide box resizes when the text size does (200% text).
    const ro = new ResizeObserver(read);
    if (wrap.current) ro.observe(wrap.current);
    if (remBox.current) ro.observe(remBox.current);
    return () => ro.disconnect();
  }, []);
  const width = size.width || DEFAULT_WIDTH;
  const ids = Object.keys(marked).join(" ");
  const L = useMemo(
    () => histLayout(points, width, size.rem, ids ? ids.split(" ") : []),
    [points, width, size.rem, ids]
  );
  const marks: ChartMark[] = L.order.map(({ p, cx, cy }) => ({
    id: p.id,
    label: p.name,
    detail: p.detail,
    shown: p.shown,
    context: p.rank,
    href: p.href,
    x: (cx / width) * 100,
    y: cy,
  }));
  const r = L.d / 2;
  const tickX = tick ? L.x(tick.value) : 0;
  return (
    <div ref={wrap} className="relative min-w-0">
      <span
        ref={remBox}
        aria-hidden="true"
        className="pointer-events-none invisible absolute left-0 top-0 h-0 w-[1rem]"
      />
      {tick && (
        <div aria-hidden="true" className="relative h-5 text-small">
          <span
            className="absolute block w-max font-semibold text-ink-lo"
            style={slid(tickX, width)}
          >
            {tick.text}
          </span>
        </div>
      )}
      <ChartReadout label={label} marks={marks} startId={startId}>
        <div className="relative" style={{ height: L.height }}>
          <svg width="100%" height={L.height} className="absolute inset-0 block overflow-visible">
            <line
              x1={0}
              x2={width}
              y1={L.baseline}
              y2={L.baseline}
              stroke={overlay(0.4)}
              strokeWidth={1}
            />
            {tick && (
              <line
                x1={tickX}
                x2={tickX}
                y1={0}
                y2={L.baseline}
                stroke="var(--ink-hi)"
                strokeWidth={1.5}
                strokeDasharray="4 3"
              />
            )}
            {L.dots
              .filter(({ p }) => !marked[p.id])
              .map(({ p, cx, cy }) =>
                dot ? (
                  <g key={p.id}>{dot(p, cx, cy, L.d)}</g>
                ) : (
                  <circle key={p.id} cx={cx} cy={cy} r={r} fill="var(--chart-muted)" />
                )
              )}
            {L.dots
              .filter(({ p }) => marked[p.id])
              .map(({ p, cx, cy }) => (
                <circle
                  key={p.id}
                  cx={cx}
                  cy={cy}
                  r={r + 1.5}
                  fill={marked[p.id].fill}
                  stroke={marked[p.id].ring}
                  strokeWidth={2}
                />
              ))}
          </svg>
          {L.overflow.map((o) => (
            <span
              key={o.value}
              className="absolute block w-max -translate-x-1/2 -translate-y-1/2 bg-surface px-0.5 font-semibold leading-none text-ink-lo"
              style={{ left: o.cx, top: o.cy, fontSize: `${LABEL_REM}rem` }}
            >
              +{o.n}
            </span>
          ))}
        </div>
      </ChartReadout>
      <div aria-hidden="true" className="relative mt-0.5 h-5 text-small text-ink-min">
        {L.ticks.map((t) => (
          <span
            key={t.value}
            className="absolute block w-max -translate-x-1/2"
            style={{ left: t.cx, fontSize: `${LABEL_REM}rem` }}
          >
            {t.value}
          </span>
        ))}
      </div>
    </div>
  );
}
