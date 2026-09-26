"use client";

import { useNow } from "@/hooks/useNow";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import type { AdminDashboard, HostStats, UptimeInfo } from "@/lib/api";
import LineChart from "./charts/LineChart";
import { SERIES } from "./charts/palette";
import { niceTicks } from "./charts/scale";
import { formatBytes, formatRate, formatTime, formatUptime, parseUTC } from "./format";
import type { HostSample } from "./useHostHistory";
import { Panel, StatusDot, UsageBar } from "./widgets";

// --- Uptime Tracker ---
export function UptimeTracker({
  uptime,
  hostUptime,
}: {
  uptime?: UptimeInfo;
  hostUptime?: number | null;
}) {
  const now = useNow();

  const processStart = uptime?.processStartedAt
    ? parseUTC(uptime.processStartedAt).getTime()
    : null;
  const appUptimeSec = processStart ? Math.max(0, Math.floor((now - processStart) / 1000)) : null;

  const firstRun = uptime?.firstPipelineRun ? parseUTC(uptime.firstPipelineRun).getTime() : null;
  const totalServiceDays = firstRun ? Math.max(1, Math.floor((now - firstRun) / 86400000)) : null;

  function tickingUptime(seconds: number): string {
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    const pad = (n: number) => String(n).padStart(2, "0");
    if (d > 0) return `${d}d ${pad(h)}:${pad(m)}:${pad(s)}`;
    return `${pad(h)}:${pad(m)}:${pad(s)}`;
  }

  return (
    <div className="panel">
      <TerminalTitlebar title="Uptime">
        <span className="ml-auto text-ink-lo text-xs font-mono mr-2">
          live
          <span className="inline-block w-1.5 h-1.5 bg-phos ml-1 animate-pulse" />
        </span>
      </TerminalTitlebar>
      <div className="p-4">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {/* App Uptime — large ticking counter */}
          <div className="sm:col-span-2">
            <div className="text-xs font-mono text-ink-lo tracking-wider mb-2">
              APPLICATION UPTIME
            </div>
            <div className="font-mono text-2xl sm:text-3xl text-signal-cyan tabular-nums tracking-wider">
              {appUptimeSec != null ? tickingUptime(appUptimeSec) : "—"}
            </div>
            <div className="text-xs font-mono text-ink-min mt-1.5">
              {uptime?.processStartedAt
                ? `started ${formatTime(uptime.processStartedAt)}`
                : "unknown start time"}
            </div>
          </div>

          {/* Sidebar stats */}
          <div className="space-y-3">
            <div>
              <div className="text-xs font-mono text-ink-lo tracking-wider mb-0.5">HOST UPTIME</div>
              <div className="font-mono text-sm text-ink-hi tabular-nums">
                {hostUptime != null ? tickingUptime(hostUptime) : "—"}
              </div>
            </div>
            <div>
              <div className="text-xs font-mono text-ink-lo tracking-wider mb-0.5">SERVICE AGE</div>
              <div className="font-mono text-sm text-ink-hi">
                {totalServiceDays != null
                  ? `${totalServiceDays} day${totalServiceDays !== 1 ? "s" : ""}`
                  : "—"}
              </div>
              <div className="text-xs font-mono text-ink-min mt-0.5">
                {uptime?.firstPipelineRun ? `since ${formatTime(uptime.firstPipelineRun)}` : ""}
              </div>
            </div>
          </div>
        </div>

        {/* Uptime bar visualization */}
        {appUptimeSec != null && (
          <div className="mt-4 pt-3 border-t border-white/[0.07]">
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-xs font-mono text-ink-min">SESSION HEALTH</span>
              <span className="text-xs font-mono text-ink-lo tabular-nums">
                {appUptimeSec >= 86400
                  ? `${Math.floor(appUptimeSec / 86400)}d`
                  : appUptimeSec >= 3600
                    ? `${Math.floor(appUptimeSec / 3600)}h`
                    : `${Math.floor(appUptimeSec / 60)}m`}{" "}
                since last deploy
              </span>
            </div>
            <div
              className="w-full h-2 bg-white/[0.03] overflow-hidden"
              role="progressbar"
              aria-valuenow={Math.min(100, Math.round((appUptimeSec / 86400) * 100))}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="Session health since last deploy"
            >
              <div
                className="h-full bg-signal-cyan transition-all duration-1000"
                style={{
                  width: `${Math.min(100, (appUptimeSec / 86400) * 100)}%`,
                }}
              />
            </div>
            <div className="flex justify-between text-xs font-mono text-ink-min mt-0.5">
              <span>0h</span>
              <span>6h</span>
              <span>12h</span>
              <span>18h</span>
              <span>24h</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** Nice ticks in the unit formatRate will print (B, KiB, MiB per second). */
function byteRateTicks(max: number): number[] {
  const unit = max >= 1024 * 1024 ? 1024 * 1024 : max >= 1024 ? 1024 : 1;
  return niceTicks(max / unit).map((t) => t * unit);
}

function sampleLabel(t: number): string {
  return new Date(t).toLocaleTimeString("en-US", { hour12: false });
}

/** Current host readings, as meters. */
function HostMeters({ stats, net }: { stats: HostStats; net: HostSample | undefined }) {
  const tempColor =
    stats.cpuTempC == null
      ? ""
      : stats.cpuTempC >= 80
        ? "text-signal-magenta"
        : stats.cpuTempC >= 65
          ? "text-signal-amber"
          : "text-ink-hi";
  const loadPct = stats.loadAvg ? Math.round((stats.loadAvg[0] / stats.cpuCount) * 100) : 0;
  const rx = net?.rxRate ?? null;
  const tx = net?.txRate ?? null;

  return (
    <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-ink-lo text-xs font-mono tracking-wider">CPU LOAD</span>
          <span className="text-ink-hi text-xs font-mono tabular-nums">
            {stats.loadAvg ? `${stats.loadAvg[0].toFixed(2)} / ${stats.cpuCount}` : "—"}
          </span>
        </div>
        <UsageBar pct={loadPct} ariaLabel="CPU load percentage" />
        <div className="text-xs text-ink-min font-mono mt-1 tabular-nums">
          {stats.loadAvg
            ? `${stats.loadAvg[0].toFixed(1)} · ${stats.loadAvg[1].toFixed(1)} · ${stats.loadAvg[2].toFixed(1)}`
            : ""}
        </div>
      </div>
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-ink-lo text-xs font-mono tracking-wider">MEMORY</span>
          <span className="text-ink-hi text-xs font-mono tabular-nums">{stats.memUsedPct}%</span>
        </div>
        <UsageBar pct={stats.memUsedPct} ariaLabel="Memory usage percentage" />
        <div className="text-xs text-ink-min font-mono mt-1 tabular-nums">
          {formatBytes(stats.memUsedBytes)} / {formatBytes(stats.memTotalBytes)}
        </div>
      </div>
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-ink-lo text-xs font-mono tracking-wider">DISK</span>
          <span className="text-ink-hi text-xs font-mono tabular-nums">{stats.diskUsedPct}%</span>
        </div>
        <UsageBar
          pct={stats.diskUsedPct}
          warnAt={80}
          critAt={95}
          ariaLabel="Disk usage percentage"
        />
        <div className="text-xs text-ink-min font-mono mt-1 tabular-nums">
          {formatBytes(stats.diskFreeBytes)} free
        </div>
      </div>
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-ink-lo text-xs font-mono tracking-wider">CPU TEMP</span>
          <span className={`text-xs font-mono tabular-nums ${tempColor}`}>
            {stats.cpuTempC != null ? `${stats.cpuTempC}°C` : "—"}
          </span>
        </div>
        {stats.cpuTempC != null && (
          <UsageBar
            pct={(stats.cpuTempC / 85) * 100}
            warnAt={76}
            critAt={94}
            ariaLabel="CPU temperature"
          />
        )}
        <div className="text-xs text-ink-min font-mono mt-1">
          uptime {formatUptime(stats.uptimeSeconds)}
        </div>
      </div>
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-ink-lo text-xs font-mono tracking-wider">NETWORK</span>
          <span className="text-ink-hi text-xs font-mono tabular-nums">
            {rx != null && tx != null ? formatRate(rx + tx) : "—"}
          </span>
        </div>
        <div className="text-xs text-ink-lo font-mono tabular-nums space-y-0.5">
          <div>▼ RX {rx != null ? formatRate(rx) : "—"}</div>
          <div>▲ TX {tx != null ? formatRate(tx) : "—"}</div>
        </div>
        <div className="text-xs text-ink-min font-mono mt-1 tabular-nums">
          {stats.netRxBytes != null
            ? `↓${formatBytes(stats.netRxBytes)} ↑${formatBytes(stats.netTxBytes ?? 0)}`
            : ""}
        </div>
      </div>
    </div>
  );
}

/** Database / LLM / vector index reachability — the "is anything down" list. */
export function ServiceHealth({ d }: { d: AdminDashboard | null }) {
  return (
    <div className="space-y-2 text-sm font-mono">
      <div className="flex justify-between">
        <span className="text-ink-lo">DATABASE</span>
        <span className="flex items-center gap-2">
          <StatusDot ok={d?.system.database === "ok"} />
          {d?.system.database?.toUpperCase() ?? "—"}
        </span>
      </div>
      <div className="flex justify-between">
        <span className="text-ink-lo">LLM</span>
        <span className="flex items-center gap-2">
          <StatusDot ok={d?.system.ollama === "ok"} />
          {d?.system.ollama?.toUpperCase() ?? "—"}
        </span>
      </div>
      <div className="flex justify-between gap-3">
        <span className="text-ink-lo">MODEL</span>
        <span className="text-ink truncate">{d?.system.ollamaModel ?? "—"}</span>
      </div>
      <div className="flex justify-between">
        <span className="text-ink-lo">DB SIZE</span>
        <span>{formatBytes(d?.system.dbSizeBytes ?? 0)}</span>
      </div>
      <div className="flex justify-between gap-3">
        <span className="text-ink-lo">VECTOR DB</span>
        <span className="flex items-center gap-2 text-right">
          <StatusDot ok={d?.system.vectorDb?.status === "ok"} />
          {d?.system.vectorDb?.status === "ok"
            ? `${(d?.system.vectorDb?.totalVectors ?? 0).toLocaleString()} vectors / ${formatBytes(d?.system.vectorDb?.sizeBytes ?? 0)}`
            : "UNAVAILABLE"}
        </span>
      </div>
    </div>
  );
}

export function SystemDashboard({
  dashboard,
  stats,
  history,
}: {
  dashboard: AdminDashboard | null;
  stats: HostStats | null;
  history: HostSample[];
}) {
  const labels = history.map((h) => sampleLabel(h.time));
  const latest = history[history.length - 1];
  const hasTemp = history.some((h) => h.tempC != null);

  return (
    <div className="space-y-6">
      <Panel title="Host — live" live>
        {stats ? (
          <HostMeters stats={stats} net={latest} />
        ) : (
          <p className="text-xs font-mono text-ink-min">Waiting for the first reading…</p>
        )}
        <p className="mt-4 text-xs font-mono text-ink-min">
          The charts below cover the time since this dashboard was opened (one reading every 5s,
          last 10 minutes). Host readings aren&apos;t stored server-side, so a reload starts them
          again.
        </p>
        <div className="mt-3 grid grid-cols-1 gap-6 lg:grid-cols-2">
          <LineChart
            title="CPU LOAD & MEMORY"
            subtitle="% of capacity"
            xLabels={labels}
            series={[
              {
                key: "cpu",
                label: "CPU load (1m avg / cores)",
                color: SERIES[0],
                values: history.map((h) => h.loadPct),
              },
              {
                key: "mem",
                label: "Memory used",
                color: SERIES[1],
                values: history.map((h) => h.memPct),
              },
            ]}
            formatValue={(v) => `${Math.round(v)}%`}
            height={130}
            emptyMessage="Collecting readings…"
          />
          <LineChart
            title="NETWORK THROUGHPUT"
            subtitle="bytes per second"
            xLabels={labels}
            series={[
              {
                key: "rx",
                label: "Received",
                color: SERIES[2],
                values: history.map((h) => h.rxRate),
              },
              { key: "tx", label: "Sent", color: SERIES[3], values: history.map((h) => h.txRate) },
            ]}
            formatValue={formatRate}
            yTicks={byteRateTicks}
            height={130}
            emptyMessage="Needs two readings to compute a rate…"
          />
          {hasTemp && (
            <LineChart
              title="CPU TEMPERATURE"
              subtitle="°C"
              xLabels={labels}
              series={[
                {
                  key: "temp",
                  label: "CPU temperature",
                  color: SERIES[3],
                  values: history.map((h) => h.tempC),
                },
              ]}
              formatValue={(v) => `${Math.round(v)}°C`}
              height={130}
              headline={latest?.tempC != null ? `${latest.tempC}°C` : undefined}
            />
          )}
        </div>
      </Panel>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <UptimeTracker uptime={dashboard?.uptime} hostUptime={stats?.uptimeSeconds} />
        <Panel title="Services">
          <ServiceHealth d={dashboard} />
        </Panel>
      </div>
    </div>
  );
}
