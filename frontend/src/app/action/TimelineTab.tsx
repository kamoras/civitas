"use client";

import { useMemo, useState } from "react";
import { useAsyncData } from "@/hooks/useAsyncData";
import Link from "next/link";
import { fetchTimeline } from "@/lib/api";
import { formatWeekRange, safeHref } from "@/lib/formatting";
import { ACTION_CENTER_MONITORS_HREF } from "@/lib/routes";
import type { TimelineEntry, TimelineWeek, TimelineMonth, UpcomingEvent } from "@/lib/api";

const MONTH_NAMES = [
  "",
  "JANUARY",
  "FEBRUARY",
  "MARCH",
  "APRIL",
  "MAY",
  "JUNE",
  "JULY",
  "AUGUST",
  "SEPTEMBER",
  "OCTOBER",
  "NOVEMBER",
  "DECEMBER",
];

const EVENT_STYLES: Record<
  string,
  { border: string; dot: string; badge: string; badgeText: string }
> = {
  election: {
    border: "border-signal-red/30",
    dot: "bg-signal-red",
    badge: "border-signal-red/40 text-signal-red bg-signal-red/10",
    badgeText: "ELECTION",
  },
  scotus: {
    border: "border-dem-blue/30",
    dot: "bg-dem-blue",
    badge: "border-dem-blue/40 text-dem-blue/90 bg-dem-blue/10",
    badgeText: "SCOTUS",
  },
  congress: {
    border: "border-phos/30",
    dot: "bg-phos",
    badge: "border-phos/40 text-phos/90 bg-phos/10",
    badgeText: "CONGRESS",
  },
  executive: {
    border: "border-signal-amber/40",
    dot: "bg-signal-amber",
    badge: "border-signal-amber/40 text-signal-amber bg-signal-amber/10",
    badgeText: "EXECUTIVE",
  },
};

function daysUntil(dateStr: string): number {
  const target = new Date(dateStr + "T00:00:00");
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - now.getTime()) / 86_400_000);
}

function EventCard({ event }: { event: UpcomingEvent }) {
  const days = daysUntil(event.date);
  const style = EVENT_STYLES[event.category] ?? EVENT_STYLES.congress;
  return (
    <div className={`panel border-l-4 ${style.border} p-4 sm:p-5`}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            <span className={`text-xs font-mono px-2 py-0.5 border ${style.badge}`}>
              {style.badgeText}
            </span>
            <span className="text-xs text-ink-min font-mono">{event.date}</span>
          </div>
          <h4 className="font-mono text-sm text-ink-hi leading-relaxed mb-1">{event.title}</h4>
          <p className="text-xs text-ink-lo leading-relaxed mb-3">{event.description}</p>
          <Link
            href={event.link}
            className="text-xs font-mono tracking-widest text-ink-lo hover:text-phos transition-colors"
          >
            {event.linkLabel.toUpperCase()} →
          </Link>
        </div>
        <div className="text-right shrink-0">
          <div className="font-display font-semibold text-2xl sm:text-3xl text-ink-hi">{days}</div>
          <div className="text-xs font-mono text-ink-min">DAY{days !== 1 ? "S" : ""} AWAY</div>
        </div>
      </div>
    </div>
  );
}

function DayEntry({ entry }: { entry: TimelineEntry }) {
  return (
    <div className="relative group">
      <div
        className="absolute -left-[21px] top-1.5 w-2.5 h-2.5 bg-ind-purple/40 border border-ind-purple/60"
        aria-hidden="true"
      />
      <Link
        href={`/action?date=${entry.date}`}
        className="block hover:bg-white/[0.02] transition-colors px-1 -mx-1 py-0.5"
      >
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs text-ink-min font-mono">{entry.date}</span>
          {entry.policyAreas.slice(0, 2).map((area) => (
            <span
              key={area}
              className="text-xs font-mono px-1.5 py-0.5 border border-signal-amber/40 text-ink-lo"
            >
              {area}
            </span>
          ))}
        </div>
        <p className="text-base text-ink-hi group-hover:text-phos font-medium leading-relaxed">
          {entry.title}
        </p>
        <p className="text-xs text-ink-lo leading-relaxed mt-1">
          {entry.summary.slice(0, 200)}
          {entry.summary.length > 200 ? "…" : ""}
        </p>
      </Link>
      <div className="flex items-center gap-3 mt-1 px-1">
        {entry.sourceUrl && (
          <a
            href={safeHref(entry.sourceUrl) || "#"}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-ink-lo hover:text-phos transition-colors"
          >
            {entry.sourceName || "Source"} <span aria-hidden="true">↗</span>
          </a>
        )}
        {entry.monitorSlug && (
          <Link
            href={ACTION_CENTER_MONITORS_HREF}
            className="text-xs font-mono text-signal-amber hover:text-signal-amber transition-colors"
          >
            ● MONITORED
          </Link>
        )}
      </div>
    </div>
  );
}

