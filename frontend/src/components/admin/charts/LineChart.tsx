"use client";

import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
} from "react";
import { GRID, SURFACE } from "./palette";
import { niceTicks, xLabelIndices } from "./scale";

export interface LineSeries {
  key: string;
  label: string;
  /** A mark colour from ./palette — never a text colour. */
  color: string;
  /** One value per x position; null is a gap ("nothing measured"), not zero. */
  values: (number | null)[];
  /**
   * Draw dashed. The secondary encoding for a sixth-or-later series, which
   * has to reuse one of the five validated hues.
   */
  dashed?: boolean;
}

/** The short stroke that keys a series in the legend and tooltip. */
function LineKey({ color, dashed }: { color: string; dashed?: boolean }) {
  return (
    <span
      aria-hidden="true"
      className="inline-block w-3.5 shrink-0"
      style={{ borderTop: `2px ${dashed ? "dashed" : "solid"} ${color}` }}
    />
  );
}

interface LineChartProps {
  title: string;
  subtitle?: ReactNode;
  /** Full label for each x position — shown in the tooltip and table. */
  xLabels: string[];
  /** Shorter label for the axis ticks; defaults to the full label. */
  xTickLabel?: (index: number) => string;
  series: LineSeries[];
  /** Value format for the tooltip and table. */
  formatValue: (n: number) => string;
  /** Value format for the y-axis ticks; defaults to formatValue. */
  formatTick?: (n: number) => string;
  /** Y-axis tick generator; niceTicks by default, niceDurationTicks for seconds. */
  yTicks?: (max: number) => number[];
  /** Plot height in px, excluding the x-axis band. */
  height?: number;
  emptyMessage?: string;
  /** Hold the previous render, dimmed, while a refetch is in flight. */
  stale?: boolean;
  /** Right-hand side of the header — typically the latest value. */
  headline?: ReactNode;
}

const PAD_TOP = 8;
const PAD_RIGHT = 10; // room for the end marker
const X_AXIS_BAND = 22;
const TICK_CHAR_PX = 6.6; // Share Tech Mono at 11px
const DEFAULT_WIDTH = 640;

/**
 * A single-axis line chart drawn as inline SVG.
 *
 * No charting library: the admin page is the only consumer, the site ships
 * none, and everything needed here is ~one scale and a path. It follows the
 * dataviz mark spec: 2px lines, a hairline solid grid, a zero baseline, an
 * end marker ringed in the surface colour, a legend whenever there are two or
 * more series (a single series is named by the title), and a crosshair that
 * snaps to the nearest x and lists every series there. The tooltip never
 * gates a value — the same numbers are in the table view, and the arrow keys
 * read them out through a live region.
 */
