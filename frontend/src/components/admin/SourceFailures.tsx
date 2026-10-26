"use client";

import { useEffect, useState } from "react";
import ChartReadout from "@/components/shared/ChartReadout";
import { fetchAdminSourceFailures, type SourceFailures as Failures } from "@/lib/api";
import { MARK_ATTR } from "@/types/readout";
import { SERIES } from "./charts/palette";
import { Panel } from "./widgets";

/** The chart and its table, from a loaded summary. */
export function SourceFailuresView({ data }: { data: Failures }) {
  if (data.hosts.length === 0)
    return (
      <p className="text-small font-mono text-ink-min">
        No source refused or failed a step in the last {data.days} days.
      </p>
    );
  return (
    <div className="space-y-4 text-small font-mono">
      <ChartReadout
        label={`Expected source failures by host, last ${data.days} days`}
        marks={data.hosts.map((h) => h.mark)}
      >
        <ul className="space-y-1">
          {data.hosts.map((h) => (
            <li key={h.host} className="grid grid-cols-[minmax(0,12rem)_minmax(0,1fr)] gap-2">
              <span className="truncate text-ink-lo">{h.host}</span>
              <span className="flex items-center gap-2">
                <span
                  {...{ [MARK_ATTR]: h.host }}
                  className="block h-4"
                  style={{ width: `${h.barPct}%`, background: SERIES[3] }}
                />
                <span className="tabular-nums">{h.total.toLocaleString("en-US")}</span>
              </span>
            </li>
          ))}
        </ul>
      </ChartReadout>
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <caption className="sr-only">Expected source failures by host since {data.since}</caption>
          <thead>
            <tr className="text-ink-lo">
              <th scope="col" className="py-1 pr-3 font-normal">
                Host
              </th>
              <th scope="col" className="py-1 pr-3 font-normal">
                Failed steps
              </th>
              <th scope="col" className="py-1 pr-3 font-normal">
                Kind
              </th>
              <th scope="col" className="py-1 pr-3 font-normal">
                Runs and steps
              </th>
              <th scope="col" className="py-1 font-normal">
                Last
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.04]">
            {data.hosts.map((h) => (
              <tr key={h.host}>
                <th scope="row" className="py-1 pr-3 font-normal [overflow-wrap:anywhere]">
                  {h.host}
                </th>
                <td className="py-1 pr-3 tabular-nums">{h.total.toLocaleString("en-US")}</td>
                <td className="py-1 pr-3">
                  {Object.entries(h.byKind)
                    .filter(([, n]) => n > 0)
                    .map(([k, n]) => `${k} ${n.toLocaleString("en-US")}`)
                    .join(", ")}
                </td>
                <td className="py-1 pr-3">{h.steps.join(", ")}</td>
                <td className="py-1 tabular-nums">{h.lastDate}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Steps that failed because a source refused Civitas or couldn't be read
 *  (backend SourceFailureCount), by host over the last 30 days. Each is a
 *  known condition with its one-line reason on the run row, not a crash. */
export function SourceFailures({ token }: { token: string }) {
  const [data, setData] = useState<Failures | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetchAdminSourceFailures(token, 30)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);
  return (
    <Panel title="Expected source failures, 30 days">
      {data ? (
        <SourceFailuresView data={data} />
      ) : failed ? (
        <p role="alert" className="text-small font-mono text-signal-red">
          Source failures couldn&apos;t be loaded.
        </p>
      ) : (
        <p className="text-small font-mono text-ink-min">Loading…</p>
      )}
    </Panel>
  );
}
