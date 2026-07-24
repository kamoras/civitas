"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Navbar from "@/components/layout/Navbar";
import MatrixRain from "@/components/effects/MatrixRain";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import GlitchText from "@/components/effects/GlitchText";
import RaceMap from "@/components/elections/RaceMap";
import RaceCard from "@/components/elections/RaceCard";
import PviMethodologyNote from "@/components/elections/PviMethodologyNote";
import { compareRaces, formatPvi } from "@/lib/elections";
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

function ElectionsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  // Derived from the URL (not one-shot useState) so the filter is
  // shareable, back-button-able, and cross-navigation between
  // /elections?state=GA and ?state=TX works.
  const selectedState = searchParams.get("state") || null;
  const setSelectedState = (state: string | null) => {
    router.replace(state ? `/elections?state=${encodeURIComponent(state)}` : "/elections", {
      scroll: false,
    });
  };
  const [races, setRaces] = useState<RaceSummary[] | null>(null);
  const [pvi, setPvi] = useState<PviMap | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  const filteredRaces = useMemo(() => {
    if (!races) return [];
    const list = selectedState ? races.filter((r) => r.state === selectedState) : races;
    // State A→Z, Senate before House within a state, district ascending.
    return list.slice().sort(compareRaces);
  }, [races, selectedState]);

  return (
    <div className="min-h-screen bg-crt-black text-matrix-green">
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-8">
            <GlitchText
              as="h1"
              text="2026 MIDTERM ELECTIONS"
              className="font-pixel text-xl sm:text-3xl text-matrix-green neon-green mb-2 block"
            />
            <p className="font-mono text-xs text-matrix-green/40">
              EVERY SENATE AND HOUSE RACE — CANDIDATES, FUNDRAISING, AND LIVE COVERAGE
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
                  <h3 className="font-pixel text-xs text-matrix-green/50">{">"} SELECT A STATE</h3>
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
                  selectedState={selectedState}
                  onStateClick={(state) => setSelectedState(selectedState === state ? null : state)}
                  getFillColor={(state) => pviFillColor(pvi?.states[state] ?? null)}
                  getHoverFillColor={(state) => pviHoverColor(pvi?.states[state] ?? null)}
                />
                <PviMethodologyNote meta={pvi?.meta} />
              </div>

              {selectedState && (
                <div className="flex items-center justify-between mb-4">
                  <h2 className="font-pixel text-sm text-white/90">
                    {selectedState} — {formatPvi(pvi?.states[selectedState] ?? null)}
                  </h2>
                  <button
                    onClick={() => setSelectedState(null)}
                    className="font-mono text-[10px] text-matrix-green/50 hover:text-matrix-green tracking-widest"
                  >
                    CLEAR FILTER ✕
                  </button>
                </div>
              )}

              {filteredRaces.length === 0 ? (
                <div className="text-center py-16 font-mono text-xs text-matrix-green/30 tracking-widest">
                  NO RACES ON RECORD FOR THIS STATE YET
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {filteredRaces.map((race) => (
                    <RaceCard key={race.id} race={race} />
                  ))}
                </div>
              )}
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
