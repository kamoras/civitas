"use client";

import { useState } from "react";
import Link from "next/link";
import { useAsyncData } from "@/hooks/useAsyncData";
import { fetchTimeline } from "@/lib/api";
import { formatUtcDate, formatWeekRange, safeHref } from "@/lib/formatting";
import { SECTION_HEADING, TEXT_LINK } from "@/components/action/IssueEnrichment";
import type { TimelineEntry, TimelineMonth, TimelineWeek, UpcomingEvent } from "@/lib/api";

/*
  The year so far: each day's top issue, grouped by month and week, with the
  period summaries the pipeline writes. Set in the records style — hairline
  rows and small mono headers — where it used to be purple panels.

  Days and monitors open inside the page through callbacks rather than
  <Link>s: both live on /action itself, and a soft navigation to the same
  route is never re-read by the page (see ActionPageInner's deep link).
*/

interface Handlers {
  onOpenDay: (date: string) => void;
  onOpenMonitor: (slug: string) => void;
  /** Days the Action Center still has issues for (its pager's list). Older
   *  unposted issues are deleted after 14 days
   *  (action_center._cleanup_old_unposted_issues), so an older day would
   *  open onto nothing; the entry here is its record. */
  openableDates: string[];
}

const dayLabel = (d: string) => formatUtcDate(d, { month: "short", day: "2-digit" }).toUpperCase();

function daysAway(dateStr: string, asOf: number): number {
  const target = new Date(dateStr + "T00:00:00");
  const now = new Date(asOf);
  now.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - now.getTime()) / 86_400_000);
}

function DayRow({
  entry,
  onOpenDay,
  onOpenMonitor,
  openableDates,
}: { entry: TimelineEntry } & Handlers) {
  const source = safeHref(entry.sourceUrl);
  const openable = openableDates.includes(entry.date);
  return (
    <li className="grid grid-cols-[4.5rem_minmax(0,1fr)] items-baseline gap-3 border-b border-white/[0.07] py-2.5">
      <span className="font-mono text-xs tracking-[0.08em] tabular-nums text-ink-min">
        {dayLabel(entry.date)}
      </span>
      <span className="min-w-0">
        {openable ? (
          <button
            onClick={() => onOpenDay(entry.date)}
            className="text-left font-display text-[15px] leading-snug text-ink-hi hover:underline"
          >
            {entry.title}
          </button>
        ) : (
          <>
            <span className="block font-display text-[15px] leading-snug text-ink-hi">
              {entry.title}
            </span>
            {entry.summary && (
              <span className="mt-0.5 block font-display text-[13px] leading-snug text-ink-lo">
                {entry.summary.length > 200 ? `${entry.summary.slice(0, 200)}…` : entry.summary}
              </span>
            )}
          </>
        )}
        <span className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1 font-mono text-xs tracking-[0.06em] text-ink-min">
          {(entry.policyAreas ?? []).length > 0 && (
            <span>{entry.policyAreas.slice(0, 2).join(" · ")}</span>
          )}
          {entry.monitorSlug && (
            <button
              onClick={() => onOpenMonitor(entry.monitorSlug!)}
              className="text-ink-lo underline decoration-white/20 underline-offset-4 hover:text-ink-hi"
            >
              Tracked concern
            </button>
          )}
          {source && (
            <a
              href={source}
              target="_blank"
              rel="noopener noreferrer"
              className="text-ink-lo underline decoration-white/20 underline-offset-4 hover:text-ink-hi"
            >
              {entry.sourceName || "Source"} <span aria-hidden="true">↗</span>
            </a>
          )}
        </span>
      </span>
    </li>
  );
}

function DayList({ entries, ...handlers }: { entries: TimelineEntry[] } & Handlers) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? entries : entries.slice(0, 7);
  const remaining = entries.length - 7;
  return (
    <>
      <ul>
        {visible.map((e) => (
          <DayRow key={e.date} entry={e} {...handlers} />
        ))}
      </ul>
      {remaining > 0 && (
        <button onClick={() => setShowAll((v) => !v)} className={`${TEXT_LINK} mt-3`}>
          {showAll ? "Show fewer days" : `Show ${remaining} more day${remaining !== 1 ? "s" : ""}`}
        </button>
      )}
    </>
  );
}

