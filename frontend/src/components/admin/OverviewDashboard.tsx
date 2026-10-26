"use client";

import { asLabel } from "@/lib/formatting";
import { useEffect, useState } from "react";
import {
  fetchAdminLoadTimes,
  fetchAdminPipelineTrend,
  fetchAdminVisitorStats,
  senateIsRunning,
  type AdminDashboard,
  type AdminPipelineStatus,
  type HostStats,
  type LoadTimes,
  type OpsAlert,
  type PipelineTrendRun,
  type VisitorStatsDay,
} from "@/lib/api";
import LineChart from "./charts/LineChart";
import { SERIES } from "./charts/palette";
import { formatCompact, formatMs } from "./charts/scale";
import { formatDay, formatDuration, formatTime, statusClass } from "./format";
import { ServiceHealth } from "./SystemDashboard";
import { formatPct } from "./useHostHistory";
import { emptyText, NotLoaded, Panel, StatTile, UsageBar } from "./widgets";

/** The sub-dashboard a row links through to. */
export type GoToTab = (tab: "alerts" | "traffic" | "pipelines" | "system") => void;

function MoreLink({ onClick, children }: { onClick: () => void; children: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-small font-mono text-ink-min hover:text-ink-lo underline underline-offset-2"
    >
      {children} →
    </button>
  );
}

/** Overview's one line about alerts: how many are open, linking to the
 *  Alerts tab, which lists them. */
