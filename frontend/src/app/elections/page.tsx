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
import { formatPvi, pviColor } from "@/lib/elections";
import { fetchPviMap } from "@/lib/api";
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
          <PageMasthead
            eyebrow="Elections · partisan lean by state"
            title={pvi?.cycleYear ? `${pvi.cycleYear} midterm ballot` : "Midterm ballot"}
          >
            Pick a state for its candidates, their filings, statewide ballot measures, and the
            coverage we have ingested. Shading is partisan lean, not a forecast.
          </PageMasthead>

          {error && (
            <div
              role="alert"
              className="mt-6 border-l-2 border-signal-red bg-surface px-4 py-3 font-mono text-sm text-signal-red"
            >
              {error}
            </div>
          )}

          {!error && !pvi && (
            <p
              role="status"
              aria-live="polite"
              className="mt-8 font-mono text-sm tracking-[0.12em] text-ink-min"
            >
              READING THE LEAN MAP…
            </p>
          )}

          {pvi && (
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
                        href={`/elections/states/${state}`}
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
