"use client";

import { useEffect, useState } from "react";
import { fetchAdminSourceHealth } from "@/lib/api";
import type { AdminSourceHealth, SourceHealthEntry } from "@/types/sources";
import { TYPE } from "@/lib/typeStyles";
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
      {/* A list, each source in two columns from 48rem of its own width:
          a five-column table on a phone showed one column and scrolled. */}
      <ul
        aria-label="Every external source and its health"
        className="@container divide-y divide-white/[0.04]"
      >
        {data.sources.map((s) => (
          <li
            key={s.host}
            className="grid grid-cols-[minmax(0,1fr)] gap-x-6 gap-y-1 py-2 @min-[48rem]:grid-cols-2"
          >
            <div className="min-w-0">
              <span className="block text-ink">{s.publisher ?? "Not named"}</span>
              <span className="block text-ink-lo [overflow-wrap:anywhere]">{s.host}</span>
              {s.reads.map((r) => (
                <span key={r} className="block text-ink-min">
                  {r}
                </span>
              ))}
            </div>
            <div className="min-w-0">
              <span className={`block ${STATE_TONE[s.state]}`}>{s.stateLabel}</span>
              <span className="block text-ink-lo">{s.line}</span>
              {s.reason && <span className="block text-ink-min">Recorded: {s.reason}</span>}
              <span className="block text-ink-lo">
                Last asked {s.lastAttemptText ?? "never"} · read by{" "}
                {s.readBy.length ? s.readBy.join("; ") : "no run or job found in the code"}
              </span>
              {s.runs.length > 0 && (
                <span className="block text-ink-min">Steps on record: {s.runs.join(", ")}</span>
              )}
              <span className="block text-ink-lo tabular-nums">
                {data.windowDays} days: {s.failuresText}
              </span>
              {s.alsoSendsTo.length > 0 && (
                <span className="block text-ink-min">
                  Also sent to by {s.alsoSendsTo.join(", ")}
                </span>
              )}
            </div>
          </li>
        ))}
      </ul>
      <section aria-labelledby="outbound-services" className="space-y-1 pt-2">
        <h3 id="outbound-services" className={TYPE.boxTitle}>
          Outbound services
        </h3>
        <p className="text-ink-lo">
          Hosts the site only sends to (alerts, posts, forwarded feedback): not sources, so not on
          the map.
        </p>
        {data.outbound.length ? (
          <ul aria-label="Outbound services" className="divide-y divide-white/[0.04]">
            {data.outbound.map((o) => (
              <li key={o.host} className="py-1">
                <span className="text-ink [overflow-wrap:anywhere]">{o.host}</span>{" "}
                <span className="text-ink-min">sent to by {o.modules.join(", ")}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-ink-min">
            None: every host a sending module names is also read as a source.
          </p>
        )}
      </section>
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