function DayList({ entries }: { entries: TimelineEntry[] }) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? entries : entries.slice(0, 7);
  const remaining = entries.length - 7;
  return (
    <div className="relative pl-4 border-l border-ind-purple/20 space-y-3">
      {visible.map((e) => (
        <DayEntry key={e.date} entry={e} />
      ))}
      {remaining > 0 && (
        <button
          onClick={() => setShowAll((v) => !v)}
          className="w-full text-center text-xs font-mono py-2 border border-ind-purple/20 text-ind-purple hover:text-ind-purple hover:border-ind-purple/40 transition-colors"
        >
          {showAll ? "▲ SHOW LESS" : `▼ SHOW ${remaining} MORE DAY${remaining !== 1 ? "S" : ""}`}
        </button>
      )}
    </div>
  );
}

function WeekCard({ week }: { week: TimelineWeek }) {
  const [expanded, setExpanded] = useState(week.isCurrent);
  const label = week.isCurrent ? "CURRENT WEEK" : formatWeekRange(week.startDate, week.endDate);

  return (
    <div className="border border-white/[0.07]">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left px-3 py-2.5 flex items-center justify-between hover:bg-white/[0.01] transition-colors"
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-2.5 flex-wrap">
          <span
            className={`font-mono text-xs ${week.isCurrent ? "text-signal-cyan" : "text-ink-lo"}`}
          >
            {label}
          </span>
          <span className="text-xs text-ink-min">
            {week.entryCount} day{week.entryCount !== 1 ? "s" : ""}
          </span>
          {week.topAreas.slice(0, 3).map((a) => (
            <span
              key={a}
              className="hidden sm:inline text-xs font-mono px-1.5 py-0.5 border border-white/[0.07] text-ink-min"
            >
              {a}
            </span>
          ))}
        </div>
        <span className="text-ink-min font-mono text-base leading-none shrink-0">
          {expanded ? "−" : "+"}
        </span>
      </button>

      {expanded && (
        <div className="px-3 pb-3 border-t border-white/[0.07] pt-3 space-y-3">
          {week.summary && !week.isCurrent && (
            <div className="border-l-2 border-ind-purple/30 pl-3 py-1">
              <div className="text-xs font-mono tracking-widest text-ind-purple mb-1">
                WEEK IN REVIEW
              </div>
              <p className="text-xs text-ink-lo leading-relaxed italic">{week.summary}</p>
            </div>
          )}
          <DayList entries={week.entries} />
        </div>
      )}
    </div>
  );
}

