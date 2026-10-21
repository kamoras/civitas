"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { fetchPipelineStatus } from "@/lib/api";
import type { PipelineStatus } from "@/lib/api";
import { parseUtc, asLabel, midSentence } from "@/lib/formatting";

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

/** "HH:MM" (UTC), for the phone line: the failed run's day is almost always
 * last night's, and the full stamp shows from `sm` up. */
function formatRunTime(iso: string): string | null {
  return formatRunTimestamp(iso)?.slice(11, 16) ?? null;
}

interface RunState {
  /** The status words, full (from `sm`) and short (phones). */
  label: string;
  short: string;
  /** Every link named, when the label names one and counts the rest
   *  ("SENATE + 2 MORE"); null when the label already names them all. */
  everyName: string | null;
  /** Tailwind text colour for the status words. */
  tone: string;
  /** When the named run ended (or started, if it never ended); null for none. */
  at: string | null;
  /** Whether every link's latest run completed. */
  complete: boolean;
}

const plain = (label: string, tone: string): RunState => ({
  label,
  short: label,
  everyName: null,
  tone,
  at: null,
  complete: false,
});

/**
 * The nightly chain is five independent runs (Senate, Supplementary, House,
 * Stock trades, Election), and one failing leaves the other four's records
 * current. So the band names the link that failed, and when, instead of
 * calling the whole site failed: until 2026-10 it read only the Senate's run,
 * and one Senate failure printed "LAST RUN FAILED" over a night where the
 * other four completed. A failure outranks a partial run, which outranks a run
 * still going: a run in progress is transient, a failed one is what a reader
 * should weigh. "COMPLETE" only when every link's latest run completed.
 */
export function describeRunState(status: PipelineStatus | null): RunState {
  if (!status) return plain("Status unavailable", "text-ink-min");
  const links = status.links ?? [];
  if (links.length === 0) return plain("No run recorded", "text-ink-min");

  for (const [state, verb, shortVerb, tone] of [
    ["failed", "failed", "failed", "text-signal-red"],
    ["partial", "partial", "partial", "text-signal-amber"],
    ["running", "in progress", "running", "text-signal-amber"],
  ] as const) {
    const hit = links.filter((l) => l.state === state);
    if (hit.length === 0) continue;
    // Two or more name the first and count the rest: every name in a row
    // ("SENATE + SUPPLEMENTARY + STOCK TRADES RUNS FAILED") wrapped the band
    // at 640px and the phone line at 320, past the header's clearance.
    const first = asLabel(hit[0].label);
    const one = hit.length === 1;
    return {
      // "DATA": the band is about Civitas's own nightly runs. "SENATE
      // FAILED 05:26 UTC", the first words a phone visitor read, said to a
      // student that the Senate had failed.
      label: one
        ? `${first} data run ${verb}`
        : `${first} + ${hit.length - 1} more data runs ${verb}`,
      // Two or more, on a phone, by count alone ("3 DATA RUNS FAILED"):
      // the first name plus a count overran 320px; every name is in the
      // link's title and accessible name.
      short: one ? `${first} data ${shortVerb}` : `${hit.length} data runs ${shortVerb}`,
      everyName: one ? null : hit.map((l) => l.label).join(", "),
      tone,
      // One time per line: with two links named, whose time it was is unclear.
      at: one && state !== "running" ? (hit[0].completedAt ?? hit[0].startedAt) : null,
      complete: false,
    };
  }
  return { ...plain("Complete", "text-phos"), complete: true };
}

const Dot = () => (
  <span className="mx-2 text-ink-min" aria-hidden="true">
    ·
  </span>
);

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

/** The run status, from the module cache: one request for the band and the
 *  phone's status square. */
function useRunStatus(): { status: PipelineStatus | null; loaded: boolean } {
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
  return { status, loaded };
}

