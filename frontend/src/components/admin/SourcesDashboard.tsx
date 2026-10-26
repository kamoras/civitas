"use client";

import { useEffect, useState } from "react";
import { fetchAdminSourceHealth } from "@/lib/api";
import type { AdminSourceHealth, SourceHealthEntry } from "@/types/sources";
import { formatTime } from "./format";
import { Panel } from "./widgets";

/** The state's colour: a failing source stands out, one not read this run
 *  is quieter, and the word always says which. */
export const STATE_TONE: Record<SourceHealthEntry["state"], string> = {
  ok: "text-ink-lo",
  not_read: "text-signal-amber",
  unavailable: "text-signal-magenta",
  refused: "text-signal-magenta",
  walled: "text-signal-magenta",
};

/** Every external source and its health (backend app/source_health.py):
 *  its state, why, when it was last asked and last answered, the runs and
 *  steps that read it, and its failures over the window. */
export function SourcesTable({ data }: { data: AdminSourceHealth }) {
  return (
    <div className="space-y-3 text-small font-mono">
      <p className="text-body">{data.summary}</p>
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <caption className="sr-only">Every external source and its health</caption>
          <thead>
            <tr className="text-ink-lo">
              <th scope="col" className="py-1 pr-3 font-normal">
                Source
              </th>
              <th scope="col" className="py-1 pr-3 font-normal">
                State
              </th>
              <th scope="col" className="py-1 pr-3 font-normal">
                Last asked
              </th>
              <th scope="col" className="py-1 pr-3 font-normal">
                Runs and steps
              </th>
              <th scope="col" className="py-1 font-normal">
                Failed, {data.windowDays} days
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.04] align-top">
            {data.sources.map((s) => (
              <tr key={s.host}>
                <th scope="row" className="py-2 pr-3 font-normal">
                  <span className="block text-ink [overflow-wrap:anywhere]">{s.host}</span>
                  {s.reads.map((r) => (
                    <span key={r} className="block text-ink-min">
                      {r}
                    </span>
                  ))}
                </th>
                <td className="py-2 pr-3">
                  <span className={`block ${STATE_TONE[s.state]}`}>{s.stateLabel}</span>
                  <span className="block text-ink-lo">{s.line}</span>
                </td>
                <td className="py-2 pr-3 tabular-nums">
                  {s.lastAttempt ? formatTime(s.lastAttempt) : "never"}
                </td>
                <td className="py-2 pr-3">{s.runs.length ? s.runs.join(", ") : "none"}</td>
                <td className="py-2 tabular-nums">
                  {s.failedSteps30d.toLocaleString("en-US")} steps,{" "}
                  {s.failedRequests30d.toLocaleString("en-US")} of{" "}
                  {s.requests30d.toLocaleString("en-US")} requests
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function SourcesDashboard({ token }: { token: string }) {
  const [data, setData] = useState<AdminSourceHealth | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetchAdminSourceHealth(token)
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
    <Panel title="Sources">
      {data ? (
        <SourcesTable data={data} />
      ) : failed ? (
        <p role="alert" className="text-small font-mono text-signal-red">
          Source health couldn&apos;t be loaded.
        </p>
      ) : (
        <p className="text-small font-mono text-ink-min">Loading…</p>
      )}
    </Panel>
  );
}
