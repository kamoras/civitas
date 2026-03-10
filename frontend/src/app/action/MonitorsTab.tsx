"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchMonitors, fetchMonitorDetail } from "@/lib/api";
import { safeHref } from "@/lib/formatting";
import type { MonitorUpdate, NationalMonitor, NationalMonitorDetail } from "@/lib/api";

const RECENT_THRESHOLD_MS = 60 * 60 * 1000; // 1 hour

function formatUpdateTime(update: MonitorUpdate): { timeLabel: string; isRecent: boolean } {
  if (!update.createdAt) {
    return { timeLabel: update.date, isRecent: false };
  }
  const created = new Date(update.createdAt);
  if (isNaN(created.getTime())) {
    return { timeLabel: update.date, isRecent: false };
  }
  const now = Date.now();
  const ageMs = now - created.getTime();
  const isRecent = ageMs >= 0 && ageMs < RECENT_THRESHOLD_MS;

  const timeStr = created.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
  const dateStr = created.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });

  const isToday = new Date().toDateString() === created.toDateString();
  const yesterday = new Date(now - 86_400_000);
  const isYesterday = yesterday.toDateString() === created.toDateString();

  let timeLabel: string;
  if (isToday) {
    timeLabel = `Today at ${timeStr}`;
  } else if (isYesterday) {
    timeLabel = `Yesterday at ${timeStr}`;
  } else {
    timeLabel = `${dateStr} at ${timeStr}`;
  }

  return { timeLabel, isRecent };
}

