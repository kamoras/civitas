import Link from "next/link";
import { connection } from "next/server";
import type { CommentDeadline, StateNextDeadline, ThisWeek } from "@/types/thisWeek";
import { countdownText } from "@/components/elections/VoterCalendar";
import { rollCallHref, shortDate, weekHref } from "@/lib/congress";
import { stateBallotHref } from "@/lib/elections";
import { STATE_NAMES } from "@/lib/stateCodes";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

// The backend's own lifetime for this answer (api/this_week.py).
const REVALIDATE_S = 300;

/**
 * This week, on the homepage and on the pages readers land on: how far off
 * Election Day is, with every state's ballot one click away; what Congress
 * is doing; and the next comment deadlines on federal rules.
 *
 * Rendered on the server, whole, with the page: no client fetch, so nothing
 * is blank until hydration. The state picker is links (a <details> list in
 * the compact form), never a field asking where the reader lives (AGENTS.md
 * §8). Every figure and sentence is the API's; this only lays them out.
 *
 * Supplementary: when the backend can't answer, the strip is left out and
 * the page renders as it would without it.
 */
export default async function ThisWeekStrip({
  variant,
  pageDate,
}: {
  variant: "full" | "compact";
  /** The day the page reports (a Congress day page): when it isn't today,
   * the strip is labelled with today's date, since its "meets today" and
   * "this week" are about now, not the page's day. */
  pageDate?: string;
}) {
  // Per request, never prerendered: `next build` can't reach the backend,
  // and the homepage would otherwise ship the strip-less page until a
  // revalidation. The fetch itself is cached for the backend's lifetime.
  await connection();
  let data: ThisWeek | null = null;
  try {
    const res = await fetch(`${BACKEND}/api/this-week`, { next: { revalidate: REVALIDATE_S } });
    if (res.ok) data = (await res.json()) as ThisWeek;
  } catch {
    data = null;
  }
  if (!data?.election || !data.congress) return null;
  return variant === "full" ? (
    <FullStrip data={data} />
  ) : (
    <CompactStrip data={data} pageDate={pageDate} />
  );
}

const LABEL = "font-mono text-xs uppercase tracking-[0.16em] text-ink-min";
const LINK = "underline decoration-white/30 underline-offset-4 hover:text-phos";

function electionLine(e: ThisWeek["election"]): { figure: string; caption: string; pick: string } {
  if (e.phase === "campaign" && e.daysUntil > 0) {
    return {
      figure: String(e.daysUntil),
      caption: `day${e.daysUntil === 1 ? "" : "s"} until Election Day · ${shortDate(e.date)}`,
      pick: "See who's on your state's ballot",
    };
  }
  if (e.phase === "election_day" || e.daysUntil === 0) {
    return {
      figure: "Today",
      caption: "is Election Day",
      pick: "See your state's ballot and count",
    };
  }
  return {
    figure: "Results",
    caption: `· the ${shortDate(e.date)} election`,
    pick: "See your state's count",
  };
}

/** Every state's ballot page, as links: navigation, not a question. */
function StatePicker({ states, compact }: { states: string[]; compact: boolean }) {
  return (
    <ul
      className={`grid gap-1 font-mono text-xs ${compact ? "grid-cols-[repeat(auto-fill,minmax(2.75rem,1fr))]" : "grid-cols-[repeat(auto-fill,minmax(2.5rem,1fr))]"}`}
    >
      {states.map((st) => (
        <li key={st}>
          <Link
            href={stateBallotHref(st)}
            aria-label={`${STATE_NAMES[st] ?? st} ballot`}
            title={STATE_NAMES[st] ?? st}
            className="flex min-h-[32px] items-center justify-center border border-white/15 text-ink-lo hover:border-phos hover:text-phos"
          >
            {st}
          </Link>
        </li>
      ))}
    </ul>
  );
}

