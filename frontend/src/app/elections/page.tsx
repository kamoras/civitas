"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import BackToTop from "@/components/BackToTop";
import Link from "next/link";
import RaceMap, { FIPS_TO_STATE } from "@/components/elections/RaceMap";
import PviMethodologyNote from "@/components/elections/PviMethodologyNote";
import ResultsOverview from "@/components/elections/results/ResultsOverview";
import { formatPvi, pviColor, stateBallotHref } from "@/lib/elections";
import { formatUtcDate } from "@/lib/formatting";
import {
  countReadRange,
  everyLiveStateVoting,
  feedFailed,
  formatEasternTime,
  pollsStillOpen,
  serverTimeOf,
  showsResults,
  stateFeedBehind,
} from "@/lib/results";
import { useResultsNow } from "@/hooks/useResultsNow";
import { fetchPviMap } from "@/lib/api";
import { describeInterval, useLiveResults } from "@/hooks/useLiveResults";
import type { PviMap } from "@/types/election";

/*
  Second page onto the records palette, after the homepage. Same recipe:
  drop the canvas animation and the glitch heading, set headings in the
  display face, move figures to mono, carry hierarchy on the three rule
  weights, and state the provenance instead of burying it at 9px.

  The map itself (RaceMap) is untouched — only the frame around it and the
  key beside it change.
*/

// Flat, unchanging fill — no hover-brighten variant — so DC reads as
// visibly non-interactive rather than inviting a click that silently
// does nothing (see onStateClick below).
const DC_FILL = "rgba(255, 255, 255, 0.06)";

// Partisan fills, keyed to the party colours in the palette (#6699FF /
// #FF5C5C) rather than the ad-hoc rgb() triples this file used before, so
// the map agrees with every party chip elsewhere on the site.
function pviFillColor(pvi: number | null): string {
  if (pvi == null) return "rgba(255, 255, 255, 0.07)";
  if (pvi === 0) return "rgba(255, 255, 255, 0.22)";
  return pvi > 0 ? "rgba(255, 92, 92, 0.32)" : "rgba(102, 153, 255, 0.32)";
}

function pviHoverColor(pvi: number | null): string {
  if (pvi == null) return "rgba(0, 255, 65, 0.30)";
  if (pvi === 0) return "rgba(255, 255, 255, 0.42)";
  return pvi > 0 ? "rgba(255, 92, 92, 0.55)" : "rgba(102, 153, 255, 0.55)";
}

// DC has no voting House/Senate race (the backend's
// election_calendar.federal_states(), read from the Senate's classes,
// excludes it the same way) even though it's clickable on the map's SVG —
// filtered out of both the map's click target and the state grid, which
// list the states with federal contests.
const STATES = Array.from(new Set(Object.values(FIPS_TO_STATE)))
  .filter((s) => s !== "DC")
  .sort();

/** Whole days from `asOf`'s calendar date to `isoDate`, both in local time. */
function daysUntil(isoDate: string, asOf: number): number | null {
  const target = new Date(`${isoDate}T00:00:00`);
  if (Number.isNaN(target.getTime())) return null;
  const today = new Date(asOf);
  today.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - today.getTime()) / 86_400_000);
}

/** The masthead's figure: how far off the next federal Election Day is. */
function ElectionCountdown({ electionDay, asOf }: { electionDay: string; asOf: number }) {
  const days = daysUntil(electionDay, asOf);
  if (days === null || days < 0) return null;
  return (
    <div className="text-right">
      <p className="font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
        Election Day ·{" "}
        {formatUtcDate(electionDay, { month: "short", day: "numeric", year: "numeric" })}
      </p>
      <p className="mt-1 font-display text-3xl font-extrabold leading-none tabular-nums text-ink-hi">
        {days === 0 ? "Today" : days}
        {days > 0 && (
          <span className="ml-2 font-mono text-xs font-normal uppercase tracking-[0.12em] text-ink-lo">
            day{days !== 1 ? "s" : ""} away
          </span>
        )}
      </p>
    </div>
  );
}

