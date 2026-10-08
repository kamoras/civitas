"use client";

import { useEffect, useState } from "react";
import { fetchAdminApiUsage, type ApiUsage } from "@/lib/api";
import LineChart from "./charts/LineChart";
import { SERIES } from "./charts/palette";
import { formatCompact } from "./charts/scale";
import { formatDay } from "./format";
import { RANGE_OPTIONS } from "./TrafficDashboard";
import { Panel, Segmented, StatTile } from "./widgets";

/** Public API and MCP use (backend ApiRequestCount): requests by channel,
 *  rate-limit refusals, requests rejected as the caller's mistake, the API's
 *  own failures, MCP connections, and why requests were rejected. Apart from Traffic on purpose —
 *  a program calling the API is not a visitor reading the site. */
export function ApiDashboard({ token }: { token: string }) {
  const [range, setRange] = useState(30);
  const [usage, setUsage] = useState<ApiUsage | null>(null);
  const [failed, setFailed] = useState(false);
  // The range `usage` was fetched for; while it differs a refetch is in
  // flight and the chart holds its last render dimmed (as Traffic does).
  const [loadedRange, setLoadedRange] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAdminApiUsage(token, range)
      .then((u) => {
        if (cancelled) return;
        setUsage(u);
        setFailed(false);
        setLoadedRange(range);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [token, range]);

  const days = usage?.days ?? [];
  const today = days[days.length - 1];
  const totals = usage?.totals;

  return (
    <div className="space-y-6">
      <Segmented label="RANGE" options={RANGE_OPTIONS} value={range} onChange={setRange} />

      {failed && (
        <p role="alert" className="text-xs font-mono text-signal-red">
          API usage couldn&apos;t be loaded.
        </p>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatTile
          label="API requests today"
          value={today ? today.http.toLocaleString() : "—"}
          note={totals ? `${formatCompact(totals.http)} in ${range}d` : undefined}
        />
        <StatTile
          label="MCP tool calls today"
          value={today ? today.mcp.toLocaleString() : "—"}
          note={totals ? `${formatCompact(totals.mcp)} in ${range}d` : undefined}
        />
        <StatTile
          label="MCP connections today"
          value={today ? today.mcpConnections.toLocaleString() : "—"}
          note="tool listings, one per client session"
        />
        <StatTile
          label="Rejected today"
          value={today ? today.rejected.toLocaleString() : "—"}
          note={
            today
              ? `invalid requests and unknown ids; ${today.rateLimited.toLocaleString()} rate-limited`
              : undefined
          }
          tone={today && today.rejected + today.rateLimited > 0 ? "text-signal-amber" : undefined}
        />
        <StatTile
          label="Server errors today"
          value={today ? today.serverErrors.toLocaleString() : "—"}
          note={totals ? `${formatCompact(totals.serverErrors)} in ${range}d (5xx)` : undefined}
          tone={today && today.serverErrors > 0 ? "text-signal-red" : undefined}
        />
      </div>

      <Panel title="Requests">
        <LineChart
          title="REQUESTS PER DAY"
          subtitle="plain HTTP and MCP tool calls; the spec and CORS preflights aren't counted"
          xLabels={days.map((d) => formatDay(d.date))}
          series={[
            { key: "http", label: "HTTP", color: SERIES[0], values: days.map((d) => d.http) },
            {
              key: "mcp",
              label: "MCP tool calls",
              color: SERIES[2],
              values: days.map((d) => d.mcp),
            },
          ]}
          formatValue={(v) => v.toLocaleString()}
          formatTick={formatCompact}
          stale={loadedRange !== range}
          emptyMessage={usage ? "No API requests in this range." : "Loading…"}
        />
      </Panel>

      <Panel title="By endpoint">
        {usage && usage.byEndpoint.length === 0 ? (
          <p className="text-xs font-mono text-ink-min">No API requests in this range.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono">
              <caption className="sr-only">
                Public API requests by endpoint, last {range} days
              </caption>
              <thead>
                <tr className="text-ink-lo border-b border-white/[0.07]">
                  <th scope="col" className="text-left py-1 pr-3 font-normal">
                    ENDPOINT
                  </th>
                  <th scope="col" className="text-right py-1 pr-3 font-normal">
                    HTTP
                  </th>
                  <th scope="col" className="text-right py-1 pr-3 font-normal">
                    MCP
                  </th>
                  <th scope="col" className="text-right py-1 pr-3 font-normal">
                    429
                  </th>
                  <th scope="col" className="text-right py-1 pr-3 font-normal">
                    REJECTED
                  </th>
                  <th scope="col" className="text-right py-1 font-normal">
                    5XX
                  </th>
                </tr>
              </thead>
              <tbody>
                {(usage?.byEndpoint ?? []).map((e) => (
                  <tr key={e.endpoint} className="border-b border-white/[0.04]">
                    <th scope="row" className="text-left py-1 pr-3 font-normal text-ink">
                      {e.endpoint}
                    </th>
                    <td className="text-right py-1 pr-3 tabular-nums text-ink-hi">
                      {e.http.toLocaleString()}
                    </td>
                    <td className="text-right py-1 pr-3 tabular-nums text-ink-hi">
                      {e.mcp.toLocaleString()}
                    </td>
                    <td className="text-right py-1 pr-3 tabular-nums text-ink-lo">
                      {e.rateLimited.toLocaleString()}
                    </td>
                    <td className="text-right py-1 pr-3 tabular-nums text-ink-lo">
                      {e.rejected.toLocaleString()}
                    </td>
                    <td className="text-right py-1 tabular-nums text-ink-lo">
                      {e.serverErrors.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="Why requests were rejected">
        {usage && usage.rejections.length === 0 ? (
          <p className="text-xs font-mono text-ink-min">No invalid requests in this range.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono">
              <caption className="sr-only">
                Invalid public API requests by parameter and rule, last {range} days
              </caption>
              <thead>
                <tr className="text-ink-lo border-b border-white/[0.07]">
                  <th scope="col" className="text-left py-1 pr-3 font-normal">
                    ENDPOINT
                  </th>
                  <th scope="col" className="text-left py-1 pr-3 font-normal">
                    PARAMETER
                  </th>
                  <th scope="col" className="text-left py-1 pr-3 font-normal">
                    RULE BROKEN
                  </th>
                  <th scope="col" className="text-right py-1 font-normal">
                    COUNT
                  </th>
                </tr>
              </thead>
              <tbody>
                {(usage?.rejections ?? []).map((r) => (
                  <tr
                    key={`${r.endpoint}-${r.parameter}-${r.reason}`}
                    className="border-b border-white/[0.04]"
                  >
                    <th scope="row" className="text-left py-1 pr-3 font-normal text-ink">
                      {r.endpoint}
                    </th>
                    <td className="text-left py-1 pr-3 text-ink-hi">{r.parameter}</td>
                    <td className="text-left py-1 pr-3 text-ink-lo">{r.reason}</td>
                    <td className="text-right py-1 tabular-nums text-ink-hi">
                      {r.count.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <p className="text-ink-min text-xs font-mono">
        Counted per day, endpoint, channel and status only (and for an invalid request, the
        parameter and the rule, never the value sent): nothing about the caller (no IP, no hash, no
        User-Agent). None of this is in the visitor figures on the Traffic tab.
      </p>
    </div>
  );
}
