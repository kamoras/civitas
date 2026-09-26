import type { ReactNode } from "react";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import { SERIES } from "./charts/palette";

export function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-block w-2 h-2 ${ok ? "bg-phos" : "bg-signal-magenta"}`}
      aria-label={ok ? "Healthy" : "Unhealthy"}
    />
  );
}

export function UsageBar({
  pct,
  warnAt = 75,
  critAt = 90,
  ariaLabel,
}: {
  pct: number;
  warnAt?: number;
  critAt?: number;
  ariaLabel: string;
}) {
  const color = pct >= critAt ? "bg-signal-magenta" : pct >= warnAt ? "bg-signal-amber" : "bg-phos";
  const value = Math.min(Math.round(pct), 100);
  return (
    <div
      className="w-full h-1.5 bg-white/[0.03] overflow-hidden"
      role="progressbar"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={ariaLabel}
    >
      <div
        className={`h-full ${color} transition-all duration-700`}
        style={{ width: `${Math.min(pct, 100)}%` }}
      />
    </div>
  );
}

/** A titled panel — the unit every sub-dashboard is built from. */
export function Panel({
  title,
  live = false,
  actions,
  className = "",
  children,
}: {
  title: string;
  live?: boolean;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`panel min-w-0 ${className}`} aria-label={title}>
      <TerminalTitlebar title={title}>
        {(live || actions) && (
          <span className="ml-auto mr-2 flex items-center gap-3 text-xs font-mono text-ink-lo">
            {actions}
            {live && (
              <span>
                live
                <span className="inline-block w-1.5 h-1.5 bg-phos ml-1 animate-pulse" />
              </span>
            )}
          </span>
        )}
      </TerminalTitlebar>
      <div className="p-4">{children}</div>
    </section>
  );
}

/**
 * One headline figure. Label in sentence case, value in proportional figures
 * (tabular digits look loose at display size), optional note underneath.
 */
export function StatTile({
  label,
  value,
  note,
  tone = "text-ink-hi",
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  tone?: string;
}) {
  return (
    <div className="border border-white/[0.07] p-3 min-w-0">
      <div className="text-xs font-mono text-ink-lo tracking-wider">{label}</div>
      <div className={`mt-1 font-mono text-xl ${tone}`}>{value}</div>
      {note && <div className="mt-0.5 text-xs font-mono text-ink-min">{note}</div>}
    </div>
  );
}

/** Horizontal ranking bars (a ranking, not a time series — bars are right here). */
export function RankBars({
  title,
  entries,
  unit,
  labelWidth = "w-14",
}: {
  title: string;
  entries: { name: string; count: number }[];
  unit: string;
  labelWidth?: string;
}) {
  if (entries.length === 0) return null;
  const max = Math.max(1, ...entries.map((e) => e.count));
  return (
    <div className="min-w-0">
      <div className="text-ink-lo text-xs font-mono tracking-wider mb-1.5">{title}</div>
      <div className="space-y-1">
        {entries.map((e) => (
          <div key={e.name} className="flex items-center gap-2">
            <span
              className={`text-ink-lo text-xs font-mono ${labelWidth} shrink-0 truncate`}
              title={e.name}
            >
              {e.name}
            </span>
            <div className="flex-1">
              <div
                className="h-1.5 w-full bg-white/[0.03]"
                role="img"
                aria-label={`${e.name}: ${e.count.toLocaleString()} ${unit}`}
              >
                <div
                  className="h-full"
                  style={{ width: `${(e.count / max) * 100}%`, background: SERIES[0] }}
                />
              </div>
            </div>
            <span className="text-ink-lo text-xs font-mono tabular-nums w-12 text-right shrink-0">
              {e.count.toLocaleString()}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** A preset row of segmented buttons — the one filter row above a dashboard. */
export function Segmented<T extends string | number>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label={label}>
      <span className="text-xs font-mono text-ink-min tracking-wider">{label}</span>
      <div className="flex gap-1">
        {options.map((o) => (
          <button
            key={String(o.value)}
            type="button"
            onClick={() => onChange(o.value)}
            aria-pressed={value === o.value}
            className={`px-2 py-1 text-xs font-mono tracking-wider border transition-colors ${
              value === o.value
                ? "border-ink-lo text-ink-hi"
                : "border-white/[0.07] text-ink-min hover:text-ink-lo"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}
