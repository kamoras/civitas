"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { fetchTimeline } from "@/lib/api";
import { safeHref } from "@/lib/formatting";
import type { TimelineResponse, UpcomingEvent } from "@/lib/api";

const INITIAL_VISIBLE = 7;

const EVENT_STYLES: Record<string, { border: string; dot: string; badge: string; badgeText: string }> = {
  election:  { border: "border-red-400/30", dot: "bg-red-400",     badge: "border-red-400/40 text-red-400/90 bg-red-400/10",    badgeText: "ELECTION" },
  scotus:    { border: "border-blue-400/30", dot: "bg-blue-400",   badge: "border-blue-400/40 text-blue-400/90 bg-blue-400/10",  badgeText: "SCOTUS" },
  congress:  { border: "border-emerald-400/30", dot: "bg-emerald-400", badge: "border-emerald-400/40 text-emerald-400/90 bg-emerald-400/10", badgeText: "CONGRESS" },
  executive: { border: "border-amber-400/30", dot: "bg-amber-400", badge: "border-amber-400/40 text-amber-400/90 bg-amber-400/10", badgeText: "EXECUTIVE" },
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
    <div className={`terminal-window border-l-4 ${style.border} p-4 sm:p-5`}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            <span className={`text-[10px] font-pixel px-2 py-0.5 border ${style.badge}`}>
              {style.badgeText}
            </span>
            <span className="text-[10px] text-matrix-green/40 font-pixel">
              {event.date}
            </span>
          </div>
          <h4 className="font-pixel text-sm text-matrix-green leading-relaxed mb-1">
            {event.title}
          </h4>
          <p className="text-xs text-matrix-green/60 leading-relaxed mb-3">
            {event.description}
          </p>
          <Link
            href={event.link}
            className="text-[10px] font-pixel text-neon-cyan/70 hover:text-neon-cyan transition-colors"
          >
            {">"} {event.linkLabel.toUpperCase()} →
          </Link>
        </div>
        <div className="text-right shrink-0">
          <div className="font-pixel text-2xl sm:text-3xl text-matrix-green/90">
            {days}
          </div>
          <div className="text-[10px] font-pixel text-matrix-green/40">
            DAY{days !== 1 ? "S" : ""} AWAY
          </div>
        </div>
      </div>
    </div>
  );
}

