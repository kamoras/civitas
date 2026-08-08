"use client";

import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import MatrixRain from "@/components/effects/MatrixRain";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import CandidateCard from "@/components/elections/CandidateCard";
import CoverageFeed from "@/components/elections/CoverageFeed";
import RaceFinancials from "@/components/elections/RaceFinancials";
import PviMethodologyNote from "@/components/elections/PviMethodologyNote";
import { formatPvi, isActiveCandidate, pviColor, raceTitleLabel, stateBallotHref } from "@/lib/elections";
import type { RaceDetail } from "@/types/election";

export default function RaceDetailClient({ race }: { race: RaceDetail }) {
  // FEC candidate files include paper filers and prior-cycle records —
  // collapse those under "OTHER FEC FILERS" so the page stays honest
  // without deleting anyone.
  const activeCandidates = race.candidates.filter(isActiveCandidate);
  const otherFilers = race.candidates.filter((c) => !isActiveCandidate(c));

  return (
    <div className="min-h-screen bg-crt-black text-matrix-green">
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-3xl mx-auto">
          {/* Back to the race's own state page, not the state index:
              under the current IA a race is always reached THROUGH its
              state, so /elections would drop the reader two levels. */}
          <Link
            href={stateBallotHref(race.state)}
            className="inline-block mb-6 font-mono text-xs text-matrix-green/50 hover:text-neon-cyan transition-colors"
          >
            ← BACK TO {race.state} BALLOT
          </Link>

          <div className="terminal-window mb-6">
            <TerminalTitlebar title={`${race.id.toLowerCase()}.dat`} />
            <div className="p-6">
              <div className="flex items-start justify-between gap-4 mb-2 flex-wrap">
                <h1 className="font-pixel text-lg sm:text-2xl text-white/90">
                  {race.cycleYear} {raceTitleLabel(race)}
                  {race.isSpecial && (
                    <span className="ml-2 text-[10px] font-pixel px-1.5 py-0.5 border border-neon-yellow/30 text-neon-yellow/80 align-middle">
                      SPECIAL
                    </span>
                  )}
                </h1>
                <span className={`font-pixel text-sm ${pviColor(race.pvi)}`}>
                  {formatPvi(race.pvi)}
                  {race.pviLevel === "state" && race.office === "H" && (
                    <span className="ml-1.5 font-pixel text-[8px] text-matrix-green/40 align-middle">
                      (STATEWIDE LEAN)
                    </span>
                  )}
                </span>
              </div>
              <p className="text-xs text-matrix-green/40">
                {race.candidates.length} {race.candidates.length === 1 ? "candidate" : "candidates"}{" "}
                on record
              </p>
              <PviMethodologyNote />
            </div>
          </div>

          <div className="terminal-window mb-6">
            <TerminalTitlebar title="candidates.dat" />
            <div className="p-6 space-y-3">
              {race.candidates.length === 0 && (
                <p className="text-sm text-matrix-green/40">
                  No candidates on record for this race yet.
                </p>
              )}
              {activeCandidates.map((c) => (
                <CandidateCard key={c.id} candidate={c} />
              ))}
              {otherFilers.length > 0 && (
                <details className="border border-matrix-green/20 bg-terminal-bg/30 p-3">
                  <summary className="font-pixel text-[10px] text-matrix-green/50 hover:text-matrix-green cursor-pointer">
                    OTHER FEC FILERS ({otherFilers.length})
                  </summary>
                  <p className="text-[10px] text-matrix-green/40 mt-2">
                    Paper filers and prior-cycle candidates on FEC record who have not raised funds
                    this cycle.
                  </p>
                  <div className="space-y-3 mt-3">
                    {otherFilers.map((c) => (
                      <CandidateCard key={c.id} candidate={c} />
                    ))}
                  </div>
                </details>
              )}
            </div>
          </div>

          <div className="terminal-window mb-6">
            <TerminalTitlebar title="fundraising.dat" />
            <div className="p-6">
              <RaceFinancials candidates={activeCandidates} />
              <p className="text-[10px] text-matrix-green/30 mt-3">
                Per FEC filings — totals lag by up to a quarter and amendments. Source: fec.gov.
              </p>
            </div>
          </div>

          <div className="terminal-window mb-6">
            <TerminalTitlebar title="coverage.dat" />
            <div className="p-6">
              <CoverageFeed items={race.coverage} />
            </div>
          </div>
        </div>
      </main>
      <BackToTop />
      <Footer />
    </div>
  );
}
