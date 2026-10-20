import Link from "next/link";
import { connection } from "next/server";
import type { RegistrationClosing, ThisWeek } from "@/types/thisWeek";
import { shortDate } from "@/lib/congress";
import { CALENDAR_HREF } from "@/lib/routes";
import StateJump from "./StateJump";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

const BOX = "flex min-w-0 flex-col border border-white/30 bg-surface p-4 sm:p-5";
const LABEL = "text-[13px] font-semibold text-ink-lo";
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

/** The states closing registration soon, as one line to the calendar: it
 * matters before the election, but the day's record leads the page. */
function Closing({ rows, days }: { rows: RegistrationClosing[]; days: number }) {
  if (rows.length === 0) return null;
  const soonest = rows.reduce((a, r) => (r.closesOn < a.closesOn ? r : a));
  const when =
    soonest.daysLeft === 0
      ? "today"
      : soonest.daysLeft === 1
        ? "tomorrow"
        : shortDate(soonest.closesOn);
  return (
    <p className="mt-3 border border-white/30 border-l-4 border-l-signal-orange bg-surface px-4 py-2 text-sm">
      <Link
        href={CALENDAR_HREF}
        className="inline-flex min-h-6 items-center font-semibold text-ink-hi underline underline-offset-2 hover:text-phos"
      >
        Registration closes in the next {days} days in {rows.length}{" "}
        {rows.length === 1 ? "state" : "states"}, soonest {when} ▸
      </Link>
    </p>
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
