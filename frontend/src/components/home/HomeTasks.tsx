import Link from "next/link";
import { connection } from "next/server";
import type { ThisWeek } from "@/types/thisWeek";
import { shortDate } from "@/lib/congress";
import ClosingLine from "@/components/elections/ClosingLine";
import StateJump from "./StateJump";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

const BOX = "flex min-w-0 flex-col border border-white/30 bg-surface p-4 sm:p-5";
const LABEL = "text-[13px] font-semibold text-ink-lo";
const TITLE = "mt-1 font-display text-[22px] font-semibold leading-tight text-ink-hi";
const BUTTON =
  "inline-flex min-h-11 w-fit items-center border border-ink-hi px-3 text-sm font-semibold text-ink-hi hover:bg-white/5";

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
          <div className="mt-auto pt-3">
            <Link href="/congress" className={BUTTON}>
              The week&apos;s record
            </Link>
          </div>
        </section>
      </div>
      {election?.registrationClosing && (
        <ClosingLine rows={election.registrationClosing} days={election.closingSoonDays ?? 10} />
      )}
    </div>
  );
}
