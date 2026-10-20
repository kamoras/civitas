import type { ConfirmDistribution, NominationKind, NominationStats } from "@/types/nomination";
import { overlay } from "@/lib/chartColors";
import { KIND_LABEL, days } from "@/lib/nominations";

const KINDS: NominationKind[] = ["civilian", "judicial", "military"];
const W = 640;
const H = 104;
const PAD_L = 4;
const PAD_R = 4;
const AXIS = 18; // room under the bars for the tick labels

/**
 * Days from the Senate receiving a nomination to confirming it, one panel per
 * kind on a shared day scale so the three can be compared at a glance: a bar
 * per `binDays` of waiting, the middle half of the waits shaded, the median a
 * rule. Every figure is the backend's (nomination_service.stats); this draws.
 */
export default function ConfirmTimeChart({ stats }: { stats: NominationStats }) {
  const span = Math.max(
    stats.binDays,
    ...KINDS.flatMap((k) => stats.kinds[k].timeToConfirm.bins.map((b) => b.end))
  );
  const x = (d: number) => PAD_L + (Math.min(d, span) / span) * (W - PAD_L - PAD_R);
  // A tick every 90 days, or every 180 past two years.
  const step = span > 730 ? 180 : 90;
  const ticks = Array.from({ length: Math.floor(span / step) + 1 }, (_, i) => i * step);
  return (
    <div className="flex flex-col gap-5">
      {KINDS.map((kind) => (
        <Panel
          key={kind}
          label={KIND_LABEL[kind]}
          dist={stats.kinds[kind].timeToConfirm}
          x={x}
          ticks={ticks}
        />
      ))}
    </div>
  );
}

function Panel({
  label,
  dist,
  x,
  ticks,
}: {
  label: string;
  dist: ConfirmDistribution;
  x: (d: number) => number;
  ticks: number[];
}) {
  const top = Math.max(1, ...dist.bins.map((b) => b.count));
  const y = (n: number) => H - AXIS - (n / top) * (H - AXIS - 14);
  const summary =
    dist.n === 0
      ? `${label}: none confirmed yet.`
      : `${label}: ${dist.n} confirmed; median ${days(dist.median)}, middle half ${days(dist.p25)} to ${days(dist.p75)}.`;
  return (
    <figure className="m-0">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="font-mono text-xs text-ink-hi">{label}</span>
        <span className="font-mono text-xs text-ink-lo">
          {dist.n === 0 ? (
            "None confirmed yet"
          ) : (
            <>
              <span className="text-ink-hi">median {days(dist.median)}</span> · middle half{" "}
              {days(dist.p25)}–{days(dist.p75)} · n {dist.n}
            </>
          )}
        </span>
      </figcaption>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="mt-1 block h-24 w-full"
        role="img"
        aria-label={summary}
        preserveAspectRatio="none"
      >
        {dist.p25 != null && dist.p75 != null && (
          <rect
            x={x(dist.p25)}
            y={8}
            width={Math.max(1, x(dist.p75) - x(dist.p25))}
            height={H - AXIS - 8}
            fill="var(--signal-cyan)"
            opacity={0.12}
          />
        )}
        {dist.bins.map((b) =>
          b.count ? (
            <rect
              key={b.start}
              x={x(b.start) + 0.5}
              y={y(b.count)}
              width={Math.max(1, x(b.end) - x(b.start) - 1)}
              height={H - AXIS - y(b.count)}
              fill="var(--ink-lo)"
            >
              <title>{`${b.start}–${b.end - 1} days: ${b.count}`}</title>
            </rect>
          ) : null
        )}
        {dist.median != null && (
          <line
            x1={x(dist.median)}
            x2={x(dist.median)}
            y1={4}
            y2={H - AXIS}
            stroke="var(--signal-cyan)"
            strokeWidth={2}
          />
        )}
        <line x1={0} x2={W} y1={H - AXIS} y2={H - AXIS} stroke={overlay(0.25)} />
        {ticks.map((t) => (
          <line key={t} x1={x(t)} x2={x(t)} y1={H - AXIS} y2={H - AXIS + 4} stroke={overlay(0.4)} />
        ))}
      </svg>
      {/* Labels in HTML, not SVG text: the bars stretch to the width, text must not. */}
      <div aria-hidden="true" className="relative -mt-4 h-4 font-mono text-xs text-ink-min">
        {ticks.map((t) => (
          <span
            key={t}
            className={`absolute top-0 ${
              t === 0 ? "" : x(t) / W > 0.95 ? "-translate-x-full" : "-translate-x-1/2"
            }`}
            style={{ left: `${(x(t) / W) * 100}%` }}
          >
            {t === 0 ? "0 days" : t}
          </span>
        ))}
      </div>
      <p className="mt-1 font-mono text-xs text-ink-min">Tallest bar: {top}</p>
    </figure>
  );
}
