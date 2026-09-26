"use client";

import { useEffect, useState } from "react";
import {
  fetchAdminLoadTimes,
  fetchAdminPipelineTrend,
  fetchAdminVisitorStats,
  type AdminDashboard,
  type AdminPipelineStatus,
  type HostStats,
  type LoadTimes,
  type PipelineTrendRun,
  type VisitorStatsDay,
} from "@/lib/api";
import LineChart from "./charts/LineChart";
import { SERIES } from "./charts/palette";
import { formatCompact, formatMs } from "./charts/scale";
import { formatDay, formatDuration, formatTime, statusClass } from "./format";
import { ServiceHealth } from "./SystemDashboard";
import { Panel, StatTile, UsageBar } from "./widgets";

/** The sub-dashboard a row links through to. */
export type GoToTab = (tab: "traffic" | "pipelines" | "system") => void;

function MoreLink({ onClick, children }: { onClick: () => void; children: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-xs font-mono text-ink-min hover:text-ink-lo underline underline-offset-2"
    >
      {children} →
    </button>
  );
}

/**
 * The landing view: one screen that answers "is anything wrong right now?".
 * Every figure here is a summary of a sub-dashboard, which carries the detail
 * and the history behind it.
 */
export function OverviewDashboard({
  token,
  dashboard,
  status,
  host,
  goTo,
}: {
  token: string;
  dashboard: AdminDashboard | null;
  status: AdminPipelineStatus | null;
  host: HostStats | null;
  goTo: GoToTab;
}) {
  const [visits, setVisits] = useState<VisitorStatsDay[] | null>(null);
  const [loadTimes, setLoadTimes] = useState<LoadTimes | null>(null);
  const [trend, setTrend] = useState<PipelineTrendRun[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAdminVisitorStats(token, 14)
      .then((v) => !cancelled && setVisits(v))
      .catch(() => !cancelled && setVisits([]));
    fetchAdminLoadTimes(token, 1)
      .then((l) => !cancelled && setLoadTimes(l))
      .catch(() => {});
    fetchAdminPipelineTrend(token, 7)
      .then((t) => !cancelled && setTrend(t.runs))
      .catch(() => !cancelled && setTrend([]));
    return () => {
      cancelled = true;
    };
  }, [token]);

  const today = visits?.[visits.length - 1];
  const loadToday = loadTimes?.days[loadTimes.days.length - 1]?.load;
  const finished = (trend ?? []).filter((r) => r.status !== "running");
  const failed = finished.filter((r) => r.status === "failed").length;
  const loadPct = host?.loadAvg ? Math.round((host.loadAvg[0] / host.cpuCount) * 100) : null;

  const pipelines: {
    label: string;
    running: boolean;
    run:
      | { status: string; startedAt: string | null; elapsedSeconds: number | null }
      | null
      | undefined;
  }[] = [
    { label: "Senate", running: !!status?.isRunning, run: status?.lastRun },
    { label: "House", running: !!status?.houseIsRunning, run: status?.houseLastRun },
    {
      label: "Supplementary",
      running: !!status?.supplementaryIsRunning,
      run: status?.supplementaryLastRun,
    },
    {
      label: "Stock trades",
      running: !!status?.stockTradesIsRunning,
      run: status?.stockTradesLastRun,
    },
    { label: "Election", running: !!status?.electionIsRunning, run: status?.electionLastRun },
  ];

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Visitors today"
          value={today ? today.uniqueVisitors.toLocaleString() : "—"}
          note={today ? `${today.pageViews.toLocaleString()} page views` : undefined}
        />
        <StatTile
          label="Page load p75 today"
          value={loadToday?.p75 != null ? formatMs(loadToday.p75) : "—"}
          note={`${(loadToday?.samples ?? 0).toLocaleString()} loads measured`}
        />
        <StatTile
          label="Failed runs, 7 days"
          value={trend ? failed.toLocaleString() : "—"}
          tone={failed > 0 ? "text-signal-magenta" : "text-ink-hi"}
          note={trend ? `of ${finished.length} finished` : undefined}
        />
        <StatTile
          label="Next scheduled run"
          value={<span className="text-base">{formatTime(dashboard?.pipeline.nextScheduled)}</span>}
          note={dashboard?.pipeline.cronSchedule}
        />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel
          title="Traffic, 14 days"
          actions={<MoreLink onClick={() => goTo("traffic")}>Traffic</MoreLink>}
        >
          <LineChart
            title="UNIQUE VISITORS PER DAY"
            xLabels={(visits ?? []).map((v) => formatDay(v.date))}
            series={[
              {
                key: "visitors",
                label: "Unique visitors",
                color: SERIES[0],
                values: (visits ?? []).map((v) => v.uniqueVisitors),
              },
            ]}
            formatValue={(v) => v.toLocaleString()}
            formatTick={formatCompact}
            height={130}
            emptyMessage={visits ? "No visits recorded yet." : "Loading…"}
          />
        </Panel>

        <Panel
          title="Pipelines"
          actions={<MoreLink onClick={() => goTo("pipelines")}>Pipelines</MoreLink>}
        >
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-ink-lo border-b border-white/[0.07]">
                <th scope="col" className="text-left py-1 pr-3 font-normal">
                  PIPELINE
                </th>
                <th scope="col" className="text-left py-1 pr-3 font-normal">
                  LAST RUN
                </th>
                <th scope="col" className="text-left py-1 pr-3 font-normal">
                  STATUS
                </th>
                <th scope="col" className="text-right py-1 font-normal">
                  TOOK
                </th>
              </tr>
            </thead>
            <tbody>
              {pipelines.map(({ label, running, run }) => {
                const stuck = run?.status === "running" && !running;
                return (
                  <tr key={label} className="border-b border-white/[0.04]">
                    <th scope="row" className="text-left py-1.5 pr-3 font-normal text-ink">
                      {label}
                    </th>
                    <td className="py-1.5 pr-3 text-ink-lo">{formatTime(run?.startedAt)}</td>
                    <td className="py-1.5 pr-3">
                      {running ? (
                        <span className="text-signal-cyan animate-pulse">RUNNING</span>
                      ) : run ? (
                        <span className={statusClass(run.status, stuck)}>
                          {stuck ? "STUCK" : run.status.toUpperCase()}
                        </span>
                      ) : (
                        <span className="text-ink-min">NEVER RUN</span>
                      )}
                    </td>
                    <td className="py-1.5 text-right tabular-nums text-ink-lo">
                      {running ? "—" : formatDuration(run?.elapsedSeconds)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Panel>

        <Panel
          title="Services"
          actions={<MoreLink onClick={() => goTo("system")}>System</MoreLink>}
        >
          <ServiceHealth d={dashboard} />
        </Panel>

        <Panel
          title="Host"
          live
          actions={<MoreLink onClick={() => goTo("system")}>System</MoreLink>}
        >
          {host ? (
            <div className="space-y-3 text-xs font-mono">
              {[
                {
                  label: "CPU LOAD",
                  pct: loadPct ?? 0,
                  text: loadPct != null ? `${loadPct}%` : "—",
                  warn: 75,
                  crit: 90,
                },
                {
                  label: "MEMORY",
                  pct: host.memUsedPct,
                  text: `${host.memUsedPct}%`,
                  warn: 75,
                  crit: 90,
                },
                {
                  label: "DISK",
                  pct: host.diskUsedPct,
                  text: `${host.diskUsedPct}%`,
                  warn: 80,
                  crit: 95,
                },
              ].map((m) => (
                <div key={m.label}>
                  <div className="mb-1 flex justify-between">
                    <span className="text-ink-lo tracking-wider">{m.label}</span>
                    <span className="text-ink-hi tabular-nums">{m.text}</span>
                  </div>
                  <UsageBar
                    pct={m.pct}
                    warnAt={m.warn}
                    critAt={m.crit}
                    ariaLabel={`${m.label} ${m.text}`}
                  />
                </div>
              ))}
              {host.cpuTempC != null && (
                <div className="flex justify-between">
                  <span className="text-ink-lo tracking-wider">CPU TEMP</span>
                  <span className="text-ink-hi tabular-nums">{host.cpuTempC}°C</span>
                </div>
              )}
            </div>
          ) : (
            <p className="text-xs font-mono text-ink-min">Waiting for the first reading…</p>
          )}
        </Panel>
      </div>
    </div>
  );
}
