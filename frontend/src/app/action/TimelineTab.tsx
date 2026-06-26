"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { fetchTimeline } from "@/lib/api";
import { safeHref } from "@/lib/formatting";
import type { TimelineResponse, TimelineEntry, TimelineWeek, TimelineMonth, UpcomingEvent } from "@/lib/api";

const EVENT_STYLES: Record<string, { border: string; dot: string; badge: string; badgeText: string }> = {
  election:  { border: "border-red-400/30",     dot: "bg-red-400",     badge: "border-red-400/40 text-red-400/90 bg-red-400/10",     badgeText: "ELECTION" },
  scotus:    { border: "border-blue-400/30",    dot: "bg-blue-400",    badge: "border-blue-400/40 text-blue-400/90 bg-blue-400/10",   badgeText: "SCOTUS" },
  congress:  { border: "border-emerald-400/30", dot: "bg-emerald-400", badge: "border-emerald-400/40 text-emerald-400/90 bg-emerald-400/10", badgeText: "CONGRESS" },
  executive: { border: "border-amber-400/30",   dot: "bg-amber-400",   badge: "border-amber-400/40 text-amber-400/90 bg-amber-400/10", badgeText: "EXECUTIVE" },
};

function daysUntil(dateStr: string): number {
  const target = new Date(dateStr + "T00:00:00");
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - now.getTime()) / 86_400_000);
}

function formatWeekRange(startDate: string, endDate: string): string {
  const start = new Date(startDate + "T00:00:00");
  const end = new Date(endDate + "T00:00:00");
  const startFmt = start.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const endFmt = end.toLocaleDateString("en-US", { day: "numeric", year: "numeric" });
  return `${startFmt}–${endFmt}`;
}

function EventCard({ event }: { event: UpcomingEvent }) {
  const days = daysUntil(event.date);
  const style = EVENT_STYLES[event.category] ?? EVENT_STYLES.congress;
  return (
    <div className={`terminal-window border-l-4 ${style.border} p-4 sm:p-5`}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            <span className={`text-[10px] font-pixel px-2 py-0.5 border ${style.badge}`}>{style.badgeText}</span>
            <span className="text-[10px] text-matrix-green/40 font-pixel">{event.date}</span>
          </div>
          <h4 className="font-pixel text-sm text-matrix-green leading-relaxed mb-1">{event.title}</h4>
          <p className="text-xs text-matrix-green/60 leading-relaxed mb-3">{event.description}</p>
          <Link href={event.link} className="text-[10px] font-mono tracking-widest text-neon-cyan/60 hover:text-neon-cyan transition-colors">
            {event.linkLabel.toUpperCase()} →
          </Link>
        </div>
        <div className="text-right shrink-0">
          <div className="font-pixel text-2xl sm:text-3xl text-matrix-green/90">{days}</div>
          <div className="text-[10px] font-pixel text-matrix-green/40">DAY{days !== 1 ? "S" : ""} AWAY</div>
        </div>
      </div>
    </div>
  );
}

