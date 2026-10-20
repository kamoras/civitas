import type { FecDeadlineEvent } from "@/types/nextCycle";
import { longDate } from "@/lib/specials";
import { safeHref } from "@/lib/formatting";

/** The FEC's campaign finance deadline as a recurring event: shown from two
 * weeks before it until a few days after (backend next_cycle.deadline_event
 * decides the window), when the filings it brings in are fresh. */
export default function FecDeadlineCard({ event }: { event: FecDeadlineEvent | null }) {
  if (!event?.leads) return null;
  const what = event.label ?? event.reportType;
  const when =
    event.status === "due_today"
      ? "are due today"
      : event.status === "passed"
        ? `were due ${event.daysAgo === 1 ? "yesterday" : `${event.daysAgo} days ago`}, on ${longDate(event.date)}`
        : `are due in ${event.daysLeft === 1 ? "1 day" : `${event.daysLeft} days`}, on ${longDate(event.date)}`;
  return (
    <section
      aria-labelledby="fec-deadline"
      className="mt-6 border font-sans border-signal-cyan/30 bg-surface px-4 py-3"
    >
      <h2 id="fec-deadline" className="font-mono text-xs text-ink-min">
        FEC filing deadline
      </h2>
      <p className="mt-1 text-sm text-ink-lo">
        The FEC&apos;s {what} reports{event.reportYear ? ` (${event.reportYear})` : ""} {when}.
        Every federal candidate and committee files them; the fundraising figures on each race
        update as they are read.{" "}
        <a
          href={safeHref(event.source)}
          className="inline-flex min-h-6 items-center underline decoration-white/30 hover:text-phos"
        >
          The FEC&apos;s dates and deadlines
        </a>
      </p>
    </section>
  );
}
