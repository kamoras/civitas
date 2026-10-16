import type { VoterDeadline } from "@/types/election";
import { shortDate } from "@/lib/congress";

const DAY_MS = 86_400_000;

function day(iso: string): number {
  return Date.parse(`${iso.slice(0, 10)}T00:00:00Z`) / DAY_MS;
}

function tone(d: VoterDeadline): string {
  if (d.kind === "registration") return "bg-phos";
  if (d.kind === "same_day_registration") return "bg-phos/40";
  if (d.kind === "early_voting") return "bg-signal-amber/60";
  return "bg-signal-cyan";
}

/**
 * A state's deadlines on one track, from the earliest of them (or today)
 * to election day: ticks for deadlines, bands for windows (early voting,
 * same-day registration), a marker for today on the state's clock, and the
 * passed ones dimmed. A picture of the list below it, which is its text
 * equivalent, so it is hidden from assistive technology. Every date is the
 * backend's.
 */
export default function DeadlineRibbon({
  items,
  today,
  electionDay,
  registerBy,
}: {
  items: VoterDeadline[];
  /** The state's own date today (VoterCalendar.localToday). */
  today: string;
  electionDay: string;
  /** The registration deadline to label, when one is still open. */
  registerBy: string | null;
}) {
  const end = day(electionDay);
  const start = Math.min(day(today), ...items.map((d) => day(d.date)));
  const span = Math.max(end - start, 1);
  const at = (iso: string) => `${(Math.min(Math.max(day(iso) - start, 0), span) / span) * 100}%`;
  const width = (from: string, to: string) =>
    `${(Math.max(day(to) - Math.max(day(from), start), 0.6) / span) * 100}%`;
  const now = day(today);
  const pct = (iso: string) => (Math.min(Math.max(day(iso) - start, 0), span) / span) * 100;
  // A label kept inside the track: left-aligned near the start, right-aligned
  // near the end, centred between (no horizontal scroll at 375 px).
  const anchor = (iso: string) =>
    pct(iso) < 25 ? "" : pct(iso) > 75 ? "-translate-x-full" : "-translate-x-1/2";
  return (
    <div aria-hidden="true" className="mt-3 select-none">
      <div className="relative h-14">
        {/* The record rule: the whole run to election day. */}
        <div className="absolute inset-x-0 top-6 h-px bg-white/25" />
        {/* Passed time, shaded. */}
        <div className="absolute left-0 top-5 h-2 bg-white/[0.06]" style={{ width: at(today) }} />
        {items.map((d) => {
          const passed = d.status === "closed";
          const band = d.endDate && d.endDate !== d.date;
          return band ? (
            <span
              key={`${d.kind}-${d.method}-${d.date}`}
              className={`absolute top-5 h-2 ${tone(d)} ${passed ? "opacity-30" : ""}`}
              style={{ left: at(d.date), width: width(d.date, d.endDate!) }}
            />
          ) : (
            <span
              key={`${d.kind}-${d.method}-${d.basis}-${d.date}`}
              className={`absolute top-3 h-6 w-0.5 -translate-x-1/2 ${tone(d)} ${passed ? "opacity-30" : ""}`}
              style={{ left: at(d.closesOn) }}
            />
          );
        })}
        <span
          className="absolute top-1 h-10 w-px -translate-x-1/2 bg-ink-hi"
          style={{ left: at(today) }}
        />
        {pct(today) < 80 && (
          <span
            className={`absolute top-10 whitespace-nowrap font-mono text-[11px] tracking-[0.12em] text-ink-hi ${anchor(today)}`}
            style={{ left: at(today) }}
          >
            TODAY
          </span>
        )}
        <span className="absolute right-0 top-10 font-mono text-[11px] tracking-[0.12em] text-ink-hi">
          {shortDate(electionDay).toUpperCase()}
        </span>
        {registerBy && day(registerBy) >= now && (
          <span
            className={`absolute top-0 whitespace-nowrap font-mono text-[11px] tracking-[0.12em] text-phos ${anchor(registerBy)}`}
            style={{ left: at(registerBy) }}
          >
            REGISTER BY {shortDate(registerBy).toUpperCase()}
          </span>
        )}
      </div>
      <p className="mt-1 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-ink-lo">
        <span>
          <span className="mr-1 inline-block h-2.5 w-0.5 bg-phos align-middle" />
          registration
        </span>
        <span>
          <span className="mr-1 inline-block h-2 w-3 bg-phos/40 align-middle" />
          same-day registration
        </span>
        <span>
          <span className="mr-1 inline-block h-2 w-3 bg-signal-amber/60 align-middle" />
          early voting
        </span>
        <span>
          <span className="mr-1 inline-block h-2.5 w-0.5 bg-signal-cyan align-middle" />
          mail ballots
        </span>
        <span>dimmed: passed</span>
      </p>
    </div>
  );
}