function DayEntry({ entry }: { entry: TimelineEntry }) {
  return (
    <div className="relative group">
      <div className="absolute -left-[21px] top-1.5 w-2.5 h-2.5 rounded-full bg-purple-400/40 border border-purple-400/60" aria-hidden="true" />
      <Link href={`/action?date=${entry.date}`} className="block hover:bg-white/[0.02] transition-colors rounded px-1 -mx-1 py-0.5">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[10px] text-matrix-green/40 font-pixel">{entry.date}</span>
          {entry.policyAreas.slice(0, 2).map((area) => (
            <span key={area} className="text-[10px] font-pixel px-1.5 py-0.5 border border-neon-yellow/20 text-neon-yellow/50">
              {area}
            </span>
          ))}
        </div>
        <p className="text-sm text-matrix-green/90 group-hover:text-matrix-green font-medium leading-relaxed">{entry.title}</p>
        <p className="text-xs text-matrix-green/50 leading-relaxed mt-1">
          {entry.summary.slice(0, 200)}{entry.summary.length > 200 ? "…" : ""}
        </p>
      </Link>
      <div className="flex items-center gap-3 mt-1 px-1">
        {entry.sourceUrl && (
          <a href={safeHref(entry.sourceUrl) || "#"} target="_blank" rel="noopener noreferrer"
             className="text-[10px] text-neon-cyan/50 hover:text-neon-cyan transition-colors">
            {entry.sourceName || "Source"} <span aria-hidden="true">↗</span>
          </a>
        )}
        {entry.monitorSlug && (
          <Link href="/action?tab=monitors" className="text-[10px] font-pixel text-amber-400/50 hover:text-amber-400 transition-colors">
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
    <div className="relative pl-4 border-l border-purple-400/20 space-y-3">
      {visible.map((e) => <DayEntry key={e.date} entry={e} />)}
      {remaining > 0 && (
        <button
          onClick={() => setShowAll((v) => !v)}
          className="w-full text-center text-[10px] font-pixel py-2 border border-purple-400/20 text-purple-400/60 hover:text-purple-400 hover:border-purple-400/40 transition-colors"
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
    <div className="border border-matrix-green/10 rounded">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left px-3 py-2.5 flex items-center justify-between hover:bg-white/[0.01] transition-colors"
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-2.5 flex-wrap">
          <span className={`font-pixel text-xs ${week.isCurrent ? "text-neon-cyan/80" : "text-matrix-green/60"}`}>
            {label}
          </span>
          <span className="text-[10px] text-matrix-green/30">{week.entryCount} day{week.entryCount !== 1 ? "s" : ""}</span>
          {week.topAreas.slice(0, 3).map((a) => (
            <span key={a} className="hidden sm:inline text-[10px] font-pixel px-1.5 py-0.5 border border-matrix-green/15 text-matrix-green/35">
              {a}
            </span>
          ))}
        </div>
        <span className="text-matrix-green/30 font-mono text-base leading-none shrink-0">{expanded ? "−" : "+"}</span>
      </button>

      {expanded && (
        <div className="px-3 pb-3 border-t border-matrix-green/10 pt-3 space-y-3">
          {week.summary && !week.isCurrent && (
            <div className="border-l-2 border-purple-400/30 pl-3 py-1">
              <div className="text-[10px] font-mono tracking-widest text-purple-400/40 mb-1">WEEK IN REVIEW</div>
              <p className="text-xs text-matrix-green/60 leading-relaxed italic">{week.summary}</p>
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
    <div className="terminal-window">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left p-4 sm:p-5 flex items-center justify-between"
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-3 flex-wrap">
          <span className={`font-pixel text-sm ${month.isCurrent ? "text-purple-400" : "text-purple-400/70"}`}>
            {month.name.toUpperCase()}
          </span>
          <span className="text-[10px] text-matrix-green/40">
            {month.entries.length} day{month.entries.length !== 1 ? "s" : ""}
          </span>
          {monthEvents.length > 0 && (
            <span className="text-[10px] font-pixel text-neon-cyan/60">
              +{monthEvents.length} event{monthEvents.length !== 1 ? "s" : ""}
            </span>
          )}
          {!month.isCurrent && month.topAreas.slice(0, 3).map((a) => (
            <span key={a} className="hidden sm:inline text-[10px] font-pixel px-1.5 py-0.5 border border-matrix-green/15 text-matrix-green/40">
              {a}
            </span>
          ))}
        </div>
        <span className="text-matrix-green/40 font-mono text-base leading-none" aria-hidden="true">{expanded ? "−" : "+"}</span>
      </button>

      {expanded && (
        <div className="px-4 sm:px-5 pb-4 sm:pb-5 border-t border-matrix-green/10 pt-4 space-y-4">
          {monthEvents.length > 0 && (
            <div className="space-y-2">
              {monthEvents.map((ev) => {
                const style = EVENT_STYLES[ev.category] ?? EVENT_STYLES.congress;
                return (
                  <Link key={ev.date + ev.category} href={ev.link}
                    className={`block border-l-2 ${style.border} pl-3 py-2 hover:bg-white/[0.02] transition-colors`}>
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className={`w-2 h-2 rounded-full ${style.dot}`} aria-hidden="true" />
                      <span className="text-[10px] text-matrix-green/40 font-pixel">{ev.date}</span>
                      <span className={`text-[10px] font-pixel px-1.5 py-0.5 border ${style.badge}`}>{style.badgeText}</span>
                    </div>
                    <span className="text-sm text-matrix-green/90 font-medium">{ev.title}</span>
                  </Link>
                );
              })}
            </div>
          )}

          {/* Past month: show LLM summary + week breakdown */}
          {!month.isCurrent && month.summary && (
            <div className="border-l-2 border-purple-400/30 pl-3 py-1">
              <div className="text-[10px] font-mono tracking-widest text-purple-400/40 mb-1">MONTH IN REVIEW</div>
              <p className="text-xs text-matrix-green/60 leading-relaxed italic">{month.summary}</p>
            </div>
          )}

          {/* Current month: show week cards */}
          {month.isCurrent ? (
            <div className="space-y-2">
              {month.weeks.map((week) => <WeekCard key={week.weekNum} week={week} />)}
            </div>
          ) : (
            /* Past month: week breakdown + days */
            <div className="space-y-2">
              {month.weeks.length > 1
                ? month.weeks.map((week) => <WeekCard key={week.weekNum} week={week} />)
                : <DayList entries={month.entries} />
              }
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function TimelineTab() {
  const [data, setData] = useState<TimelineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(false);

  useEffect(() => {
    fetchTimeline()
      .then(setData)
      .catch(() => setFetchError(true))
      .finally(() => setLoading(false));
  }, []);

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
        <div className="text-purple-400/50 font-mono text-xs tracking-widest animate-pulse">LOADING TIMELINE...</div>
      </div>
    );
  }

  if (fetchError) {
    return (
      <div className="terminal-window max-w-lg mx-auto p-8 text-center space-y-4" role="alert">
        <div className="font-pixel text-sm text-red-400">CONNECTION ERROR</div>
        <p className="text-matrix-green/50 text-sm">Could not load timeline data.</p>
      </div>
    );
  }

  if (!data || (data.totalDays === 0 && (!data.upcomingEvents || data.upcomingEvents.length === 0))) {
    return (
      <div className="terminal-window max-w-lg mx-auto p-8 text-center space-y-4">
        <div className="font-pixel text-sm text-purple-400/80">NO TIMELINE DATA YET</div>
        <p className="text-matrix-green/50 text-sm">The timeline builds automatically as issues are tracked each day. Check back as the year progresses.</p>
      </div>
    );
  }

  const isYearComplete = !data.months.some((m) => m.isCurrent);
  const upcomingEvents = data.upcomingEvents ?? [];
  const currentMonthData = data.months.find((m) => m.isCurrent);
  const pastMonths = data.months.filter((m) => !m.isCurrent);

  // Future months with only events (no entries yet)
  const monthsWithEntries = new Set(data.months.map((m) => m.month));
  const futureMonthsFromEvents = Object.keys(eventsByMonth)
    .map(Number)
    .filter((m) => !monthsWithEntries.has(m))
    .sort((a, b) => a - b);

  const MONTH_NAMES = ["", "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"];

  return (
    <div className="space-y-6">
      {/* Year header */}
      <div className="terminal-window border-t-2 border-t-purple-400/50 p-5 sm:p-6 text-center">
        <h2 className="font-pixel text-xl sm:text-2xl text-purple-400 mb-2">
          {isYearComplete ? `${data.year} YEAR IN REVIEW` : `${data.year} — IN PROGRESS`}
        </h2>
        <p className="text-matrix-green/50 text-sm mb-4">
          {data.totalDays} day{data.totalDays !== 1 ? "s" : ""} tracked
          {data.monitors.length > 0 && ` · ${data.monitors.length} ongoing monitor${data.monitors.length !== 1 ? "s" : ""}`}
          {upcomingEvents.length > 0 && ` · ${upcomingEvents.length} upcoming event${upcomingEvents.length !== 1 ? "s" : ""}`}
        </p>
        {data.topThemes.length > 0 && (
          <div className="flex items-center justify-center gap-2 flex-wrap">
            <span className="font-mono text-[10px] tracking-widest text-matrix-green/35">TOP THEMES</span>
            {data.topThemes.slice(0, 6).map((t) => (
              <span key={t.area} className="text-[10px] font-pixel px-2 py-0.5 border border-purple-400/30 text-purple-400/70 bg-purple-400/5">
                {t.area} ({t.count})
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Year summary (if complete year) */}
      {data.yearSummary && (
        <div className="terminal-window border-l-4 border-l-purple-400/50 p-5">
          <div className="text-[10px] font-mono tracking-widest text-purple-400/50 mb-2">YEAR IN REVIEW — {data.year}</div>
          <p className="text-sm text-matrix-green/70 leading-relaxed italic">{data.yearSummary.summary}</p>
          {data.yearSummary.topAreas.length > 0 && (
            <div className="flex gap-2 flex-wrap mt-3">
              {data.yearSummary.topAreas.map((a) => (
                <span key={a} className="text-[10px] font-pixel px-1.5 py-0.5 border border-purple-400/20 text-purple-400/50">{a}</span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Upcoming events */}
      {upcomingEvents.length > 0 && (
        <div className="space-y-3">
          <h3 className="font-mono text-[10px] tracking-widest text-neon-cyan/50 text-center">UPCOMING EVENTS</h3>
          {upcomingEvents.map((event) => <EventCard key={event.date + event.category} event={event} />)}
        </div>
      )}

      {/* Current month (expanded by default) */}
      {currentMonthData && (
        <MonthCard month={currentMonthData} defaultExpanded={true} eventsByMonth={eventsByMonth} />
      )}

      {/* Past months (collapsed by default) */}
      {pastMonths.length > 0 && (
        <div className="space-y-3">
          <h3 className="font-mono text-[10px] tracking-widest text-matrix-green/25 text-center">EARLIER THIS YEAR</h3>
          {pastMonths.map((month) => (
            <MonthCard key={month.month} month={month} defaultExpanded={false} eventsByMonth={eventsByMonth} />
          ))}
        </div>
      )}

      {/* Future months with only events */}
      {futureMonthsFromEvents.length > 0 && (
        <div className="space-y-3">
          {futureMonthsFromEvents.map((monthNum) => {
            const monthEvents = eventsByMonth[monthNum];
            return (
              <div key={monthNum} className="terminal-window">
                <details>
                  <summary className="w-full text-left p-4 sm:p-5 flex items-center justify-between cursor-pointer list-none">
                    <div className="flex items-center gap-3">
                      <span className="font-pixel text-sm text-purple-400/50">{MONTH_NAMES[monthNum]}</span>
                      <span className="text-[10px] font-pixel text-neon-cyan/60">{monthEvents.length} event{monthEvents.length !== 1 ? "s" : ""}</span>
                    </div>
                    <span className="text-matrix-green/40 font-mono text-base leading-none">+</span>
                  </summary>
                  <div className="px-4 sm:px-5 pb-4 sm:pb-5 border-t border-matrix-green/10 pt-4 space-y-2">
                    {monthEvents.map((ev) => {
                      const style = EVENT_STYLES[ev.category] ?? EVENT_STYLES.congress;
                      return (
                        <Link key={ev.date + ev.category} href={ev.link}
                          className={`block border-l-2 ${style.border} pl-3 py-2 hover:bg-white/[0.02] transition-colors`}>
                          <div className="flex items-center gap-2 mb-0.5">
                            <span className={`w-2 h-2 rounded-full ${style.dot}`} aria-hidden="true" />
                            <span className="text-[10px] text-matrix-green/40 font-pixel">{ev.date}</span>
                            <span className={`text-[10px] font-pixel px-1.5 py-0.5 border ${style.badge}`}>{style.badgeText}</span>
                          </div>
                          <span className="text-sm text-matrix-green/90 font-medium">{ev.title}</span>
                          <p className="text-xs text-matrix-green/50 mt-1">{ev.description}</p>
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
      {data.monitors.length > 0 && (
        <div className="terminal-window p-4 sm:p-5">
          <h3 className="font-pixel text-sm text-amber-400/80 mb-3">{">"} ONGOING MONITORS ({data.year})</h3>
          <div className="space-y-2">
            {data.monitors.map((m) => (
              <Link key={m.slug} href="/action?tab=monitors"
                className="flex items-center justify-between text-sm hover:bg-white/[0.02] transition-colors px-2 py-1.5 -mx-2 rounded">
                <span className="text-matrix-green/70">{m.title}</span>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-[10px] text-matrix-green/40">{m.updateCount} update{m.updateCount !== 1 ? "s" : ""}</span>
                  <span className={`w-2 h-2 rounded-full ${m.status === "active" ? "bg-green-400" : "bg-amber-400/50"}`} aria-hidden="true" />
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
