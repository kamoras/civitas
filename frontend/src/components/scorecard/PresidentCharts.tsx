"use client";

import type {
  BarsChart,
  LineChart,
  PresidentChart,
  PresidentCharts,
} from "@/types/presidentCharts";
import { onThisPage } from "@/lib/onThisPage";
import { SHARE_SECTION_ATTR } from "@/lib/shareImage";
import ShareSectionButton from "@/components/share/ShareSectionButton";

// Drawn in a fixed box and stretched to the column: strokes keep their
// width (non-scaling-stroke) and every label is HTML beside the drawing.
const W = 600;
const H = 200;

function scale(min: number, max: number) {
  const pad = (max - min || 1) * 0.06;
  return (v: number) => H - ((v - min + pad) / (max - min + 2 * pad)) * H;
}

function path(points: [number, number][], x: (v: number) => number, y: (v: number) => number) {
  return points.map(([px, py], i) => `${i ? "L" : "M"}${x(px)},${y(py)}`).join(" ");
}

function Axis({ c, children }: { c: PresidentChart; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2" aria-hidden="true">
      <div className="flex flex-col justify-between py-0.5 text-right text-xs text-ink-min">
        <span>{c.yTop}</span>
        <span>{c.yBottom}</span>
      </div>
      {children}
    </div>
  );
}

