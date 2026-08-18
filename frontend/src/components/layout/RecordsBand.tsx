"use client";

import { useEffect, useState } from "react";
import { fetchPipelineStatus } from "@/lib/api";
import type { PipelineStatus } from "@/lib/api";

/**
 * The document header band that sits above every page.
 *
 * This is the credibility furniture the site was missing: until now the only
 * place a visitor could learn when the data was last computed was /admin.
 * A score with no "as of" is a claim; a score with a run number and a
 * timestamp is a record.
 *
 * Deliberately renders its frame before the fetch resolves rather than
 * returning null — a band that appears late shifts the whole page down, and
 * this sits above the masthead.
 */

/** UTC, minute precision, no locale drift between server and client. */
export function formatRunTimestamp(iso: string): string | null {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`
  );
}

interface RunState {
  label: string;
  /** Tailwind text colour for the status word. */
  tone: string;
}

/**
 * A run is only "COMPLETE" when it actually finished. Anything else says so
 * plainly instead of dressing a stalled or failed run as fresh data — the
 * whole point of the band is that a stale record is visible rather than
 * assumed.
 */
export function describeRunState(status: PipelineStatus | null): RunState {
  if (!status) return { label: "STATUS UNAVAILABLE", tone: "text-ink-min" };
  if (status.isRunning) return { label: "RUN IN PROGRESS", tone: "text-signal-amber" };

  const run = status.lastRun;
  if (!run) return { label: "NO RUN RECORDED", tone: "text-ink-min" };
  if (run.errorMessage || run.status === "failed") {
    return { label: "LAST RUN FAILED", tone: "text-signal-red" };
  }
  if (!run.completedAt) return { label: "RUN INCOMPLETE", tone: "text-signal-amber" };
  return { label: "COMPLETE", tone: "text-phos" };
}

export default function RecordsBand() {
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    // fetchPipelineStatus already swallows network and non-2xx failures into
    // null, so there is nothing to catch here — the null path is the error
    // path, and describeRunState renders it honestly.
    fetchPipelineStatus()
      .then(setStatus)
      .finally(() => setLoaded(true));
  }, []);

  const state = describeRunState(status);
  const run = status?.lastRun;
  const filed = run?.completedAt ? formatRunTimestamp(run.completedAt) : null;

  return (
    <div className="border-b-3 border-phos bg-surface-base">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-x-6 gap-y-1 px-4 py-2 sm:px-6">
        {/* Hidden on mobile: the wordmark below already says this, and a
            second line here would push the fixed header past the pt-24 that
            every page uses to clear it. */}
        <span className="hidden font-mono text-xs tracking-[0.16em] text-ink-lo sm:inline">
          CIVITAS <span className="text-ink-min">/</span> PUBLIC RECORDS OFFICE
        </span>

        <span
          className="font-mono text-xs tracking-[0.16em] text-ink-lo"
          role="status"
          aria-live="polite"
        >
          {loaded ? (
            <>
              {/* Dropped on mobile: at 390px the full string wraps to two
                  lines and pushes the fixed header to 98px, past the 96px
                  (`pt-24`) every page reserves for it — measured, not
                  guessed. The run number is the least load-bearing part;
                  the timestamp and the state are what make a stale record
                  visible. */}
              {run?.id != null && (
                <span className="hidden sm:inline">
                  RUN NO. {run.id}
                  <span className="mx-2 text-ink-min" aria-hidden="true">
                    ·
                  </span>
                </span>
              )}
              {filed && (
                <>
                  FILED {filed}
                  <span className="mx-2 text-ink-min" aria-hidden="true">
                    ·
                  </span>
                </>
              )}
              <span className={state.tone}>{state.label}</span>
            </>
          ) : (
            <span className="text-ink-min">READING RUN STATUS…</span>
          )}
        </span>
      </div>
    </div>
  );
}