export default function TimelineTab() {
  const [data, setData] = useState<TimelineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(false);
  const [expandedMonth, setExpandedMonth] = useState<number | null>(null);
  const [showAll, setShowAll] = useState<Record<number, boolean>>({});

  useEffect(() => {
    fetchTimeline()
      .then((d) => {
        setData(d);
        if (d.months.length > 0) {
          setExpandedMonth(d.months[d.months.length - 1].month);
        }
      })
      .catch(() => setFetchError(true))
      .finally(() => setLoading(false));
  }, []);

  const toggleShowAll = useCallback((month: number) => {
    setShowAll((prev) => ({ ...prev, [month]: !prev[month] }));
  }, []);

  const eventsByMonth = useMemo(() => {
    if (!data?.upcomingEvents) return {};
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
        <div className="text-purple-400 animate-pulse font-pixel text-sm">
          {">"} LOADING TIMELINE...
        </div>
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
        <p className="text-matrix-green/50 text-sm">
          The timeline builds automatically as issues are tracked each day.
          Check back as the year progresses.
        </p>
      </div>
    );
  }

  const isYearComplete = data.currentMonth === 12;
  const upcomingEvents = data.upcomingEvents ?? [];

  const monthsWithEntries = new Set(data.months.map((m) => m.month));
  const futureMonthsFromEvents = Object.keys(eventsByMonth)
    .map(Number)
    .filter((m) => !monthsWithEntries.has(m))
    .sort((a, b) => a - b);

  return (
    <div className="space-y-6">
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
            <span className="font-pixel text-[10px] text-matrix-green/40">TOP THEMES:</span>
            {data.topThemes.slice(0, 6).map((t) => (
              <span
                key={t.area}
                className="text-[10px] font-pixel px-2 py-0.5 border border-purple-400/30 text-purple-400/70 bg-purple-400/5"
              >
                {t.area} ({t.count})
              </span>
            ))}
          </div>
        )}
      </div>

      {upcomingEvents.length > 0 && (
        <div className="space-y-3">
          <h3 className="font-pixel text-[10px] text-neon-cyan/60 tracking-widest text-center">
            UPCOMING EVENTS
          </h3>
          {upcomingEvents.map((event) => (
            <EventCard key={event.date + event.category} event={event} />
          ))}
        </div>
      )}

      <div className="space-y-3">
        {data.months.map((month) => {
          const isExpanded = expandedMonth === month.month;
          const monthEvents = eventsByMonth[month.month] ?? [];
          return (
            <div key={month.month} className="terminal-window">
              <button
                onClick={() => setExpandedMonth(isExpanded ? null : month.month)}
                className="w-full text-left p-4 sm:p-5 flex items-center justify-between"
                aria-expanded={isExpanded}
              >
                <div className="flex items-center gap-3">
                  <span className="font-pixel text-sm text-purple-400">
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
                  {month.topThemes.length > 0 && (
                    <div className="hidden sm:flex items-center gap-1.5">
                      {month.topThemes.slice(0, 3).map(([area]) => (
                        <span
                          key={area}
                          className="text-[10px] font-pixel px-1.5 py-0.5 border border-matrix-green/15 text-matrix-green/40"
                        >
                          {area}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <span className="text-matrix-green/40 font-pixel text-sm" aria-hidden="true">
                  {isExpanded ? "[-]" : "[+]"}
                </span>
              </button>

              {isExpanded && (() => {
                const expanded = showAll[month.month];
                const visible = expanded
                  ? month.entries
                  : month.entries.slice(0, INITIAL_VISIBLE);
                const remaining = month.entries.length - INITIAL_VISIBLE;

                return (
                  <div className="px-4 sm:px-5 pb-4 sm:pb-5 border-t border-matrix-green/10 pt-4 space-y-4">
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
                                <span className={`w-2 h-2 rounded-full ${style.dot}`} aria-hidden="true" />
                                <span className="text-[10px] text-matrix-green/40 font-pixel">{ev.date}</span>
                                <span className={`text-[10px] font-pixel px-1.5 py-0.5 border ${style.badge}`}>
                                  {style.badgeText}
                                </span>
                              </div>
                              <span className="text-sm text-matrix-green/90 font-medium">{ev.title}</span>
                            </Link>
                          );
                        })}
                      </div>
                    )}
                    <div className="relative pl-4 border-l border-purple-400/20 space-y-3">
                      {visible.map((entry) => (
                        <div key={entry.date} className="relative group">
                          <div
                            className="absolute -left-[21px] top-1.5 w-2.5 h-2.5 rounded-full bg-purple-400/40 border border-purple-400/60"
                            aria-hidden="true"
                          />
                          <Link
                            href={`/action?date=${entry.date}`}
                            className="block hover:bg-white/[0.02] transition-colors rounded px-1 -mx-1 py-0.5"
                          >
                            <div className="flex items-center gap-2 mb-1">
                              <span className="text-[10px] text-matrix-green/40 font-pixel">
                                {entry.date}
                              </span>
                              {entry.policyAreas.slice(0, 2).map((area) => (
                                <span
                                  key={area}
                                  className="text-[10px] font-pixel px-1.5 py-0.5 border border-neon-yellow/20 text-neon-yellow/50"
                                >
                                  {area}
                                </span>
                              ))}
                            </div>
                            <p className="text-sm text-matrix-green/90 group-hover:text-matrix-green font-medium leading-relaxed">
                              {entry.title}
                            </p>
                            <p className="text-xs text-matrix-green/50 leading-relaxed mt-1">
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
                                className="text-[10px] text-neon-cyan/50 hover:text-neon-cyan transition-colors"
                              >
                                {entry.sourceName || "Source"} <span aria-hidden="true">↗</span>
                              </a>
                            )}
                            {entry.monitorSlug && (
                              <Link
                                href="/action?tab=monitors"
                                className="text-[10px] font-pixel text-amber-400/50 hover:text-amber-400 transition-colors"
                              >
                                ● MONITORED
                              </Link>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                    {remaining > 0 && (
                      <button
                        onClick={() => toggleShowAll(month.month)}
                        className="w-full text-center text-[10px] font-pixel py-2 border border-purple-400/20 text-purple-400/60 hover:text-purple-400 hover:border-purple-400/40 transition-colors"
                      >
                        {expanded
                          ? "▲ SHOW LESS"
                          : `▼ SHOW ${remaining} MORE DAY${remaining !== 1 ? "S" : ""}`}
                      </button>
                    )}
                  </div>
                );
              })()}
            </div>
          );
        })}

        {futureMonthsFromEvents.map((monthNum) => {
          const monthEvents = eventsByMonth[monthNum];
          const monthName = [
            "", "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
            "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
          ][monthNum];
          const isExpanded = expandedMonth === monthNum;

          return (
            <div key={monthNum} className="terminal-window">
              <button
                onClick={() => setExpandedMonth(isExpanded ? null : monthNum)}
                className="w-full text-left p-4 sm:p-5 flex items-center justify-between"
                aria-expanded={isExpanded}
              >
                <div className="flex items-center gap-3">
                  <span className="font-pixel text-sm text-purple-400/50">
                    {monthName}
                  </span>
                  <span className="text-[10px] font-pixel text-neon-cyan/60">
                    {monthEvents.length} event{monthEvents.length !== 1 ? "s" : ""}
                  </span>
                </div>
                <span className="text-matrix-green/40 font-pixel text-sm" aria-hidden="true">
                  {isExpanded ? "[-]" : "[+]"}
                </span>
              </button>
              {isExpanded && (
                <div className="px-4 sm:px-5 pb-4 sm:pb-5 border-t border-matrix-green/10 pt-4 space-y-2">
                  {monthEvents.map((ev) => {
                    const style = EVENT_STYLES[ev.category] ?? EVENT_STYLES.congress;
                    return (
                      <Link
                        key={ev.date + ev.category}
                        href={ev.link}
                        className={`block border-l-2 ${style.border} pl-3 py-2 hover:bg-white/[0.02] transition-colors`}
                      >
                        <div className="flex items-center gap-2 mb-0.5">
                          <span className={`w-2 h-2 rounded-full ${style.dot}`} aria-hidden="true" />
                          <span className="text-[10px] text-matrix-green/40 font-pixel">{ev.date}</span>
                          <span className={`text-[10px] font-pixel px-1.5 py-0.5 border ${style.badge}`}>
                            {style.badgeText}
                          </span>
                        </div>
                        <span className="text-sm text-matrix-green/90 font-medium">{ev.title}</span>
                        <p className="text-xs text-matrix-green/50 mt-1">{ev.description}</p>
                      </Link>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {data.monitors.length > 0 && (
        <div className="terminal-window p-4 sm:p-5">
          <h3 className="font-pixel text-sm text-amber-400/80 mb-3">
            {">"} ONGOING MONITORS ({data.year})
          </h3>
          <div className="space-y-2">
            {data.monitors.map((m) => (
              <Link
                key={m.slug}
                href="/action?tab=monitors"
                className="flex items-center justify-between text-sm hover:bg-white/[0.02] transition-colors px-2 py-1.5 -mx-2 rounded"
              >
                <span className="text-matrix-green/70 group-hover:text-matrix-green">{m.title}</span>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-[10px] text-matrix-green/40">
                    {m.updateCount} update{m.updateCount !== 1 ? "s" : ""}
                  </span>
                  <span
                    className={`w-2 h-2 rounded-full ${
                      m.status === "active" ? "bg-green-400" : "bg-amber-400/50"
                    }`}
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