function Lines({ c }: { c: LineChart }) {
  const x = (v: number) => (v / Math.max(c.xMax, 1)) * W;
  const y = scale(c.yMin, c.yMax);
  const band = c.band;
  const area =
    band.length > 1
      ? `${band.map((b, i) => `${i ? "L" : "M"}${x(b.x)},${y(b.high)}`).join(" ")} ${band
          .slice()
          .reverse()
          .map((b) => `L${x(b.x)},${y(b.low)}`)
          .join(" ")} Z`
      : null;
  return (
    <>
      <Axis c={c}>
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-48 w-full">
          {c.yMin < 0 && c.yMax > 0 && (
            <line
              x1={0}
              x2={W}
              y1={y(0)}
              y2={y(0)}
              stroke="var(--ink-lo)"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          )}
          {c.others.map((o) => (
            <path
              key={o.id}
              d={path(o.points, x, y)}
              fill="none"
              stroke="var(--chart-muted)"
              strokeOpacity={0.3}
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {area && <path d={area} fill="var(--chart-muted)" fillOpacity={0.18} />}
          {band.length > 1 && (
            <>
              <path
                d={path(
                  band.map((b) => [b.x, b.high]),
                  x,
                  y
                )}
                fill="none"
                stroke="var(--chart-muted)"
                strokeDasharray="5 4"
                strokeWidth={1.5}
                vectorEffect="non-scaling-stroke"
              />
              <path
                d={path(
                  band.map((b) => [b.x, b.low]),
                  x,
                  y
                )}
                fill="none"
                stroke="var(--chart-muted)"
                strokeDasharray="5 4"
                strokeWidth={1.5}
                vectorEffect="non-scaling-stroke"
              />
            </>
          )}
          <path
            d={path(c.series.points, x, y)}
            fill="none"
            stroke="var(--ink-hi)"
            strokeWidth={3}
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      </Axis>
      <div className="ml-10 flex justify-between text-xs text-ink-min" aria-hidden="true">
        <span>{c.xLabel} 1</span>
        <span>{c.xMax + 1}</span>
      </div>
      <p className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-lo" aria-hidden="true">
        <span>
          <span className="mr-1 inline-block h-[3px] w-5 bg-ink-hi align-middle" />
          {c.series.name}
        </span>
        <span>
          <span className="mr-1 inline-block w-5 border-t-2 border-dashed border-[var(--chart-muted)] align-middle" />
          middle half of the other {c.n} presidencies
        </span>
        <span>
          <span className="mr-1 inline-block h-px w-5 bg-[var(--chart-muted)] align-middle" />
          each other presidency
        </span>
      </p>
      <div className="sr-only">
        <table>
          <caption>
            {c.title}: {c.series.name}, and the middle half of other presidencies
          </caption>
          <thead>
            <tr>
              <th scope="col">{c.xLabel}</th>
              <th scope="col">{c.series.name}</th>
              <th scope="col">Other presidencies, middle half</th>
            </tr>
          </thead>
          <tbody>
            {c.table.map((r) => (
              <tr key={r.x}>
                <th scope="row">{r.x + 1}</th>
                <td>{r.value}</td>
                <td>{r.band ?? "too few to compare"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Bars({ c }: { c: BarsChart }) {
  const y = scale(c.yMin, c.yMax);
  const pct = (v: number) => `${(y(v) / H) * 100}%`;
  const band = c.eraBand;
  return (
    <>
      <Axis c={c}>
        <div className="relative h-48">
          {band && (
            <div
              className="absolute inset-x-0 border-y-2 border-dashed border-[var(--chart-muted)] bg-[color-mix(in_srgb,var(--chart-muted)_18%,transparent)]"
              style={{ top: pct(band.high), bottom: `calc(100% - ${pct(band.low)})` }}
            />
          )}
          <div className="absolute inset-x-0 h-px bg-ink-lo" style={{ top: pct(0) }} />
          <div className="absolute inset-0 flex items-stretch gap-1">
            {c.bars.map((b) => {
              const top = Math.min(y(b.value), y(0));
              const height = Math.abs(y(b.value) - y(0));
              return (
                <div key={b.label} className="relative flex-1">
                  <div
                    className={`absolute inset-x-0 ${b.first ? "border-2 border-ink-hi bg-transparent" : "bg-ink-hi"}`}
                    style={{
                      top: `${(top / H) * 100}%`,
                      height: `${Math.max((height / H) * 100, 0.8)}%`,
                    }}
                  />
                </div>
              );
            })}
          </div>
        </div>
      </Axis>
      <div className="ml-10 flex gap-1 text-[11px] text-ink-min" aria-hidden="true">
        {c.bars.map((b) => (
          <span key={b.label} className="min-w-0 flex-1 truncate text-center">
            {c.bars.length > 6 ? `'${b.label.slice(2)}` : b.label}
          </span>
        ))}
      </div>
      <p className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-lo" aria-hidden="true">
        <span>
          <span className="mr-1 inline-block h-2.5 w-3 bg-ink-hi align-middle" />
          each year of the term
        </span>
        <span>
          <span className="mr-1 inline-block h-2.5 w-3 border-2 border-ink-hi align-middle" />
          first year
        </span>
        {c.bandShown && (
          <span>
            <span className="mr-1 inline-block w-5 border-t-2 border-dashed border-[var(--chart-muted)] align-middle" />
            other presidencies&apos; years: {c.bandShown}
          </span>
        )}
      </p>
      <div className="sr-only">
        <table>
          <caption>{c.title}</caption>
          <thead>
            <tr>
              <th scope="col">Year</th>
              <th scope="col">{c.yLabel}</th>
            </tr>
          </thead>
          <tbody>
            {c.bars.map((b) => (
              <tr key={b.label}>
                <th scope="row">{b.label}</th>
                <td>
                  {b.shown}
                  {b.first ? " (first year)" : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Chart({ c }: { c: PresidentChart }) {
  const id = `chart-${c.key}`;
  return (
    <figure
      id={id}
      aria-labelledby={`${id}-caption`}
      {...{ [SHARE_SECTION_ATTR]: id }}
      className="flex min-w-0 flex-col gap-2 border border-white/25 bg-surface px-4 py-4 sm:px-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-display text-lg font-semibold text-ink-hi">{c.title}</h3>
          <p className="text-sm text-ink">{c.why}</p>
        </div>
        <ShareSectionButton label={c.title} />
      </div>
      {c.kind === "line" ? <Lines c={c} /> : <Bars c={c} />}
      <figcaption id={`${id}-caption`} className="text-sm text-ink">
        {c.caption}
      </figcaption>
      {/* Inside the captured area, so a shared image carries its source. */}
      <p className="text-xs text-ink-lo">
        {c.window} Source:{" "}
        <a href={c.source.url} className="text-phos underline underline-offset-2">
          {c.source.name}
        </a>
        .
      </p>
    </figure>
  );
}

/** The term against the other presidencies, drawn: approval through the
 *  term, jobs since the inauguration, growth each year. Every figure and
 *  sentence comes from the API (backend app/president_charts.py). */
export default function PresidentChartsSection({ data }: { data: PresidentCharts }) {
  if (data.charts.length === 0) return null;
  return (
    <section aria-labelledby="president-charts-heading" className="font-sans">
      <h2
        id="president-charts-heading"
        {...onThisPage("Through the term")}
        className="border-t-[3px] border-ink-hi pt-3 font-display text-[22px] font-semibold text-ink-hi sm:text-[26px]"
      >
        Through the term
      </h2>
      <p className="mt-1 max-w-prose text-sm text-ink-lo">
        Each line or bar is what happened during the term, beside other presidencies over the same
        stretch of theirs. The middle half is drawn where at least {data.minBand} of them reach that
        point. It shows when, not why.
      </p>
      <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-2">
        {data.charts.map((c) => (
          <Chart key={c.key} c={c} />
        ))}
      </div>
    </section>
  );
}
