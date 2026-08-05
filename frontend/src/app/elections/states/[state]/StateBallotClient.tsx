"use client";

import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import MatrixRain from "@/components/effects/MatrixRain";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import RaceCard from "@/components/elections/RaceCard";
import BallotMeasureCard from "@/components/elections/BallotMeasureCard";
import { formatPvi, pviColor } from "@/lib/elections";
import type { StateBallot } from "@/types/election";

/** The measures section, including the three ways it can be empty.
 *
 * The whole point of this component is that "this state has no measures"
 * and "we don't know this state's measures" are different claims. An
 * empty section under a heading like "STATEWIDE BALLOT MEASURES" reads as
 * the first, so a state we simply have not ingested — 17 amendments and
 * all — would silently tell a voter there is nothing to research.
 */
function MeasuresSection({ ballot }: { ballot: StateBallot }) {
  const { measures, measureCoverage, officialLookup, state } = ballot;

  if (measures.length > 0) {
    return (
      <div className="space-y-3">
        {measures.map((m) => (
          <BallotMeasureCard key={m.id} measure={m} />
        ))}
        <p className="text-[10px] text-matrix-green/40">
          {measures.length} statewide {measures.length === 1 ? "measure" : "measures"} on
          record from {measureCoverage.sourceName || "the source"}
          {measureCoverage.checkedAt
            ? ` · last checked ${measureCoverage.checkedAt.slice(0, 10)}`
            : ""}
          . Local measures on your ballot are not shown here.
        </p>
      </div>
    );
  }

  if (measureCoverage.status === "confirmed_none") {
    return (
      <div className="border border-matrix-green/20 p-4">
        <p className="text-sm text-matrix-green/70">
          No statewide ballot measures are on {state}&apos;s {ballot.electionDate} ballot.
        </p>
        <p className="text-[10px] text-matrix-green/40 mt-2">
          Per {measureCoverage.sourceName || "our source"}
          {measureCoverage.checkedAt
            ? `, checked ${measureCoverage.checkedAt.slice(0, 10)}`
            : ""}
          . Your county or city may still have local measures — check the official
          lookup above.
        </p>
      </div>
    );
  }

  // not_yet_covered / ingest_failed — say so plainly. Never imply zero.
  return (
    <div className="border border-neon-yellow/30 bg-neon-yellow/5 p-4">
      <p className="text-sm text-neon-yellow/80">
        Civitas does not have {state}&apos;s statewide ballot measures yet.
      </p>
      <p className="text-xs text-matrix-green/60 mt-2">
        This does <strong>not</strong> mean there are none —{" "}
        {measureCoverage.status === "ingest_failed"
          ? "our last attempt to load them failed"
          : "we have not ingested this state yet"}
        . Use the official lookup above to see everything on your ballot.
      </p>
      {measureCoverage.checkedAt && (
        <p className="text-[10px] text-matrix-green/40 mt-2">
          Last attempt {measureCoverage.checkedAt.slice(0, 10)}.
        </p>
      )}
    </div>
  );
}

