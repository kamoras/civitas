import type { VoterCalendar as Calendar, VoterDeadline } from "@/types/election";
import { safeHref } from "@/lib/formatting";
import { shortDate } from "@/lib/congress";

/** "6 days left", "last day", "in 6 days": the backend's countdown, worded
 *  for a deadline or for a window that hasn't opened. */
export function countdownText(d: Pick<VoterDeadline, "status" | "countdown">): string | null {
  const c = d.countdown;
  if (!c) return null;
  if (d.status === "upcoming") return c === "today" || c === "tomorrow" ? `opens ${c}` : `in ${c}`;
  if (c === "today") return "last day";
  return c === "tomorrow" ? "1 day left" : `${c} left`;
}

function isRegistration(d: VoterDeadline): boolean {
  return d.kind === "registration" || d.kind === "same_day_registration";
}

function checked(d: VoterDeadline): string {
  const on = d.verification.checkedOn ? `, checked ${shortDate(d.verification.checkedOn)}` : "";
  const who = d.source.publisher ?? "the state";
  if (d.verification.status === "verified") return `From ${who}${on}`;
  if (d.verification.status === "statute") return `From state law${on}`;
  return `From ${who}. We could not confirm this page automatically.`;
}

function Row({ d }: { d: VoterDeadline }) {
  const href = safeHref(d.source.url);
  const left = countdownText(d);
  const closed = d.status === "closed";
  return (
    <li className="border-t border-white/[0.09] py-2 first:border-t-0">
      <p className={closed ? "text-ink-lo" : "text-ink-hi"}>
        {closed && <span className="mr-2 font-mono text-xs text-ink-min">CLOSED</span>}
        {d.sentence}
        {left && <span className="ml-2 font-mono text-xs text-phos">{left}</span>}
      </p>
      <p className="mt-0.5 text-xs text-ink-lo">
        {d.rule}
        {d.adjustment && ` ${d.adjustment}`}{" "}
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-signal-cyan hover:text-phos"
            aria-label={`${checked(d)} (opens in new tab)`}
          >
            {checked(d)} ↗
          </a>
        ) : (
          checked(d)
        )}
      </p>
    </li>
  );
}

/**
 * A state's voter deadlines: the next one first, with a countdown, then
 * every one with its rule and source. A deadline that has passed stays and
 * says so; it never disappears. Every date, status and sentence comes from
 * the API (backend app/voter_calendar.py), on the state's own clock.
 */
export default function VoterCalendar({
  calendar,
  stateName,
}: {
  calendar: Calendar;
  stateName: string;
}) {
  const { next } = calendar;
  const left = next ? countdownText(next) : null;
  return (
    <section
      id="voter-deadlines"
      aria-labelledby="voter-deadlines-heading"
      className="mb-6 border border-white/15 bg-surface px-4 py-3"
    >
      <h2 id="voter-deadlines-heading" className="font-mono text-xs tracking-[0.16em] text-ink-min">
        VOTER DEADLINES · {stateName.toUpperCase()}
      </h2>
      {calendar.registrationSummary && (
        <p className="mt-1 font-display text-lg font-semibold text-ink-hi">
          {calendar.registrationSummary}
        </p>
      )}
      {next && !isRegistration(next) && (
        <p className="mt-1 text-base text-ink">
          Next deadline: {next.sentence}
          {left && <span className="ml-2 font-mono text-sm text-phos">{left}</span>}
        </p>
      )}
      {!next && (
        <p className="mt-1 text-base text-ink">Every deadline for this election has passed.</p>
      )}
      {calendar.note && <p className="mt-1 text-sm text-ink-lo">{calendar.note}</p>}
      <details className="group mt-2">
        <summary className="cursor-pointer list-none font-mono text-xs text-signal-cyan hover:text-phos [&::-webkit-details-marker]:hidden">
          Every deadline, with its source{" "}
          <span aria-hidden="true" className="inline-block group-open:rotate-180">
            &#9662;
          </span>
        </summary>
        <ul className="mt-2 text-sm">
          {calendar.items.map((d) => (
            <Row key={`${d.kind}-${d.method}-${d.basis}-${d.date}`} d={d} />
          ))}
        </ul>
      </details>
    </section>
  );
}