function PeriodSummary({ children }: { children: React.ReactNode }) {
  return (
    <p className="my-3 max-w-3xl font-display text-[15px] leading-relaxed text-ink">{children}</p>
  );
}

function WeekBlock({ week, ...handlers }: { week: TimelineWeek } & Handlers) {
  return (
    <div className="mt-5">
      <h3 className="flex flex-wrap items-baseline justify-between gap-2 font-mono text-xs uppercase tracking-[0.12em] text-ink-lo">
        <span>{week.isCurrent ? "This week" : formatWeekRange(week.startDate, week.endDate)}</span>
        <span className="text-ink-min">
          {week.entryCount} day{week.entryCount !== 1 ? "s" : ""}
        </span>
      </h3>
      {week.summary && !week.isCurrent && <PeriodSummary>{week.summary}</PeriodSummary>}
      <DayList entries={week.entries} {...handlers} />
    </div>
  );
}

function MonthBody({ month, ...handlers }: { month: TimelineMonth } & Handlers) {
  return (
    <>
      {!month.isCurrent && month.summary && <PeriodSummary>{month.summary}</PeriodSummary>}
      {month.weeks.length > 1 ? (
        month.weeks.map((week) => <WeekBlock key={week.weekNum} week={week} {...handlers} />)
      ) : (
        <DayList entries={month.entries} {...handlers} />
      )}
    </>
  );
}

function monthMeta(month: TimelineMonth): string {
  const n = month.entries.length;
  const parts = [`${n} day${n !== 1 ? "s" : ""}`];
  if (!month.isCurrent && month.topAreas.length > 0)
    parts.push(month.topAreas.slice(0, 3).join(", "));
  return parts.join(" · ");
}