export function OpenAlertsLine({
  alerts,
  goTo,
}: {
  alerts: OpsAlert[] | undefined;
  goTo: GoToTab;
}) {
  if (!alerts) return <NotLoaded />;
  const open = alerts.filter((a) => a.open).length;
  return (
    <p className="text-body font-mono">
      <button
        type="button"
        onClick={() => goTo("alerts")}
        className={`${open ? "text-signal-magenta" : "text-ink-lo"} min-h-6 underline underline-offset-2 hover:text-ink-hi`}
      >
        {open === 0 ? "No open alerts" : `${open} open alert${open === 1 ? "" : "s"}`} →
      </button>
    </p>
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
  hostCpuPct,
  goTo,
}: {
  token: string;
  dashboard: AdminDashboard | null;
  status: AdminPipelineStatus | null;
  host: HostStats | null;
  /** Latest CPU utilisation from the host poller; null until two readings. */
  hostCpuPct: number | null;
  goTo: GoToTab;
}) {
  const [visits, setVisits] = useState<VisitorStatsDay[] | null>(null);
  const [loadTimes, setLoadTimes] = useState<LoadTimes | null>(null);
  const [trend, setTrend] = useState<PipelineTrendRun[] | null>(null);
  // A failed request keeps the last figures it had (or none): it used to
  // become an empty list, which read as "no visits" and "0 of 0 runs".
  const [loadFailed, setLoadFailed] = useState({ visits: false, loadTimes: false, trend: false });

  useEffect(() => {
    let cancelled = false;
    const settle = <T,>(key: keyof typeof loadFailed, p: Promise<T>, set: (v: T) => void) =>
      p
        .then((v) => {
          if (cancelled) return;
          set(v);
          setLoadFailed((f) => ({ ...f, [key]: false }));
        })
        .catch(() => {
          if (!cancelled) setLoadFailed((f) => ({ ...f, [key]: true }));
        });
    const load = () => {
      settle("visits", fetchAdminVisitorStats(token, 14), setVisits);
      settle("loadTimes", fetchAdminLoadTimes(token, 1), setLoadTimes);
      settle("trend", fetchAdminPipelineTrend(token, 7), (t) => setTrend(t.runs));
    };
    load();
    // Refreshed on the same cadence as the pipeline table beside these
    // tiles, so a run that fails while Overview is open shows up in the
    // "failed runs" count too, not only as FAILED in the table.
    const id = setInterval(load, 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [token]);

  const today = visits?.[visits.length - 1];
  const loadToday = loadTimes?.days[loadTimes.days.length - 1]?.load;
  const finished = (trend ?? []).filter((r) => r.status !== "running");
  const failed = finished.filter((r) => r.status === "failed").length;
  const cpuPct = hostCpuPct;

  const pipelines: {
    label: string;
    running: boolean;
    run:
      | { status: string; startedAt: string | null; elapsedSeconds: number | null }
      | null
      | undefined;
  }[] = [
    { label: "Senate", running: senateIsRunning(status), run: status?.lastRun },
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
          label="Confirmed visitors today"
          value={
            today?.confirmedVisitors != null ? today.confirmedVisitors.toLocaleString("en-US") : "—"
          }
          note={
            today
              ? `${today.firstPaintLoads.toLocaleString("en-US")} first-paint loads${
                  today.visitorsExceedLoads
                    ? `; more visitors than the ${today.loadsReported.toLocaleString("en-US")} loads reported`
                    : ""
                }`
              : loadFailed.visits
                ? "couldn't load"
                : undefined
          }
        />
        <StatTile
          label="Page load p75 today"
          value={loadToday?.p75 != null ? formatMs(loadToday.p75) : "—"}
          note={
            loadTimes
              ? `${(loadToday?.samples ?? 0).toLocaleString("en-US")} loads measured`
              : loadFailed.loadTimes
                ? "couldn't load"
                : undefined
          }
        />
        <StatTile
          label="Failed runs, 7 days"
          value={trend ? failed.toLocaleString("en-US") : "—"}
          tone={failed > 0 ? "text-signal-magenta" : "text-ink-hi"}
          note={
            trend
              ? `of ${finished.length} finished`
              : loadFailed.trend
                ? "couldn't load"
                : undefined
          }
        />
        <StatTile
          label="Next scheduled run"
          value={<span className="text-body">{formatTime(dashboard?.pipeline.nextScheduled)}</span>}
          note={dashboard?.pipeline.cronSchedule}
        />
      </div>

      <OpenAlertsLine alerts={dashboard?.opsAlerts} goTo={goTo} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel
          title="Traffic, 14 days"
          actions={<MoreLink onClick={() => goTo("traffic")}>Traffic</MoreLink>}
        >
          <LineChart
            title="Confirmed visitors and first-paint loads per day"
            xLabels={(visits ?? []).map((v) => formatDay(v.date))}
            series={[
              {
                key: "confirmed",
                label: "Confirmed visitors",
                color: SERIES[0],
                values: (visits ?? []).map((v) => v.confirmedVisitors),
              },
              {
                key: "painted",
                label: "First-paint loads",
                color: SERIES[1],
                values: (visits ?? []).map((v) => v.firstPaintLoads),
              },
            ]}
            formatValue={(v) => v.toLocaleString("en-US")}
            formatTick={formatCompact}
            height={130}
            emptyMessage={emptyText(!!visits, loadFailed.visits, "No visits recorded yet.")}
          />
        </Panel>

        <Panel
          title="Pipelines"
          actions={<MoreLink onClick={() => goTo("pipelines")}>Pipelines</MoreLink>}
        >
          <table className="w-full text-small font-mono">
            <caption className="sr-only">Latest run of each pipeline</caption>
            <thead>
              <tr className="text-ink-lo border-b border-white/[0.07]">
                <th scope="col" className="text-left py-1 pr-3 font-normal">
                  Pipeline
                </th>
                <th scope="col" className="text-left py-1 pr-3 font-normal">
                  Last run
                </th>
                <th scope="col" className="text-left py-1 pr-3 font-normal">
                  Status
                </th>
                <th scope="col" className="text-right py-1 font-normal">
                  Took
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
                        <span className="text-signal-cyan pulse-subtle">Running</span>
                      ) : run ? (
                        <span className={statusClass(run.status, stuck)}>
                          {stuck ? "Stuck" : asLabel(run.status)}
                        </span>
                      ) : (
                        <span className="text-ink-min">Never run</span>
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
            <div className="space-y-3 text-small font-mono">
              {[
                {
                  label: "CPU",
                  pct: cpuPct ?? 0,
                  text: cpuPct != null ? formatPct(cpuPct) : "—",
                  warn: 75,
                  crit: 90,
                },
                {
                  label: "Memory",
                  pct: host.memUsedPct,
                  text: `${host.memUsedPct}%`,
                  warn: 75,
                  crit: 90,
                },
                {
                  label: "Disk",
                  pct: host.diskUsedPct,
                  text: `${host.diskUsedPct}%`,
                  warn: 80,
                  crit: 95,
                },
              ].map((m) => (
                <div key={m.label}>
                  <div className="mb-1 flex justify-between">
                    <span className="text-ink-lo">{m.label}</span>
                    <span className="text-ink-hi tabular-nums">{m.text}</span>
                  </div>
                  <UsageBar
                    pct={m.pct}
                    warnAt={m.warn}
                    critAt={m.crit}
                    ariaLabel={m.label}
                    valueText={m.text === "—" ? "no reading yet" : m.text}
                  />
                </div>
              ))}
              {host.cpuTempC != null && (
                <div className="flex justify-between">
                  <span className="text-ink-lo">CPU temp</span>
                  <span className="text-ink-hi tabular-nums">{host.cpuTempC}°C</span>
                </div>
              )}
            </div>
          ) : (
            <p className="text-small font-mono text-ink-min">Waiting for the first reading…</p>
          )}
        </Panel>
      </div>
    </div>
  );
}
