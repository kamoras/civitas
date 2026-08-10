"use client";

import { useState } from "react";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import MatrixRain from "@/components/effects/MatrixRain";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import BallotRaceOptions from "@/components/elections/BallotRaceOptions";
import CoverageFeed from "@/components/elections/CoverageFeed";
import AddressLookup from "@/components/elections/AddressLookup";
import PviMethodologyNote from "@/components/elections/PviMethodologyNote";
import { districtCountiesLabel, formatPvi, pviColor, raceShortLabel } from "@/lib/elections";
import type { StateBallot } from "@/types/election";

function HouseSection({
  state,
  houseRaces,
}: {
  state: string;
  houseRaces: StateBallot["houseRaces"];
}) {
  const [selectedId, setSelectedId] = useState("");
  const selected = houseRaces.find((r) => r.id === selectedId) || null;

  return (
    <section className="terminal-window mb-6">
      <TerminalTitlebar title="house.dat" />
      <div className="p-6">
        <h2 className="font-pixel text-xs text-matrix-green/50 mb-1">
          U.S. HOUSE — {houseRaces.length} {houseRaces.length === 1 ? "DISTRICT" : "DISTRICTS"}
        </h2>
        {/* You vote in exactly one of these. The address lookup below is
            optional and resolve-only (never stored) — entering your
            address is not required; the dropdown works on its own. */}
        <p className="text-[11px] text-matrix-green/40 mb-3">
          You vote in exactly one of these. Enter your address below to find it automatically, or
          pick it from the dropdown, or{" "}
          <a
            href="https://www.house.gov/representatives/find-your-representative"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Find your representative at house.gov (opens in new tab)"
            className="text-neon-cyan/70 hover:text-neon-cyan"
          >
            look it up at house.gov ↗
          </a>
          .
        </p>
        <AddressLookup
          ballotState={state}
          onResolved={(district) => {
            const match = houseRaces.find((r) => r.district === district);
            if (match) setSelectedId(match.id);
            return match != null;
          }}
        />
        <select
          value={selectedId}
          onChange={(e) => setSelectedId(e.target.value)}
          className="bg-crt-black border border-matrix-green/30 text-matrix-green font-mono text-xs px-3 py-2 mb-4"
          aria-label="Select your district"
        >
          <option value="">— select your district —</option>
          {houseRaces.map((r) => {
            const countiesLabel = districtCountiesLabel(r.counties);
            return (
              <option key={r.id} value={r.id}>
                {raceShortLabel(r)}
                {countiesLabel ? ` — ${countiesLabel}` : ""}
              </option>
            );
          })}
        </select>

        {selected && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="font-pixel text-xs text-white/80">{raceShortLabel(selected)}</span>
              <span className={`font-pixel text-[10px] ${pviColor(selected.pvi)}`}>
                {formatPvi(selected.pvi)}
                {selected.pviLevel === "state" && (
                  <span className="ml-1 text-matrix-green/40">(statewide lean)</span>
                )}
              </span>
            </div>
            {selected.counties && (
              <p className="text-[11px] text-matrix-green/40 mb-3">
                Covers: {selected.counties.join(", ")}
              </p>
            )}
            <BallotRaceOptions race={selected} />
          </div>
        )}
      </div>
    </section>
  );
}

export default function StateBallotClient({ ballot }: { ballot: StateBallot }) {
  const hasFederalRaces = ballot.senateRaces.length > 0 || ballot.houseRaces.length > 0;

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
                {ballot.state} — {ballot.cycleYear} GENERAL ELECTION
              </h1>
              <p className="font-mono text-xs text-matrix-green/50">{ballot.electionDate}</p>
              {ballot.statePvi !== null && (
                <>
                  <p className={`font-pixel text-sm mt-2 ${pviColor(ballot.statePvi)}`}>
                    {formatPvi(ballot.statePvi)}{" "}
                    <span className="font-mono text-[10px] text-matrix-green/40">statewide lean</span>
                  </p>
                  {/* This is the newest page showing a raw PVI figure — the
                      map and per-race pages already explain it, this one
                      didn't (2026-08 review): "R+12"/"D+8" means nothing
                      to a reader who hasn't seen Cook PVI notation before. */}
                  <PviMethodologyNote />
                </>
              )}
            </div>
          </div>

          {/* Front and center, not one click away on a per-race page —
              a voter's first question is usually "what's being said
              about my ballot", not just "who's on it" (2026-08 review). */}
          <section className="terminal-window mb-6">
            <TerminalTitlebar title="coverage.dat" />
            <div className="p-6">
              <h2 className="font-pixel text-xs text-matrix-green/50 mb-3">
                NEWS COVERAGE — {ballot.state}
              </h2>
              <div className="max-h-[420px] overflow-y-auto pr-2">
                <CoverageFeed items={ballot.coverage} />
              </div>
            </div>
          </section>

          <section className="terminal-window mb-6 border-t-2 border-t-neon-cyan/40">
            <div className="p-5">
              <h2 className="font-pixel text-xs text-neon-cyan/70 mb-2">
                THIS IS NOT YOUR FULL BALLOT
              </h2>
              <p className="text-xs text-matrix-green/70">
                This page shows federal races only — U.S. Senate and House. Ballots are printed per
                precinct, so state and local races, ballot questions, and judicial retention votes
                are not shown here. Check with your local election office for everything else on
                your ballot.
              </p>
            </div>
          </section>

          {ballot.senateRaces.length > 0 && (
            <section className="terminal-window mb-6">
              <TerminalTitlebar title="senate.dat" />
              <div className="p-6">
                <h2 className="font-pixel text-xs text-matrix-green/50 mb-3">U.S. SENATE</h2>
                {/* A state normally has exactly one Senate race per cycle
                    (only one of its two seats' classes is ever up at
                    once) — the ONLY way a second one appears is a
                    special election filling a vacancy alongside the
                    regularly-scheduled race. When that happens, showing
                    both candidate lists under one unlabeled "U.S.
                    SENATE" heading would blend two different seats'
                    options together, so label each race once there's
                    more than one. */}
                {ballot.senateRaces.map((race) => (
                  <div key={race.id} className={ballot.senateRaces.length > 1 ? "mb-5" : ""}>
                    {ballot.senateRaces.length > 1 && (
                      <p className="font-pixel text-[10px] text-neon-cyan/70 mb-2 tracking-widest">
                        {race.isSpecial ? "SPECIAL ELECTION" : "REGULAR ELECTION"}
                      </p>
                    )}
                    <BallotRaceOptions race={race} />
                  </div>
                ))}
              </div>
            </section>
          )}

          {ballot.houseRaces.length > 0 && (
            <HouseSection state={ballot.state} houseRaces={ballot.houseRaces} />
          )}

          {!hasFederalRaces && (
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
