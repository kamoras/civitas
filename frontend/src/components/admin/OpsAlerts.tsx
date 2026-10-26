"use client";

import type { OpsAlert } from "@/lib/api";
import { TYPE } from "@/lib/typeStyles";
import { formatTime } from "./format";
import { NotLoaded } from "./widgets";

/** One alert: subject, when, and the body; a resolved or replaced one says when. */
function OpsAlertItem({ a }: { a: OpsAlert }) {
  return (
    <li className="py-2">
      <div className="flex flex-wrap justify-between gap-x-3">
        <span className={a.open ? "text-signal-magenta" : "text-ink"}>{a.subject}</span>
        <time dateTime={a.at} className="text-ink-lo tabular-nums">
          {formatTime(a.at)}
        </time>
      </div>
      <p className="mt-1 text-ink-lo whitespace-pre-wrap break-words">{a.body}</p>
      {a.resolvedAt ? (
        <p className="mt-1 text-ink-min">
          Resolved <time dateTime={a.resolvedAt}>{formatTime(a.resolvedAt)}</time>
        </p>
      ) : (
        a.supersededAt && (
          <p className="mt-1 text-ink-min">
            Replaced by a newer alert{" "}
            <time dateTime={a.supersededAt}>{formatTime(a.supersededAt)}</time>
          </p>
        )
      )}
    </li>
  );
}

/** The pipelines' operator alerts: the ones still open first, then the
 *  rest (resolved conditions and one-off events), newest first. An alert
 *  closes when the code that raised it sees its condition gone
 *  (ops_alerts.resolve_ops_alert), so "Active" is what's wrong right now. */
export function OpsAlerts({ alerts }: { alerts: OpsAlert[] | undefined }) {
  if (!alerts) return <NotLoaded />;
  if (alerts.length === 0)
    return <p className="text-small font-mono text-ink-min">No alerts recorded.</p>;
  const active = alerts.filter((a) => a.open);
  const earlier = alerts.filter((a) => !a.open);
  const key = (a: OpsAlert) => `${a.at}-${a.subject}`;
  return (
    <div className="space-y-4 text-small font-mono">
      <section aria-label="Active alerts">
        <h3 className={`${TYPE.boxTitle} mb-1`}>Active ({active.length})</h3>
        {active.length === 0 ? (
          <p className="text-ink-min">Nothing is wrong right now.</p>
        ) : (
          <ul className="divide-y divide-white/[0.04]">
            {active.map((a) => (
              <OpsAlertItem key={key(a)} a={a} />
            ))}
          </ul>
        )}
      </section>
      {earlier.length > 0 && (
        <section aria-label="Earlier alerts">
          <h3 className={`${TYPE.boxTitle} mb-1`}>Earlier</h3>
          <ul className="divide-y divide-white/[0.04]">
            {earlier.map((a) => (
              <OpsAlertItem key={key(a)} a={a} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
