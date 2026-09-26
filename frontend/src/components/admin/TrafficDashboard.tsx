"use client";

import { useEffect, useState } from "react";
import {
  fetchAdminLoadTimes,
  fetchAdminTopPages,
  fetchAdminVisitorBreakdown,
  fetchAdminVisitorStats,
  type LoadTimeMetric,
  type LoadTimes,
  type TopPageEntry,
  type VisitorBreakdown,
  type VisitorStatsDay,
} from "@/lib/api";
import LineChart from "./charts/LineChart";
import { SERIES } from "./charts/palette";
import { formatCompact, formatMs } from "./charts/scale";
import { formatDay } from "./format";
import { Panel, RankBars, Segmented, StatTile } from "./widgets";

export const RANGE_OPTIONS = [
  { value: 7, label: "7D" },
  { value: 30, label: "30D" },
  { value: 90, label: "90D" },
];

const METRIC_OPTIONS: { value: LoadTimeMetric; label: string; blurb: string }[] = [
  {
    value: "load",
    label: "PAGE LOAD",
    blurb: "Navigation start to the end of the load event — the whole cold visit.",
  },
  {
    value: "fcp",
    label: "FIRST PAINT",
    blurb: "Until the browser first painted text or an image (first contentful paint).",
  },
  {
    value: "ttfb",
    label: "SERVER RESPONSE",
    blurb: "Until the first byte of the page arrived (TTFB) — the part this host controls.",
  },
];

interface TrafficData {
  days: VisitorStatsDay[];
  loadTimes: LoadTimes | null;
  topPages: TopPageEntry[];
  breakdown: VisitorBreakdown | null;
}

