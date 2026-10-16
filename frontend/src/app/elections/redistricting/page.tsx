import Link from "next/link";
import { connection } from "next/server";
import type { RedistrictingIndex } from "@/types/redistricting";
import { pageMetadata } from "@/lib/site";
import { describeRedistrictingIndex } from "@/lib/seo";
import { REDISTRICTING_HREF, netWord, redistrictingHref } from "@/lib/redistricting";
import { safeHref } from "@/lib/formatting";
import SectionTabs from "@/components/layout/SectionTabs";
import Navbar from "@/components/layout/Navbar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
import { SeatStrip } from "@/components/redistricting/RedistrictingView";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";

async function fetchIndex(): Promise<RedistrictingIndex | null> {
  const res = await fetch(`${BACKEND}/api/elections/redistricting`, { next: { revalidate: 3600 } });
  if (!res.ok) throw new Error(`redistricting index: HTTP ${res.status}`);
  return (await res.json()) as RedistrictingIndex;
}

export async function generateMetadata() {
  // Rendered per request (below), so this reads the backend, not a build.
  const index = await fetchIndex().catch(() => null);
  return pageMetadata({
    ...describeRedistrictingIndex(
      index?.cycle ?? null,
      index?.states.length ?? 0,
      index?.upcoming ?? true
    ),
    path: REDISTRICTING_HREF,
  });
}

export default async function RedistrictingIndexPage() {
  // Never prerendered: `next build` runs where the backend isn't reachable.
  await connection();
  const index = await fetchIndex();
  const states = index?.states ?? [];
  return (
    <div className="min-h-screen bg-surface-base font-sans text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="px-4 pb-16 pt-[var(--header-clearance)]">
        <div className="mx-auto max-w-4xl">
          <SectionTabs section="elections" active="redistricting" />
          <PageMasthead eyebrow={`Elections · ${index?.cycle ?? ""}`} title="New House maps">
            <p>
              {states.length} states {index?.upcoming === false ? "voted" : "vote"} on new U.S.
              House district lines in {index?.cycle}. Pick a state to see whether your district
              changed, where its residents lived before, and how its lean moved. Counted from Census
              blocks; every figure downloads.
            </p>
          </PageMasthead>

          {/* Every redrawn state in one row, so a reader who knows their
              state goes straight to it rather than scrolling the list. */}
          {states.length > 0 && (
            <nav aria-label="Redrawn states" className="mt-6">
              <ul className="flex flex-wrap gap-2">
                {states.map((s) => (
                  <li key={s.state}>
                    <Link
                      href={redistrictingHref(s.state)}
                      className="flex min-h-11 min-w-11 items-center justify-center border border-white/15 px-3 font-mono text-sm tracking-[0.12em] text-ink-lo transition-colors hover:border-phos/40 hover:text-phos"
                    >
                      {s.state}
                      <span className="sr-only"> {s.stateName}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </nav>
          )}

          <ul className="mt-6">
            {states.map((s) => (
              <li key={s.state} className="border-t border-white/[0.07] py-4">
                <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                  <Link
                    href={redistrictingHref(s.state)}
                    className="font-display text-lg font-bold text-ink-hi hover:text-phos"
                  >
                    {s.stateName}
                  </Link>
                  <span className="font-mono text-[11px] tracking-[0.1em] text-ink-min">
                    {s.summary.seats} SEATS · {s.summary.residentsMovedPct}% IN A MOSTLY NEW
                    DISTRICT · R-LEANING {s.summary.seatsByLeanBefore.R} →{" "}
                    {s.summary.seatsByLeanAfter.R} {netWord(s.summary.netSeatChangeByLean.R)} ·
                    D-LEANING {s.summary.seatsByLeanBefore.D} → {s.summary.seatsByLeanAfter.D}{" "}
                    {netWord(s.summary.netSeatChangeByLean.D)}
                  </span>
                </div>
                {s.map?.title && <p className="mt-0.5 text-xs text-ink-lo">{s.map.title}</p>}
                <div className="mt-2 space-y-2">
                  <SeatStrip label="BEFORE" counts={s.summary.seatsByLeanBefore} />
                  <SeatStrip label="AFTER" counts={s.summary.seatsByLeanAfter} />
                </div>
                <Link
                  href={redistrictingHref(s.state)}
                  className="mt-2 inline-block min-h-[44px] py-2 font-mono text-xs tracking-[0.1em] text-signal-cyan hover:text-phos"
                  aria-label={`What changed in ${s.stateName}`}
                >
                  [ WHAT CHANGED → ]
                </Link>
              </li>
            ))}
          </ul>

          {(index?.heldMaps ?? []).length > 0 && (
            <section aria-labelledby="held" className="mt-8 border-t border-white/[0.09] pt-4">
              <h2 id="held" className="font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
                Kept their old lines
              </h2>
              <ul className="mt-2 space-y-2 text-sm text-ink-lo">
                {index!.heldMaps.map((h) => {
                  const url = safeHref(h.url);
                  return (
                    <li key={h.state}>
                      <span className="text-ink-hi">{h.stateName}:</span> {h.reason}
                      {url && (
                        <>
                          {" "}
                          <a
                            href={url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="font-mono text-xs text-signal-cyan hover:text-phos"
                          >
                            [ OFFICIAL SOURCE ↗ ]
                          </a>
                        </>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          )}

          <p className="mt-8 max-w-2xl text-xs leading-relaxed text-ink-min">
            Lean is Cook PVI: how a district voted for president relative to the nation, on the same
            presidential elections before and after, so a change in lean is a change in the lines.
            It is not a forecast. Each state page explains the method and lists every source.
          </p>
        </div>
      </main>
      <Footer />
    </div>
  );
}