function ComingUp({ events, asOf }: { events: UpcomingEvent[]; asOf: number }) {
  return (
    <section aria-labelledby="coming-up-heading" className="mt-10">
      <h2 id="coming-up-heading" className={SECTION_HEADING}>
        Coming up
      </h2>
      <ul>
        {events.map((ev) => {
          const days = daysAway(ev.date, asOf);
          return (
            <li
              key={ev.date + ev.category}
              className="grid grid-cols-[4.5rem_minmax(0,1fr)_auto] items-baseline gap-3 border-b border-white/[0.07] py-3"
            >
              <span className="font-mono text-xs tracking-[0.08em] tabular-nums text-ink-min">
                {dayLabel(ev.date)}
              </span>
              <span className="min-w-0">
                <Link
                  href={ev.link}
                  className="font-display text-[15px] leading-snug text-ink-hi hover:underline"
                >
                  {ev.title}
                </Link>
                <span className="mt-0.5 block font-display text-[13px] leading-snug text-ink-lo">
                  {ev.description}
                </span>
              </span>
              <span className="whitespace-nowrap font-mono text-xs tabular-nums text-ink-lo">
                {days <= 0 ? "today" : `in ${days} day${days !== 1 ? "s" : ""}`}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export default function TimelineTab(handlers: Handlers) {
  const request = useAsyncData("action-timeline", fetchTimeline);
  // Read once, so the "in N days" figures don't change on a re-render.
  const [asOf] = useState(() => Date.now());

  if (request.loading) {
    return (
      <p
        role="status"
        aria-live="polite"
        className="py-10 font-mono text-sm tracking-[0.12em] text-ink-min"
      >
        Reading the archive…
      </p>
    );
  }

  if (request.error !== null) {
    return (
      <div
        role="alert"
        className="flex flex-wrap items-baseline justify-between gap-3 border-l-2 border-signal-red bg-surface px-4 py-3 font-mono text-sm text-signal-red"
      >
        <span>Could not load the archive.</span>
        <button onClick={request.retry} className={TEXT_LINK}>
          Try again
        </button>
      </div>
    );
  }

  const data = request.data;
  // Every list is normalized once, here, rather than defended at each of its
  // use sites. A payload missing `months` used to reach `.some()` and
  // white-screen the whole tab; a partial answer should render the part that
  // did arrive, and an empty one should say so.
  const months = data?.months ?? [];
  const monitors = data?.monitors ?? [];
  const topThemes = data?.topThemes ?? [];
  const upcomingEvents = data?.upcomingEvents ?? [];

  if (!data || (!data.totalDays && months.length === 0 && upcomingEvents.length === 0)) {
    return (
      <p className="border-l-2 border-ink-min/60 py-2 pl-4 font-display text-base text-ink-lo">
        The archive fills in as each day&apos;s top issue is recorded. Nothing is on it yet.
      </p>
    );
  }

  const currentMonth = months.find((m) => m.isCurrent);
  const pastMonths = months.filter((m) => !m.isCurrent);
  const activeMonitors = monitors.filter((m) => m.status === "active").length;
  const top = topThemes[0];

  return (
    <div>
      <p className="mb-5 max-w-2xl font-display text-base leading-relaxed text-ink-lo">
        Each day&apos;s top issue since January, with a summary of each finished week and month.
        Recent days open in Today, with every issue from that day.
      </p>

      <dl className="grid grid-cols-1 gap-px border border-white/[0.07] bg-white/[0.07] sm:grid-cols-3">
        <div className="bg-surface-base px-4 py-3.5">
          <dt className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
            Days on record · {data.year}
          </dt>
          <dd className="mt-1.5 font-display text-2xl font-bold tabular-nums text-ink-hi">
            {data.totalDays}
          </dd>
        </div>
        <div className="bg-surface-base px-4 py-3.5">
          <dt className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
            Most covered
          </dt>
          <dd className="mt-1.5 font-display text-2xl font-bold text-ink-hi">
            {top ? (
              <>
                {top.area}{" "}
                <span className="text-sm font-normal text-ink-lo">
                  {top.count} day{top.count !== 1 ? "s" : ""}
                </span>
              </>
            ) : (
              "—"
            )}
          </dd>
        </div>
        <div className="bg-surface-base px-4 py-3.5">
          <dt className="font-mono text-xs uppercase tracking-[0.12em] text-ink-min">
            Concerns tracked
          </dt>
          <dd className="mt-1.5 font-display text-2xl font-bold tabular-nums text-ink-hi">
            {monitors.length}{" "}
            <span className="text-sm font-normal text-ink-lo">{activeMonitors} active</span>
          </dd>
        </div>
      </dl>

      {data.yearSummary && (
        <section aria-labelledby="year-heading" className="mt-10">
          <h2 id="year-heading" className={SECTION_HEADING}>
            The year in review
          </h2>
          <PeriodSummary>{data.yearSummary.summary}</PeriodSummary>
        </section>
      )}

      {upcomingEvents.length > 0 && <ComingUp events={upcomingEvents} asOf={asOf} />}

      {currentMonth && (
        <section aria-labelledby="current-month-heading" className="mt-10">
          <h2 id="current-month-heading" className={SECTION_HEADING}>
            <span>
              {currentMonth.name} {data.year}
            </span>
            <span aria-hidden="true">{monthMeta(currentMonth)}</span>
          </h2>
          <MonthBody month={currentMonth} {...handlers} />
        </section>
      )}

      {pastMonths.length > 0 && (
        <section aria-labelledby="earlier-heading" className="mt-10">
          <h2 id="earlier-heading" className={SECTION_HEADING}>
            Earlier this year
          </h2>
          <ul>
            {pastMonths.map((month) => (
              <li key={month.month} className="border-b border-white/[0.07]">
                <details className="group">
                  <summary className="flex cursor-pointer list-none items-start justify-between gap-4 py-3.5 [&::-webkit-details-marker]:hidden">
                    <span>
                      <span className="block font-display text-lg font-semibold text-ink-hi">
                        {month.name}
                      </span>
                      <span className="mt-0.5 block font-mono text-xs tracking-[0.06em] text-ink-min">
                        {monthMeta(month)}
                      </span>
                    </span>
                    <span
                      className="mt-1 font-mono text-lg leading-none text-ink-min after:content-['+'] group-open:after:content-['−']"
                      aria-hidden="true"
                    />
                  </summary>
                  <div className="pb-5">
                    <MonthBody month={month} {...handlers} />
                  </div>
                </details>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