export default function LineChart({
  title,
  subtitle,
  xLabels,
  xTickLabel,
  series,
  formatValue,
  formatTick = formatValue,
  yTicks = niceTicks,
  height = 160,
  emptyMessage = "No data in this range yet.",
  stale = false,
  headline,
}: LineChartProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [active, setActive] = useState<number | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w && w > 0) setWidth(Math.round(w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const n = xLabels.length;
  const allValues = series.flatMap((s) => s.values).filter((v): v is number => v != null);
  const hasData = n > 0 && allValues.length > 0;

  const ticks = yTicks(Math.max(0, ...allValues));
  const yMax = ticks[ticks.length - 1];
  const tickLabels = ticks.map(formatTick);
  const padLeft = Math.ceil(Math.max(...tickLabels.map((t) => t.length)) * TICK_CHAR_PX) + 10;
  const plotW = Math.max(40, width - padLeft - PAD_RIGHT);
  const svgH = PAD_TOP + height + X_AXIS_BAND;

  const x = (i: number) => padLeft + (n <= 1 ? plotW / 2 : (i * plotW) / (n - 1));
  const y = (v: number) => PAD_TOP + height - (v / yMax) * height;

  const paths = series.map((s) => {
    let d = "";
    let penDown = false;
    const singletons: number[] = [];
    s.values.forEach((v, i) => {
      if (v == null) {
        penDown = false;
        return;
      }
      d += `${penDown ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      penDown = true;
      // An isolated point between gaps has no segment to draw — give it a
      // dot, or a day with data between two empty days would be invisible.
      if (s.values[i - 1] == null && s.values[i + 1] == null) singletons.push(i);
    });
    let last = -1;
    for (let i = s.values.length - 1; i >= 0; i--) {
      if (s.values[i] != null) {
        last = i;
        break;
      }
    }
    return { s, d, singletons, last };
  });

  // As many x labels as fit at their own length plus a gap, so hourly
  // "Sep 24, 13:00" ticks thin out further than daily "Sep 24" ones.
  const tickText = (i: number) => (xTickLabel ? xTickLabel(i) : xLabels[i]);
  const longestTick = Math.max(1, ...Array.from({ length: n }, (_, i) => tickText(i).length));
  const maxXLabels = Math.max(2, Math.floor(plotW / (longestTick * TICK_CHAR_PX + 24)));
  const xTicks = xLabelIndices(n, maxXLabels);

  const indexFromPointer = (e: PointerEvent<SVGRectElement>) => {
    const rect = e.currentTarget.ownerSVGElement?.getBoundingClientRect();
    if (!rect || n === 0) return null;
    const px = ((e.clientX - rect.left) / rect.width) * width;
    if (n === 1) return 0;
    return Math.min(n - 1, Math.max(0, Math.round(((px - padLeft) / plotW) * (n - 1))));
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (n === 0) return;
    const cur = active ?? n - 1;
    const next =
      e.key === "ArrowRight"
        ? Math.min(n - 1, cur + 1)
        : e.key === "ArrowLeft"
          ? Math.max(0, cur - 1)
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? n - 1
              : null;
    if (e.key === "Escape") {
      setActive(null);
      return;
    }
    if (next == null) return;
    e.preventDefault();
    setActive(next);
  };

  const readout = (i: number) =>
    `${xLabels[i]}: ${series
      .map(
        (s) => `${s.label} ${s.values[i] == null ? "no data" : formatValue(s.values[i] as number)}`
      )
      .join(", ")}`;

  const tooltipLeft = active == null ? 0 : (x(active) / width) * 100;
  const tooltipFlip = tooltipLeft > 60;

  return (
    <figure className="min-w-0">
      <figcaption className="mb-2 flex items-baseline justify-between gap-3">
        <span className="min-w-0">
          <span className="block text-xs font-mono tracking-wider text-ink-lo">{title}</span>
          {subtitle && <span className="block text-xs font-mono text-ink-min">{subtitle}</span>}
        </span>
        {headline && <span className="shrink-0 text-right font-mono text-ink-hi">{headline}</span>}
      </figcaption>

      {series.length >= 2 && (
        <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1" aria-label={`${title} legend`}>
          {series.map((s) => (
            <li key={s.key} className="flex items-center gap-1.5 text-xs font-mono text-ink-lo">
              <LineKey color={s.color} dashed={s.dashed} />
              {s.label}
            </li>
          ))}
        </ul>
      )}

      <div
        ref={wrapRef}
        className={`relative transition-opacity duration-300 ${stale ? "opacity-50" : ""}`}
      >
        {!hasData ? (
          <div
            className="flex items-center justify-center border border-white/[0.07] text-xs font-mono text-ink-min"
            style={{ height: svgH }}
          >
            {emptyMessage}
          </div>
        ) : (
          // A slider over the x positions: Arrow/Home/End move a cursor
          // point by point, and assistive tech announces aria-valuetext —
          // the same readout the tooltip shows — natively.
          <div
            tabIndex={0}
            role="slider"
            aria-label={`${title} — line chart; arrow keys step through the points`}
            aria-valuemin={0}
            aria-valuemax={Math.max(0, n - 1)}
            aria-valuenow={active ?? n - 1}
            aria-valuetext={readout(active ?? n - 1)}
            onKeyDown={onKeyDown}
            onBlur={() => setActive(null)}
            className="outline-none focus-visible:ring-1 focus-visible:ring-signal-cyan"
          >
            <svg
              width={width}
              height={svgH}
              viewBox={`0 0 ${width} ${svgH}`}
              className="block max-w-full overflow-visible"
              aria-hidden="true"
            >
              {ticks.map((t, i) => (
                <g key={t}>
                  <line
                    x1={padLeft}
                    x2={padLeft + plotW}
                    y1={y(t)}
                    y2={y(t)}
                    stroke={GRID}
                    strokeWidth={1}
                    shapeRendering="crispEdges"
                  />
                  <text
                    x={padLeft - 6}
                    y={y(t)}
                    dy="0.32em"
                    textAnchor="end"
                    fontSize={11}
                    fill="var(--ink-min)"
                    style={{ fontVariantNumeric: "tabular-nums" }}
                  >
                    {tickLabels[i]}
                  </text>
                </g>
              ))}

              {xTicks.map((i) => (
                <text
                  key={i}
                  x={x(i)}
                  y={PAD_TOP + height + 15}
                  textAnchor={n > 1 && i === 0 ? "start" : n > 1 && i === n - 1 ? "end" : "middle"}
                  fontSize={11}
                  fill="var(--ink-min)"
                >
                  {tickText(i)}
                </text>
              ))}

              {active != null && (
                <line
                  x1={x(active)}
                  x2={x(active)}
                  y1={PAD_TOP}
                  y2={PAD_TOP + height}
                  stroke="var(--ink-min)"
                  strokeWidth={1}
                  shapeRendering="crispEdges"
                />
              )}

              {paths.map(({ s, d, singletons, last }) => (
                <g key={s.key}>
                  <path
                    d={d}
                    fill="none"
                    stroke={s.color}
                    strokeWidth={2}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                    strokeDasharray={s.dashed ? "5 4" : undefined}
                  />
                  {singletons.map((i) => (
                    <circle key={i} cx={x(i)} cy={y(s.values[i] as number)} r={3} fill={s.color} />
                  ))}
                  {last >= 0 && (
                    <circle
                      cx={x(last)}
                      cy={y(s.values[last] as number)}
                      r={4}
                      fill={s.color}
                      stroke={SURFACE}
                      strokeWidth={2}
                    />
                  )}
                  {active != null && s.values[active] != null && (
                    <circle
                      cx={x(active)}
                      cy={y(s.values[active] as number)}
                      r={4}
                      fill={s.color}
                      stroke={SURFACE}
                      strokeWidth={2}
                    />
                  )}
                </g>
              ))}

              {/* Hit layer: the whole plot, so the reader aims at a date,
                  never at a 2px line. */}
              <rect
                x={padLeft - 6}
                y={0}
                width={plotW + 12}
                height={PAD_TOP + height}
                fill="transparent"
                onPointerMove={(e) => setActive(indexFromPointer(e))}
                onPointerDown={(e) => setActive(indexFromPointer(e))}
                onPointerLeave={() => setActive(null)}
              />
            </svg>

            {active != null && (
              <div
                className="pointer-events-none absolute top-0 z-10 min-w-[8rem] border border-white/15 bg-surface-raised px-2.5 py-1.5 font-mono text-xs shadow-lg"
                style={
                  tooltipFlip
                    ? { right: `calc(${100 - tooltipLeft}% + 10px)` }
                    : { left: `calc(${tooltipLeft}% + 10px)` }
                }
              >
                <div className="mb-1 text-ink-lo">{xLabels[active]}</div>
                {series.map((s) => (
                  <div key={s.key} className="flex items-center gap-2 whitespace-nowrap">
                    <LineKey color={s.color} dashed={s.dashed} />
                    <span className="text-ink-hi tabular-nums">
                      {s.values[active] == null ? "—" : formatValue(s.values[active] as number)}
                    </span>
                    {series.length > 1 && <span className="text-ink-min">{s.label}</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {hasData && (
        <details className="mt-1">
          <summary className="cursor-pointer text-xs font-mono text-ink-min hover:text-ink-lo">
            Table view
          </summary>
          <div className="mt-1 max-h-64 overflow-auto">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="border-b border-white/[0.07] text-ink-lo">
                  <th scope="col" className="py-1 pr-3 text-left font-normal">
                    &nbsp;
                  </th>
                  {series.map((s) => (
                    <th key={s.key} scope="col" className="py-1 pl-3 text-right font-normal">
                      {s.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {xLabels.map((label, i) => (
                  <tr key={`${label}-${i}`} className="border-b border-white/[0.04]">
                    <th scope="row" className="py-0.5 pr-3 text-left font-normal text-ink-lo">
                      {label}
                    </th>
                    {series.map((s) => (
                      <td key={s.key} className="py-0.5 pl-3 text-right tabular-nums text-ink">
                        {s.values[i] == null ? "—" : formatValue(s.values[i] as number)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </figure>
  );
}
