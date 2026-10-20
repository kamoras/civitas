import Link from "next/link";
import { connection } from "next/server";
import type { RegistrationClosing, ThisWeek } from "@/types/thisWeek";
import { shortDate } from "@/lib/congress";
import { stateBallotHref } from "@/lib/elections";
import { CALENDAR_HREF } from "@/lib/routes";
import StateJump from "./StateJump";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

const BOX = "flex min-w-0 flex-col border border-white/30 bg-surface p-4 sm:p-5";
const LABEL = "text-[13px] font-semibold uppercase tracking-[0.06em] text-ink-lo";
const TITLE = "mt-1 font-display text-[22px] font-semibold leading-tight text-ink-hi";
const BUTTON =
  "mt-3 inline-flex min-h-11 w-fit items-center border border-ink-hi px-3 text-sm font-semibold text-ink-hi hover:bg-white/5";

/** Congress in one heading: in session today, back on a day, or neither. */
function congressLine(c: ThisWeek["congress"]): string {
  const chambers = Object.values(c.chambers);
  if (chambers.some((ch) => ch.inSessionToday)) return "In session today";
  const next = chambers
    .map((ch) => ch.nextMeeting)
    .filter((d): d is string => !!d)
    .sort()[0];
  return next ? `Back ${shortDate(next)}` : "Not in session";
}

function ballotLine(e: ThisWeek["election"]): string {
  if (e.phase === "results") return "The count, as states report it";
  if (e.phase === "election_day" || e.daysUntil === 0) return "Election Day is today";
  return `${e.daysUntil} day${e.daysUntil === 1 ? "" : "s"} to Election Day`;
}

function closesLabel(r: RegistrationClosing): string {
  if (r.daysLeft === 0) return "Today";
  if (r.daysLeft === 1) return "Tomorrow";
  return shortDate(r.closesOn);
}

/** The states closing registration soon, by day: the next three days. */
function Closing({ rows, days }: { rows: RegistrationClosing[]; days: number }) {
  if (rows.length === 0) return null;
  const byDay = new Map<string, RegistrationClosing[]>();
  for (const r of rows) byDay.set(r.closesOn, [...(byDay.get(r.closesOn) ?? []), r]);
  const groups = [...byDay.values()].slice(0, 3);
  return (
    <section
      aria-labelledby="closing-heading"
      className="mt-6 border border-white/30 border-l-4 border-l-signal-orange bg-surface p-4 sm:p-5"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="closing-heading" className="font-display text-[20px] font-semibold text-ink-hi">
          Registration closes in the next {days} days in {rows.length}{" "}
          {rows.length === 1 ? "state" : "states"}
        </h2>
        <Link
          href={CALENDAR_HREF}
          className="inline-flex min-h-11 items-center text-sm font-semibold text-phos underline underline-offset-2"
        >
          Every state ▸
        </Link>
      </div>
      <ul className="mt-1">
        {groups.map((g) => (
          <li
            key={g[0].closesOn}
            className="grid grid-cols-1 gap-x-6 border-t border-white/15 py-2 sm:grid-cols-[8rem_minmax(0,1fr)]"
          >
            <span className="font-semibold text-ink-hi">{closesLabel(g[0])}</span>
            <span className="flex flex-wrap gap-x-3 gap-y-1">
              {g.map((r) => (
                <Link
                  key={r.state}
                  href={stateBallotHref(r.state)}
                  className="inline-flex min-h-6 items-center text-ink underline decoration-white/40 underline-offset-2 hover:text-ink-hi"
                >
                  {r.stateName}
                </Link>
              ))}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * The home page's three tasks (your members, your ballot, Congress this
 * week) and the states whose registration closes soon, from GET
 * /api/this-week. Every date and count is the backend's; the pickers ask
 * nothing to remember (AGENTS.md section 8).
 */
export default async function HomeTasks() {
  await connection();
  let data: ThisWeek | null = null;
  try {
    const res = await fetch(`${BACKEND}/api/this-week`, { next: { revalidate: 300 } });
    if (res.ok) data = (await res.json()) as ThisWeek;
  } catch {
    data = null;
  }
  const election = data?.election;
  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6">
      <div className="mt-6 grid grid-cols-1 gap-3 md:grid-cols-3">
        <section aria-labelledby="task-members" className={BOX}>
          <p className={LABEL}>Your members</p>
          <h2 id="task-members" className={TITLE}>
            Who represents you
          </h2>
          <StateJump label="State" to="members" />
        </section>
        <section aria-labelledby="task-ballot" className={BOX}>
          <p className={LABEL}>Your ballot</p>
          <h2 id="task-ballot" className={TITLE}>
            {election ? ballotLine(election) : "Your state's ballot"}
          </h2>
          {election && <p className="text-sm text-ink-lo">{shortDate(election.date)}</p>}
          <StateJump label="State" to="ballot" />
        </section>
        <section aria-labelledby="task-congress" className={BOX}>
          <p className={LABEL}>Congress this week</p>
          <h2 id="task-congress" className={TITLE}>
            {data?.congress ? congressLine(data.congress) : "This week in Congress"}
          </h2>
          {data?.congress && <p className="text-sm text-ink-lo">{data.congress.sentence}</p>}
          <Link href="/congress" className={BUTTON}>
            The week&apos;s record
          </Link>
        </section>
      </div>
      {election?.registrationClosing && (
        <Closing rows={election.registrationClosing} days={election.closingSoonDays ?? 10} />
      )}
    </div>
  );
}
