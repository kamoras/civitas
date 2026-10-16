import type { VoterCalendar as Calendar, VoterDeadline } from "@/types/election";
import { safeHref } from "@/lib/formatting";
import { shortDate } from "@/lib/congress";
import DeadlineRibbon from "@/components/elections/DeadlineRibbon";
import RegisterLink from "@/components/elections/RegisterLink";
import { INLINE_LINK } from "@/lib/controlStyles";

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
  // Only verified items reach the page: the generator refuses any other.
  if (d.verification.status === "statute") return `From state law${on}`;
  return `From ${d.source.publisher ?? "the state"}${on}`;
}

function Row({ d, register }: { d: VoterDeadline; register?: Calendar["register"] }) {
  const href = safeHref(d.source.url);
  const left = countdownText(d);
  const closed = d.status === "closed";
  return (
    <li className="border-t border-white/[0.09] py-2 first:border-t-0">
      <p className={closed ? "text-ink-lo" : "text-ink-hi"}>
        {closed && <span className="mr-2 font-mono text-xs text-ink-min">CLOSED</span>}
        {d.sentence}
        {left && <span className="ml-2 font-mono text-xs text-phos">{left}</span>}
        {register && d.kind === "registration" && !closed && (
          <>
            {" "}
            <RegisterLink link={register} showNote={false} className="ml-1 text-sm" />
          </>
        )}
      </p>
      <p className="mt-0.5 text-xs text-ink-lo">
        {d.rule}
        {d.adjustment && ` ${d.adjustment}`}{" "}
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className={INLINE_LINK}
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

/** The first line on registering: "Register online by Mon, Oct 19", its
 *  countdown and the register link; what follows the deadline (same-day
 *  registration, in the state's words); or that the state has no
 *  registration. Falls back to the older API's summary sentence. */
function Registration({ calendar }: { calendar: Calendar }) {
  const line = calendar.registration;
  if (!line) {
    return calendar.registrationSummary ? (
      <p className="mt-1 font-display text-lg font-semibold text-ink-hi">
        {calendar.registrationSummary}
      </p>
    ) : null;
  }
  const left =
    line.status === "open" ? countdownText({ status: "open", countdown: line.countdown }) : null;
  const link = calendar.register;
  const canRegister = line.status === "open" || line.status === "same_day";
  return (
    <div className="mt-1">
      <p className="font-display text-xl font-semibold text-ink-hi">
        {line.sentence}
        {left && <span className="ml-2 font-mono text-sm text-phos">{left}</span>}
      </p>
      {line.sameDay && <p className="mt-0.5 text-sm text-ink">After that: {line.sameDay}</p>}
      {link && canRegister && (
        <p className="mt-1 text-sm">
          <RegisterLink
            link={link}
            label={line.status === "open" ? "Register to vote" : "How to register"}
            className="font-mono text-sm"
          />
        </p>
      )}
      {link && line.status === "open" && !link.statePage && (
        <p className="mt-0.5 text-xs text-ink-lo">
          vote.gov is the federal government&apos;s registration directory.
        </p>
      )}
    </div>
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
  lookupHref,
  lookupIsStateSpecific,
}: {
  calendar: Calendar;
  stateName: string;
  /** Where to send a reader when no deadline could be read: the state's
   *  own voter lookup, or the national directory of election offices. */
  lookupHref: string;
  lookupIsStateSpecific: boolean;
}) {
  const { next } = calendar;
  const left = next ? countdownText(next) : null;
  const heading = (
    <h2 id="voter-deadlines-heading" className="font-mono text-xs tracking-[0.16em] text-ink-min">
      VOTER DEADLINES · {stateName.toUpperCase()}
    </h2>
  );
  if (calendar.unread) {
    // No dates from anywhere but an official source: say so, and point to
    // the people who have them.
    const where = lookupIsStateSpecific
      ? `${stateName}'s official voter lookup`
      : "the national directory of election offices";
    return (
      <section
        id="voter-deadlines"
        aria-labelledby="voter-deadlines-heading"
        className="mb-6 border border-white/15 bg-surface px-4 py-3"
      >
        {heading}
        <p className="mt-1 text-base text-ink">
          {calendar.unread} Check your deadlines with your county board of elections, or use{" "}
          <a
            href={lookupHref}
            target="_blank"
            rel="noopener noreferrer"
            className={INLINE_LINK}
            aria-label={`${where} (opens in new tab)`}
          >
            {where} ↗
          </a>
          .
        </p>
      </section>
    );
  }
  return (
    <section
      id="voter-deadlines"
      aria-labelledby="voter-deadlines-heading"
      className="mb-6 border border-white/15 bg-surface px-4 py-3"
    >
      {heading}
      <Registration calendar={calendar} />
      {calendar.electionDay && calendar.items.length > 0 && (
        <DeadlineRibbon
          items={calendar.items}
          today={calendar.localToday}
          electionDay={calendar.electionDay}
          registerBy={
            calendar.registration?.status === "open" ? calendar.registration.closesOn : null
          }
        />
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
      <details className="group mt-2" open>
        <summary className="cursor-pointer list-none font-mono text-xs text-signal-cyan hover:text-phos [&::-webkit-details-marker]:hidden">
          Every deadline, with its source{" "}
          <span aria-hidden="true" className="inline-block group-open:rotate-180">
            &#9662;
          </span>
        </summary>
        <ul className="mt-2 text-sm">
          {calendar.items.map((d) => (
            <Row
              key={`${d.kind}-${d.method}-${d.basis}-${d.date}`}
              d={d}
              register={calendar.register}
            />
          ))}
        </ul>
      </details>
    </section>
  );
}