export function TrafficDashboard({ token }: { token: string }) {
  const [range, setRange] = useState(30);
  const [metric, setMetric] = useState<LoadTimeMetric>("load");
  const [data, setData] = useState<TrafficData | null>(null);
  // The range the current `data` was fetched for. While it differs from the
  // selected range a refetch is in flight, and the charts hold their previous
  // render dimmed rather than blanking.
  const [loadedRange, setLoadedRange] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Each request settles on its own: a failing load-times endpoint must
    // not blank the visitor chart, which is the one people come here for.
    Promise.all([
      fetchAdminVisitorStats(token, range).catch(() => [] as VisitorStatsDay[]),
      fetchAdminLoadTimes(token, range).catch(() => null),
      fetchAdminTopPages(token, range).catch(() => [] as TopPageEntry[]),
      fetchAdminVisitorBreakdown(token).catch(() => null),
    ]).then(([days, loadTimes, topPages, breakdown]) => {
      if (cancelled) return;
      setData({ days, loadTimes, topPages, breakdown });
      setLoadedRange(range);
    });
    return () => {
      cancelled = true;
    };
  }, [token, range]);

  const stale = loadedRange !== range;
  const days = data?.days ?? [];
  const labels = days.map((d) => formatDay(d.date));
  const today = days[days.length - 1];
  const visitorsTotal = days.reduce((s, d) => s + d.uniqueVisitors, 0);
  const viewsTotal = days.reduce((s, d) => s + d.pageViews, 0);

  const lt = data?.loadTimes;
  const ltDays = lt?.days ?? [];
  const ltToday = ltDays[ltDays.length - 1]?.[metric];
  const ltSamples = ltDays.reduce((s, d) => s + d[metric].samples, 0);
  const metricInfo = METRIC_OPTIONS.find((m) => m.value === metric)!;

  return (
    <div className="space-y-6">
      <Segmented label="RANGE" options={RANGE_OPTIONS} value={range} onChange={setRange} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Visitors today"
          value={today ? today.uniqueVisitors.toLocaleString() : "—"}
          note={`${formatCompact(visitorsTotal)} visitor-days in ${range}d`}
        />
        <StatTile
          label="Page views today"
          value={today ? today.pageViews.toLocaleString() : "—"}
          note={`${formatCompact(viewsTotal)} in ${range}d`}
        />
        <StatTile
          label="Page load p75 today"
          value={
            ltDays.length && ltDays[ltDays.length - 1].load.p75 != null
              ? formatMs(ltDays[ltDays.length - 1].load.p75 as number)
              : "—"
          }
          note={`${(ltDays[ltDays.length - 1]?.load.samples ?? 0).toLocaleString()} loads measured`}
        />
        <StatTile
          label="Server response p75 today"
          value={
            ltDays.length && ltDays[ltDays.length - 1].ttfb.p75 != null
              ? formatMs(ltDays[ltDays.length - 1].ttfb.p75 as number)
              : "—"
          }
          note="time to first byte"
        />
      </div>

      <Panel title="Visitors">
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/* Two charts, not one with two lines: page views run ~5-10x
              unique visitors, so on a shared axis the visitor line would
              flatten against the baseline. */}
          <LineChart
            title="UNIQUE VISITORS PER DAY"
            subtitle="one per IP per UTC day"
            xLabels={labels}
            series={[
              {
                key: "visitors",
                label: "Unique visitors",
                color: SERIES[0],
                values: days.map((d) => d.uniqueVisitors),
              },
            ]}
            formatValue={(v) => v.toLocaleString()}
            formatTick={formatCompact}
            stale={stale}
            headline={
              today ? (
                <span className="text-lg">{today.uniqueVisitors.toLocaleString()}</span>
              ) : undefined
            }
            emptyMessage={data ? "No visits recorded in this range." : "Loading…"}
          />
          <LineChart
            title="PAGE VIEWS PER DAY"
            subtitle="every page load, not deduped by visitor"
            xLabels={labels}
            series={[
              {
                key: "views",
                label: "Page views",
                color: SERIES[2],
                values: days.map((d) => d.pageViews),
              },
            ]}
            formatValue={(v) => v.toLocaleString()}
            formatTick={formatCompact}
            stale={stale}
            headline={
              today ? (
                <span className="text-lg">{today.pageViews.toLocaleString()}</span>
              ) : undefined
            }
            emptyMessage={data ? "No page views recorded in this range." : "Loading…"}
          />
        </div>
      </Panel>

      <Panel title="Load times">
        <div className="mb-4">
          <Segmented label="METRIC" options={METRIC_OPTIONS} value={metric} onChange={setMetric} />
          <p className="mt-2 text-xs font-mono text-ink-min">{metricInfo.blurb}</p>
        </div>
        <LineChart
          title={`${metricInfo.label} — P50 / P75 / P95 PER DAY`}
          subtitle={`${ltSamples.toLocaleString()} cold page loads measured in ${range}d; days with none are gaps`}
          xLabels={labels}
          series={[
            {
              key: "p50",
              label: "p50 (median)",
              color: SERIES[0],
              values: ltDays.map((d) => d[metric].p50),
            },
            {
              key: "p75",
              label: "p75",
              color: SERIES[1],
              values: ltDays.map((d) => d[metric].p75),
            },
            {
              key: "p95",
              label: "p95 (slowest 1 in 20)",
              color: SERIES[2],
              values: ltDays.map((d) => d[metric].p95),
            },
          ]}
          formatValue={formatMs}
          stale={stale}
          height={180}
          headline={
            ltToday?.p75 != null ? (
              <span className="text-sm">
                p75 today <span className="text-lg">{formatMs(ltToday.p75)}</span>
              </span>
            ) : undefined
          }
          emptyMessage={
            data
              ? "No load timings yet — browsers report them after each cold page load."
              : "Loading…"
          }
        />
        {lt && lt.byPath.length > 0 && (
          <div className="mt-6">
            <div className="text-ink-lo text-xs font-mono tracking-wider mb-1.5">
              SLOWEST ROUTES — PAGE LOAD, LAST {range} DAYS
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs font-mono">
                <thead>
                  <tr className="text-ink-lo border-b border-white/[0.07]">
                    <th scope="col" className="text-left py-1 pr-3 font-normal">
                      ROUTE
                    </th>
                    <th scope="col" className="text-right py-1 pr-3 font-normal">
                      P50
                    </th>
                    <th scope="col" className="text-right py-1 pr-3 font-normal">
                      P95
                    </th>
                    <th scope="col" className="text-right py-1 font-normal">
                      LOADS
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {lt.byPath.slice(0, 10).map((p) => (
                    <tr key={p.path} className="border-b border-white/[0.04]">
                      <th scope="row" className="text-left py-1 pr-3 font-normal text-ink">
                        {p.path}
                      </th>
                      <td className="text-right py-1 pr-3 tabular-nums text-ink-lo">
                        {p.p50 != null ? formatMs(p.p50) : "—"}
                      </td>
                      <td className="text-right py-1 pr-3 tabular-nums text-ink-hi">
                        {p.p95 != null ? formatMs(p.p95) : "—"}
                      </td>
                      <td className="text-right py-1 tabular-nums text-ink-lo">
                        {p.samples.toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Panel>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel title="Top pages">
          {data && data.topPages.length === 0 ? (
            <p className="text-xs font-mono text-ink-min">No page views in this range.</p>
          ) : (
            <RankBars
              title={`MOST VIEWED — LAST ${range} DAYS`}
              entries={(data?.topPages ?? []).map((p) => ({ name: p.path, count: p.views }))}
              unit="views"
              labelWidth="w-36"
            />
          )}
        </Panel>
        <Panel title="Devices today">
          {data?.breakdown &&
          (data.breakdown.browsers.length > 0 ||
            data.breakdown.os.length > 0 ||
            data.breakdown.devices.length > 0) ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3 lg:grid-cols-1 xl:grid-cols-3">
              <RankBars title="BROWSER" entries={data.breakdown.browsers} unit="visitors" />
              <RankBars title="OS" entries={data.breakdown.os} unit="visitors" />
              <RankBars title="DEVICE" entries={data.breakdown.devices} unit="visitors" />
            </div>
          ) : (
            <p className="text-xs font-mono text-ink-min">No visitors yet today.</p>
          )}
        </Panel>
      </div>

      <p className="text-ink-min text-xs font-mono">
        Visitors are counted by a hash under a random salt that is deleted when its day ends — no IP
        addresses are stored, and past days&apos; hashes can&apos;t be traced back to one.
        Browser/OS/device are coarse categories only, never the raw User-Agent string. Page views
        are raw counts grouped by route (all politician profiles count as one row). Load times are
        kept only as a per-route daily histogram, with nothing that identifies the visit they came
        from.
      </p>
    </div>
  );
}
