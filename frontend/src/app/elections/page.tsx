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
import { formatEasternTime, pollsStillOpen, showsResults } from "@/lib/results";
import { useNow } from "@/hooks/useNow";
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

// DC has no voting House/Senate race (STATES_WITH_FEDERAL_RACES on the
// backend excludes it the same way) even though it's clickable on the
// map's SVG — filtered out of both the map's click target and the
// directory grid rather than letting either lead to a 404.
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
  const { data: results, error: resultsError, retryMs } = useLiveResults();
  const retryEvery = describeInterval(retryMs ?? 60_000).toUpperCase();
  const resultsMode = !!results && showsResults(results.phase);
  // The lean map waits until the phase is known: on election night a lean
  // map drawn first, then swapped for the count, reads as a prediction of
  // it. If the results check fails the campaign page stands (and says the
  // check failed); the hook keeps retrying and switches when it answers.
  const phaseKnown = !!results || !!resultsError;
  const campaignMode = phaseKnown && !resultsMode;
  // Election day before any covered state's polls close: people are still
  // voting, so the masthead says results come in as polls close — not
  // "results" as if there were some. Once one state's polls close, the count
  // leads.
  // The clock is only read on election day before any count: any other
  // time, subscribing would re-render the whole page once a second.
  const beforeAnyCount =
    resultsMode &&
    !!results &&
    results.phase.phase === "election_day" &&
    results.races.length === 0;
  const now = useNow(beforeAnyCount);
  const stillVoting =
    beforeAnyCount &&
    !!results &&
    results.liveStates.every((st) => pollsStillOpen(results, st, now));
  const firstClose = stillVoting
    ? Object.values(results?.pollsClose ?? {})
        .filter((t) => Date.parse(t) > now)
        .sort((a, b) => Date.parse(a) - Date.parse(b))[0]
    : undefined;

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
          {/* ── Masthead ── */}
          {stillVoting && results ? (
            <PageMasthead
              eyebrow={`Elections · election day · ${results.phase.electionDate}`}
              title={`${results.cycleYear} midterms: polls are open`}
              aside={
                <p
                  role="status"
                  aria-live="polite"
                  className="flex items-center gap-2 border border-signal-cyan/40 px-3 py-1.5 font-mono text-xs tracking-[0.12em] text-signal-cyan"
                >
                  <span aria-hidden="true" className="inline-block h-2 w-2 bg-signal-cyan" />
                  {resultsError
                    ? `REFRESH FAILED · RETRYING ${retryEvery}`
                    : firstClose
                      ? `POLLS OPEN · FIRST CLOSE ${formatEasternTime(firstClose).toUpperCase()}`
                      : "POLLS OPEN"}
                </p>
              }
            >
              Voting is under way. Counts appear here as each state&apos;s polls close, as the
              state&apos;s own election office publishes them — nothing of a state&apos;s count is
              shown before its last polls close. Every state&apos;s ballot research is one click
              away.
            </PageMasthead>
          ) : resultsMode && results ? (
            <PageMasthead
              eyebrow={`Elections · ${results.phase.phase === "election_day" ? "election day" : "results"} · ${
                results.phase.electionDate
              }`}
              title={`${results.cycleYear} midterm results`}
              aside={
                <p
                  role="status"
                  aria-live="polite"
                  className="flex items-center gap-2 border border-signal-amber/40 px-3 py-1.5 font-mono text-xs tracking-[0.12em] text-signal-amber"
                >
                  <span aria-hidden="true" className="inline-block h-2 w-2 bg-signal-amber" />
                  {resultsError
                    ? `REFRESH FAILED · RETRYING ${retryEvery}`
                    : results.phase.lastResultChange
                      ? `LIVE · LAST CHANGE ${formatEasternTime(results.phase.lastResultChange)}`
                      : "LIVE · WAITING FOR FIRST COUNTS"}
                </p>
              }
            >
              Counts as each state&apos;s own election office publishes them, refreshed every
              minute. A race is leading until the state calls its count official; Civitas does not
              call races.
            </PageMasthead>
          ) : campaignMode ? (
            <PageMasthead
              eyebrow="Elections · partisan lean by state"
              title={pvi?.cycleYear ? `${pvi.cycleYear} midterm ballot` : "Midterm ballot"}
              aside={
                pvi?.electionDay && asOf !== null ? (
                  <ElectionCountdown electionDay={pvi.electionDay} asOf={asOf} />
                ) : undefined
              }
            >
              Pick a state for its candidates, their filings, statewide ballot measures, and the
              coverage we have ingested. Shading is partisan lean, not a forecast.
            </PageMasthead>
          ) : (
            // Phase not known yet: say nothing either mode would contradict.
            <PageMasthead
              eyebrow="Elections"
              title={pvi?.cycleYear ? `${pvi.cycleYear} midterm elections` : "Midterm elections"}
            />
          )}

          {resultsMode && results && <ResultsOverview results={results} states={STATES} />}

          {campaignMode && resultsError && !results && (
            <p role="status" className="mt-6 font-mono text-xs tracking-[0.1em] text-ink-min">
              COULDN&apos;T CHECK FOR LIVE RESULTS · RETRYING {retryEvery}
            </p>
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