/** The status line: "FILED … · COMPLETE", or the link that failed and when. */
function StatusLine({ status, loaded }: { status: PipelineStatus | null; loaded: boolean }) {
  const state = describeRunState(status);
  // Only a run that actually completed gets a "FILED" stamp, and with a link
  // named as failed it says how many links it covers: "FILED" alone beside
  // "SENATE RUN FAILED" would read as the whole record being current.
  const filedAt = status?.filedAt ? formatRunTimestamp(status.filedAt) : null;
  const total = status?.links?.length ?? 0;
  const done = status?.linksCompleted ?? 0;
  const at = state.at ? formatRunTimestamp(state.at) : null;
  const atShort = state.at ? formatRunTime(state.at) : null;

  return (
    <span className="font-mono text-xs text-ink-lo">
      {loaded ? (
        <>
          {/* One line at every width: the fixed header's 96px clearance
              (`--header-clearance`) allows no wrap. So the phone line
              (288px at 320) is the short form, "SUPPLEMENTARY DATA
              FAILED · 4/5 FILED", with the time from 360px and UTC from
              370px; from `sm` the full words and dates; the filed time
              from `lg`. Two or more failed links name the first and
              count the rest (on a phone, the count alone: "3 DATA RUNS
              FAILED"), every name in the link's title and its
              accessible name. Measured for
              every link and state, one to five failed, at 320, 360, 375,
              640, 1024 and 1280. */}
          {state.complete ? (
            <>
              {filedAt && (
                <>
                  Filed {filedAt}
                  <Dot />
                </>
              )}
              <span className={state.tone}>{state.label}</span>
            </>
          ) : total > 0 ? (
            <>
              <Link
                href="/about/data#nightly-runs"
                title={state.everyName ?? undefined}
                className={`${state.tone} underline decoration-dotted underline-offset-2 hover:decoration-solid`}
              >
                <span className="hidden sm:inline">{state.label}</span>
                <span className="sm:hidden">{state.short}</span>
                {state.everyName && <span className="sr-only"> ({state.everyName})</span>}
                {at && (
                  <>
                    {" "}
                    <span className="hidden sm:inline">{at}</span>
                    {/* From 360px: at 320, "SUPPLEMENTARY DATA FAILED"
                        leaves no room for it on the one line. */}
                    <span className="hidden min-[360px]:inline sm:hidden">
                      {atShort}
                      <span className="hidden min-[370px]:inline"> UTC</span>
                    </span>
                  </>
                )}
              </Link>
              <Dot />
              <span className="sm:hidden">
                {done}/{total} filed
              </span>
              <span className="hidden sm:inline">
                {done} of {total} filed
                {filedAt && <span className="hidden lg:inline"> {filedAt}</span>}
              </span>
            </>
          ) : (
            <span className={state.tone}>{state.label}</span>
          )}
        </>
      ) : (
        <span className="text-ink-min">Reading run status…</span>
      )}
    </span>
  );
}

/** `aside`: what the band carries on its right where the desktop row is
 *  shown (56rem, as Navbar); below that it is in MENU. Below 56rem the band
 *  itself folds into the status square beside the wordmark (`RunStatusSquare`):
 *  it was 34px of a phone's 86px sticky header on every screen (mobile spec
 *  P8). */
export default function RecordsBand({ aside }: { aside?: React.ReactNode }) {
  const { status, loaded } = useRunStatus();
  return (
    <div className="hidden border-b-3 border-ink-min/60 bg-surface-base min-[56rem]:block">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-x-6 gap-y-1 px-4 py-2 sm:px-6">
        {/* No "PUBLIC RECORDS OFFICE" label (2026-10): Civitas is a
            volunteer-run open source project, not an office, and a
            letterhead claiming to be one cost the record the credibility
            the run status beside it earns. The status is the band. */}

        {/*
          Deliberately NOT role="status"/aria-live. Navbar (and so this band)
          mounts per page rather than in the root layout, so a live region
          here would re-announce the run number and timestamp ahead of every
          page's own content on every client-side navigation. It is ambient
          provenance, not an update the reader asked for.
        */}
        <StatusLine status={status} loaded={loaded} />
        {aside && <div className="hidden min-[56rem]:block">{aside}</div>}
      </div>
    </div>
  );
}

/**
 * The records band on a phone: a 10px square left of the wordmark, filled
 * when every run completed and hollow otherwise (the tone says which kind of
 * trouble), in a 44px button whose name is the whole status line. A tap opens
 * the line under the header bar; Escape, a second tap or a tap elsewhere
 * closes it. Not a live region, like the band.
 */
/** The square's edge for each status tone, written out for Tailwind's scan. */
const SQUARE_TONE: Record<string, string> = {
  "text-signal-red": "border-signal-red",
  "text-signal-amber": "border-signal-amber",
  "text-ink-min": "border-ink-min",
};

export function RunStatusSquare() {
  const { status, loaded } = useRunStatus();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLSpanElement>(null);
  const id = useId();
  const state = describeRunState(status);
  const filedAt = status?.filedAt ? formatRunTimestamp(status.filedAt) : null;
  const words = !loaded
    ? "Reading run status"
    : state.complete
      ? `${filedAt ? `Filed ${filedAt}, ` : ""}${midSentence(state.label)}`
      : `${midSentence(state.label)}${state.everyName ? ` (${state.everyName})` : ""}, ${status?.linksCompleted ?? 0} of ${status?.links?.length ?? 0} filed`;
  const tone = !loaded
    ? "border-ink-min"
    : state.complete
      ? "border-phos bg-phos"
      : (SQUARE_TONE[state.tone] ?? "border-ink-min");

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onDown = (e: PointerEvent) =>
      box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  return (
    <span ref={box} className="-my-3 -ml-3 flex min-[56rem]:hidden">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        aria-label={`Data status: ${words}`}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex min-h-11 min-w-11 items-center justify-center"
      >
        <span aria-hidden="true" className={`block h-2.5 w-2.5 border-2 ${tone}`} />
      </button>
      <span
        id={id}
        hidden={!open}
        className="absolute inset-x-0 top-full border-b-3 border-ink-min/60 bg-surface-base px-4 py-2"
      >
        <StatusLine status={status} loaded={loaded} />
      </span>
    </span>
  );
}
