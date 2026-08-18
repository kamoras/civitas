"use client";

import { useEffect, useState } from "react";
import { fetchPipelineStatus } from "@/lib/api";
import type { PipelineStatus } from "@/lib/api";
import { parseUtc } from "@/lib/formatting";

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

/**
 * UTC, minute precision, no locale drift between server and client.
 *
 * `parseUtc`, not `new Date`: the backend's utcnow() returns a naive UTC
 * datetime, so Pydantic serialises `completedAt` with no `Z` and no offset —
 * and ECMA-262 says an offset-less date-TIME form is local time. Parsing it
 * raw and then reading it back with getUTC* would shift the stamp by the
 * viewer's offset and still label it "UTC".
 */
export function formatRunTimestamp(iso: string): string | null {
  const d = parseUtc(iso);
  if (!d) return null;
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

/**
 * Module-scoped cache for the run status.
 *
 * This band lives in Navbar, which every page mounts itself rather than
 * inheriting from the root layout — so without this, each client-side
 * navigation refires `GET /api/pipeline/status`. That endpoint calls
 * `db.expire_all()` and runs two queries with no `Cache-Control`, on a
 * Raspberry Pi.
 *
 * Cached here rather than by switching `fetchPipelineStatus` to the module's
 * shared `cachedFetch`, deliberately: the admin console polls the same
 * endpoint for live run progress, and a 2-minute cache there would freeze a
 * running pipeline's progress bar. The staleness that is fine for a "data as
 * of" stamp is not fine for a progress display.
 *
 * The in-flight promise is shared too, so a page mounting two bands (or a
 * fast double navigation) makes one request rather than two.
 */
const STATUS_TTL_MS = 120_000;
let statusCache: { at: number; value: PipelineStatus | null } | null = null;
let statusInFlight: Promise<PipelineStatus | null> | null = null;

function loadStatus(now: number): Promise<PipelineStatus | null> {
  if (statusCache && now - statusCache.at < STATUS_TTL_MS) {
    return Promise.resolve(statusCache.value);
  }
  if (!statusInFlight) {
    statusInFlight = fetchPipelineStatus()
      .then((value) => {
        statusCache = { at: Date.now(), value };
        return value;
      })
      .finally(() => {
        statusInFlight = null;
      });
  }
  return statusInFlight;
}

/** Test seam — the cache is module state and would otherwise leak between cases. */
export function __resetStatusCache() {
  statusCache = null;
  statusInFlight = null;
}

export default function RecordsBand() {
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let active = true;
    // Reading the cache here rather than seeding useState from it, on purpose:
    // `Date.now()` during render is impure, and it would differ between the
    // server render (cache always cold) and a client that has navigated
    // (cache warm) — a hydration mismatch. On a warm cache loadStatus returns
    // an already-resolved promise, so this settles in a microtask and the
    // "READING RUN STATUS…" text does not get a chance to paint.
    //
    // fetchPipelineStatus already swallows network and non-2xx failures into
    // null, so there is nothing to catch — the null path is the error path,
    // and describeRunState renders it honestly.
    loadStatus(Date.now()).then((value) => {
      if (!active) return;
      setStatus(value);
      setLoaded(true);
    });
    return () => {
      active = false;
    };
  }, []);

  const state = describeRunState(status);
  const run = status?.lastRun;
  // Only a run that actually succeeded gets a "FILED" stamp. Printing one
  // beside "LAST RUN FAILED" would say the record is current as of a run
  // that wrote nothing, which is the exact misreading this band exists to
  // prevent.
  const filed =
    run?.completedAt && state.label === "COMPLETE" ? formatRunTimestamp(run.completedAt) : null;

  return (
    <div className="border-b-3 border-phos bg-surface-base">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-x-6 gap-y-1 px-4 py-2 sm:px-6">
        {/* Revealed at `lg`, matching the nav's breakpoint — not `sm`.

            The two halves of this band do not fit on one row until ~768px, so
            showing this from 640px wrapped the band and put the fixed header
            at 102px, past the clearance every page reserves. Below
            `lg` the wordmark immediately underneath already says "CIVITAS",
            so nothing is lost. Measured across 320-1280px. */}
        <span className="hidden font-mono text-xs tracking-[0.16em] text-ink-lo lg:inline">
          CIVITAS <span className="text-ink-min">/</span> PUBLIC RECORDS OFFICE
        </span>

        {/*
          Deliberately NOT role="status"/aria-live. Navbar (and so this band)
          mounts per page rather than in the root layout, so a live region
          here would re-announce the run number and timestamp ahead of every
          page's own content on every client-side navigation. It is ambient
          provenance, not an update the reader asked for.
        */}
        <span className="font-mono text-xs tracking-[0.06em] text-ink-lo lg:tracking-[0.16em]">
          {loaded ? (
            <>
              {/* Dropped on mobile. The full string wraps to a second line
                  on narrow phones and pushes this fixed header past the 96px
                  (`--header-clearance`) every page reserves for it — measured across every
                  run-state label at 320-1280px, not guessed. The run number is
                  the least load-bearing part; the timestamp and the state are
                  what make a stale record visible.

                  The tighter mobile letter-spacing above is part of the same
                  budget: at 0.16em the COMPLETE line needs ~311px of the 288px
                  available at 320px, so the tracking alone caused the wrap. */}
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