export default function MonitorsTab() {
  const [monitors, setMonitors] = useState<NationalMonitor[]>([]);
  const [selected, setSelected] = useState<NationalMonitorDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const detailRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchMonitors()
      .then((d) => setMonitors(d.monitors))
      .catch(() => setFetchError(true))
      .finally(() => setLoading(false));
  }, []);

  const openMonitor = useCallback((slug: string) => {
    setDetailLoading(true);
    fetchMonitorDetail(slug)
      .then((d) => {
        setSelected(d);
        setTimeout(() => {
          detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        }, 100);
      })
      .catch(() => {})
      .finally(() => setDetailLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="text-amber-400 animate-pulse font-pixel text-sm">
          {">"} SCANNING NATIONAL CONCERNS...
        </div>
      </div>
    );
  }

  if (fetchError) {
    return (
      <div className="terminal-window max-w-lg mx-auto p-8 text-center space-y-4" role="alert">
        <div className="font-pixel text-sm text-red-400">CONNECTION ERROR</div>
        <p className="text-matrix-green/50 text-sm">Could not load monitors.</p>
        <button
          onClick={() => { setFetchError(false); setLoading(true); fetchMonitors().then((d) => setMonitors(d.monitors)).catch(() => setFetchError(true)).finally(() => setLoading(false)); }}
          className="text-neon-cyan font-pixel text-sm border border-neon-cyan/30 px-4 py-2 hover:bg-neon-cyan/10 transition-colors"
        >
          [RETRY]
        </button>
      </div>
    );
  }

  if (monitors.length === 0) {
    return (
      <div className="terminal-window max-w-lg mx-auto p-8 text-center space-y-4">
        <div className="font-pixel text-sm text-amber-400/80">NO ACTIVE MONITORS</div>
        <p className="text-matrix-green/50 text-sm">
          National monitors are automatically created when an issue persists across
          multiple days in the news cycle. Check back as the system identifies
          ongoing concerns.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="text-center text-[10px] text-matrix-green/40 font-pixel mb-2">
        ONGOING NATIONAL CONCERNS — AUTO-DETECTED FROM RECURRING NEWS PATTERNS
      </div>

      <div className="grid grid-cols-1 gap-3">
        {monitors.map((m) => (
          <button
            key={m.slug}
            onClick={() => openMonitor(m.slug)}
            className={`terminal-window p-4 text-left transition-colors hover:border-amber-400/30 ${
              selected?.slug === m.slug ? "border-amber-400/50" : ""
            }`}
            aria-label={`View monitor: ${m.title}`}
          >
            <div className="flex items-center gap-2 mb-2">
              <span
                className={`w-2 h-2 rounded-full ${
                  m.status === "active" ? "bg-green-400" : "bg-amber-400/50"
                }`}
                aria-label={m.status === "active" ? "Active" : "Watching"}
              />
              <span className="font-pixel text-[10px] text-amber-400/60 uppercase">
                {m.category}
              </span>
            </div>
            <h3 className="font-pixel text-sm text-matrix-green mb-1 leading-relaxed">
              {m.title}
            </h3>
            <div className="flex items-center gap-3 text-[10px] text-matrix-green/40">
              <span>{m.updateCount} update{m.updateCount !== 1 ? "s" : ""}</span>
              {m.lastArticleDate && (
                <span>latest: {m.lastArticleDate}</span>
              )}
              <span>tracking since {m.createdAt}</span>
            </div>
          </button>
        ))}
      </div>

      {detailLoading && (
        <div className="flex items-center justify-center py-8">
          <div className="text-amber-400 animate-pulse font-pixel text-sm">
            {">"} LOADING TIMELINE...
          </div>
        </div>
      )}

      {selected && !detailLoading && (
        <div
          ref={detailRef}
          className="terminal-window border-t-2 border-t-amber-400/50 p-5 sm:p-6 scroll-mt-4"
          role="region"
          aria-label={`Monitor: ${selected.title}`}
        >
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="font-pixel text-base sm:text-lg text-amber-400">
                {selected.title}
              </h3>
              <div className="flex items-center gap-2 mt-1">
                <span
                  className={`text-[10px] font-pixel px-2 py-0.5 border ${
                    selected.status === "active"
                      ? "border-green-400/30 text-green-400/80"
                      : "border-amber-400/30 text-amber-400/60"
                  }`}
                >
                  {selected.status.toUpperCase()}
                </span>
                {selected.policyAreas.map((area) => (
                  <span
                    key={area}
                    className="text-[10px] font-pixel px-2 py-0.5 border border-neon-yellow/30 text-neon-yellow/80"
                  >
                    {area}
                  </span>
                ))}
              </div>
            </div>
            <button
              onClick={() => setSelected(null)}
              className="text-matrix-green/40 hover:text-matrix-green font-pixel text-xs"
              aria-label="Close monitor detail"
            >
              [CLOSE]
            </button>
          </div>

          <p className="text-matrix-green/70 text-sm mb-6 leading-relaxed">
            {selected.description}
          </p>

          <h4 className="font-pixel text-sm text-amber-400/80 mb-4">
            {">"} TIMELINE ({selected.updates.length} updates)
          </h4>

          <div className="relative pl-4 border-l border-amber-400/20 space-y-4" role="list">
            {selected.updates.map((update) => {
              const { timeLabel, isRecent } = formatUpdateTime(update);
              return (
                <div key={update.id} className="relative" role="listitem">
                  <div
                    className={`absolute -left-[21px] top-1 w-2.5 h-2.5 rounded-full border ${
                      isRecent
                        ? "bg-red-400 border-red-400 animate-pulse"
                        : "bg-amber-400/40 border-amber-400/60"
                    }`}
                    aria-hidden="true"
                  />
                  <div className="flex items-center gap-2 mb-1">
                    <time
                      dateTime={update.createdAt || undefined}
                      className="text-[10px] text-matrix-green/40 font-pixel"
                    >
                      {timeLabel}
                    </time>
                    {isRecent && (
                      <span className="text-[9px] font-pixel px-1.5 py-0.5 bg-red-500/20 border border-red-400/40 text-red-400 animate-pulse">
                        BREAKING
                      </span>
                    )}
                    {update.sourceName && (
                      <span className="text-[10px] text-matrix-green/30 font-pixel">
                        via {update.sourceName}
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-matrix-green/80 leading-relaxed mb-1">
                    {update.summary}
                  </p>
                  <a
                    href={safeHref(update.sourceUrl) || "#"}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[10px] text-neon-cyan/60 hover:text-neon-cyan transition-colors"
                  >
                    {update.articleTitle || "Source"} <span aria-hidden="true">↗</span>
                  </a>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
