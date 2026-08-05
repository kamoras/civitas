"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import MatrixRain from "@/components/effects/MatrixRain";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import GlitchText from "@/components/effects/GlitchText";
import RaceMap from "@/components/elections/RaceMap";
import { STATE_CODES } from "@/lib/stateCodes";
import PviMethodologyNote from "@/components/elections/PviMethodologyNote";
import { formatPvi, stateBallotHref } from "@/lib/elections";
import { fetchPviMap, fetchRaces } from "@/lib/api";
import type { PviMap, RaceSummary } from "@/types/election";

function pviFillColor(pvi: number | null): string {
  if (pvi == null) return "rgba(0, 255, 65, 0.15)";
  if (pvi === 0) return "rgba(255, 255, 255, 0.2)";
  return pvi > 0 ? "rgba(255, 60, 60, 0.3)" : "rgba(60, 120, 255, 0.3)";
}

function pviHoverColor(pvi: number | null): string {
  if (pvi == null) return "rgba(0, 255, 65, 0.35)";
  if (pvi === 0) return "rgba(255, 255, 255, 0.4)";
  return pvi > 0 ? "rgba(255, 60, 60, 0.5)" : "rgba(60, 120, 255, 0.5)";
}

/** Race counts per state, for the index tiles. */
function summarize(races: RaceSummary[] | null) {
  const counts: Record<string, { senate: number; house: number }> = {};
  for (const race of races ?? []) {
    const entry = counts[race.state] || { senate: 0, house: 0 };
    if (race.office === "S") entry.senate += 1;
    else entry.house += 1;
    counts[race.state] = entry;
  }
  return counts;
}

function ElectionsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [races, setRaces] = useState<RaceSummary[] | null>(null);
  const [pvi, setPvi] = useState<PviMap | null>(null);
  const [error, setError] = useState<string | null>(null);

  // /elections used to BE the per-state view via ?state=XX, and those URLs
  // are linked from the Action Center and exist in anything anyone has
  // shared. Forward them to the real state page rather than breaking them.
  const legacyState = searchParams.get("state");
  useEffect(() => {
    if (legacyState) router.replace(stateBallotHref(legacyState));
  }, [legacyState, router]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchRaces(), fetchPviMap()])
      .then(([r, p]) => {
        if (cancelled) return;
        setRaces(r);
        setPvi(p);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || "Failed to load races");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const counts = useMemo(() => summarize(races), [races]);
  const cycleYear = races?.[0]?.cycleYear;

  return (
    <div className="min-h-screen bg-crt-black text-matrix-green">
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-8">
            <GlitchText
              as="h1"
              text={cycleYear ? `${cycleYear} ELECTIONS` : "ELECTIONS"}
              className="font-pixel text-xl sm:text-3xl text-matrix-green neon-green mb-2 block"
            />
            <p className="font-mono text-xs text-matrix-green/40">
              PICK YOUR STATE — FEDERAL CONTESTS AND STATEWIDE BALLOT MEASURES
            </p>
          </div>

          {error && (
            <div className="text-center py-16 font-mono text-xs text-red-400/60">{error}</div>
          )}

          {!error && !races && (
            <div className="text-center py-16 font-mono text-xs text-matrix-green/30 tracking-widest animate-pulse">
              LOADING...
            </div>
          )}

          {races && (
            <>
              <div className="terminal-window p-4 mb-6">
                <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                  <h2 className="font-pixel text-xs text-matrix-green/50">{">"} SELECT A STATE</h2>
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className="flex items-center gap-1.5 text-[10px] text-matrix-green/40">
                      <span
                        className="w-3 h-2 inline-block"
                        style={{ backgroundColor: "rgba(255, 60, 60, 0.4)" }}
                      />{" "}
                      R-LEANING
                    </span>
                    <span className="flex items-center gap-1.5 text-[10px] text-matrix-green/40">
                      <span
                        className="w-3 h-2 inline-block"
                        style={{ backgroundColor: "rgba(60, 120, 255, 0.4)" }}
                      />{" "}
                      D-LEANING
                    </span>
                  </div>
                </div>
                <RaceMap
                  selectedState={null}
                  onStateClick={(state) => router.push(stateBallotHref(state))}
                  getFillColor={(state) => pviFillColor(pvi?.states[state] ?? null)}
                  getHoverFillColor={(state) => pviHoverColor(pvi?.states[state] ?? null)}
                />
                <PviMethodologyNote meta={pvi?.meta} />
              </div>

              {/* The map's keyboard/screen-reader equivalent. Not a
                  disclosure this time — with the map now navigating rather
                  than filtering, this grid IS the navigation for anyone not
                  using a pointer, so it stays open. */}
              <h2 className="font-pixel text-xs text-neon-cyan/60 mb-3 tracking-widest">
                ALL STATES
              </h2>
              <ul className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2 list-none">
                {STATE_CODES.map((code) => {
                  const count = counts[code];
                  const lean = pvi?.states[code] ?? null;
                  return (
                    <li key={code}>
                      <Link
                        href={stateBallotHref(code)}
                        className="flex items-center justify-between gap-2 border border-matrix-green/20
                                   bg-terminal-bg/50 px-3 py-2.5 hover:border-neon-cyan/40 transition-colors"
                      >
                        <span className="font-pixel text-xs text-white/90">{code}</span>
                        <span className="font-mono text-[10px] text-matrix-green/40 text-right">
                          {count
                            ? `${count.senate ? "SEN · " : ""}${count.house} HOUSE`
                            : "NO RACES ON FILE"}
                          {lean !== null && (
                            <span className="ml-2 text-matrix-green/30">{formatPvi(lean)}</span>
                          )}
                        </span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>
      </main>
      <BackToTop />
      <Footer />
    </div>
  );
}

export default function ElectionsPage() {
  return (
    <Suspense fallback={null}>
      <ElectionsPageContent />
    </Suspense>
  );
}
