"use client";

import Link from "next/link";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import CandidateCard from "@/components/elections/CandidateCard";
import CoverageFeed from "@/components/elections/CoverageFeed";
import RaceFinancials from "@/components/elections/RaceFinancials";
import PviMethodologyNote from "@/components/elections/PviMethodologyNote";
import { formatPvi, isActiveCandidate, raceTitleLabel } from "@/lib/elections";
import type { RaceDetail } from "@/types/election";

/* The `.dat` titlebars come off here for the same reason they came off the
   scorecard: a race is a contest between people, not a file on a disk, and
   four stacked fake window chromes were the loudest thing on the page.
   Sections are now separated by the rule weights the rest of the register
   uses. */

/** Solid palette hex — a lean figure must never render below the contrast floor. */
function pviTextColor(pvi: number | null): string {
  if (pvi == null) return "text-ink-min";
  if (pvi === 0) return "text-ink";
  return pvi > 0 ? "text-signal-red" : "text-dem-blue";
}

function SectionHeading({ children, aside }: { children: React.ReactNode; aside?: string }) {
  return (
    <h2 className="mb-3 flex items-baseline justify-between border-b border-white/15 pb-2 font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
      <span>{children}</span>
      {aside && <span aria-hidden="true">{aside}</span>}
    </h2>
  );
}

export default function RaceDetailClient({ race }: { race: RaceDetail }) {
  // FEC candidate files include paper filers and prior-cycle records —
  // collapse those under "OTHER FEC FILERS" so the page stays honest
  // without deleting anyone.
  const activeCandidates = race.candidates.filter(isActiveCandidate);
  const otherFilers = race.candidates.filter((c) => !isActiveCandidate(c));

  return (
    <div className="min-h-screen bg-surface-base text-ink">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-24 sm:px-6">
        <div className="mx-auto max-w-4xl">
          <Link
            href="/elections"
            className="mb-6 inline-block font-mono text-xs uppercase tracking-[0.12em] text-ink-lo transition-colors hover:text-ink-hi"
          >
            ← Back to races
          </Link>

          {/* ── Masthead ── */}
          <header className="border-b-3 border-phos pb-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
                  Race · {race.id}
                </p>
                <h1 className="mt-3 font-display text-3xl font-extrabold uppercase leading-none tracking-[-0.02em] text-ink-hi sm:text-4xl">
                  {race.cycleYear} {raceTitleLabel(race)}
                </h1>
              </div>

              <div className="text-right">
                <p className="font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
                  Partisan lean
                </p>
                <p className={`mt-1 font-mono text-2xl ${pviTextColor(race.pvi)}`}>
                  {formatPvi(race.pvi)}
                </p>
                {race.pviLevel === "state" && race.office === "H" && (
                  <p className="font-mono text-xs text-ink-min">statewide</p>
                )}
              </div>
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-3 font-mono text-xs text-ink-lo">
              <span>
                {race.candidates.length} {race.candidates.length === 1 ? "candidate" : "candidates"}{" "}
                on record
              </span>
              {race.isSpecial && (
                <span className="border border-signal-amber/40 px-2 py-0.5 tracking-[0.12em] text-signal-amber">
                  SPECIAL ELECTION
                </span>
              )}
            </div>

            <div className="mt-3">
              <PviMethodologyNote />
            </div>
          </header>

          {/* ── Candidates ── */}
          <section className="mt-10">
            <SectionHeading aside={`${activeCandidates.length} active`}>Candidates</SectionHeading>

            {race.candidates.length === 0 && (
              <p className="font-mono text-base text-ink-min">
                No candidates on record for this race yet.
              </p>
            )}

            <div className="space-y-3">
              {activeCandidates.map((c) => (
                <CandidateCard key={c.id} candidate={c} />
              ))}
            </div>

            {otherFilers.length > 0 && (
              <details className="mt-4 border border-white/[0.09] bg-surface p-4">
                <summary className="cursor-pointer font-mono text-xs uppercase tracking-[0.14em] text-ink-lo hover:text-ink-hi">
                  Other FEC filers ({otherFilers.length})
                </summary>
                <p className="mt-2 font-mono text-xs leading-relaxed text-ink-min">
                  Paper filers and prior-cycle candidates on FEC record who have not raised funds
                  this cycle.
                </p>
                <div className="mt-3 space-y-3">
                  {otherFilers.map((c) => (
                    <CandidateCard key={c.id} candidate={c} />
                  ))}
                </div>
              </details>
            )}
          </section>

          {/* ── Fundraising ── */}
          <section className="mt-10">
            <SectionHeading>Fundraising</SectionHeading>
            <RaceFinancials candidates={activeCandidates} />
            <p className="mt-3 font-mono text-xs leading-relaxed text-ink-min">
              Per FEC filings — totals lag by up to a quarter and amendments. Source: fec.gov.
            </p>
          </section>

          {/* ── Coverage ── */}
          <section className="mt-10">
            <SectionHeading aside={`${race.coverage.length} items`}>Coverage</SectionHeading>
            <CoverageFeed items={race.coverage} />
          </section>
        </div>
      </main>
      <BackToTop />
      <Footer />
    </div>
  );
}
