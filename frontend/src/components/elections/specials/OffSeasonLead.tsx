import Link from "next/link";
import type { ElectionCalendar, SpecialsBoard } from "@/types/specials";
import { CALENDAR_HREF } from "@/lib/routes";
import { calendarEntries, longDate } from "@/lib/specials";
import SpecialsBoardView from "./Specials";

/** How many calendar lines the lead shows before linking the rest. */
const NEXT_DATES = 6;
/** How many of each specials list the lead shows. */
const SPECIALS_SHOWN = 3;

/** /elections between cycles (the backend's season: from the results
 * window's end to the cycle's first federal primary): the specials board
 * and what is next on the calendar lead, rather than a countdown to a
 * general a year or two away. Every list says so when it is empty. */
export default function OffSeasonLead({
  board,
  cal,
}: {
  board: SpecialsBoard;
  cal: ElectionCalendar | null;
}) {
  const next = cal
    ? calendarEntries(cal)
        .filter((e) => !e.passed)
        .slice(0, NEXT_DATES)
    : [];
  const { season } = board;
  return (
    <section
      aria-labelledby="between-cycles"
      className="mt-6 border border-phos/20 bg-surface px-4 pb-4 font-sans"
    >
      <h2
        id="between-cycles"
        className="border-b border-white/[0.07] py-2.5 font-mono text-xs uppercase tracking-[0.16em] text-ink-min"
      >
        Between general elections
      </h2>
      <p className="mt-3 text-sm text-ink-lo">
        {season.firstPrimary
          ? `The ${season.cycle} primaries begin on ${longDate(season.firstPrimary)}, by the FEC's calendar.`
          : `The ${season.cycle} primaries are not on the FEC's calendar yet.`}{" "}
        Until then, seats that fall vacant are filled by special elections, listed here with each
        state&apos;s own count.
      </p>
      <div className="grid gap-x-8 lg:grid-cols-[3fr_2fr]">
        <SpecialsBoardView board={board} limit={SPECIALS_SHOWN} />
        <div>
          <h3 className="mt-6 font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
            Next on the calendar
          </h3>
          {next.length ? (
            <ul className="mt-1">
              {next.map((e) => (
                <li key={e.key} className="border-t border-white/[0.07] py-2">
                  <p className="font-mono text-[11px] tracking-[0.12em] text-ink-min">
                    {longDate(e.date).toUpperCase()} · {e.tag} · {e.status}
                  </p>
                  <p className="text-sm text-ink-lo">{e.text}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-ink-lo">
              {cal
                ? "Nothing is on the calendar for the year ahead."
                : "The calendar couldn't be read just now."}
            </p>
          )}
          <Link
            href={CALENDAR_HREF}
            className="mt-3 inline-flex min-h-11 items-center font-mono text-xs tracking-[0.1em] text-signal-cyan hover:text-phos"
          >
            [ THE FULL CALENDAR → ]
          </Link>
        </div>
      </div>
    </section>
  );
}