/** How many states' next deadlines show before "every state". */
const DEADLINES_SHOWN = 5;

function DeadlineRow({ d }: { d: StateNextDeadline }) {
  const left = countdownText(d);
  return (
    <li className="text-sm leading-snug">
      <Link
        href={`${stateBallotHref(d.state)}#voter-deadlines`}
        className="text-ink-hi underline decoration-white/30 underline-offset-4 hover:text-phos"
      >
        {STATE_NAMES[d.state] ?? d.state}
      </Link>
      <span className="text-ink-lo">: {d.sentence}</span>
      {left && <span className="ml-1 font-mono text-xs text-phos">{left}</span>}
    </li>
  );
}

/** Each state's next voter deadline, soonest first: the five nearest, and
 *  every state behind a disclosure. Links, not a question (AGENTS.md §8). */
function StateDeadlines({ rows }: { rows: StateNextDeadline[] }) {
  const soon = rows.slice(0, DEADLINES_SHOWN);
  const rest = rows.slice(DEADLINES_SHOWN);
  return (
    <div className="flex flex-col gap-2 pt-2">
      <h3 className={LABEL}>Next voter deadlines</h3>
      <ul className="flex flex-col gap-1.5">
        {soon.map((d) => (
          <DeadlineRow key={d.state} d={d} />
        ))}
      </ul>
      {rest.length > 0 && (
        <details className="group">
          <summary className="cursor-pointer list-none font-mono text-xs text-ink-hi underline decoration-white/30 underline-offset-4 hover:text-phos [&::-webkit-details-marker]:hidden">
            Every state&apos;s next deadline
            <span aria-hidden="true" className="ml-1 inline-block group-open:rotate-180">
              &#9662;
            </span>
          </summary>
          <ul className="flex flex-col gap-1.5 pt-2">
            {rest.map((d) => (
              <DeadlineRow key={d.state} d={d} />
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function deadlineWhy(d: CommentDeadline) {
  if (d.issueId) {
    return (
      <Link href={`/issue/${encodeURIComponent(d.issueId)}`} className={LINK}>
        In the news
      </Link>
    );
  }
  if (d.citedBy > 0) return `Cited by ${d.citedBy} other document${d.citedBy === 1 ? "" : "s"}`;
  return null;
}

function daysLeft(n: number): string {
  return n === 0 ? "closes today" : `${n} day${n === 1 ? "" : "s"} left`;
}

function FullStrip({ data }: { data: ThisWeek }) {
  const { election, congress, commentDeadlines } = data;
  const line = electionLine(election);
  return (
    <section aria-labelledby="this-week-heading" className="mx-auto mt-8 max-w-7xl px-4 sm:px-6">
      <h2
        id="this-week-heading"
        className="border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min"
      >
        This week
      </h2>
      <div className="grid grid-cols-1 gap-8 pt-4 md:grid-cols-3 md:gap-9">
        <div className="flex flex-col gap-3">
          <p className="font-display text-ink-hi">
            <span className="text-4xl font-extrabold leading-none tabular-nums">{line.figure}</span>{" "}
            <span className="font-mono text-xs uppercase tracking-[0.12em] text-ink-lo">
              {line.caption}
            </span>
          </p>
          <p className="font-display text-base text-ink">{line.pick}:</p>
          <StatePicker states={election.states} compact={false} />
          {election.deadlines && election.deadlines.length > 0 && (
            <StateDeadlines rows={election.deadlines} />
          )}
        </div>

        <div className="flex flex-col gap-3">
          <h3 className={LABEL}>Congress</h3>
          <p className="font-display text-base leading-relaxed text-ink">{congress.sentence}</p>
          <ul className="flex flex-col gap-1 font-mono text-xs text-ink-lo">
            {(["senate", "house"] as const).map((c) => {
              const ch = congress.chambers[c];
              const n = ch.recordVotesThisWeek;
              return (
                <li key={c}>
                  {c === "senate" ? "SENATE" : "HOUSE"} · {n === 0 ? "no" : n} record vote
                  {n === 1 ? "" : "s"} this week
                </li>
              );
            })}
          </ul>
          {congress.recentVotes.length > 0 && (
            <ul className="flex flex-col gap-2">
              {congress.recentVotes.map((v) => (
                <li
                  key={`${v.chamber}-${v.congress}-${v.session}-${v.number}`}
                  className="text-sm leading-snug"
                >
                  <Link href={rollCallHref(v)} className="text-ink-hi hover:underline">
                    {v.billLabel ? `${v.billLabel}: ` : ""}
                    {v.title || v.question}
                  </Link>
                  <span className="block font-mono text-xs text-ink-min">
                    {shortDate(v.date)} · {v.chamber === "senate" ? "Senate" : "House"} · {v.result}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <Link href={weekHref(congress.weekStart)} className={`font-mono text-xs ${LINK}`}>
            What Congress did this week &rarr;
          </Link>
        </div>

        <div className="flex flex-col gap-3">
          <h3 className={LABEL}>Comment deadlines</h3>
          {commentDeadlines.length === 0 ? (
            <p className="font-display text-base text-ink-lo">
              No federal rule is open for comment right now.
            </p>
          ) : (
            <ul className="flex flex-col gap-3">
              {commentDeadlines.map((d) => {
                const why = deadlineWhy(d);
                return (
                  <li key={d.id} className="text-sm leading-snug">
                    <Link
                      href={`/explore/${d.id}`}
                      className="line-clamp-2 text-ink-hi hover:underline"
                    >
                      {d.title}
                    </Link>
                    <span className="block font-mono text-xs text-ink-min">
                      {d.docType} · closes {shortDate(d.commentsCloseOn)} · {daysLeft(d.daysLeft)}
                      {why && <> · {why}</>}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
          <Link href="/explore?comment=open" className={`font-mono text-xs ${LINK}`}>
            Every open comment period &rarr;
          </Link>
        </div>
      </div>
    </section>
  );
}

function CompactStrip({ data, pageDate }: { data: ThisWeek; pageDate?: string }) {
  const { election, congress, commentDeadlines } = data;
  const line = electionLine(election);
  const next = commentDeadlines[0];
  // On a page about another day, "This week … meets today" read as that
  // day's (2026-09-16's page, on 2026-10-15): say which day it is.
  const label =
    pageDate && congress.today && pageDate !== congress.today
      ? `Now · ${shortDate(congress.today)}`
      : "This week";
  return (
    <aside
      aria-label={label}
      className="mb-6 border border-white/15 bg-surface px-4 py-3 font-mono text-xs text-ink-lo"
    >
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-2">
        <span className="uppercase tracking-[0.16em] text-ink-min">{label}</span>
        <span>
          <span className="font-semibold text-ink-hi">{line.figure}</span> {line.caption}
        </span>
        <span>
          {congress.sentence}{" "}
          <Link href={weekHref(congress.weekStart)} className={LINK}>
            This week&apos;s votes
          </Link>
        </span>
        {next && (
          <span>
            Next comment deadline:{" "}
            <Link href={`/explore/${next.id}`} className={LINK}>
              {next.title.length > 70 ? `${next.title.slice(0, 67).trimEnd()}...` : next.title}
            </Link>{" "}
            ({shortDate(next.commentsCloseOn)})
          </span>
        )}
        <details className="group open:basis-full">
          <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden text-ink-hi underline decoration-white/30 underline-offset-4 hover:text-phos">
            {line.pick}
            <span aria-hidden="true" className="ml-1 inline-block group-open:rotate-180">
              &#9662;
            </span>
          </summary>
          <div className="pt-3">
            <StatePicker states={election.states} compact />
          </div>
        </details>
      </div>
    </aside>
  );
}

export { FullStrip, CompactStrip };