function MonthCard({
  month,
  defaultExpanded,
  eventsByMonth,
}: {
  month: TimelineMonth;
  defaultExpanded: boolean;
  eventsByMonth: Record<number, UpcomingEvent[]>;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const monthEvents = eventsByMonth[month.month] ?? [];

  return (
    <div className="panel">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left p-4 sm:p-5 flex items-center justify-between"
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-3 flex-wrap">
          <span
            className={`font-mono text-sm ${month.isCurrent ? "text-ind-purple" : "text-ind-purple"}`}
          >
            {month.name.toUpperCase()}
          </span>
          <span className="text-xs text-ink-min">
            {month.entries.length} day{month.entries.length !== 1 ? "s" : ""}
          </span>
          {monthEvents.length > 0 && (
            <span className="text-xs font-mono text-ink-lo">
              +{monthEvents.length} event{monthEvents.length !== 1 ? "s" : ""}
            </span>
          )}
          {!month.isCurrent &&
            month.topAreas.slice(0, 3).map((a) => (
              <span
                key={a}
                className="hidden sm:inline text-xs font-mono px-1.5 py-0.5 border border-white/[0.07] text-ink-min"
              >
                {a}
              </span>
            ))}
        </div>
        <span className="text-ink-min font-mono text-base leading-none" aria-hidden="true">
          {expanded ? "−" : "+"}
        </span>
      </button>

      {expanded && (
        <div className="px-4 sm:px-5 pb-4 sm:pb-5 border-t border-white/[0.07] pt-4 space-y-4">
          {monthEvents.length > 0 && (
            <div className="space-y-2">
              {monthEvents.map((ev) => {
                const style = EVENT_STYLES[ev.category] ?? EVENT_STYLES.congress;
                return (
                  <Link
                    key={ev.date + ev.category}
                    href={ev.link}
                    className={`block border-l-2 ${style.border} pl-3 py-2 hover:bg-white/[0.02] transition-colors`}
                  >
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className={`w-2 h-2 ${style.dot}`} aria-hidden="true" />
                      <span className="text-xs text-ink-min font-mono">{ev.date}</span>
                      <span className={`text-xs font-mono px-1.5 py-0.5 border ${style.badge}`}>
                        {style.badgeText}
                      </span>
                    </div>
                    <span className="text-sm text-ink-hi font-medium">{ev.title}</span>
                  </Link>
                );
              })}
            </div>
          )}

          {/* Past month: show LLM summary + week breakdown */}
          {!month.isCurrent && month.summary && (
            <div className="border-l-2 border-ind-purple/30 pl-3 py-1">
              <div className="text-xs font-mono tracking-widest text-ind-purple mb-1">
                MONTH IN REVIEW
              </div>
              <p className="text-xs text-ink-lo leading-relaxed italic">{month.summary}</p>
            </div>
          )}

          {/* Current month: show week cards */}
          {month.isCurrent ? (
            <div className="space-y-2">
              {month.weeks.map((week) => (
                <WeekCard key={week.weekNum} week={week} />
              ))}
            </div>
          ) : (
            /* Past month: week breakdown + days */
            <div className="space-y-2">
              {month.weeks.length > 1 ? (
                month.weeks.map((week) => <WeekCard key={week.weekNum} week={week} />)
              ) : (
                <DayList entries={month.entries} />
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function TimelineTab() {
  const request = useAsyncData("action-timeline", fetchTimeline);
  const data = request.data;
  const loading = request.loading;
  const fetchError = request.error !== null;

  const eventsByMonth = useMemo(() => {
    if (!data?.upcomingEvents) return {} as Record<number, UpcomingEvent[]>;
    const map: Record<number, UpcomingEvent[]> = {};
    for (const ev of data.upcomingEvents) {
      const m = parseInt(ev.date.slice(5, 7), 10);
      (map[m] ??= []).push(ev);
    }
    return map;
  }, [data]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="text-ind-purple font-mono text-xs tracking-widest animate-pulse">
          LOADING TIMELINE...
        </div>
      </div>
    );
  }

  if (fetchError) {
    return (
      <div className="panel max-w-lg mx-auto p-8 text-center space-y-4" role="alert">
        <div className="font-mono text-sm text-signal-red">CONNECTION ERROR</div>
        <p className="text-ink-lo text-base">Could not load timeline data.</p>
      </div>
    );
  }

  // Every list is normalized once, here, rather than defended at each of its
  // dozen use sites. A payload missing `months` used to reach `.some()` and
  // white-screen the whole tab; a partial answer should render the part that
  // did arrive, and an empty one should say so.
  const months = data?.months ?? [];
  const monitors = data?.monitors ?? [];
  const topThemes = data?.topThemes ?? [];
  const upcomingEvents = data?.upcomingEvents ?? [];

  if (!data || (!data.totalDays && months.length === 0 && upcomingEvents.length === 0)) {
    return (
      <div className="panel max-w-lg mx-auto p-8 text-center space-y-4">
        <div className="font-mono text-sm text-ind-purple">NO TIMELINE DATA YET</div>
        <p className="text-ink-lo text-base">
          The timeline builds automatically as issues are tracked each day. Check back as the year
          progresses.
        </p>
      </div>
    );
  }

  const isYearComplete = !months.some((m) => m.isCurrent);
  const currentMonthData = months.find((m) => m.isCurrent);
  const pastMonths = months.filter((m) => !m.isCurrent);

  // Future months with only events (no entries yet)
  const monthsWithEntries = new Set(months.map((m) => m.month));
  const futureMonthsFromEvents = Object.keys(eventsByMonth)
    .map(Number)
    .filter((m) => !monthsWithEntries.has(m))
    .sort((a, b) => a - b);

  return (
    <div className="space-y-6">
      {/* Year header */}
      <div className="panel border-t-2 border-t-ind-purple/50 p-5 sm:p-6 text-center">
        <h2 className="font-display font-semibold text-xl sm:text-2xl text-ind-purple mb-2">
          {isYearComplete ? `${data.year} YEAR IN REVIEW` : `${data.year} — IN PROGRESS`}
        </h2>
        <p className="text-ink-lo text-base mb-4">
          {data.totalDays} day{data.totalDays !== 1 ? "s" : ""} tracked
          {monitors.length > 0 &&
            ` · ${monitors.length} ongoing monitor${monitors.length !== 1 ? "s" : ""}`}
          {upcomingEvents.length > 0 &&
            ` · ${upcomingEvents.length} upcoming event${upcomingEvents.length !== 1 ? "s" : ""}`}
        </p>
        {topThemes.length > 0 && (
          <div className="flex items-center justify-center gap-2 flex-wrap">
            <span className="font-mono text-xs tracking-widest text-ink-min">TOP THEMES</span>
            {topThemes.slice(0, 6).map((t) => (
              <span
                key={t.area}
                className="text-xs font-mono px-2 py-0.5 border border-ind-purple/30 text-ind-purple bg-ind-purple/5"
              >
                {t.area} ({t.count})
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Year summary (if complete year) */}
      {data.yearSummary && (
        <div className="panel border-l-4 border-l-ind-purple/50 p-5">
          <div className="text-xs font-mono tracking-widest text-ind-purple mb-2">
            YEAR IN REVIEW — {data.year}
          </div>
          <p className="text-base text-ink leading-relaxed italic">{data.yearSummary.summary}</p>
          {(data.yearSummary.topAreas?.length ?? 0) > 0 && (
            <div className="flex gap-2 flex-wrap mt-3">
              {data.yearSummary.topAreas!.map((a) => (
                <span
                  key={a}
                  className="text-xs font-mono px-1.5 py-0.5 border border-ind-purple/20 text-ind-purple"
                >
                  {a}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Upcoming events */}
      {upcomingEvents.length > 0 && (
        <div className="space-y-3">
          <h3 className="font-mono text-xs tracking-widest text-ink-lo text-center">
            UPCOMING EVENTS
          </h3>
          {upcomingEvents.map((event) => (
            <EventCard key={event.date + event.category} event={event} />
          ))}
        </div>
      )}

      {/* Current month (expanded by default) */}
      {currentMonthData && (
        <MonthCard month={currentMonthData} defaultExpanded={true} eventsByMonth={eventsByMonth} />
      )}

      {/* Past months (collapsed by default) */}
      {pastMonths.length > 0 && (
        <div className="space-y-3">
          <h3 className="font-mono text-xs tracking-widest text-ink-min text-center">
            EARLIER THIS YEAR
          </h3>
          {pastMonths.map((month) => (
            <MonthCard
              key={month.month}
              month={month}
              defaultExpanded={false}
              eventsByMonth={eventsByMonth}
            />
          ))}
        </div>
      )}

      {/* Future months with only events */}
      {futureMonthsFromEvents.length > 0 && (
        <div className="space-y-3">
          {futureMonthsFromEvents.map((monthNum) => {
            const monthEvents = eventsByMonth[monthNum];
            return (
              <div key={monthNum} className="panel">
                <details>
                  <summary className="w-full text-left p-4 sm:p-5 flex items-center justify-between cursor-pointer list-none">
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-sm text-ind-purple">
                        {MONTH_NAMES[monthNum]}
                      </span>
                      <span className="text-xs font-mono text-ink-lo">
                        {monthEvents.length} event{monthEvents.length !== 1 ? "s" : ""}
                      </span>
                    </div>
                    <span className="text-ink-min font-mono text-base leading-none">+</span>
                  </summary>
                  <div className="px-4 sm:px-5 pb-4 sm:pb-5 border-t border-white/[0.07] pt-4 space-y-2">
                    {monthEvents.map((ev) => {
                      const style = EVENT_STYLES[ev.category] ?? EVENT_STYLES.congress;
                      return (
                        <Link
                          key={ev.date + ev.category}
                          href={ev.link}
                          className={`block border-l-2 ${style.border} pl-3 py-2 hover:bg-white/[0.02] transition-colors`}
                        >
                          <div className="flex items-center gap-2 mb-0.5">
                            <span className={`w-2 h-2 ${style.dot}`} aria-hidden="true" />
                            <span className="text-xs text-ink-min font-mono">{ev.date}</span>
                            <span
                              className={`text-xs font-mono px-1.5 py-0.5 border ${style.badge}`}
                            >
                              {style.badgeText}
                            </span>
                          </div>
                          <span className="text-sm text-ink-hi font-medium">{ev.title}</span>
                          <p className="text-xs text-ink-lo mt-1">{ev.description}</p>
                        </Link>
                      );
                    })}
                  </div>
                </details>
              </div>
            );
          })}
        </div>
      )}

      {/* Monitors */}
      {monitors.length > 0 && (
        <div className="panel p-4 sm:p-5">
          <h3 className="font-mono text-sm text-signal-amber mb-3">
            {">"} ONGOING MONITORS ({data.year})
          </h3>
          <div className="space-y-2">
            {monitors.map((m) => (
              <Link
                key={m.slug}
                href={ACTION_CENTER_MONITORS_HREF}
                className="flex items-center justify-between text-sm hover:bg-white/[0.02] transition-colors px-2 py-1.5 -mx-2"
              >
                <span className="text-ink">{m.title}</span>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-xs text-ink-min">
                    {m.updateCount} update{m.updateCount !== 1 ? "s" : ""}
                  </span>
                  <span
                    className={`w-2 h-2 ${m.status === "active" ? "bg-phos" : "bg-signal-amber/10"}`}
                    aria-hidden="true"
                  />
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
