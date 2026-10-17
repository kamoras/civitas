import Link from "next/link";
import type { RegistrationClosing } from "@/types/thisWeek";
import { shortDate } from "@/lib/congress";
import { stateBallotHref } from "@/lib/elections";
import { CALENDAR_HREF } from "@/lib/routes";
import RegisterLink from "@/components/elections/RegisterLink";
import YourStateClosing from "@/components/elections/YourStateClosing";

function when(r: RegistrationClosing): string {
  if (r.daysLeft === 0) return "Closes today";
  if (r.daysLeft === 1) return "Closes tomorrow";
  return `${shortDate(r.closesOn)} · ${r.daysLeft} days left`;
}

/**
 * The states whose registration closes in the next few days, grouped by
 * the day it closes, each with its ballot page and where to register.
 * Every date and count is the backend's (voter_calendar.registration_closing),
 * on each state's own clock. With none closing, it says so: an empty strip
 * is still an answer.
 */
export default function ClosingSoon({
  rows,
  days = 10,
  headingLevel = 2,
  className = "",
  compact = false,
}: {
  rows: RegistrationClosing[];
  days?: number;
  headingLevel?: 2 | 3;
  className?: string;
  /** Each state as its name and register link only, in a line (the
   *  homepage); the state's page has the rest. */
  compact?: boolean;
}) {
  const H = headingLevel === 2 ? "h2" : "h3";
  const byDay = new Map<string, RegistrationClosing[]>();
  for (const r of rows) byDay.set(r.closesOn, [...(byDay.get(r.closesOn) ?? []), r]);
  return (
    <section aria-labelledby="closing-soon-heading" className={className}>
      <H
        id="closing-soon-heading"
        className="font-mono text-xs uppercase tracking-[0.16em] text-ink-min"
      >
        Registration closes soon
      </H>
      {rows.length === 0 ? (
        <p className="mt-1 text-sm text-ink">
          No state&apos;s regular registration closes in the next {days} days.{" "}
          <Link
            href={CALENDAR_HREF}
            className="text-signal-cyan underline decoration-signal-cyan/40 underline-offset-4 hover:text-phos"
          >
            Every state&apos;s deadlines
          </Link>
        </p>
      ) : (
        <>
          <p className="mt-1 text-sm text-ink">
            In {rows.length} {rows.length === 1 ? "state" : "states"}, registration closes in the
            next {days} days.
          </p>
          <YourStateClosing rows={rows} />
          <ol className="mt-2 flex flex-col gap-2">
            {Array.from(byDay.entries()).map(([day, states]) => (
              <li key={day} className="border-t border-white/[0.09] pt-2 first:border-t-0">
                <p className="font-mono text-xs uppercase tracking-[0.12em] text-phos">
                  {when(states[0])}
                </p>
                <ul
                  className={
                    compact ? "mt-1 flex flex-wrap gap-x-4 gap-y-1" : "mt-1 flex flex-col gap-1"
                  }
                >
                  {states.map((r) => (
                    <li key={r.state} className="text-sm leading-snug">
                      <Link
                        href={`${stateBallotHref(r.state)}#voter-deadlines`}
                        className="text-ink-hi underline decoration-white/30 underline-offset-4 hover:text-phos"
                      >
                        {r.stateName}
                      </Link>
                      {!compact && <span className="text-ink-lo">: {r.sentence}</span>}
                      {r.register && (
                        <>
                          {" "}
                          <RegisterLink link={r.register} showNote={false} className="text-sm" />
                          {!r.register.confirmed && (
                            <span className="text-xs text-ink-min"> (unconfirmed)</span>
                          )}
                        </>
                      )}
                      {r.sameDay && !compact && (
                        <span className="block text-xs text-ink-min">After that: {r.sameDay}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
