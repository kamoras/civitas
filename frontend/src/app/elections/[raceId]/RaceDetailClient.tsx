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
import { formatPvi, pviColor } from "@/components/elections/RaceCard";
import type { RaceDetail } from "@/types/election";

function raceLabel(race: RaceDetail): string {
  if (race.office === "S") return `${race.state} Senate`;
  return race.district ? `${race.state}-${race.district} House` : `${race.state} House`;
}

export default function RaceDetailClient({ race }: { race: RaceDetail }) {
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
            ← BACK TO RACES
          </Link>

          <div className="terminal-window mb-6">
            <TerminalTitlebar title={`${race.id.toLowerCase()}.dat`} />
            <div className="p-6">
              <div className="flex items-start justify-between gap-4 mb-2 flex-wrap">
                <h1 className="font-pixel text-lg sm:text-2xl text-white/90">
                  {race.cycleYear} {raceLabel(race)}
                  {race.isSpecial && (
                    <span className="ml-2 text-[10px] font-pixel px-1.5 py-0.5 border border-neon-yellow/30 text-neon-yellow/80 align-middle">
                      SPECIAL
                    </span>
                  )}
                </h1>
                <span className={`font-pixel text-sm ${pviColor(race.pvi)}`}>{formatPvi(race.pvi)}</span>
              </div>
              <p className="text-xs text-matrix-green/40">
                {race.candidates.length} {race.candidates.length === 1 ? "candidate" : "candidates"} on record
              </p>
            </div>
          </div>

          <div className="terminal-window mb-6">
            <TerminalTitlebar title="candidates.dat" />
            <div className="p-6 space-y-3">
              {race.candidates.length === 0 ? (
                <p className="text-sm text-matrix-green/40">No candidates on record for this race yet.</p>
              ) : (
                race.candidates.map((c) => <CandidateCard key={c.id} candidate={c} />)
              )}
            </div>
          </div>

          <div className="terminal-window mb-6">
            <TerminalTitlebar title="fundraising.dat" />
            <div className="p-6">
              <RaceFinancials candidates={race.candidates} />
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
