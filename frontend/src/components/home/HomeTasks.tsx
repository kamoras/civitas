import Link from "@/components/Link";
import { connection } from "next/server";
import type { ThisWeek } from "@/types/thisWeek";
import { RECORD_SOURCE, shortDate } from "@/lib/congress";
import { ELECTION_DAY_LAW } from "@/lib/elections";
import { CALENDAR_HREF } from "@/lib/routes";
import { INLINE_LINK } from "@/lib/controlStyles";
import ClosingLine from "@/components/elections/ClosingLine";
import { SourceLine } from "@/components/shared/Type";
import StateJump from "./StateJump";
import { TYPE } from "@/lib/typeStyles";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// One ruled band, not three boxes: a cell per task, its heading on one line
// and its control on the next. As three padded boxes they filled the first
// screen (300px at 1280px wide, 776px on an iPhone 13, measured 2026-11-23)
// and pushed the record they introduce below it.
const CELL = "flex min-w-0 flex-col gap-1 py-3 md:px-4 md:first:pl-0 md:last:pr-0";
const KICKER = "font-normal";
const TASK = "text-ink-hi";

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
      <div className="mt-5 grid grid-cols-1 divide-y divide-white/30 border-y border-white/30 md:grid-cols-3 md:divide-x md:divide-y-0">
        <section aria-labelledby="task-members" className={CELL}>
          <h2 id="task-members" className={TYPE.kicker}>
            <span className={KICKER}>Your members · </span>
            <span className={TASK}>Who represents you</span>
          </h2>
          <StateJump label="State" to="members" />
        </section>
        <section aria-labelledby="task-ballot" className={CELL}>
          <h2 id="task-ballot" className={TYPE.kicker}>
            <span className={KICKER}>Your ballot · </span>
            <span className={TASK}>{election ? ballotLine(election) : "Your state's ballot"}</span>
            {election && (
              <>
                {" "}
                <span className={`${KICKER} whitespace-nowrap`}>· {shortDate(election.date)}</span>
              </>
            )}
          </h2>
          <StateJump label="State" to="ballot" />
        </section>
        <section aria-labelledby="task-congress" className={CELL}>
          <h2 id="task-congress" className={TYPE.kicker}>
            <span className={KICKER}>Congress this week · </span>
            <span className={TASK}>
              {data?.congress ? congressLine(data.congress) : "This week in Congress"}
            </span>
          </h2>
          <Link
            href="/congress"
            className={`mt-auto inline-flex min-h-11 w-fit items-center ${INLINE_LINK}`}
          >
            The week&apos;s record &rarr;
          </Link>
        </section>
      </div>
      <SourceLine className="mt-2">
        Election Day by law (
        <a href={ELECTION_DAY_LAW.url} className={INLINE_LINK}>
          {ELECTION_DAY_LAW.name}
        </a>
        ); registration deadlines from each state&apos;s election office, on the{" "}
        <Link href={CALENDAR_HREF} className={INLINE_LINK}>
          calendar
        </Link>
        . Congress: {RECORD_SOURCE}.
      </SourceLine>
      {election?.registrationClosing && (
        <ClosingLine rows={election.registrationClosing} days={election.closingSoonDays ?? 10} />
      )}
    </div>
  );
}
