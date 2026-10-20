import Link from "next/link";
import type { ElectionCalendar, Season } from "@/types/specials";
import { CALENDAR_HREF } from "@/lib/routes";
import { dayText, longDate } from "@/lib/specials";

/** How many primary dates the lead lists before linking the calendar. */
const SHOWN = 5;

/** The FEC's codes for a regular primary, caucus and primary runoff
 * (backend election_phase._OPENS_PRIMARIES / _SETTLES_NOMINEES). */
const PRIMARY_CODES = new Set(["P", "CAU", "PR"]);

/** /elections in primary season (the backend's ramp: the cycle's first
 * primary or caucus to its last congressional primary or runoff): the next
 * primaries, by the FEC's calendar, grouped by day. */
export default function PrimariesLead({
  season,
  cal,
}: {
  season: Season;
  cal: ElectionCalendar | null;
}) {
  const ahead = (cal?.elections ?? []).filter(
    (e) => !e.special && PRIMARY_CODES.has(e.typeCode) && e.status !== "held"
  );
  const byDay = new Map<string, typeof ahead>();
  for (const e of ahead) byDay.set(e.date, [...(byDay.get(e.date) ?? []), e]);
  const days = [...byDay.entries()].slice(0, SHOWN);
  return (
    <section
      aria-labelledby="season-primaries"
      className="mt-6 border border-phos/20 bg-surface px-4 pb-4 font-sans"
    >
      <h2
        id="season-primaries"
        className="border-b border-white/[0.07] py-2.5 font-mono text-xs uppercase tracking-[0.16em] text-ink-min"
      >
        The {season.cycle} primaries
      </h2>
      <p className="mt-3 text-sm text-ink-lo">
        {season.firstPrimary &&
          `They opened on ${longDate(season.firstPrimary)}, by the FEC's calendar`}
        {season.lastPrimary ? `, and run through ${longDate(season.lastPrimary)}. ` : ". "}
        Each state picks its nominees for the {longDate(season.electionDay)} general; the dates are
        the FEC&apos;s, the rules each state&apos;s own.
      </p>
      {days.length ? (
        <ul className="mt-2">
          {days.map(([day, rows]) => (
            <li key={day} className="border-t border-white/[0.07] py-2">
              <p className="font-mono text-[11px] tracking-[0.12em] text-ink-min">
                {longDate(day).toUpperCase()} · {dayText(rows[0].status, rows[0].daysLeft)}
              </p>
              <p className="text-sm text-ink-lo">
                {[...new Set(rows.map((r) => `${r.stateName} (${r.label ?? r.typeCode})`))].join(
                  ", "
                )}
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-ink-lo">
          {cal
            ? "No primary is ahead in the calendar's year; the last one has been held."
            : "The calendar couldn't be read just now."}
        </p>
      )}
      <Link
        href={CALENDAR_HREF}
        className="mt-2 inline-flex min-h-11 items-center font-mono text-xs tracking-[0.1em] text-signal-cyan hover:text-phos"
      >
        [ EVERY DATE AND DEADLINE → ]
      </Link>
    </section>
  );
}
