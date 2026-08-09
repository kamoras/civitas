"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Navbar from "@/components/layout/Navbar";
import MatrixRain from "@/components/effects/MatrixRain";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import GlitchText from "@/components/effects/GlitchText";
import Link from "next/link";
import RaceMap, { FIPS_TO_STATE } from "@/components/elections/RaceMap";
import PviMethodologyNote from "@/components/elections/PviMethodologyNote";
import { formatPvi, pviColor } from "@/lib/elections";
import { fetchPviMap } from "@/lib/api";
import type { PviMap } from "@/types/election";

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

// DC has no voting House/Senate race (STATES_WITH_FEDERAL_RACES on the
// backend excludes it the same way) even though it's clickable on the
// map's SVG — filtered out of both the map's click target and the
// directory grid rather than letting either lead to a 404.
const STATES = Array.from(new Set(Object.values(FIPS_TO_STATE)))
  .filter((s) => s !== "DC")
  .sort();

export default function ElectionsPage() {
  const router = useRouter();
  const [pvi, setPvi] = useState<PviMap | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchPviMap()
      .then((p) => {
        if (!cancelled) setPvi(p);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || "Failed to load election data");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const goToBallot = (state: string) => router.push(`/elections/states/${state}`);

  return (
    <div className="min-h-screen bg-crt-black text-matrix-green">
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-8">
            <GlitchText
              as="h1"
              text={pvi?.cycleYear ? `${pvi.cycleYear} MIDTERM ELECTIONS` : "MIDTERM ELECTIONS"}
              className="font-pixel text-xl sm:text-3xl text-matrix-green neon-green mb-2 block"
            />
            <p className="font-mono text-xs text-matrix-green/40">
              FIND YOUR STATE&apos;S BALLOT — CANDIDATES, PARTISAN LEAN, AND LIVE COVERAGE
            </p>
          </div>

          {error && (
            <div className="text-center py-16 font-mono text-xs text-red-400/60">{error}</div>
          )}

          {!error && !pvi && (
            <div className="text-center py-16 font-mono text-xs text-matrix-green/30 tracking-widest animate-pulse">
              LOADING...
            </div>
          )}

          {pvi && (
            <>
              <div className="terminal-window p-4 mb-6">
                <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                  <h3 className="font-pixel text-xs text-matrix-green/50">
                    {">"} CLICK A STATE FOR ITS BALLOT
                  </h3>
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
                  onStateClick={(state) => {
                    if (state !== "DC") goToBallot(state);
                  }}
                  getFillColor={(state) => pviFillColor(pvi.states[state] ?? null)}
                  getHoverFillColor={(state) => pviHoverColor(pvi.states[state] ?? null)}
                />
                <PviMethodologyNote meta={pvi.meta} />
              </div>

              <h2 className="font-pixel text-xs text-matrix-green/50 mb-3">{">"} ALL STATES</h2>
              <div className="grid grid-cols-3 sm:grid-cols-5 md:grid-cols-6 lg:grid-cols-8 gap-2">
                {STATES.map((state) => (
                  <Link
                    key={state}
                    href={`/elections/states/${state}`}
                    className="flex items-center justify-between border border-matrix-green/20 bg-terminal-bg/50 px-3 py-2 hover:border-neon-cyan/40 transition-colors"
                  >
                    <span className="font-pixel text-xs text-white/90">{state}</span>
                    <span className={`font-pixel text-[9px] ${pviColor(pvi.states[state] ?? null)}`}>
                      {formatPvi(pvi.states[state] ?? null)}
                    </span>
                  </Link>
                ))}
              </div>
            </>
          )}
        </div>
      </main>
      <BackToTop />
      <Footer />
    </div>
  );
}