export default function StateBallotClient({ ballot }: { ballot: StateBallot }) {
  const { officialLookup } = ballot;

  return (
    <div className="min-h-screen bg-crt-black text-matrix-green">
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-3xl mx-auto">
          <Link
            href="/elections"
            className="inline-block mb-6 font-mono text-xs text-matrix-green/50 hover:text-neon-cyan transition-colors"
          >
            ← ALL STATES
          </Link>

          <div className="terminal-window mb-6">
            <TerminalTitlebar title={`${ballot.state.toLowerCase()}-ballot.dat`} />
            <div className="p-6">
              <h1 className="font-pixel text-lg sm:text-2xl text-white/90 mb-1">
                {ballot.state}
                {" — federal contests & statewide measures"}
              </h1>
              {/* Naming the election is load-bearing, not decoration:
                  primaries are party-specific and on ~50 different dates,
                  so a page that just says "the ballot" is wrong for most
                  of the year. */}
              <p className="font-mono text-xs text-matrix-green/50">
                {ballot.electionType.toUpperCase()} ELECTION · {ballot.electionDate}
              </p>
              {ballot.statePvi !== null && (
                <p className={`font-pixel text-sm mt-2 ${pviColor(ballot.statePvi)}`}>
                  {formatPvi(ballot.statePvi)}{" "}
                  <span className="font-mono text-[10px] text-matrix-green/40">
                    statewide lean
                  </span>
                </p>
              )}
            </div>
          </div>

          {/* Scope + the way out, ABOVE the content rather than in a
              footnote — the page shows a minority of what a voter will
              actually be handed, and burying that under the content is
              how a partial digest gets read as a complete ballot. */}
          <section className="terminal-window mb-6 border-t-2 border-t-neon-cyan/40">
            <div className="p-5">
              <h2 className="font-pixel text-xs text-neon-cyan/70 mb-2">
                THIS IS NOT YOUR FULL BALLOT
              </h2>
              <p className="text-xs text-matrix-green/70 mb-3">
                Ballots are printed per precinct, so most of what you will vote on
                cannot be shown on a statewide page. Not included here:
              </p>
              <ul className="text-xs text-matrix-green/60 list-disc pl-5 mb-4 space-y-0.5">
                {ballot.omits.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
              <a
                href={officialLookup.url}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={`${officialLookup.label} (opens in new tab)`}
                className="inline-block font-pixel text-[11px] px-3 py-2 border border-neon-cyan/40
                           text-neon-cyan/90 hover:bg-neon-cyan/10 transition-colors"
              >
                {officialLookup.isStateSpecific
                  ? `SEE YOUR FULL ${ballot.state} BALLOT ↗`
                  : "FIND YOUR ELECTION OFFICE ↗"}
              </a>
              <p className="text-[10px] text-matrix-green/40 mt-2">
                {officialLookup.label} · {officialLookup.sourceName}
              </p>
            </div>
          </section>

          {ballot.senateRaces.length > 0 && (
            <section className="terminal-window mb-6">
              <TerminalTitlebar title="senate.dat" />
              <div className="p-6 space-y-3">
                <h2 className="font-pixel text-xs text-matrix-green/50">U.S. SENATE</h2>
                {ballot.senateRaces.map((race) => (
                  <RaceCard key={race.id} race={race} />
                ))}
              </div>
            </section>
          )}

          <section className="terminal-window mb-6">
            <TerminalTitlebar title="measures.dat" />
            <div className="p-6">
              <h2 className="font-pixel text-xs text-matrix-green/50 mb-3">
                STATEWIDE BALLOT MEASURES
              </h2>
              <MeasuresSection ballot={ballot} />
            </div>
          </section>

          {ballot.houseRaces.length > 0 && (
            <section className="terminal-window mb-6">
              <TerminalTitlebar title="house.dat" />
              <div className="p-6">
                <h2 className="font-pixel text-xs text-matrix-green/50 mb-1">
                  U.S. HOUSE — {ballot.houseRaces.length}{" "}
                  {ballot.houseRaces.length === 1 ? "DISTRICT" : "DISTRICTS"}
                </h2>
                {/* Only one of these is on any given ballot, and we can't
                    tell which without an address we deliberately don't
                    collect — so say that rather than implying all of them
                    are the visitor's. */}
                <p className="text-[11px] text-matrix-green/40 mb-3">
                  You vote in exactly one of these. Civitas does not ask for your
                  address, so it cannot tell which —{" "}
                  <a
                    href="https://www.house.gov/representatives/find-your-representative"
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label="Find your representative at house.gov (opens in new tab)"
                    className="text-neon-cyan/70 hover:text-neon-cyan"
                  >
                    look up your district at house.gov ↗
                  </a>
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {ballot.houseRaces.map((race) => (
                    <RaceCard key={race.id} race={race} />
                  ))}
                </div>
              </div>
            </section>
          )}

          {ballot.senateRaces.length === 0 && ballot.houseRaces.length === 0 && (
            <p className="text-sm text-matrix-green/40">
              No federal races on record for {ballot.state} in {ballot.cycleYear} yet.
            </p>
          )}
        </div>
      </main>
      <BackToTop />
      <Footer />
    </div>
  );
}