export default function ElectionsPage() {
  const router = useRouter();
  const [pvi, setPvi] = useState<PviMap | null>(null);
  // The clock is read when the data lands, not on every render, so the
  // countdown is a function of what was fetched.
  const [asOf, setAsOf] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // From election day until the results window closes the page leads with
  // the live count (backend election_phase). One request says which; it
  // polls while there are results to show, and — slowly — while election
  // day is near, so a page left open switches over by itself. A request
  // that keeps failing is retried on a growing backoff.
  const { data: results, error: resultsError, retryMs, failedAt } = useLiveResults();
  const retryEvery = describeInterval(retryMs ?? 60_000).toUpperCase();
  const resultsMode = !!results && showsResults(results.phase);
  // The lean map waits until the phase is known: on election night a lean
  // map drawn first, then swapped for the count, reads as a prediction of
  // it. If the results check fails the campaign page stands (and says the
  // check failed); the hook keeps retrying and switches when it answers.
  const phaseKnown = !!results || !!resultsError;
  const campaignMode = phaseKnown && !resultsMode;
  // The clock is read only while the page shows results — for when polls
  // close, and for whether the backend is still reading the feeds. Any
  // other time, subscribing would re-render the whole page once a second.
  // It is the server's clock as of the last answer (resultsNow), not the
  // browser's; it stops where it stood while this page's own refreshes fail,
  // and never runs backwards (useResultsNow). The one clock for the page:
  // ResultsOverview is handed it.
  const now = useResultsNow(results, failedAt, resultsMode);
  // Election day before any covered state's polls close: nothing of any
  // count is shown yet, so the masthead says when the first can be — not
  // "results" as if there were some. Once one state's polls close, the count
  // leads. The same rule words the page's search and link-card metadata
  // (elections/layout.tsx).
  const stillVoting = resultsMode && !!results && everyLiveStateVoting(results, now);
  // The earliest a count can appear: the soonest of the covered states'
  // LAST closing times (nothing of a state is read before its last polls
  // close) — not when the first polls anywhere close.
  const firstCount = stillVoting
    ? Object.values(results?.pollsClose ?? {})
        .filter((t) => Date.parse(t) > now)
        .sort((a, b) => Date.parse(a) - Date.parse(b))[0]
    : undefined;
  // How old the counts on screen are, said whenever they may not be live —
  // a refresh that failed, or a backend that has stopped reading the feeds.
  // Oldest and newest: the newest state's read alone would pass for all.
  const range = results ? countReadRange(results) : null;
  const readAtText = range
    ? range.oldest === range.newest
      ? `SHOWING THE COUNT READ AT ${formatEasternTime(range.newest).toUpperCase()}`
      : `SHOWING COUNTS READ BETWEEN ${formatEasternTime(range.oldest).toUpperCase()} AND ${formatEasternTime(range.newest).toUpperCase()}`
    : null;
  const refreshFailedDetail = [
    failedAt != null
      ? // On the server's clock, like the read times beside it: the
        // browser's can be hours off, and would date the failure before
        // the count it failed to refresh.
        `AT ${formatEasternTime(new Date(serverTimeOf(results, failedAt)).toISOString()).toUpperCase()}`
      : null,
    stillVoting ? null : readAtText,
    `RETRYING ${retryEvery}`,
  ]
    .filter(Boolean)
    .join(" · ");
  // Every covered state past its polls is either failing to read or hasn't
  // been read for well over a sync pass (a state with no read record at all
  // by then counts: stateFeedBehind): nothing on the page is live, so the
  // masthead mustn't say LIVE. (Some but not all: their own rows say
  // STALE.)
  const closedLive =
    resultsMode && results
      ? results.liveStates.filter((st) => !pollsStillOpen(results, st, now))
      : [];
  const behindStates = results ? closedLive.filter((st) => stateFeedBehind(results, st, now)) : [];
  const failedStates = closedLive.filter((st) => feedFailed(results?.feeds?.[st]));
  const nothingLive =
    closedLive.length > 0 &&
    closedLive.every((st) => behindStates.includes(st) || failedStates.includes(st));
  // Which it is, since the two mean different things: the backend has
  // stopped reading, or it reads and every read fails.
  const staleWhy =
    behindStates.length === closedLive.length
      ? "NO STATE'S FEED CHECKED LATELY"
      : behindStates.length === 0
        ? "EVERY STATE'S LATEST FEED READ FAILED"
        : "NO STATE'S FEED READ SUCCESSFULLY LATELY";

  // The masthead's one status line (see the masthead below): `text` is the
  // live region — what the page's state is, which changes only on a
  // transition — and `detail` its times and figures beside it, outside
  // the region, so a count moving or a retry's wait growing isn't
  // re-announced every pass.
  const status: { text: string; detail?: string; tone: "cyan" | "amber" | "muted" } | null =
    stillVoting && results
      ? resultsError
        ? { tone: "cyan", text: "REFRESH FAILED", detail: refreshFailedDetail }
        : {
            tone: "cyan",
            text: "ELECTION DAY",
            detail: firstCount
              ? `FIRST STATE'S COUNT SHOWN AFTER ${formatEasternTime(firstCount).toUpperCase()}`
              : "NO COUNT SHOWN UNTIL A STATE'S LAST POLLS CLOSE",
          }
      : resultsMode && results
        ? resultsError
          ? { tone: "amber", text: "REFRESH FAILED", detail: refreshFailedDetail }
          : results.liveStates.length === 0
            ? { tone: "muted", text: "NO STATE'S COUNT IS READ LIVE HERE" }
            : nothingLive
              ? { tone: "amber", text: `STALE · ${staleWhy}`, detail: readAtText ?? undefined }
              : {
                  tone: "amber",
                  text: "LIVE",
                  detail: results.phase.lastResultChange
                    ? `LAST CHANGE ${formatEasternTime(results.phase.lastResultChange).toUpperCase()}`
                    : "NO COUNT HAS COME IN YET",
                }
        : campaignMode && resultsError && !results
          ? {
              tone: "muted",
              text: "COULDN'T CHECK FOR LIVE RESULTS",
              detail: `RETRYING ${retryEvery}`,
            }
          : null;

  useEffect(() => {
    let cancelled = false;
    fetchPviMap()
      .then((p) => {
        if (!cancelled) {
          setPvi(p);
          setAsOf(Date.now());
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || "Failed to load election data");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const goToBallot = (state: string) => router.push(stateBallotHref(state));

  /* `states` is indexed unguarded in three places below, and a payload without
     it takes the whole page down with "Cannot read properties of undefined" —
     a white screen, not a degraded map. That is reachable: `meta` on this same
     response is already documented as possibly missing on older or cached
     backend responses, and nothing validates the shape on the way in. A map
     with no lean data still renders and still links to every state ballot,
     which is the page's actual job. */
  const leanByState: Record<string, number> = pvi?.states ?? {};

  const leans = STATES.map((s) => leanByState[s]).filter((v): v is number => typeof v === "number");
  const rLean = leans.filter((v) => v > 0).length;
  const dLean = leans.filter((v) => v < 0).length;
  const even = leans.filter((v) => v === 0).length;

  return (
    <div className="min-h-screen bg-surface-base text-ink">
      <Navbar />
      <main
        id="main-content"
        tabIndex={-1}
        className="pt-[var(--header-clearance)] pb-16 px-4 sm:px-6"
      >
        <div className="mx-auto max-w-7xl">
          {/* ── Masthead ── One header whatever the mode, and one status
              line in it that exists from the first render and only changes
              its words: a live region inserted when the mode switches (the
              page left open as polls close, or as results start) is
              usually not announced, which is exactly the moment worth
              announcing. */}
          <PageMasthead
            eyebrow={
              stillVoting && results
                ? `Elections · election day · ${results.phase.electionDate}`
                : resultsMode && results
                  ? `Elections · ${results.phase.phase === "election_day" ? "election day" : "results"} · ${
                      results.phase.electionDate
                    }`
                  : campaignMode
                    ? "Elections · partisan lean by state"
                    : "Elections"
            }
            title={
              stillVoting && results
                ? `${results.cycleYear} midterms: election day`
                : resultsMode && results
                  ? `${results.cycleYear} midterm results`
                  : campaignMode
                    ? pvi?.cycleYear
                      ? `${pvi.cycleYear} midterm ballot`
                      : "Midterm ballot"
                    : // Phase not known yet: say nothing either mode would
                      // contradict.
                      pvi?.cycleYear
                      ? `${pvi.cycleYear} midterm elections`
                      : "Midterm elections"
            }
            aside={
              <div className="flex flex-col items-end gap-3">
                <p
                  className={
                    status
                      ? `flex flex-wrap items-center gap-x-2 border px-3 py-1.5 font-mono text-xs tracking-[0.12em] ${
                          status.tone === "cyan"
                            ? "border-signal-cyan/40 text-signal-cyan"
                            : status.tone === "amber"
                              ? "border-signal-amber/40 text-signal-amber"
                              : "border-white/15 text-ink-min"
                        }`
                      : "sr-only"
                  }
                >
                  {status && status.tone !== "muted" && (
                    <span
                      aria-hidden="true"
                      className={`inline-block h-2 w-2 ${
                        status.tone === "cyan" ? "bg-signal-cyan" : "bg-signal-amber"
                      }`}
                    />
                  )}
                  {/* The live region: the page's state alone, so only a
                      change of state is announced. */}
                  <span role="status" aria-live="polite">
                    {status?.text ?? ""}
                  </span>
                  {/* A space for the line's text as a whole (the flex gap
                      is only visual). */}
                  {status?.detail && (
                    <>
                      {" "}
                      <span>· {status.detail}</span>
                    </>
                  )}
                </p>
                {campaignMode && pvi?.electionDay && asOf !== null && (
                  <ElectionCountdown electionDay={pvi.electionDay} asOf={asOf} />
                )}
              </div>
            }
          >
            {stillVoting && results ? (
              <>
                It&apos;s election day. Counts appear here as each state&apos;s last polls close, as
                the state&apos;s own election office publishes them: nothing of a state&apos;s count
                is shown before then. Every state&apos;s ballot research is one click away.
              </>
            ) : resultsMode && results ? (
              <>
                Counts as each state&apos;s own election office publishes them. Civitas reads each
                state&apos;s feed every five minutes (hourly once no count has moved for a day), and
                this page checks for a new read every minute. A race &ldquo;leads&rdquo; even once
                the state lists its count as official; Civitas calls no race.
              </>
            ) : campaignMode ? (
              <>
                Pick a state for its candidates, their filings, statewide ballot measures, and the
                coverage we have ingested. Shading is partisan lean, not a forecast.
              </>
            ) : null}
          </PageMasthead>

          {resultsMode && results && (
            <ResultsOverview
              results={results}
              states={STATES}
              now={now}
              refreshFailed={!!resultsError}
            />
          )}

          {error && campaignMode && (
            <div
              role="alert"
              className="mt-6 border-l-2 border-signal-red bg-surface px-4 py-3 font-mono text-sm text-signal-red"
            >
              {error}
            </div>
          )}

          {!error && (!pvi || !phaseKnown) && !resultsMode && (
            <p
              role="status"
              aria-live="polite"
              className="mt-8 font-mono text-sm tracking-[0.12em] text-ink-min"
            >
              READING THE LEAN MAP…
            </p>
          )}

          {pvi && campaignMode && (
            <>
              {/* ── Map ── */}
              <section className="mt-6 border border-phos/20 bg-surface">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.07] px-4 py-2.5">
                  <h2 className="font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
                    Click a state for its ballot
                  </h2>
                  <div className="flex flex-wrap items-center gap-4">
                    <span className="flex items-center gap-2 font-mono text-xs text-ink-lo">
                      <span
                        className="inline-block h-2.5 w-4"
                        style={{ backgroundColor: "rgba(102, 153, 255, 0.32)" }}
                        aria-hidden="true"
                      />
                      D-LEANING <span className="text-ink-min">{dLean}</span>
                    </span>
                    <span className="flex items-center gap-2 font-mono text-xs text-ink-lo">
                      <span
                        className="inline-block h-2.5 w-4"
                        style={{ backgroundColor: "rgba(255, 92, 92, 0.32)" }}
                        aria-hidden="true"
                      />
                      R-LEANING <span className="text-ink-min">{rLean}</span>
                    </span>
                    <span className="flex items-center gap-2 font-mono text-xs text-ink-lo">
                      <span
                        className="inline-block h-2.5 w-4"
                        style={{ backgroundColor: "rgba(255, 255, 255, 0.22)" }}
                        aria-hidden="true"
                      />
                      EVEN <span className="text-ink-min">{even}</span>
                    </span>
                  </div>
                </div>

                <div className="px-4 pt-3">
                  <RaceMap
                    selectedState={null}
                    onStateClick={(state) => {
                      if (state !== "DC") goToBallot(state);
                    }}
                    getFillColor={(state) =>
                      state === "DC" ? DC_FILL : pviFillColor(leanByState[state] ?? null)
                    }
                    getHoverFillColor={(state) =>
                      state === "DC" ? DC_FILL : pviHoverColor(leanByState[state] ?? null)
                    }
                  />
                </div>

                <div className="border-t border-white/[0.07] px-4 py-3">
                  <PviMethodologyNote meta={pvi.meta} />
                </div>
              </section>

              {/* ── Directory ── */}
              <section className="mt-10">
                <h2 className="flex items-baseline justify-between border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
                  <span>All states</span>
                  <span aria-hidden="true">{STATES.length} on file</span>
                </h2>
                <ul className="mt-3 grid grid-cols-3 gap-px bg-white/[0.07] sm:grid-cols-5 md:grid-cols-6 lg:grid-cols-8">
                  {STATES.map((state) => (
                    <li key={state}>
                      <Link
                        href={stateBallotHref(state)}
                        className="flex items-baseline justify-between bg-surface-base px-3 py-2.5 transition-colors hover:bg-surface-raised"
                      >
                        <span className="font-mono text-sm text-ink-hi">{state}</span>
                        <span
                          className={`font-mono text-xs ${pviColor(leanByState[state] ?? null)}`}
                        >
                          {formatPvi(leanByState[state] ?? null)}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </section>
            </>
          )}
        </div>
      </main>
      <BackToTop />
      <Footer />
    </div>
  );
}
